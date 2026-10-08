"""One-task trusted coordinator with separate synthetic and real admission.

The solver is re-executed with clean process state and exits before private
staging. Model/native imports remain inside admitted worker execution.
"""
from __future__ import annotations

import os
import json
import hashlib
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from ._contracts import ContractError, canonical_json, canonical_sha256, hex_digest, load_json_object
from .solver_task import SolverTask
from .staging import stage_solver_assets
from .verifier_task import VerifierTask
from .verifier_data import load_verifier_material
from .worker_common import CASES, E0_SHA256, E0_SIZE, seal_json
from .runtime_result import ArtifactRef, SolverRunResult, VerifierRunResult, TaskRuntimeResult
from tools.common import REPO_ROOT
from tools.h23_v4.filesystem import anchor_directory, read_regular

_COMMON_CODE = (
    "eval/__init__.py", "eval/_contracts.py", "eval/solver_task.py", "eval/staging.py",
    "eval/runtime_result.py", "eval/runtime_events.py", "eval/runtime_adapter.py",
    "eval/runtime_fixture.py", "eval/worker_common.py", "tools/__init__.py", "tools/common.py",
    "tools/h23_v4/__init__.py", "tools/h23_v4/filesystem.py", "tools/h23_v4/schema.py",
    "tools/harness_cert/__init__.py", "tools/harness_cert/_probe_safety.py",
    "tools/harness_cert/_scripted_loopback.py", "tools/harness_cert/run_h04_h05_h18.py",
)
_VERIFIER_CODE = ("eval/verifier_worker.py", "eval/verifier_task.py", "eval/verifier_data.py",
                  "eval/runtime_verifier_fixture.py", "tools/harness_cert/_synthetic_verification.py")
_REAL_COMMON_CODE = ("eval/real_contracts.py", "eval/runtime_real.py", "eval/runtime_provenance.py")
_LINUX_LOCK = REPO_ROOT / "harness_cert/locks/harness_linux_x86_64_py312.lock"
_LINUX_LOCK_SHA256 = "40c3b8a158eb4ab38289cfd3976759c7d2a5ceea6ddb91924f7121719d904c2d"
_PREREGISTRATION = REPO_ROOT / "docs/experiments/DEV_E0_FORENSICS_V1_S1_PREREG.md"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    python_executable: Path
    harness_root: Path
    candidate_path: Path
    artifact_root: Path
    source_wheels_root: Path | None = None
    synthetic_case: str | None = "H05"
    mode: str = "native_scripted"
    observation_enabled: bool = True
    task_manifest_sha256: str = "UNKNOWN"
    sandbox_image: str = "UNKNOWN"
    worker_timeout_seconds: int = 120
    model_endpoint: str = "SCRIPTED_ONLY"
    budget: dict | None = None
    compaction: dict | None = None
    worker_user: str | None = None
    preregistration_sha256: str = "UNKNOWN"
    harness_lock_path: Path | None = None
    worker_parent_root: Path | None = None

    def __post_init__(self):
        if type(self) is not RuntimeConfig:
            raise ContractError("exact RuntimeConfig required")
        for name in ("python_executable", "harness_root", "candidate_path", "artifact_root"):
            path = getattr(self, name)
            if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
                raise ContractError("runtime requires explicit absolute paths")
        if self.source_wheels_root is not None and (
                not isinstance(self.source_wheels_root, Path) or not self.source_wheels_root.is_absolute()):
            raise ContractError("explicit source wheel root required")
        if self.mode == "real_public":
            from .real_contracts import normalize_endpoint, validate_budget, validate_compaction
            from .runtime_linux import validate_worker_user
            if self.synthetic_case is not None:
                raise ContractError("real execution cannot receive a synthetic case")
            normalize_endpoint(self.model_endpoint)
            validate_budget(self.budget)
            validate_compaction(self.compaction)
            validate_worker_user(self.worker_user)
            hex_digest(self.preregistration_sha256, "preregistration_sha256")
            if self.source_wheels_root is None:
                raise ContractError("real execution requires explicit native source wheels")
            if self.harness_lock_path is not None and (not isinstance(self.harness_lock_path, Path)
                    or not self.harness_lock_path.is_absolute() or ".." in self.harness_lock_path.parts):
                raise ContractError("Linux lock requires an explicit absolute path")
            if self.worker_parent_root is not None and (not isinstance(self.worker_parent_root, Path)
                    or not self.worker_parent_root.is_absolute() or ".." in self.worker_parent_root.parts):
                raise ContractError("worker parent requires an explicit absolute path")
            with anchor_directory(self.candidate_path.parent) as fd:
                observed = read_regular(fd, self.candidate_path.name, max_bytes=E0_SIZE,
                                        include=False, reject_hardlinks=True)
            if (observed.sha256, observed.size) != (E0_SHA256, E0_SIZE):
                raise ContractError("only frozen E0 is admitted; other candidates require later candidate admission")
        else:
            if self.mode not in ("native_scripted", "isolation_probe") or self.synthetic_case not in CASES:
                raise ContractError("only fixed synthetic execution is admitted")
            if (self.model_endpoint != "SCRIPTED_ONLY" or self.budget is not None or self.compaction is not None
                    or self.worker_user is not None or self.preregistration_sha256 != "UNKNOWN"
                    or self.harness_lock_path is not None or self.worker_parent_root is not None):
                raise ContractError("synthetic execution cannot receive real-runtime fields")
        if type(self.observation_enabled) is not bool:
            raise ContractError("observation_enabled must be bool")
        ceiling = 3600 if self.mode == "real_public" else 600
        if type(self.worker_timeout_seconds) is not int or not 1 <= self.worker_timeout_seconds <= ceiling:
            raise ContractError("invalid worker timeout")
        if self.task_manifest_sha256 != "UNKNOWN":
            hex_digest(self.task_manifest_sha256, "task_manifest_sha256")
        # Evidence must stay under an already ignored generated artifact root.
        base = REPO_ROOT / "artifacts" / "dev_runtime"
        if not self.artifact_root.is_relative_to(base):
            raise ContractError("runtime evidence must be under artifacts/dev_runtime")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    with anchor_directory(path, private=True):
        pass


