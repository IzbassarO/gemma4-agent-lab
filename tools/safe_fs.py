"""Directory-descriptor-relative filesystem operations (POSIX: Linux, macOS).

Below a trusted, already-open directory, no path is ever re-resolved. Every component is created and opened
relative to its parent's descriptor with O_NOFOLLOW and checked with fstat. Swapping a path component for a symlink
(before or after creation) therefore cannot redirect a write: the open fails, or the write lands in the original
inode, never in the symlink target. Unsupported platforms fail closed.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from tools.common import UnsafeOutputError

_NOFOLLOW = getattr(os, "O_NOFOLLOW", None)
_DIRECTORY = getattr(os, "O_DIRECTORY", None)
MKDIR = os.mkdir  # indirection so tests can simulate an attacker racing directory creation


def _require_support() -> None:
    if _NOFOLLOW is None or _DIRECTORY is None or not {os.open, os.mkdir, os.stat, os.rename} <= os.supports_dir_fd \
            or not shutil.rmtree.avoids_symlink_attacks:
        raise UnsafeOutputError("platform lacks dir_fd/O_NOFOLLOW support; refusing to extract evidence")


def _name(name: str) -> str:
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise UnsafeOutputError(f"invalid path component {name!r}")
    return name


def open_root(path: Path) -> int:
    """Open the trust anchor (an existing canonical directory) and confirm the fd is that same directory."""
    _require_support()
    canonical = Path(path).resolve(strict=True)
    fd = os.open(canonical, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    st, lst = os.fstat(fd), os.lstat(canonical)
    if not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != (lst.st_dev, lst.st_ino):
        os.close(fd)
        raise UnsafeOutputError(f"{canonical} changed while opening")
    return fd


def kind(dir_fd: int, name: str) -> str | None:
    try:
        st = os.stat(_name(name), dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return "symlink" if stat.S_ISLNK(st.st_mode) else "dir" if stat.S_ISDIR(st.st_mode) else "file" \
        if stat.S_ISREG(st.st_mode) else "other"


def open_dir(dir_fd: int, name: str, *, create: bool = False, exclusive: bool = False) -> int:
    """Open (optionally create) a child directory relative to dir_fd; a symlink is never followed."""
    _name(name)
    if create or exclusive:
        try:
            MKDIR(name, 0o755, dir_fd=dir_fd)
        except FileExistsError:
            if exclusive:
                raise
    try:
        fd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=dir_fd)
    except OSError as e:
        raise UnsafeOutputError(f"{name!r} is not a real directory (symlink or replaced): {e.strerror}") from None
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise UnsafeOutputError(f"{name!r} is not a directory")
    return fd


def write_file(dir_fd: int, name: str, data: bytes) -> None:
    fd = os.open(_name(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644, dir_fd=dir_fd)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def read_file(dir_fd: int, name: str) -> bytes:
    fd = os.open(_name(name), os.O_RDONLY | _NOFOLLOW, dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise UnsafeOutputError(f"{name!r} is not a regular file")
        return f.read()


def write_tree(root_fd: int, files: dict[str, bytes]) -> None:
    """Create every file (relative POSIX path) below root_fd; directories created/opened component by component."""
    for rel in sorted(files):
        parts = [_name(p) for p in rel.split("/")]
        fds, cur = [], root_fd
        try:
            for p in parts[:-1]:
                cur = open_dir(cur, p, create=True)
                fds.append(cur)
            write_file(cur, parts[-1], files[rel])
        finally:
            for f in fds:
                os.close(f)


def read_tree(root_fd: int, prefix: str = "") -> dict[str, bytes]:
    """All regular files below root_fd; any symlink or special file is an error."""
    out = {}
    for name in sorted(os.listdir(root_fd)):
        k = kind(root_fd, name)
        if k == "dir":
            fd = open_dir(root_fd, name)
            try:
                out.update(read_tree(fd, f"{prefix}{name}/"))
            finally:
                os.close(fd)
        elif k == "file":
            out[f"{prefix}{name}"] = read_file(root_fd, name)
        else:
            raise UnsafeOutputError(f"unexpected {k} at {prefix}{name}")
    return out


def rename(dir_fd: int, src: str, dst: str) -> None:
    os.rename(_name(src), _name(dst), src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def remove_tree(dir_fd: int, name: str) -> None:
    shutil.rmtree(_name(name), dir_fd=dir_fd)
