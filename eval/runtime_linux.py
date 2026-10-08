"""Linux worker account separation; no namespace, cgroup, or network claims."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

from ._contracts import ContractError

LIMITATIONS = ("no memory or CPU caps", "no network namespace", "filesystem permissions only")


def validate_worker_user(user: str | None) -> str:
    if type(user) is not str or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) is None or user == "root":
        raise ContractError("an explicit unprivileged worker account is required")
    return user


def worker_account(user):
    validate_worker_user(user)
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        raise ContractError("worker account does not exist") from None
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise ContractError("worker account cannot have root user/group identity")
    return account


def linux_prefix(user: str) -> tuple[list[str], str]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ContractError("Linux confinement requires the privileged Linux coordinator")
    worker_account(user)
    executable = shutil.which("runuser", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable:
        return [executable, "-u", user, "--"], "runuser"
    executable = shutil.which("sudo", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable:
        return [executable, "-u", user, "--"], "sudo"
    raise ContractError("neither runuser nor the admitted sudo fallback is available")


def worker_pids(uid: int) -> set[int]:
    """Read process UID metadata only; never process environments or contents."""
    found = set()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        raise ContractError("Linux process inventory is unavailable") from None
    for path in entries:
        if not path.name.isdecimal():
            continue
        try:
            lines = (path / "status").read_text().splitlines()
        except FileNotFoundError:
            continue
        except OSError:
            raise ContractError("Linux process identity cannot be inspected") from None
        identities = next((line.split()[1:] for line in lines if line.startswith("Uid:")), None)
        if identities is None:
            raise ContractError("Linux process UID metadata is missing")
        if str(uid) in identities:
            found.add(int(path.name))
    return found


def enable_subreaper() -> None:
    """Adopt orphaned worker descendants so they can be waited before gold IO."""
    library = ctypes.CDLL(None, use_errno=True)
    observed = ctypes.c_int()
    if library.prctl(36, 1, 0, 0, 0) != 0 or library.prctl(37, ctypes.byref(observed), 0, 0, 0) != 0 or observed.value != 1:
        raise ContractError("Linux descendant reaping cannot be enabled")


def terminate_worker_descendants(user: str, *, timeout: float = 5.0) -> None:
    """Dedicated account plus /proc inventory also catches setsid descendants."""
    account = worker_account(user)
    deadline = time.monotonic() + timeout
    while True:
        pids = worker_pids(account.pw_uid)
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except OSError:
                raise ContractError("worker descendant could not be terminated") from None
        for pid in pids:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
        if time.monotonic() >= deadline:
            raise ContractError("worker descendants remain; private evidence must not be read")
        time.sleep(0.01)


def verify_worker_confinement(config, roots: dict[str, Path]) -> dict:
    """Fail closed on actual worker opens, before task staging or private reads."""
    prefix, backend = linux_prefix(config.worker_user)
    account = worker_account(config.worker_user)
    if worker_pids(account.pw_uid):
        raise ContractError("worker account must be dedicated and have no existing processes")
    if set(roots) != {"public", "private", "artifacts", "repository_git"}:
        raise ContractError("the complete protected-root probe set is required")
    for path in roots.values():
        if not isinstance(path, Path) or not path.is_absolute() or not path.is_dir():
            raise ContractError("protected confinement root is unavailable")
    script = (
        "import json,os,sys\n"
        "observed={}\n"
        "for name,path in json.loads(sys.argv[1]).items():\n"
        " try:\n"
        "  descriptor=os.open(path,os.O_RDONLY)\n"
        " except PermissionError:\n"
        "  observed[name]='PermissionError'\n"
        " else:\n"
        "  os.close(descriptor); raise RuntimeError('protected root readable')\n"
        "descriptor=os.open(sys.argv[2],os.O_RDONLY); os.close(descriptor)\n"
        "print(json.dumps({'protected_roots':observed,'interpreter_readable':True}))\n"
    )
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
    paths = {name: str(path) for name, path in roots.items()}
    try:
        probe = subprocess.run([*prefix, str(config.python_executable), "-I", "-B", "-S", "-c", script,
                                json.dumps(paths), str(config.python_executable)],
                               cwd="/", env=environment, close_fds=True, capture_output=True,
                               text=True, timeout=15)
        launch = subprocess.run([*prefix, str(config.python_executable), "-I", "-B", "-S", "-c", "pass"],
                                cwd="/", env=environment, close_fds=True, capture_output=True,
                                text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise ContractError("worker confinement probe could not execute") from None
    try:
        result = json.loads(probe.stdout)
    except (ValueError, TypeError):
        raise ContractError("worker confinement probe returned invalid evidence") from None
    expected = {"protected_roots": dict.fromkeys(roots, "PermissionError"), "interpreter_readable": True}
    if probe.returncode != 0 or launch.returncode != 0 or result != expected or worker_pids(account.pw_uid):
        raise ContractError("worker confinement probe did not establish the required separation")
    enable_subreaper()
    return {"schema_version": 1, "confinement": "linux_user_separation", "backend": backend,
            "worker_user": config.worker_user, "worker_uid": account.pw_uid,
            "worker_gid": account.pw_gid, "probes": result,
            "interpreter_exec_passed": True, "limitations": list(LIMITATIONS)}


def own_worker_root(root: Path, user: str) -> None:
    account = worker_account(user)
    if root.is_symlink() or not root.is_dir():
        raise ContractError("worker root must be a fresh directory")
    for path in [*root.rglob("*"), root]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ContractError("worker tree contains an unsafe entry")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ContractError("worker files must be fresh single-link copies")
        os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
    root.chmod(0o700)
    observed = root.lstat()
    if (observed.st_uid, observed.st_gid, stat.S_IMODE(observed.st_mode)) != (account.pw_uid, account.pw_gid, 0o700):
        raise ContractError("worker ownership was not established")