def _contains_directory(root: Path, candidate: Path) -> bool:
    if candidate.is_relative_to(root):
        return True
    try:
        info = root.stat()
    except FileNotFoundError:
        return False
    identity = (info.st_dev, info.st_ino)
    for ancestor in (candidate, *candidate.parents):
        try:
            current = ancestor.stat()
        except FileNotFoundError:
            continue
        if (current.st_dev, current.st_ino) == identity:
            return True
    return False


def _overlap(left: Path, right: Path) -> bool:
    """Lexical plus inode ancestry: also reject APFS case aliases."""
    return _contains_directory(left, right) or _contains_directory(right, left)


def _copy_regular(source: Path, destination: Path, *, sha256: str | None = None,
                  size: int | None = None) -> None:
    with anchor_directory(source.parent) as fd:
        observed = read_regular(fd, source.name, max_bytes=max(size or 0, 64 * 1024 * 1024),
                                include=True, reject_hardlinks=True)
    if (sha256 is not None and observed.sha256 != sha256) or (size is not None and observed.size != size):
        raise ContractError("runtime source identity mismatch")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        os.chmod(destination, 0o600)
        stream.write(observed.data)


def _phase_code_paths(*, verifier: bool, real: bool = False) -> tuple[str, ...]:
    return (*_COMMON_CODE, *(_REAL_COMMON_CODE if real else ()),
            *(_VERIFIER_CODE if verifier else ("eval/solver_worker.py",)))


def _stage_code(root: Path, *, verifier: bool, real: bool = False, source_records=None) -> None:
    paths = _phase_code_paths(verifier=verifier, real=real)
    if real:
        _private_directory(root / "code")
    for relative in paths:
        if real:
            parent = root / "code"
            for component in Path(relative).parts[:-1]:
                parent = parent / component
                parent.mkdir(mode=0o700, exist_ok=True)
                with anchor_directory(parent, private=True):
                    pass
        identity = source_records.get(relative) if real and source_records is not None else None
        if real and identity is None:
            raise ContractError("staged code lacks coordinator source identity")
        _copy_regular(REPO_ROOT / relative, root / "code" / relative,
                      sha256=identity["sha256"] if identity else None,
                      size=identity["size_bytes"] if identity else None)
    if (root / "evidence").is_dir():
        records = []
        for relative in paths:
            path = root / "code" / relative
            with anchor_directory(path.parent) as descriptor:
                observed = read_regular(descriptor, path.name, include=False, reject_hardlinks=True)
            records.append({"relative_path": relative, "sha256": observed.sha256, "size_bytes": observed.size})
        seal_json(root / "evidence/runtime_sources.json", {"sources": records})


def _stage_wheels(config: RuntimeConfig, root: Path) -> Path:
    target = root / "code/artifacts/harness_wheels/v28"
    if config.mode == "real_public":
        for path in (root / "code/artifacts", root / "code/artifacts/harness_wheels", target):
            _private_directory(path)
    else:
        target.mkdir(mode=0o700, parents=True)
    if config.mode in ("native_scripted", "real_public"):
        if config.source_wheels_root is None:
            raise ContractError("native source pins require explicit wheel root")
        # Existing source constants are stdlib-only; this does not import native code.
        from tools.harness_cert.run_h04_h05_h18 import WHEELS
        for name, digest in WHEELS.items():
            _copy_regular(config.source_wheels_root / name, target / name, sha256=digest)
        if config.mode == "real_public":
            _copy_regular(config.harness_lock_path or _LINUX_LOCK,
                          target / _LINUX_LOCK.name, sha256=_LINUX_LOCK_SHA256)
    return target


