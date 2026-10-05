"""Fail-closed admission for the synthetic harness probes.

The parent audit hook is not an OS firewall and is not inherited by subprocesses.
Only the separate, exact sandbox-command allowlist admits subprocess task work.
This module imports no harness or optional dependency.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, FrameType

BASELINE = "ea5b857487ae9e146e94a88e108962742fcbdef8"
BUDGET_BASELINE = "12c319fa8bcf5b313175f80cee3ce06a77a59619"
GRAPH_BASELINE = "81ac5b150c719cafc671b884dce7f525219e4ea0"
NULL_SINK = Path("/dev/null")
# Freeze the loaded stdlib code identities before optional dependencies import.
_DEVNULL_CODE = subprocess.Popen._get_devnull.__code__
_STDIO_CODE = subprocess.Popen._get_handles.__code__
_POPEN_CODE = subprocess.Popen.__init__.__code__
PROBE_FILES = frozenset({
    "tools/harness_cert/__init__.py",
    "tools/harness_cert/_probe_safety.py",
    "tools/harness_cert/_scripted_loopback.py",
    "tools/harness_cert/run_h04_h05_h18.py",
    "tools/harness_cert/README.md",
    "tests/test_harness_loopback.py",
    "tests/test_harness_probe.py",
})
# The next tranche has its own reviewed HEAD and exact change surface. The
# historical dispatch probe deliberately retains its original baseline pin.
BUDGET_PROBE_FILES = frozenset({
    "tools/harness_cert/_probe_safety.py",
    "tools/harness_cert/_scripted_loopback.py",
    "tools/harness_cert/run_h13_h14_h29.py",
    "tools/harness_cert/_synthetic_verification.py",
    "tools/harness_cert/README.md",
    "tools/harness_cert/H13_H14_H29_DESIGN.md",
    "tests/test_harness_budget_probe.py",
    "tests/test_harness_budget_admission.py",
    "tests/test_synthetic_verification.py",
    "tests/test_harness_loopback.py",
})
# H30 records prompt/schema observations at its own exact reviewed baseline.
# It changes neither historical admission profile nor runtime guard policy.
GRAPH_PROBE_FILES = frozenset({
    "tools/harness_cert/_probe_safety.py",
    "tools/harness_cert/run_h30.py",
    "tools/harness_cert/README.md",
    "tools/harness_cert/H30_DESIGN.md",
    "tests/test_harness_graph_probe.py",
})
# The support-pin correction is the sole admitted tracked documentation change.
# Unlike probe sources, its entire reviewed content must match this digest.
REVIEWED_SUPPORT_DOCUMENTS = {
    "harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md": "bacc52dd519d55c4316237f52daee4da32c4c808598512a18f4924a372a258c0",
}


class ProbeRefused(RuntimeError):
    """An infrastructure/safety refusal, never evidence of a tool hypothesis."""


def ip_literal(host) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse numeric addresses without DNS, scoped addresses or hostname aliases."""
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeError:
            return None
    if not isinstance(host, str) or "%" in host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


IPV4_SERVER = ipaddress.IPv4Address("127.0.0.1")
IPV6_FEATURE_LOOPBACK = ipaddress.IPv6Address("::1")
URLLIB3_CONNECTION_SHA256 = "2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74"


@dataclass(frozen=True)
class IPv6FeatureProbe:
    """Admit only urllib3 2.8.0's audited import-time IPv6 capability bind."""

    source: Path
    module_code: CodeType
    function_code: CodeType

    @classmethod
    def from_site(cls, site: Path) -> IPv6FeatureProbe:
        # Read/compile for identity checks only; never import or execute here.
        source = confined(site / "urllib3/util/connection.py", site)
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != URLLIB3_CONNECTION_SHA256:
            raise ProbeRefused(f"urllib3 IPv6 feature source differs from reviewed 2.8.0: {source}")
        module_code = compile(data, str(source), "exec", dont_inherit=True)
        function_code = next(code for code in module_code.co_consts
                             if isinstance(code, CodeType) and code.co_name == "_has_ipv6")
        return cls(source, module_code, function_code)

    def permits_bind(self, sock, address, caller: FrameType) -> bool:
        if (not isinstance(address, tuple) or len(address) != 2
                or type(address[1]) is not int or address[1] != 0
                or ip_literal(address[0]) != IPV6_FEATURE_LOOPBACK
                or getattr(sock, "family", None) != socket.AF_INET6
                or getattr(sock, "type", None) != socket.SOCK_STREAM
                or getattr(sock, "proto", None) != 0):
            return False
        module = sys.modules.get("urllib3.util.connection")
        parent = caller.f_back
        function = caller.f_globals.get("_has_ipv6")
        return (module is not None and caller.f_globals is module.__dict__
                and caller.f_globals.get("__name__") == "urllib3.util.connection"
                and caller.f_globals.get("__file__") == str(self.source)
                and getattr(getattr(module, "__spec__", None), "origin", None) == str(self.source)
                and getattr(getattr(module, "__spec__", None), "_initializing", False) is True
                and caller.f_code == self.function_code
                and caller.f_code.co_filename == str(self.source)
                and caller.f_code is getattr(function, "__code__", None)
                and caller.f_locals.get("sock") is sock
                and ip_literal(caller.f_locals.get("host")) == IPV6_FEATURE_LOOPBACK
                and parent is not None and parent.f_code == self.module_code
                and parent.f_code.co_filename == str(self.source)
                and parent.f_globals is caller.f_globals)


