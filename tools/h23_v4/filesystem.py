"""Descriptor-relative reads and a single-file, no-replace evidence transaction.

The configured private evidence root MUST already exist. A committed evidence
ZIP contains the exact capture.zip snapshot and receipt.json. A hard-link into
the committed namespace atomically publishes both, without replacement.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import uuid
import zipfile

from .schema import PolicyError

MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_STALE_STAGING = 16
MAX_ROOT_ENTRIES = 8192
_STAGING = re.compile(r"\.h23v4-staging-[0-9a-f]{32}\Z")
_STAGED_FILES = frozenset(("bundle.tmp", "bundle.prepared"))
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True)
class FileSnapshot:
    size: int
    sha256: str
    nlink: int
    data: bytes | None


@dataclass(frozen=True)
class EvidenceRoot:
    fd: int
    path: Path
    protected_roots: tuple


@dataclass(frozen=True)
class Publication:
    path: Path
    archive_sha256: str
    status: str
    durability: str
    warning_codes: tuple


def _parts(relative):
    if type(relative) is not str or not relative or relative.startswith("/") or "\\" in relative:
        raise PolicyError("LOCAL_PATH")
    if re.match(r"^[A-Za-z]:", relative) or any(ord(char) < 32 or ord(char) == 127 for char in relative):
        raise PolicyError("LOCAL_PATH")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PolicyError("LOCAL_PATH")
    return parts


def _absolute(path):
    try:
        raw = os.fspath(path)
    except TypeError:
        raise PolicyError("LOCAL_ROOT_PATH") from None
    if type(raw) is not str or not raw.startswith("/") or "\\" in raw:
        raise PolicyError("LOCAL_ROOT_PATH")
    if raw != "/" and (raw.endswith("/") or "//" in raw):
        raise PolicyError("LOCAL_ROOT_PATH")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise PolicyError("LOCAL_ROOT_PATH")
    if any(part in (".", "..") for part in raw.split("/")[1:]):
        raise PolicyError("LOCAL_ROOT_PATH")
    return Path(raw)


def _private(info):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PolicyError("LOCAL_ROOT_PERMISSIONS")


def _overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def _protected(path, protected_roots):
    try:
        path = path.resolve(strict=False)
    except (OSError, RuntimeError):
        raise PolicyError("LOCAL_ROOT_UNAVAILABLE") from None
    for protected in protected_roots:
        target = Path(protected)
        if not target.is_absolute():
            raise PolicyError("LOCAL_PROTECTED_ROOT")
        # Protected paths are trusted local configuration, never archive values.
        try:
            target = target.resolve(strict=False)
        except (OSError, RuntimeError):
            raise PolicyError("LOCAL_PROTECTED_ROOT") from None
        if _overlaps(path, target):
            raise PolicyError("LOCAL_ROOT_PROTECTED")


def _reject_git_directory(descriptor):
    """Recognize conventional worktree and bare markers without reading Git.

    Unusual externally configured Git directories require trusted exclusions.
    Marker aliases are never followed and fail closed when the shape matches.
    """
    try:
        os.stat(".git", dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise PolicyError("LOCAL_ROOT_GIT")
    markers = {}
    for name in ("HEAD", "objects", "refs"):
        try:
            markers[name] = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
    head = markers["HEAD"].st_mode
    directories = (markers["objects"].st_mode, markers["refs"].st_mode)
    if ((stat.S_ISREG(head) or stat.S_ISLNK(head))
            and all(stat.S_ISDIR(mode) or stat.S_ISLNK(mode) for mode in directories)):
        raise PolicyError("LOCAL_ROOT_GIT")


@contextmanager
def anchor_directory(path, *, private=False, protected_roots=(), reject_git=False):
    """Open EVERY absolute-path component from / without following symlinks."""
    path = _absolute(path)
    if not path.is_absolute() or path.anchor != "/" or any(part in (".", "..") for part in path.parts):
        raise PolicyError("LOCAL_ROOT_PATH")
    _protected(path, protected_roots)
    descriptor = None
    try:
        descriptor = os.open("/", _DIR_FLAGS)
        if reject_git:
            _reject_git_directory(descriptor)
        for component in path.parts[1:]:
            next_fd = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
            if reject_git:
                _reject_git_directory(descriptor)
        if private:
            _private(os.fstat(descriptor))
        # Opening a symlink after a path race fails above; no directory is made.
        _protected(path, protected_roots)
        yield descriptor
    except PolicyError:
        raise
    except OSError:
        raise PolicyError("LOCAL_ROOT_UNAVAILABLE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def anchored_root(root, protected_roots):
    with anchor_directory(root, private=True, protected_roots=protected_roots, reject_git=True) as descriptor:
        handle = EvidenceRoot(descriptor, Path(root), tuple(protected_roots))
        _dedicated_root(handle)
        yield handle


def read_regular(dir_fd, relative, *, max_bytes=MAX_FILE_BYTES, include=True, reject_hardlinks=False, private=False):
    """Observe hash and size from one opened stream; optionally retain its bytes."""
    parts = _parts(relative)
    parent = os.dup(dir_fd)
    file_fd = None
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, _DIR_FLAGS, dir_fd=parent)
            os.close(parent)
            parent = next_fd
        file_fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=parent)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or (reject_hardlinks and before.st_nlink != 1):
            raise PolicyError("LOCAL_FILE_TYPE")
        if private and (before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600):
            raise PolicyError("LOCAL_FILE_PERMISSIONS")
        if before.st_size > max_bytes:
            raise PolicyError("LOCAL_FILE_SIZE")
        digest = hashlib.sha256()
        chunks = [] if include else None
        total = 0
        while True:
            chunk = os.read(file_fd, min(65536, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise PolicyError("LOCAL_FILE_SIZE")
            digest.update(chunk)
            if include:
                chunks.append(chunk)
        after = os.fstat(file_fd)
        if reject_hardlinks and after.st_nlink != 1:
            raise PolicyError("LOCAL_FILE_TYPE")
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or total != before.st_size:
            raise PolicyError("LOCAL_FILE_CHANGED")
        return FileSnapshot(total, digest.hexdigest(), before.st_nlink, b"".join(chunks) if include else None)
    except PolicyError:
        raise
    except OSError:
        raise PolicyError("LOCAL_FILE_UNAVAILABLE") from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent)


def load_snapshot(path, max_bytes=MAX_SNAPSHOT_BYTES):
    path = Path(path)
    if not path.is_absolute():
        raise PolicyError("LOCAL_SNAPSHOT_PATH")
    with anchor_directory(path.parent) as descriptor:
        return read_regular(descriptor, path.name, max_bytes=max_bytes).data


@contextmanager
def _locked(handle):
    descriptor = os.open(".h23v4.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=handle.fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PolicyError("LOCAL_LOCK")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _reserve_root_capacity(handle, final_name):
    """Read-only reservation: missing lock plus new stage and final entry.

    An existing final entry needs no stage/final slots. It is still independently
    checked under the publication lock before an idempotent result is returned.
    Remnants are not credited until recovery has actually removed them.
    """
    names = set()
    with os.scandir(handle.fd) as iterator:
        for entry in iterator:
            names.add(entry.name)
            if len(names) > MAX_ROOT_ENTRIES:
                raise PolicyError("LOCAL_ROOT_LIMIT")
    required = int(".h23v4.lock" not in names) + (0 if final_name in names else 2)
    if len(names) + required > MAX_ROOT_ENTRIES:
        raise PolicyError("LOCAL_ROOT_LIMIT")


def _checked_staged_entries(descriptor):
    _private(os.fstat(descriptor))
    entries = []
    with os.scandir(descriptor) as iterator:
        for entry in iterator:
            entries.append(entry.name)
            if len(entries) > len(_STAGED_FILES) or entry.name not in _STAGED_FILES:
                raise PolicyError("LOCAL_STAGING")
    for entry in entries:
        info = os.stat(entry, dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PolicyError("LOCAL_STAGING")
    return entries


def _dedicated_root(handle):
    """Read-only, bounded preflight; no foreign directory trees are permitted.

    Together with ancestor marker checks, this excludes unrelated Git worktrees
    without recursively scanning arbitrary local directory trees.
    """
    with os.scandir(handle.fd) as iterator:
        for count, entry in enumerate(iterator, 1):
            if count > MAX_ROOT_ENTRIES:
                raise PolicyError("LOCAL_ROOT_LIMIT")
            if entry.name == ".git":
                raise PolicyError("LOCAL_ROOT_GIT")
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                continue
            if not stat.S_ISDIR(info.st_mode) or _STAGING.fullmatch(entry.name) is None:
                raise PolicyError("LOCAL_ROOT_NOT_DEDICATED")
            descriptor = os.open(entry.name, _DIR_FLAGS, dir_fd=handle.fd)
            try:
                _checked_staged_entries(descriptor)
            finally:
                os.close(descriptor)


def _remove_staging(root_fd, name):
    """Bounded cleanup removes only our exact staged regular-file names."""
    if _STAGING.fullmatch(name) is None:
        raise PolicyError("LOCAL_STAGING")
    descriptor = os.open(name, _DIR_FLAGS, dir_fd=root_fd)
    try:
        entries = _checked_staged_entries(descriptor)
        for entry in entries:
            os.unlink(entry, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=root_fd)


def recover_staging(handle, max_remnants=MAX_STALE_STAGING):
    """Recover recognized remnants; never touch committed evidence or aliases."""
    remnants = []
    with os.scandir(handle.fd) as iterator:
        for count, entry in enumerate(iterator, 1):
            if count > MAX_ROOT_ENTRIES:
                raise PolicyError("LOCAL_ROOT_LIMIT")
            if _STAGING.fullmatch(entry.name):
                remnants.append(entry.name)
                if len(remnants) > max_remnants:
                    raise PolicyError("LOCAL_STAGING_LIMIT")
    for name in sorted(remnants):
        _remove_staging(handle.fd, name)


def _bundle(snapshot, receipt):
    if type(snapshot) is not bytes or len(snapshot) > MAX_SNAPSHOT_BYTES:
        raise PolicyError("LOCAL_SNAPSHOT_SIZE")
    if type(receipt) is not bytes or len(receipt) > MAX_RECEIPT_BYTES:
        raise PolicyError("LOCAL_RECEIPT_SIZE")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in (("capture.zip", snapshot), ("receipt.json", receipt)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payload)
    return stream.getvalue()


def _write_new(dir_fd, name, data):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PolicyError("LOCAL_FILE_TYPE")
        view = memoryview(data)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise PolicyError("LOCAL_WRITE")
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _existing_bundle(handle, final_name):
    try:
        return read_regular(handle.fd, final_name,
                            max_bytes=MAX_SNAPSHOT_BYTES + MAX_RECEIPT_BYTES + 4096,
                            reject_hardlinks=True, private=True)
    except PolicyError as error:
        if error.code != "LOCAL_FILE_UNAVAILABLE":
            raise PolicyError("EVIDENCE_CONFLICT") from None
        # A no-follow read failure is absence only when no entry exists.
        try:
            os.stat(final_name, dir_fd=handle.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        raise PolicyError("EVIDENCE_CONFLICT") from None


def _fsync_existing_bundle(handle, final_name):
    """Reconfirm the already verified existing file and its published name."""
    descriptor = os.open(final_name, _FILE_FLAGS, dir_fd=handle.fd)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise PolicyError("LOCAL_FILE_TYPE")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(handle.fd)


def publish_evidence(handle, snapshot_bytes, receipt_bytes):
    """Atomically publish a complete bundle, or preserve an exact prior bundle.

    The successful no-replace link is the commit point. Subsequent durability
    and cleanup errors are bounded warnings and never turn a committed result
    into rejection. Already committed bytes are never rolled back or changed.
    """
    _private(os.fstat(handle.fd))
    digest = hashlib.sha256(snapshot_bytes).hexdigest()
    final_name = digest + ".evidence.zip"
    data = _bundle(snapshot_bytes, receipt_bytes)
    status = None
    warnings = set()
    durability = "CONFIRMED"

    def confirm_durability(operation):
        nonlocal durability
        try:
            operation()
        except (OSError, PolicyError):
            durability = "UNCONFIRMED"
            warnings.add("PUBLICATION_DURABILITY_UNCONFIRMED")

    try:
        # Reserve before even creating the fixed lock entry. New publication
        # needs at most three slots; exact reimport needs only a missing lock.
        _reserve_root_capacity(handle, final_name)
        with _locked(handle):
            recover_staging(handle)
            existing = _existing_bundle(handle, final_name)
            if existing is not None:
                if existing.data != data:
                    raise PolicyError("EVIDENCE_CONFLICT")
                status = "IDEMPOTENT"
                confirm_durability(lambda: _fsync_existing_bundle(handle, final_name))
            else:
                # Recheck under the lock after recovery, before new child entries.
                _reserve_root_capacity(handle, final_name)
                stage_name = ".h23v4-staging-" + uuid.uuid4().hex
                stage_fd = None
                created = False
                try:
                    os.mkdir(stage_name, mode=0o700, dir_fd=handle.fd)
                    created = True
                    stage_fd = os.open(stage_name, _DIR_FLAGS, dir_fd=handle.fd)
                    _private(os.fstat(stage_fd))
                    _write_new(stage_fd, "bundle.tmp", data)
                    os.rename("bundle.tmp", "bundle.prepared", src_dir_fd=stage_fd, dst_dir_fd=stage_fd)
                    os.fsync(stage_fd)
                    try:
                        os.link("bundle.prepared", final_name, src_dir_fd=stage_fd, dst_dir_fd=handle.fd,
                                follow_symlinks=False)
                    except FileExistsError:
                        existing = _existing_bundle(handle, final_name)
                        if existing is None or existing.data != data:
                            raise PolicyError("EVIDENCE_CONFLICT")
                        status = "IDEMPOTENT"
                        confirm_durability(lambda: _fsync_existing_bundle(handle, final_name))
                    else:
                        status = "CREATED"
                        confirm_durability(lambda: os.fsync(handle.fd))
                finally:
                    cleanup_error = None
                    if stage_fd is not None:
                        try:
                            os.close(stage_fd)
                        except OSError as error:
                            cleanup_error = error
                    if created:
                        try:
                            _remove_staging(handle.fd, stage_name)
                        except (OSError, PolicyError) as error:
                            cleanup_error = error
                    if cleanup_error is not None:
                        if status is None:
                            raise cleanup_error
                        warnings.add("PUBLICATION_CLEANUP_UNCONFIRMED")
                # Confirm cleanup changes too; any prior failure stays explicit.
                confirm_durability(lambda: os.fsync(handle.fd))
    except (OSError, PolicyError) as error:
        if status is None:
            if isinstance(error, PolicyError):
                raise
            raise PolicyError("LOCAL_STORAGE") from None
        # Descriptor/lock teardown is also after the irrevocable commit point.
        warnings.add("PUBLICATION_CLEANUP_UNCONFIRMED")
    return Publication(handle.path / final_name, digest, status, durability, tuple(sorted(warnings)))