def worker_environment(root: Path, *, real: bool = False) -> dict[str, str]:
    """Construct from literals, never from os.environ or a secret-bearing parent."""
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"), "HF_HOME": str(root / "hf_cache"),
        "KAGGLE_SANDBOX_DIR": str(root / "setup"), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "PYTHON_DOTENV_DISABLED": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": os.devnull,
        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "OTEL_SDK_DISABLED": "true",
        "DO_NOT_TRACK": "1", "LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_MODE": "PRODUCTION",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "OPENAI_API_KEY": "SYNTHETIC_DUMMY", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "LANG": "C.UTF-8", "TZ": "UTC",
    }
    if real:
        environment["PATH"] = "/usr/bin:/bin"
        environment.pop("OPENAI_API_KEY")
        environment["KAGGLE_SANDBOX_DIR"] = str(root / "public/sandbox")
    return environment


def _prepare_worker(config: RuntimeConfig, task: SolverTask, public_root: Path, *, verifier: bool,
                    source_records=None) -> tuple[Path, dict]:
    # Independently unpredictable roots; verifier root does not exist while solving.
    real = config.mode == "real_public"
    parent = (config.worker_parent_root or Path("/tmp")) if real else Path("/private/tmp")
    with anchor_directory(parent):
        pass
    root = Path(tempfile.mkdtemp(prefix="gemma-runtime-", dir=parent))
    try:
        for name in ("public", "evidence", "submission", "wheels", "setup", "home", "tmp", "hf_cache"):
            _private_directory(root / name)
        if real:
            _refuse_secret_ancestors(root)
            _stage_code(root, verifier=verifier, real=True, source_records=source_records)
        else:
            _stage_code(root, verifier=verifier)
        source_wheels = _stage_wheels(config, root)
        if config.mode == "native_scripted":
            # Native SubprocessManager selects its local wheel root before any
            # fallback lookup. A real pinned source wheel makes that branch
            # explicit; the native synthetic backend never installs task deps.
            from tools.harness_cert.run_h04_h05_h18 import WHEELS
            wheel_name = "adk_eval_core-0.1.0-py3-none-any.whl"
            _copy_regular(source_wheels / wheel_name, root / "wheels" / wheel_name,
                          sha256=WHEELS[wheel_name])
        stage_solver_assets(task, public_root=public_root, staging_root=root / "public")
        _copy_regular(config.candidate_path, root / "submission/submission.zip", sha256=E0_SHA256, size=E0_SIZE)
        # Stops native import-time ancestor/secret discovery. It is not executed.
        if real:
            _stage_public_support(public_root, root / "public")
            wheels_root, setup_root = root / "public/wheels", root / "public/sandbox"
        else:
            setup = root / "setup/setup.py"
            setup.write_text("# Inert synthetic runtime setup.\n", encoding="utf-8")
            setup.chmod(0o600)
            wheels_root, setup_root = root / "wheels", root / "setup"
        runtime = {"harness_root": str(config.harness_root), "public_root": str(root / "public"),
                   "worker_root": str(root), "evidence_root": str(root / "evidence"),
                   "submission_root": str(root / "submission"), "wheels_root": str(wheels_root),
                   "setup_root": str(setup_root), "sandbox": "subprocess",
                   "image": "SUBPROCESS_NO_IMAGE" if real else config.sandbox_image,
                   "model_endpoint": config.model_endpoint, "source_wheels_root": str(source_wheels),
                   "pytest_support_root": None}
        if real:
            runtime.update(budget=dict(config.budget), compaction=config.compaction,
                           worker_user=config.worker_user, preregistration_sha256=config.preregistration_sha256)
            from .runtime_linux import own_worker_root
            own_worker_root(root, config.worker_user)
        return root, runtime
    except BaseException:
        shutil.rmtree(root)
        raise


def _refuse_secret_ancestors(root: Path) -> None:
    for ancestor in (root, *root.parents):
        candidate = ancestor / "secret"
        if candidate.exists() or candidate.is_symlink():
            raise ContractError("real worker/public storage has a secret-discovery ancestor")


def _stage_public_support(public_root: Path, destination: Path) -> None:
    """Fresh copies of official setup/wheels; no dataset rows or Git injection."""
    with anchor_directory(public_root / "wheels"):
        pass
    _private_directory(destination / "wheels")
    _private_directory(destination / "sandbox")
    for source in sorted((public_root / "wheels").rglob("*")):
        target = destination / "wheels" / source.relative_to(public_root / "wheels")
        if source.is_symlink():
            raise ContractError("official wheel tree contains a symlink")
        if source.is_dir():
            _private_directory(target)
        elif source.is_file():
            with anchor_directory(source.parent) as fd:
                observed = read_regular(fd, source.name, max_bytes=64 * 1024 * 1024,
                                        include=True, reject_hardlinks=False)
            with target.open("xb") as stream:
                target.chmod(0o600)
                stream.write(observed.data)
        else:
            raise ContractError("official wheel tree contains a non-regular entry")
    _copy_regular(public_root / "sandbox/setup.py", destination / "sandbox/setup.py")


def verify_worker_confinement(config: RuntimeConfig, roots: dict[str, Path]) -> dict:
    from .runtime_linux import verify_worker_confinement as probe
    return probe(config, roots)