@dataclass(frozen=True)
class SystemRuntimeReads:
    """Frozen, read-only OS resources; never a library or mutation root."""

    zoneinfo_root: Path | None = None

    def permits_read(self, path: Path) -> bool:
        resolved = path.resolve()
        return (self.zoneinfo_root is not None
                and resolved.is_relative_to(self.zoneinfo_root)
                and resolved.is_file())


def _trusted_runtime_path(path: Path):
    """OS-owned aliases and directory ancestors cannot be user-writable."""
    for entry in (path, *path.parents):
        info = entry.lstat()
        if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode)
                               and info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise ProbeRefused(f"untrusted system timezone path: {entry}")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise ProbeRefused(f"system timezone path is not a directory/alias: {entry}")


def discover_system_runtime_reads() -> SystemRuntimeReads:
    """Capture only the active OS zoneinfo tree, before installing the hook.

    Python's TZPATH and dateutil use /usr/share/zoneinfo. On macOS that alias
    resolves outside /usr to a versioned OS timezone database. /etc/localtime
    needs no separate exception when it resolves into this same frozen tree.
    """
    alias = Path("/usr/share/zoneinfo")
    try:
        root = alias.resolve(strict=True)
        if sys.platform == "darwin":
            anchor = Path("/private/var/db/timezone/tz")
            relative = root.relative_to(anchor) if root.is_relative_to(anchor) else None
            supported = relative is not None and len(relative.parts) == 2 and relative.parts[1] == "zoneinfo"
        else:
            supported = root == alias
        if not supported:
            raise ProbeRefused(f"unsupported system timezone tree: {root}")
        if not root.is_dir():
            raise ProbeRefused(f"system timezone tree is not a directory: {root}")
        _trusted_runtime_path(alias)
        _trusted_runtime_path(root)
    except FileNotFoundError:
        return SystemRuntimeReads()
    except OSError as exc:
        raise ProbeRefused(f"cannot inspect system timezone tree: {exc}") from exc
    return SystemRuntimeReads(zoneinfo_root=root)


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args], env=env,
        check=True, capture_output=True, timeout=15,
    ).stdout.decode("utf-8", errors="strict")


def check_repository(repo: Path, *, profile: str = "dispatch") -> list[str]:
    if profile == "dispatch":
        baseline, probe_files = BASELINE, PROBE_FILES
        support_documents = REVIEWED_SUPPORT_DOCUMENTS
    elif profile == "budget":
        baseline, probe_files = BUDGET_BASELINE, BUDGET_PROBE_FILES
        support_documents = {}
    elif profile == "graph":
        baseline, probe_files = GRAPH_BASELINE, GRAPH_PROBE_FILES
        support_documents = {}
    else:
        raise ProbeRefused(f"unknown repository admission profile: {profile!r}")
    if Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve() != repo.resolve():
        raise ProbeRefused(f"not the repository root: {repo}")
    if git(repo, "rev-parse", "HEAD").strip() != baseline:
        raise ProbeRefused(f"HEAD must equal reviewed baseline {baseline}")
    records = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    changes = []
    for record in records:
        if not record:
            continue
        if len(record) < 4 or record[:2] not in {"??", " M", "M ", "MM", "A ", "AM"}:
            raise ProbeRefused(f"unreviewed Git change: {record!r}")
        path = record[3:]
        if path in support_documents:
            document = repo / path
            if record[:2] not in {" M", "M ", "MM"}:
                raise ProbeRefused(f"support document must be a tracked modification: {path}")
            if (not stat.S_ISREG(document.lstat().st_mode)
                    or document.resolve() != document.absolute()
                    or hashlib.sha256(document.read_bytes()).hexdigest() != support_documents[path]):
                raise ProbeRefused(f"support document differs from reviewed pin correction: {path}")
            changes.append(path)
            continue
        if path not in probe_files:
            raise ProbeRefused(f"repository is dirty outside the probe: {path}")
        changes.append(path)
    return changes


