"""Descriptor-bound byte copies of admitted public assets into private roots.

Public source hardlinks are permitted: full stream identity, not link count, is
the admission authority. Published copies use fresh inodes with one link. This
module has no trusted raw-row intake or private verifier dependency.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import uuid

from eval._contracts import ContractError
from eval.solver_task import PublicAssetRef, SolverTask
from tools.h23_v4.filesystem import anchor_directory
from tools.h23_v4.schema import PolicyError

MAX_PUBLIC_ASSET_BYTES = 8 * 1024 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class StagingError(ContractError):
    """An asset cannot be staged without breaking its admitted identity."""


@dataclass(frozen=True, slots=True)
class StagedPublicAsset:
    asset: PublicAssetRef
    path: Path

    def __post_init__(self) -> None:
        if type(self) is not StagedPublicAsset or type(self.asset) is not PublicAssetRef:
            raise StagingError("staged public asset requires the exact contract type")
        PublicAssetRef.__post_init__(self.asset)
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise StagingError("staged asset requires an absolute Path")


def _root_path(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise StagingError("an explicit absolute staging/source root Path is required")
    return value


def _state(info: os.stat_result) -> tuple:
    # Reading may update atime; every identity/mutation-relevant field is stable.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _descriptor_contains(ancestor: int, descendant: int) -> bool:
    """Compare actual directory ancestry, including case aliases on APFS.

    Both roots have already been opened component-by-component without
    symlinks. Walking opened parent directories reads metadata only and never
    creates source entries. Lexical Path ancestry alone cannot bind identity.
    """
    expected = os.fstat(ancestor)
    current = os.dup(descendant)
    try:
        for _ in range(4096):
            observed = os.fstat(current)
            if _same_inode(expected, observed):
                return True
            parent = os.open("..", _DIR_FLAGS, dir_fd=current)
            os.close(current)
            current = parent
            if _same_inode(observed, os.fstat(current)):
                return False
        raise StagingError("directory ancestry exceeds staging limit")
    finally:
        os.close(current)


def _private_file(info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise StagingError("staged asset must be a private single-link regular file")


def _private_directory(info: os.stat_result) -> None:
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise StagingError("staging directories must be private with mode 0700")


@contextmanager
def _parent(root_fd: int, relative: str, *, create: bool = False, private: bool = False):
    # PublicAssetRef has already restricted this to exactly namespace/filename.
    components = relative.split("/")
    descriptor = os.dup(root_fd)
    try:
        for component in components[:-1]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            if private:
                _private_directory(os.fstat(descriptor))
        yield descriptor, components[-1]
    finally:
        os.close(descriptor)


@contextmanager
def _opened_asset(root_fd: int, asset: PublicAssetRef, *, private: bool = False):
    with _parent(root_fd, asset.source_relative_path, private=private) as (parent, name):
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise StagingError("public source must be a regular file")
            if private:
                _private_file(info)
            yield descriptor, info
        finally:
            os.close(descriptor)


def _hash_descriptor(descriptor: int, expected_size: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(descriptor, min(_CHUNK_BYTES, expected_size - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > expected_size:
            raise StagingError("public asset size differs from admission")
        digest.update(chunk)
    return digest.hexdigest(), total


def _match(asset: PublicAssetRef, digest: str, size: int) -> None:
    if digest != asset.sha256 or size != asset.size_bytes:
        raise StagingError("public asset hash/size differs from admission")


def _check_path_identity(root: Path, root_fd: int, asset: PublicAssetRef,
                         observed: os.stat_result, *, private: bool = False) -> None:
    # Reopen through both the pinned root and its current absolute name. This
    # detects replacement of the file, namespace directory, or root during IO.
    with _opened_asset(root_fd, asset, private=private) as (_, current):
        if _state(current) != _state(observed):
            raise StagingError("public asset path changed during staging")
    with anchor_directory(root, private=private) as current_root:
        if not _same_inode(os.fstat(root_fd), os.fstat(current_root)):
            raise StagingError("public root changed during staging")
        with _opened_asset(current_root, asset, private=private) as (_, current):
            if _state(current) != _state(observed):
                raise StagingError("public asset path changed during staging")


def _check_staging_root(root: Path, descriptor: int) -> None:
    _private_directory(os.fstat(descriptor))
    with anchor_directory(root, private=True) as current:
        if not _same_inode(os.fstat(descriptor), os.fstat(current)):
            raise StagingError("staging root changed during staging")


def _validate_asset(asset: PublicAssetRef) -> None:
    if type(asset) is not PublicAssetRef:
        raise StagingError("staging requires exactly PublicAssetRef")
    PublicAssetRef.__post_init__(asset)
    if asset.size_bytes > MAX_PUBLIC_ASSET_BYTES:
        raise StagingError("public asset exceeds staging size limit")


def _copy_descriptor(source: int, target: int, asset: PublicAssetRef) -> None:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(source, min(_CHUNK_BYTES, asset.size_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > asset.size_bytes:
            raise StagingError("public asset size differs from admission")
        digest.update(chunk)
        remaining = memoryview(chunk)
        while remaining:
            written = os.write(target, remaining)
            if written <= 0:
                raise StagingError("staging write failed")
            remaining = remaining[written:]
    _match(asset, digest.hexdigest(), total)


def stage_public_asset(public_root: Path, asset: PublicAssetRef,
                       staging_root: Path) -> StagedPublicAsset:
    """Copy/hash one exact opened source, then publish only fully checked bytes.

    Both roots must already exist. The staging root and every created namespace
    are mode 0700. No source inode is exposed. Failed copies leave no final file;
    existing destination entries are never replaced. Call before solver launch.
    """
    _validate_asset(asset)
    public_root, staging_root = _root_path(public_root), _root_path(staging_root)
    if (public_root == staging_root or public_root in staging_root.parents
            or staging_root in public_root.parents):
        raise StagingError("public source and staging roots must be separate")
    try:
        with anchor_directory(public_root) as source_root, anchor_directory(staging_root, private=True) as target_root:
            if (_descriptor_contains(source_root, target_root)
                    or _descriptor_contains(target_root, source_root)):
                raise StagingError("public source and staging roots must be separate")
            _check_staging_root(staging_root, target_root)
            with _opened_asset(source_root, asset) as (source, before):
                if before.st_size != asset.size_bytes:
                    raise StagingError("public asset size differs from admission")
                with _parent(target_root, asset.source_relative_path, create=True, private=True) as (parent, name):
                    temporary = ".runtime-staging-" + uuid.uuid4().hex
                    target = None
                    published = False
                    try:
                        target = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=parent)
                        _private_file(os.fstat(target))
                        _copy_descriptor(source, target, asset)
                        after = os.fstat(source)
                        if _state(before) != _state(after):
                            raise StagingError("public source changed during staging")
                        _check_path_identity(public_root, source_root, asset, after)
                        os.fsync(target)
                        staged_before = os.fstat(target)
                        _private_file(staged_before)
                        os.lseek(target, 0, os.SEEK_SET)
                        _match(asset, *_hash_descriptor(target, asset.size_bytes))
                        if _state(staged_before) != _state(os.fstat(target)):
                            raise StagingError("staged bytes changed during validation")
                        # Recheck source after the staged reread as well.
                        if _state(before) != _state(os.fstat(source)):
                            raise StagingError("public source changed during staging")
                        _check_path_identity(public_root, source_root, asset, before)
                        _check_staging_root(staging_root, target_root)
                        with _parent(target_root, asset.source_relative_path, private=True) as (current, _):
                            if not _same_inode(os.fstat(parent), os.fstat(current)):
                                raise StagingError("staging namespace changed during staging")
                        # link is an atomic no-replace publication of the freshly
                        # created inode, never a link to any source/private file.
                        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent,
                                follow_symlinks=False)
                        published = True
                        os.unlink(temporary, dir_fd=parent)
                        _private_file(os.fstat(target))
                        published_info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if not _same_inode(os.fstat(target), published_info):
                            raise StagingError("published staging identity changed")
                        _check_staging_root(staging_root, target_root)
                        _check_path_identity(staging_root, target_root, asset,
                                             os.fstat(target), private=True)
                        os.fsync(parent)
                    except BaseException:
                        if published:
                            # No worker is launched until staging returns.
                            os.unlink(name, dir_fd=parent)
                        raise
                    finally:
                        if target is not None:
                            os.close(target)
                        try:
                            os.unlink(temporary, dir_fd=parent)
                        except FileNotFoundError:
                            pass
    except (OSError, PolicyError):
        raise StagingError("cannot safely stage public asset") from None
    return validate_staged_public_asset(staging_root, asset)


def validate_staged_public_asset(staging_root: Path, asset: PublicAssetRef) -> StagedPublicAsset:
    """Rehash a private single-link copy immediately before native worker use."""
    _validate_asset(asset)
    staging_root = _root_path(staging_root)
    try:
        with anchor_directory(staging_root, private=True) as root:
            _check_staging_root(staging_root, root)
            with _opened_asset(root, asset, private=True) as (descriptor, before):
                _match(asset, *_hash_descriptor(descriptor, asset.size_bytes))
                after = os.fstat(descriptor)
                _private_file(after)
                if _state(before) != _state(after):
                    raise StagingError("staged asset changed during validation")
                _check_path_identity(staging_root, root, asset, after, private=True)
    except (OSError, PolicyError):
        raise StagingError("cannot safely validate staged public asset") from None
    return StagedPublicAsset(asset, staging_root / asset.source_relative_path)


def _solver_assets(task: SolverTask) -> tuple[PublicAssetRef, ...]:
    if type(task) is not SolverTask:
        raise StagingError("staging requires exactly SolverTask")
    SolverTask.__post_init__(task)
    return tuple(asset for asset in (task.snapshot, task.graph, task.embedding) if asset is not None)


def stage_solver_assets(task: SolverTask, public_root: Path,
                        staging_root: Path) -> tuple[StagedPublicAsset, ...]:
    """Stage only the closed solver contract's explicitly admitted assets."""
    return tuple(stage_public_asset(public_root, asset, staging_root) for asset in _solver_assets(task))


def validate_staged_solver_assets(task: SolverTask, staging_root: Path) -> tuple[StagedPublicAsset, ...]:
    return tuple(validate_staged_public_asset(staging_root, asset) for asset in _solver_assets(task))