def _interpreter_roots(python_executable: Path, root: Path) -> tuple[Path, ...]:
    # No site/.pth execution in the unconfined stdlib-only prefix query. Python
    # 3.12 with -S reports the base prefix; the explicit venv path supplies its
    # admitted library root without executing its startup files.
    info = subprocess.run([str(python_executable), "-I", "-B", "-S", "-c",
                           "import sys,json; print(json.dumps(sys.base_prefix))"],
                          cwd=root, env=worker_environment(root), close_fds=True,
                          capture_output=True, text=True, check=True, timeout=10)
    base = Path(json.loads(info.stdout)).resolve(strict=True)
    supplied = python_executable.parent.parent
    with anchor_directory(supplied):
        pass
    if (supplied / "pyvenv.cfg").is_file():
        return (supplied.resolve(strict=True), base)
    return (base,)


_OS_READ_ROOTS = ("/usr", "/bin", "/sbin", "/System", "/Library",
                  "/private/var/db/timezone", "/private/var/db/dyld")


def confined_worker_argv(python_executable: Path, root: Path, arguments: list[str], *,
                        config: RuntimeConfig | None = None) -> list[str]:
    """macOS OS confinement, inherited by native sandbox children. Fail closed.

    Directory metadata is readable for Python/dyld traversal; file contents and
    directory listings outside the explicit roots are denied. No verifier/source
    storage is admitted, even if its path is guessed by subprocess code.
    """
    if __import__("sys").platform == "linux":
        if config is None or config.mode != "real_public":
            raise ContractError("Linux workers require explicit real admission and worker_user")
        from .runtime_linux import linux_prefix
        prefix, _ = linux_prefix(config.worker_user)
        return [*prefix, str(python_executable), "-I", "-B", *arguments]
    if __import__("sys").platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise ContractError("this synthetic runtime requires admitted macOS sandbox-exec confinement")
    libraries = [str(p) for p in _interpreter_roots(python_executable, root)]
    reads = [str(root), *libraries, *_OS_READ_ROOTS]
    filters = " ".join(f"(subpath {json.dumps(p)})" for p in reads)
    # The nofollow descriptor reader opens each ancestor directory. Grant those
    # exact directory objects, never their children or sibling file contents.
    ancestors = sorted({str(parent) for p in reads for parent in Path(p).parents})
    filters += " " + " ".join(f"(literal {json.dumps(p)})" for p in ancestors)
    profile = ("(version 1)(deny default)(allow process*)(allow sysctl-read)(allow mach-lookup)"
               "(allow file-read-metadata)"
               f"(allow file-read* (literal \"/\") (literal \"/dev/null\") (literal \"/dev/random\") "
               f"(literal \"/dev/urandom\") {filters})"
               f"(allow file-write* (subpath {json.dumps(str(root))}) (literal \"/dev/null\") "
               "(literal \"/dev/dtracehelper\"))"
               "(allow network-bind (local ip \"localhost:*\"))"
               "(allow network-inbound (local ip \"localhost:*\"))"
               "(allow network-outbound (remote ip \"localhost:*\"))")
    return ["/usr/bin/sandbox-exec", "-p", profile, str(python_executable), "-I", "-B", *arguments]