def wheel_candidates(cwd: Path, tasks_path: Path, snapshots_dir: Path) -> list[Path]:
    """Union of BOTH audited resolvers, without opening any wheel or task data."""
    return list(dict.fromkeys([
        tasks_path.parent / "wheels", tasks_path.parent / "sandbox" / "wheels",
        snapshots_dir.parent / "wheels",
        Path("/kaggle/input/datasets/metric/gemma-4-developer-agent-metric-data/sandbox/wheels"),
        Path("/kaggle/input/gemma-4-developer-agent-metric-data/sandbox/wheels"),
        Path("/kaggle/input/competition-data/wheels"),
        cwd / "data/competition_data/secret/sandbox/wheels", cwd / "wheels",
        Path("/wheels"), Path("/tmp/wheels"),
        Path("/kaggle/input/datasets/ryanholbrook/gemma4swe-wheelhouses"),
        cwd / "build/wheelhouse", cwd / "data/wheels", Path("/tmp/wheelhouse"),
    ]))


def check_wheels(paths: list[Path]) -> list[dict]:
    observations = []
    for path in dict.fromkeys(paths):
        # Any matching entry, including a symlink, is enough for the real resolvers.
        try:
            try:
                info = path.lstat()
            except FileNotFoundError:
                observations.append({"path": str(path.absolute()), "top_level_wheels": []})
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise ProbeRefused(f"wheel candidate is not a directory: {path}")
            # pathlib.glob suppresses scandir errors in Python 3.12: use scandir.
            with os.scandir(path) as directory:
                entries = sorted(entry.name for entry in directory if entry.name.endswith(".whl"))
        except OSError as exc:
            raise ProbeRefused(f"cannot inspect wheel candidate {path}: {exc}") from exc
        if entries:
            raise ProbeRefused(f"discoverable wheelhouse: {path} (first wheel: {entries[0]})")
        observations.append({"path": str(path.absolute()), "top_level_wheels": entries})
    return observations


