"""Shared helpers: dataset-root resolution, output-path safety, symlink-safe traversal, hashing, JSON."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

DATASET_ENV = "GEMMA4_DATASET_ROOT"
REPO_ROOT = Path(__file__).resolve().parent.parent
CHUNK = 1 << 20


class DatasetRootError(RuntimeError):
    pass


class UnsafeOutputError(RuntimeError):
    """A tool was asked to write at or below a protected (read-only) root."""


class SymlinkError(RuntimeError):
    """A symlink was found where only regular files are acceptable."""


def dataset_root(value: str | None = None) -> Path:
    """Resolve the read-only Kaggle dataset root from an explicit value or $GEMMA4_DATASET_ROOT."""
    raw = value if value is not None else os.environ.get(DATASET_ENV)
    if not raw:
        raise DatasetRootError(f"{DATASET_ENV} is not set")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise DatasetRootError(f"{DATASET_ENV}={raw!r} is not a directory")
    return root


# ---- output-path safety (the ONLY sanctioned way for repo tools to write) -----------------------

def _protected_roots(extra: tuple[Path, ...]) -> list[Path]:
    roots = list(extra)
    env = os.environ.get(DATASET_ENV)
    if env and Path(env).expanduser().is_dir():
        roots.append(Path(env).expanduser())  # always protect the configured dataset, even if --dataset-root differs
    return roots


def guard_output(dest: Path, *protected: Path) -> Path:
    """Return dest resolved, or raise UnsafeOutputError if it is, or lies below, any protected root.

    Uses resolve() (follows symlinks in every existing component) plus os.path.samefile on every existing
    ancestor, so symlinked parents, `..` tricks and case-insensitive aliases (APFS) are all caught.
    Call it BEFORE creating directories or opening files.
    """
    resolved = Path(dest).expanduser().resolve()
    for root in _protected_roots(protected):
        root_r = Path(root).expanduser().resolve()
        if resolved == root_r or root_r in resolved.parents:
            raise UnsafeOutputError(f"refusing to write {dest} (resolves to {resolved}) inside protected root {root_r}")
        if root_r.exists():
            for anc in (resolved, *resolved.parents):
                if anc.exists() and os.path.samefile(anc, root_r):
                    raise UnsafeOutputError(f"refusing to write {dest}: {anc} is the protected root {root_r}")
    return resolved


def write_bytes_safe(path: Path, data: bytes, *protected: Path) -> None:
    """Guard, then write atomically (temp file + os.replace): never truncates in place, never writes through
    an existing hard link or symlink at the destination."""
    target = guard_output(path, *protected)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_text_safe(path: Path, text: str, *protected: Path) -> None:
    write_bytes_safe(path, text.encode("utf-8"), *protected)


def write_json(path: Path, obj, *protected: Path) -> None:
    write_text_safe(path, dumps(obj), *protected)


# ---- symlink-safe traversal / hashing -----------------------------------------------------------

def scan_tree(root: Path, ignore_names=(".DS_Store",)) -> tuple[list[Path], list[Path]]:
    """(regular files, symlinks) under root, each sorted by POSIX relative path.

    Never follows symlinks: symlinked dirs are not descended, symlinked files are reported, not returned as
    files. Non-regular, non-symlink entries (fifos, sockets, devices) are reported with the symlinks.
    """
    files, links = [], []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        keep = []
        for name in dirnames:
            if (d / name).is_symlink():
                links.append(d / name)  # os.walk lists dir symlinks but (followlinks=False) never enters them
            else:
                keep.append(name)
        dirnames[:] = sorted(keep)
        for name in filenames:
            p = d / name
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                links.append(p)
            elif name not in ignore_names:
                files.append(p)
    key = lambda p: p.relative_to(root).as_posix()
    return sorted(files, key=key), sorted(links, key=key)


def iter_files(root: Path, ignore_names=(".DS_Store",)) -> list[Path]:
    """Regular files under root. Raises SymlinkError if any symlink or special file exists."""
    files, links = scan_tree(root, ignore_names)
    if links:
        raise SymlinkError(f"symlinks/special files under {root}: {[p.relative_to(root).as_posix() for p in links]}")
    return files


def open_nofollow(path: Path):
    """Open for reading; fails (ELOOP) if the final path component is a symlink."""
    return os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open_nofollow(path) as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def partial_sha256(path: Path, window: int = CHUNK) -> str:
    """Hash of size + first and last `window` bytes. Cheap integrity signal for huge immutable blobs."""
    with open_nofollow(path) as f:
        size = os.fstat(f.fileno()).st_size
        h = hashlib.sha256(str(size).encode())
        h.update(f.read(window))
        if size > window:
            f.seek(max(window, size - window))
            h.update(f.read(window))
    return h.hexdigest()


def dumps(obj) -> str:
    """Deterministic JSON (sorted keys, stable separators, trailing newline)."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