def _execute_worker(config: RuntimeConfig, request: dict, root: Path, phase: str) -> dict:
    # -I and exec give macOS/Linux the same semantics: no forked Python heap,
    # cwd import, PYTHONPATH, user site, inherited environment or private FDs.
    bootstrap = "import sys; sys.path.insert(0, sys.argv[1]); from eval." + phase + "_worker import main; main()"
    real = config.mode == "real_public"
    if real:
        argv = confined_worker_argv(config.python_executable, root, ["-c", bootstrap, str(root / "code")], config=config)
    else:
        argv = confined_worker_argv(config.python_executable, root, ["-c", bootstrap, str(root / "code")])
    environment = worker_environment(root, real=real)
    launch = {"argv": argv, "cwd": str(root), "environment": environment,
              "close_fds": True, "start_new_session": True}
    if real:
        from .runtime_provenance import sanitize_evidence
        launch = sanitize_evidence(launch)
        # The coordinator's late launch seal must be readable by the worker.
        from .runtime_linux import worker_account
        account = worker_account(config.worker_user)
    seal_json(root / "evidence/worker_launch.json", launch)
    if real:
        os.chown(root / "evidence/worker_launch.json", account.pw_uid, account.pw_gid, follow_symlinks=False)
    log = root / "evidence/process.log"
    with log.open("xb") as output:
        log.chmod(0o600)
        if real:
            os.chown(log, account.pw_uid, account.pw_gid, follow_symlinks=False)
        process = subprocess.Popen(argv, cwd=root, env=environment, stdin=subprocess.PIPE,
                                   stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                   start_new_session=True)
        try:
            process.communicate(canonical_json(request).encode("utf-8"), timeout=config.worker_timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ContractError("worker timed out before sealing result") from None
        finally:
            # Any surviving native sandbox descendants must terminate before
            # the coordinator reads evidence or stages private verifier bytes.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if real:
                from .runtime_linux import terminate_worker_descendants
                try:
                    terminate_worker_descendants(config.worker_user)
                finally:
                    output.flush()
                    _sanitize_worker_log(log)
    # communicate waits/reaps the solver before any verifier staging or launch.
    if process.returncode != 0:
        raise ContractError(f"{phase} worker failed before sealing result; inspect preserved process.log")
    with anchor_directory(root / "evidence", private=not real) as fd:
        observed = read_regular(fd, "result.json", max_bytes=16 * 1024 * 1024, include=True,
                                reject_hardlinks=True, private=not real)
    result = load_json_object(observed.data, "sealed worker result")
    if observed.data != canonical_json(result).encode("utf-8"):
        raise ContractError("sealed worker result must have canonical bytes")
    return result


def _sanitize_worker_log(log: Path) -> None:
    from .runtime_provenance import sanitize_evidence
    with anchor_directory(log.parent) as fd:
        observed = read_regular(fd, log.name, max_bytes=16 * 1024 * 1024,
                                include=True, reject_hardlinks=True)
    cleaned = sanitize_evidence(observed.data.decode("utf-8", errors="replace")).encode("utf-8")
    fd = os.open(log, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(cleaned)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def _result_reference(result) -> ArtifactRef:
    raw = canonical_json(result.to_dict()).encode("utf-8")
    return ArtifactRef("result", "result.json", hashlib.sha256(raw).hexdigest(), len(raw))


def _archive_phase(root: Path, destination: Path, artifacts=(), *, real: bool = False) -> None:
    if len({ref.relative_path for ref in artifacts}) != len(artifacts):
        raise ContractError("sealed evidence references contain duplicate paths")
    _private_directory(destination)
    # Worker evidence only. Workspaces and actor inputs never go to the verifier.
    expected = {ref.relative_path: ref for ref in artifacts}
    for path in sorted((root / "evidence").rglob("*")):
        relative = path.relative_to(root / "evidence")
        if path.is_symlink():
            raise ContractError("worker evidence contains a symlink")
        if path.is_dir():
            (destination / relative).mkdir(mode=0o700)
        else:
            if real and relative.as_posix() == "process.log":
                from .runtime_provenance import sanitize_evidence
                with anchor_directory(path.parent) as descriptor:
                    raw = read_regular(descriptor, path.name, max_bytes=16 * 1024 * 1024,
                                       include=True, reject_hardlinks=True)
                cleaned = sanitize_evidence(raw.data.decode("utf-8", errors="replace")).encode("utf-8")
                with (destination / relative).open("xb") as stream:
                    os.chmod(destination / relative, 0o600)
                    stream.write(cleaned)
                continue
            if real and relative.as_posix() not in expected:
                if not artifacts:
                    # A failed worker did not seal its evidence inventory. Only
                    # the sanitized process log can be exported as diagnostics.
                    continue
                raise ContractError("real worker evidence contains an unsealed artifact")
            if real:
                from .runtime_provenance import assert_secret_free
                with anchor_directory(path.parent) as descriptor:
                    raw = read_regular(descriptor, path.name, max_bytes=16 * 1024 * 1024,
                                       include=True, reject_hardlinks=True)
                if path.suffix == ".json":
                    assert_secret_free(load_json_object(raw.data, "real evidence"))
                else:
                    assert_secret_free(raw.data.decode("utf-8", errors="replace"))
            ref = expected.pop(relative.as_posix(), None)
            _copy_regular(path, destination / relative, sha256=ref.sha256 if ref else None,
                          size=ref.size_bytes if ref else None)
    if expected:
        raise ContractError("sealed evidence reference is missing")


def _admit_real_task(solver_task, verifier_task, public_root, config) -> dict:
    from .real_contracts import validate_public_task_identity
    from .runtime_real import real_verification_config
    if __import__("sys").platform != "linux":
        raise ContractError("real public execution requires the admitted Linux runtime")
    validate_public_task_identity(solver_task)
    validate_public_task_identity(verifier_task)
    if solver_task.graph is None or solver_task.embedding is None:
        raise ContractError("real public task requires admitted graph and embedding identities")
    if verifier_task.fail_to_pass is not None or verifier_task.pass_to_pass is not None:
        raise ContractError("real public verifier cannot receive private node lists")
    _refuse_secret_ancestors(public_root)
    verification = real_verification_config(public_root, config.budget)
    if canonical_sha256(verification) != verifier_task.verification_config_sha256:
        raise ContractError("real verification configuration differs from task contract")
    return verification


def _real_provenance(solver_task, config, *, confinement) -> dict:
    from .runtime_provenance import runtime_source_identity, runtime_source_records
    from tools.common import tree_sha256
    source = runtime_source_identity(REPO_ROOT)
    records = runtime_source_records(REPO_ROOT)
    if tree_sha256({path: value["sha256"] for path, value in records.items()}) != source["runtime_source_sha256"]:
        raise ContractError("runtime source changed while its identity was captured")
    with anchor_directory(_PREREGISTRATION.parent) as fd:
        prereg = read_regular(fd, _PREREGISTRATION.name, include=False, reject_hardlinks=True)
    if prereg.sha256 != config.preregistration_sha256:
        raise ContractError("preregistration identity differs from admitted plan")
    with anchor_directory((config.harness_lock_path or _LINUX_LOCK).parent) as fd:
        lock = read_regular(fd, (config.harness_lock_path or _LINUX_LOCK).name,
                            max_bytes=4 * 1024 * 1024, include=False, reject_hardlinks=True)
    if lock.sha256 != _LINUX_LOCK_SHA256:
        raise ContractError("Linux harness lock identity differs from admission")
    hex_digest(config.task_manifest_sha256, "task_manifest_sha256")
    return {"git_head": source["git_head"], "solver_contract_sha256": solver_task.sha256(),
            "task_manifest_sha256": config.task_manifest_sha256,
            "eval_infra_source_identity": source,
            "candidate_identity": {"sha256": E0_SHA256, "size_bytes": E0_SIZE},
            "preregistration_identity": {"sha256": prereg.sha256, "size_bytes": prereg.size},
            "model_endpoint_identity": config.model_endpoint,
            "public_identities": {"task_manifest_sha256": config.task_manifest_sha256,
                                  **{key: getattr(solver_task, key).to_dict()
                                     for key in ("snapshot", "graph", "embedding")}},
            "harness_lock_sha256": lock.sha256, "confinement": confinement,
            "runtime_sources": records,
            "staged_source_paths": list(_phase_code_paths(verifier=False, real=True))}


def _read_real_fingerprint(root: Path, result, provenance: dict, phase: str) -> dict:
    from .runtime_provenance import IDENTITY_FIELDS, validate_real_fingerprint
    refs = [ref for ref in result.artifacts if ref.relative_path == "real_fingerprint.json"]
    if len(refs) != 1 or result.fingerprint is None:
        raise ContractError("real phase is missing its mandatory sealed runtime fingerprint")
    with anchor_directory(root / "evidence") as fd:
        observed = read_regular(fd, "real_fingerprint.json", include=True, reject_hardlinks=True)
    if (observed.sha256, observed.size) != (refs[0].sha256, refs[0].size_bytes):
        raise ContractError("real phase fingerprint artifact seal mismatch")
    value = load_json_object(observed.data, "real phase fingerprint")
    if observed.data != canonical_json(value).encode():
        raise ContractError("real phase fingerprint must have canonical bytes")
    validate_real_fingerprint(value, phase=phase)
    for key in (*IDENTITY_FIELDS, "public_identities", "harness_lock_sha256", "confinement"):
        if value[key] != provenance[key]:
            raise ContractError("real phase fingerprint differs from coordinator admission")
    return value


def run_task(solver_task: SolverTask, verifier_task: VerifierTask, *, public_root: Path,
             private_root: Path, test_patch_relative_path: str, config: RuntimeConfig) -> TaskRuntimeResult:
    """Execute once, using native returned patch and a fresh verifier only."""
    if type(solver_task) is not SolverTask or type(verifier_task) is not VerifierTask or type(config) is not RuntimeConfig:
        raise ContractError("runtime requires exact admitted boundary types")
    solver_task.to_dict()
    verifier_task.to_dict()
    config.__post_init__()
    if ((solver_task.instance_id, solver_task.repo, solver_task.base_commit, solver_task.snapshot)
            != (verifier_task.instance_id, verifier_task.repo, verifier_task.base_commit, verifier_task.snapshot)):
        raise ContractError("solver/verifier task identity mismatch")
    real = config.mode == "real_public"
    if real:
        verification = _admit_real_task(solver_task, verifier_task, public_root, config)
    elif solver_task.repo != "synthetic/probe" or not solver_task.instance_id.startswith("synthetic_"):
        raise ContractError("public DEV execution requires independent audit and operator admission")
    with anchor_directory(public_root):
        pass
    with anchor_directory(private_root, private=True):
        pass
    if _overlap(config.artifact_root, public_root):
        raise ContractError("evidence and source roots must be separate")
    if _overlap(private_root, config.artifact_root):
        raise ContractError("private source and runtime evidence roots must be separate")
    if real and _overlap(public_root, private_root):
        raise ContractError("public and private source roots must be separate")
    # Validate existing ancestors before creating ignored outputs.
    nearest = config.artifact_root
    while not nearest.exists():
        nearest = nearest.parent
    with anchor_directory(nearest):
        pass
    # An explicitly supplied data root must never overlap a library exception
    # in the worker's OS profile. Metadata inspection does not read test bytes.
    permitted = (*_interpreter_roots(config.python_executable, nearest),
                 *(Path(p) for p in _OS_READ_ROOTS))
    for data_root in (public_root, private_root, config.artifact_root):
        if any(_overlap(data_root, p) for p in permitted):
            raise ContractError("data storage overlaps worker runtime library access")
    config.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with anchor_directory(config.artifact_root, private=True):
        pass
    run_root = config.artifact_root / uuid.uuid4().hex
    _private_directory(run_root)
    if real:
        seal_json(run_root / "run_identity.json", {"schema_version": 1,
                  "task_id": solver_task.instance_id, "solver_contract_sha256": solver_task.sha256(),
                  "preregistration_sha256": config.preregistration_sha256, "candidate_sha256": E0_SHA256})
    git_head = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
    if real:
        confinement = verify_worker_confinement(config, {"public": public_root, "private": private_root,
            "artifacts": config.artifact_root, "repository_git": REPO_ROOT / ".git"})
        seal_json(run_root / "confinement.json", confinement)
        provenance = _real_provenance(solver_task, config, confinement=confinement)
    else:
        provenance = {"git_head": git_head, "solver_contract_sha256": solver_task.sha256(),
                      "task_manifest_sha256": config.task_manifest_sha256}
    common = {"schema_version": 1, "candidate": {"relative_path": "submission.zip", "sha256": E0_SHA256,
              "size_bytes": E0_SIZE}, "observation_enabled": config.observation_enabled,
              "synthetic_case": config.synthetic_case, "mode": config.mode, "provenance": provenance}
    if real:
        solver_root, runtime = _prepare_worker(config, solver_task, public_root, verifier=False,
                                                source_records=provenance["runtime_sources"])
    else:
        solver_root, runtime = _prepare_worker(config, solver_task, public_root, verifier=False)
    solver = None
    try:
        request = {**common, "task": solver_task.to_dict(), "runtime": runtime}
        solver = SolverRunResult.from_dict(_execute_worker(config, request, solver_root, "solver"))
        if solver.task_id != solver_task.instance_id or solver.fingerprint is None:
            raise ContractError("sealed solver task identity mismatch")
        if solver.fingerprint.solver_contract_sha256 != solver_task.sha256() or solver.fingerprint.candidate_sha256 != E0_SHA256:
            raise ContractError("sealed solver provenance mismatch")
        if real:
            solver_fingerprint = _read_real_fingerprint(solver_root, solver, provenance, "solver")
    finally:
        try:
            refs = (*solver.artifacts, _result_reference(solver)) if solver else ()
            if real:
                _archive_phase(solver_root, run_root / "solver", refs, real=True)
            else:
                _archive_phase(solver_root, run_root / "solver", refs)
        finally:
            shutil.rmtree(solver_root)
    # Private bytes are first read here, after the solver process and root are gone.
    material = load_verifier_material(verifier_task, private_root=private_root,
                                      test_patch_relative_path=test_patch_relative_path)
    if real:
        verifier_root, runtime = _prepare_worker(config, solver_task, public_root, verifier=True,
                                                  source_records=provenance["runtime_sources"])
    else:
        verifier_root, runtime = _prepare_worker(config, solver_task, public_root, verifier=True)
    verifier = None
    try:
        if config.mode == "native_scripted":
            from tools.harness_cert._synthetic_verification import prepare_support
            support = prepare_support(REPO_ROOT, verifier_root)
            runtime["pytest_support_root"] = support["root"]
            Path(support["root"]).chmod(0o700)
        if real:
            verifier_config = verification
        else:
            from .runtime_verifier_fixture import verification_config
            verifier_config = verification_config()
        request = {**common, "task": verifier_task.to_dict(), "runtime": runtime,
                   "returned_patch": solver.returned_patch, "returned_patch_sha256": solver.returned_patch_sha256,
                   "agent_error": solver.agent_error, "test_patch": material.test_patch,
                   "verification_config": verifier_config}
        if real:
            request["provenance"] = {**provenance,
                "staged_source_paths": list(_phase_code_paths(verifier=True, real=True))}
        verifier = VerifierRunResult.from_dict(_execute_worker(config, request, verifier_root, "verifier"))
        if real:
            verifier_fingerprint = _read_real_fingerprint(verifier_root, verifier, provenance, "verifier")
            from .runtime_provenance import crosscheck_real_fingerprints
            crosscheck_real_fingerprints(solver_fingerprint, verifier_fingerprint, expected=provenance)
    finally:
        try:
            refs = (*verifier.artifacts, _result_reference(verifier)) if verifier else ()
            if real:
                _archive_phase(verifier_root, run_root / "verifier", refs, real=True)
            else:
                _archive_phase(verifier_root, run_root / "verifier", refs)
        finally:
            shutil.rmtree(verifier_root)
    phase_refs = []
    for phase in ("solver", "verifier"):
        with anchor_directory(run_root / phase, private=True) as fd:
            observed = read_regular(fd, "result.json", max_bytes=16 * 1024 * 1024, include=False,
                                    reject_hardlinks=True, private=True)
        phase_refs.append(ArtifactRef(f"{phase}_result", f"{run_root.name}/{phase}/result.json",
                                      observed.sha256, observed.size))
    result = TaskRuntimeResult(1, solver_task.instance_id, solver, verifier, verifier.resolved,
                               artifacts=tuple(phase_refs), fingerprint=solver.fingerprint)
    seal_json(run_root / "task_result.json", result.to_dict())
    return result


def run_verifier_control(solver_task: SolverTask, verifier_task: VerifierTask, *, public_root: Path,
                         private_root: Path, test_patch_relative_path: str, config: RuntimeConfig,
                         returned_patch: str, control: str) -> dict:
    """Verifier-only control; never fabricate a solver process or agent events."""
    if (type(solver_task) is not SolverTask or type(verifier_task) is not VerifierTask
            or type(config) is not RuntimeConfig or config.mode != "real_public"):
        raise ContractError("verifier controls require exact real-public boundary types")
    if control not in ("no_patch", "known_patch") or type(returned_patch) is not str:
        raise ContractError("unknown verifier-only control")
    if control == "no_patch" and returned_patch:
        raise ContractError("no-patch control cannot receive a patch")
    config.__post_init__()
    solver_task.to_dict()
    verifier_task.to_dict()
    if ((solver_task.instance_id, solver_task.repo, solver_task.base_commit, solver_task.snapshot)
            != (verifier_task.instance_id, verifier_task.repo, verifier_task.base_commit, verifier_task.snapshot)):
        raise ContractError("control public/verifier identity mismatch")
    verification = _admit_real_task(solver_task, verifier_task, public_root, config)
    with anchor_directory(public_root):
        pass
    with anchor_directory(private_root, private=True):
        pass
    if any(_overlap(left, right) for left, right in ((public_root, private_root),
            (public_root, config.artifact_root), (private_root, config.artifact_root))):
        raise ContractError("control source, private, and artifact roots must be separate")
    config.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with anchor_directory(config.artifact_root, private=True):
        pass
    run_root = config.artifact_root / uuid.uuid4().hex
    _private_directory(run_root)
    gold = control == "known_patch"
    manifest = {"schema_version": 1, "role": "verifier_only_control", "control": control,
                "task_id": solver_task.instance_id, "gold_assisted": gold, "solver_started": False,
                "excluded_from_normal_reports": True, "excluded_from_candidate_comparison": True,
                "excluded_from_designer_inspection": True, "excluded_from_solver_inspection": True}
    seal_json(run_root / "run_manifest.json", manifest)
    if gold:
        seal_json(run_root / "GOLD_ASSISTED_VERIFIER_ONLY.json", manifest)
    confinement = verify_worker_confinement(config, {"public": public_root, "private": private_root,
        "artifacts": config.artifact_root, "repository_git": REPO_ROOT / ".git"})
    seal_json(run_root / "confinement.json", confinement)
    provenance = _real_provenance(solver_task, config, confinement=confinement)
    provenance["staged_source_paths"] = list(_phase_code_paths(verifier=True, real=True))
    material = load_verifier_material(verifier_task, private_root=private_root,
                                      test_patch_relative_path=test_patch_relative_path)
    root, runtime = _prepare_worker(config, solver_task, public_root, verifier=True,
                                    source_records=provenance["runtime_sources"])
    verifier = None
    try:
        from .worker_common import patch_sha256
        request = {"schema_version": 1, "candidate": {"relative_path": "submission.zip",
                    "sha256": E0_SHA256, "size_bytes": E0_SIZE}, "mode": "real_public",
                   "synthetic_case": None, "observation_enabled": config.observation_enabled,
                   "provenance": provenance, "task": verifier_task.to_dict(), "runtime": runtime,
                   "returned_patch": returned_patch, "returned_patch_sha256": patch_sha256(returned_patch),
                   "agent_error": None, "test_patch": material.test_patch, "verification_config": verification}
        verifier = VerifierRunResult.from_dict(_execute_worker(config, request, root, "verifier"))
        if verifier.task_id != solver_task.instance_id or verifier.returned_patch_sha256 != patch_sha256(returned_patch):
            raise ContractError("control result did not consume the authoritative control patch")
        _read_real_fingerprint(root, verifier, provenance, "verifier")
    finally:
        try:
            refs = (*verifier.artifacts, _result_reference(verifier)) if verifier else ()
            _archive_phase(root, run_root / "verifier", refs, real=True)
        finally:
            shutil.rmtree(root)
    observations = {}
    native_path = run_root / "verifier/native_observation.json"
    if native_path.exists():
        with anchor_directory(native_path.parent, private=True) as fd:
            raw = read_regular(fd, native_path.name, include=True, reject_hardlinks=True, private=True)
        native = load_json_object(raw.data, "control native observations")
        observations = {key: native.get(key) for key in ("apply_status", "apply_error", "required_tests_passed", "test_exit_code")}
    result = {"schema_version": 1, "control": control, "task_id": solver_task.instance_id,
              "verifier_result": verifier.to_dict(), "resolved": verifier.resolved,
              "run_root": str(run_root), "gold_assisted": gold, "solver_started": False,
              "verification_observations": observations}
    seal_json(run_root / "control_result.json", result)
    return result