def confined(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ProbeRefused(f"path escapes permitted root {root}: {path}")
    return resolved


class ParentGuard:
    """Audit only this parent process; children are admitted by exact commands."""

    def __init__(self, repo: Path, run_root: Path, outputs: list[Path], protected: list[Path],
                 read_roots: list[Path] | None = None, metadata_dirs: list[Path] | None = None,
                 system_runtime_reads: SystemRuntimeReads | None = None,
                 ipv6_feature_probe: IPv6FeatureProbe | None = None):
        self.repo = repo.resolve()
        self.run_root = run_root.resolve()
        self.outputs = [p.resolve() for p in outputs]
        self.protected = [p.resolve() for p in protected]
        self.read_roots = [p.resolve() for p in read_roots] if read_roots is not None else None
        self.metadata_dirs = [p.resolve() for p in (metadata_dirs or [])]
        self.system_runtime_reads = system_runtime_reads or SystemRuntimeReads()
        self.ipv6_feature_probe = ipv6_feature_probe
        self.port: int | None = None
        self.violations: list[str] = []
        self.connections: list[dict] = []
        self.bindings: list[dict] = []
        self.processes: list[dict] = []
        self._scope = threading.local()

    def refuse(self, message: str):
        self.violations.append(message)
        raise ProbeRefused(message)

    @contextmanager
    def processes_allowed(self, kind: str, argv: list[str] | None = None):
        previous = getattr(self._scope, "kind", None)
        previous_argv = getattr(self._scope, "argv", None)
        self._scope.kind = kind
        self._scope.argv = argv
        try:
            yield
        finally:
            self._scope.kind = previous
            self._scope.argv = previous_argv

    @staticmethod
    def mutation_path(raw, dir_fd=-1) -> Path:
        path = Path(os.fsdecode(raw))
        if not path.is_absolute() and dir_fd not in {-1, None}:
            if sys.platform == "darwin":
                import fcntl
                base = os.fsdecode(fcntl.fcntl(dir_fd, 50, bytes(1024)).split(b"\0", 1)[0])
            else:
                base = os.readlink(f"/proc/self/fd/{dir_fd}")
            path = Path(base) / path
        path = path.absolute()
        # Unlink/rename affect the link itself, not a symlink's final target.
        return path.parent.resolve() / path.name

    def check_mutation(self, raw, dir_fd=-1, *, follow_final=False):
        path = self.mutation_path(raw, dir_fd)
        paths = [path, path.resolve()] if follow_final else [path]
        for candidate in paths:
            if not any(candidate.is_relative_to(root) for root in [self.run_root, *self.outputs]):
                self.refuse(f"parent mutation outside probe outputs refused: {candidate}")

    def permits_null_sink(self, raw, flags: int, caller: FrameType) -> bool:
        """Exact OS null sink only; never a writable root or a general read.

        Write/append opens may carry Python's create/truncate flags: the verified
        existing null device discards writes. RDWR is reserved for stdlib Popen
        stdout/stderr setup in an admitted process scope, never DEVNULL stdin.
        Process launch still goes through the independent argv admission check.
        """
        if os.fsdecode(raw) != str(NULL_SINK):
            return False
        incidental = os.O_CLOEXEC | os.O_NOFOLLOW
        write_flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_TRUNC | incidental
        if flags & os.O_ACCMODE == os.O_WRONLY:
            if flags & ~write_flags:
                return False
        elif flags & os.O_ACCMODE == os.O_RDWR:
            stdio = caller.f_back
            constructor = stdio.f_back if stdio is not None else None
            owner = caller.f_locals.get("self")
            if (flags & ~(os.O_RDWR | incidental)
                    or getattr(self._scope, "kind", None) not in {"shell", "venv", "fixture_git"}
                    or caller.f_code is not _DEVNULL_CODE
                    or caller.f_globals is not subprocess.__dict__
                    or stdio is None or stdio.f_code is not _STDIO_CODE
                    or stdio.f_globals is not subprocess.__dict__
                    or stdio.f_locals.get("self") is not owner
                    or stdio.f_locals.get("stdin") == subprocess.DEVNULL
                    or not any(stdio.f_locals.get(stream) == subprocess.DEVNULL
                               for stream in ("stdout", "stderr"))
                    or constructor is None or constructor.f_code is not _POPEN_CODE
                    or constructor.f_globals is not subprocess.__dict__
                    or constructor.f_locals.get("self") is not owner):
                return False
        else:
            return False
        # Fail closed on unsupported platforms, aliases, nondevices and a wrong
        # device number. Darwin's null is cdev 3:2; Linux's is cdev 1:3.
        expected = {"darwin": (3, 2), "linux": (1, 3)}.get(sys.platform)
        if expected is None:
            return False
        try:
            info = NULL_SINK.lstat()
            parent = NULL_SINK.parent.lstat()
            return (NULL_SINK.resolve(strict=True) == NULL_SINK
                    and stat.S_ISCHR(info.st_mode) and info.st_uid == 0
                    and (os.major(info.st_rdev), os.minor(info.st_rdev)) == expected
                    and stat.S_ISDIR(parent.st_mode) and parent.st_uid == 0
                    and not parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        except OSError:
            return False

    def audit(self, event: str, args: tuple):
        if event == "socket.getaddrinfo":
            if ip_literal(args[0]) != IPV4_SERVER:
                self.refuse(f"non-loopback DNS refused: {args[0]!r}")
        elif event in {"socket.gethostbyaddr", "socket.gethostbyname", "socket.getnameinfo", "socket.sendto"}:
            self.refuse(f"DNS/datagram operation refused: {event}")
        elif event == "socket.connect":
            sock, address = args
            if not (isinstance(address, tuple) and len(address) == 2
                    and isinstance(address[0], str)
                    and ip_literal(address[0]) == IPV4_SERVER
                    and type(address[1]) is int and address[1] == self.port
                    and self.port is not None
                    and getattr(sock, "family", None) == socket.AF_INET
                    and getattr(sock, "type", None) == socket.SOCK_STREAM
                    and getattr(sock, "proto", None) in {0, socket.IPPROTO_TCP}):
                self.refuse(f"non-probe connection refused: {address!r}")
            self.connections.append({"host": address[0], "port": address[1]})
        elif event == "socket.bind":
            sock, address = args
            caller = sys._getframe(1)
            ipv4_listener = (isinstance(address, tuple) and len(address) == 2
                             and ip_literal(address[0]) == IPV4_SERVER
                             and type(address[1]) is int and address[1] == 0
                             and getattr(sock, "family", None) == socket.AF_INET
                             and getattr(sock, "type", None) == socket.SOCK_STREAM
                             and getattr(sock, "proto", None) in {0, socket.IPPROTO_TCP})
            ipv6_detection = (self.ipv6_feature_probe is not None
                              and self.ipv6_feature_probe.permits_bind(sock, address, caller))
            if not (ipv4_listener or ipv6_detection):
                self.refuse(f"non-loopback/ephemeral bind refused: {address!r}; "
                            f"caller={caller.f_code.co_filename}:{caller.f_lineno} ({caller.f_code.co_name})")
            self.bindings.append({"host": str(ip_literal(address[0])), "port": 0,
                                  "purpose": "urllib3_ipv6_detection" if ipv6_detection else "scripted_listener",
                                  "source": caller.f_code.co_filename, "line": caller.f_lineno,
                                  "function": caller.f_code.co_name})
        elif event == "subprocess.Popen":
            argv = args[1]
            kind = getattr(self._scope, "kind", None)
            if not isinstance(argv, (list, tuple)):
                self.refuse("unstructured subprocess refused")
            if kind == "shell":
                accepted = len(argv) == 3 and argv[:2] == ["/bin/bash", "-c"]
            elif kind == "venv":
                # Lexical venv path: its Python executable can be a trusted symlink.
                executable = Path(argv[0]).absolute()
                accepted = executable.is_relative_to(self.run_root) and list(argv[1:]) in (
                    ["-Im", "ensurepip", "--upgrade", "--default-pip"],
                    ["-m", "ensurepip", "--upgrade", "--default-pip"],
                    ["-m", "pip", "--version"],
                )
            elif kind == "fixture_git":
                accepted = argv[0] == "/usr/bin/git" and list(argv) == getattr(self._scope, "argv", None)
            else:
                accepted = False
            if not accepted:
                self.refuse(f"unapproved subprocess refused: {argv!r}")
            self.processes.append({"kind": kind, "argv": list(argv), "cwd": str(args[2])})
        elif event in {"os.system", "os.exec", "os.posix_spawn", "os.spawn"}:
            self.refuse(f"unapproved process launch refused: {event}")
        elif event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).absolute().resolve()
            flags = args[2] or 0
            if path == NULL_SINK or os.fsdecode(args[0]) == str(NULL_SINK):
                if not self.permits_null_sink(args[0], flags, sys._getframe(1)):
                    self.refuse(f"parent null access outside exact sink policy refused: {args[0]!r}")
                return
            writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if writing:
                # An outside alias must not make a system resource writable by
                # pointing at a synthetic output. Require both locations.
                for candidate in (self.mutation_path(args[0]), path):
                    if not any(candidate.is_relative_to(root) for root in [self.run_root, *self.outputs]):
                        self.refuse(f"parent write outside probe outputs refused: {candidate}")
            elif any(path.is_relative_to(root) for root in self.protected):
                self.refuse(f"dataset/competition read refused: {path}")
            elif path.is_relative_to(self.repo) and not any(path.is_relative_to(root) for root in [
                self.repo / "tools/harness_cert", *self.outputs,
            ]):
                self.refuse(f"non-probe repository read refused: {path}")
            elif self.read_roots is not None and not any(path.is_relative_to(root) for root in [
                self.run_root, self.repo / "tools/harness_cert", *self.outputs, *self.read_roots,
            ]) and not self.system_runtime_reads.permits_read(path):
                self.refuse(f"read outside synthetic/library roots refused: {path}")
        elif event in {"os.listdir", "os.scandir"} and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).absolute().resolve()
            if self.read_roots is not None and path not in self.metadata_dirs and not any(
                path.is_relative_to(root) for root in [self.run_root, *self.outputs, *self.read_roots]
            ):
                self.refuse(f"directory read outside synthetic/library roots refused: {path}")
        elif event in {"os.mkdir", "os.chmod", "os.chown"}:
            self.check_mutation(args[0], args[2] if event != "os.chown" else args[3], follow_final=event != "os.mkdir")
        elif event in {"os.remove", "os.rmdir"}:
            self.check_mutation(args[0], args[1])
        elif event in {"os.rename", "os.link"}:
            self.check_mutation(args[0], args[2], follow_final=event == "os.link")
            self.check_mutation(args[1], args[3])
        elif event == "os.symlink":
            self.check_mutation(args[1], args[2])
        elif event == "os.utime":
            self.check_mutation(args[0], args[3], follow_final=True)
        elif event == "os.truncate":
            self.check_mutation(args[0], follow_final=True)
