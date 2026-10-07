"""One-task trusted coordinator. Only fixed synthetic execution is admitted.

No native imports, scheduling, retries, model endpoint, or mixed-row intake. The
solver is re-executed with clean process state and exits before private staging.
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

from ._contracts import ContractError, canonical_json, hex_digest, load_json_object
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


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    python_executable: Path
    harness_root: Path
    candidate_path: Path
    artifact_root: Path
    source_wheels_root: Path | None = None
    synthetic_case: str = "H05"
    mode: str = "native_scripted"
    observation_enabled: bool = True
    task_manifest_sha256: str = "UNKNOWN"
    sandbox_image: str = "UNKNOWN"
    worker_timeout_seconds: int = 120

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
        if self.mode not in ("native_scripted", "isolation_probe") or self.synthetic_case not in CASES:
            raise ContractError("only fixed synthetic execution is admitted")
        if type(self.observation_enabled) is not bool:
            raise ContractError("observation_enabled must be bool")
        if type(self.worker_timeout_seconds) is not int or not 1 <= self.worker_timeout_seconds <= 600:
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


def _stage_code(root: Path, *, verifier: bool) -> None:
    paths = (*_COMMON_CODE, *(_VERIFIER_CODE if verifier else ("eval/solver_worker.py",)))
    for relative in paths:
        _copy_regular(REPO_ROOT / relative, root / "code" / relative)
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
    target.mkdir(mode=0o700, parents=True)
    if config.mode == "native_scripted":
        if config.source_wheels_root is None:
            raise ContractError("native source pins require explicit wheel root")
        # Existing source constants are stdlib-only; this does not import native code.
        from tools.harness_cert.run_h04_h05_h18 import WHEELS
        for name, digest in WHEELS.items():
            _copy_regular(config.source_wheels_root / name, target / name, sha256=digest)
    return target


def worker_environment(root: Path) -> dict[str, str]:
    """Construct from literals, never from os.environ or a secret-bearing parent."""
    return {
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


def _prepare_worker(config: RuntimeConfig, task: SolverTask, public_root: Path, *, verifier: bool) -> tuple[Path, dict]:
    # Independently unpredictable roots; verifier root does not exist while solving.
    root = Path(tempfile.mkdtemp(prefix="gemma-runtime-", dir="/private/tmp"))
    try:
        for name in ("public", "evidence", "submission", "wheels", "setup", "home", "tmp", "hf_cache"):
            _private_directory(root / name)
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
        setup = root / "setup/setup.py"
        setup.write_text("# Inert synthetic runtime setup.\n", encoding="utf-8")
        setup.chmod(0o600)
        runtime = {"harness_root": str(config.harness_root), "public_root": str(root / "public"),
                   "worker_root": str(root), "evidence_root": str(root / "evidence"),
                   "submission_root": str(root / "submission"), "wheels_root": str(root / "wheels"),
                   "setup_root": str(root / "setup"), "sandbox": "subprocess", "image": config.sandbox_image,
                   "model_endpoint": "SCRIPTED_ONLY", "source_wheels_root": str(source_wheels),
                   "pytest_support_root": None}
        return root, runtime
    except BaseException:
        shutil.rmtree(root)
        raise


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


def confined_worker_argv(python_executable: Path, root: Path, arguments: list[str]) -> list[str]:
    """macOS OS confinement, inherited by native sandbox children. Fail closed.

    Directory metadata is readable for Python/dyld traversal; file contents and
    directory listings outside the explicit roots are denied. No verifier/source
    storage is admitted, even if its path is guessed by subprocess code.
    """
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
    argv = confined_worker_argv(config.python_executable, root, ["-c", bootstrap, str(root / "code")])
    seal_json(root / "evidence/worker_launch.json", {"argv": argv, "cwd": str(root),
              "environment": worker_environment(root), "close_fds": True, "start_new_session": True})
    log = root / "evidence/process.log"
    with log.open("xb") as output:
        log.chmod(0o600)
        process = subprocess.Popen(argv, cwd=root, env=worker_environment(root), stdin=subprocess.PIPE,
                                   stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                   start_new_session=True)
        try:
            process.communicate(canonical_json(request).encode("utf-8"), timeout=config.worker_timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ContractError("synthetic worker timed out before sealing result") from None
        finally:
            # Any surviving native sandbox descendants must terminate before
            # the coordinator reads evidence or stages private verifier bytes.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    # communicate waits/reaps the solver before any verifier staging or launch.
    if process.returncode != 0:
        raise ContractError(f"{phase} worker failed before sealing result; inspect preserved process.log")
    with anchor_directory(root / "evidence", private=True) as fd:
        observed = read_regular(fd, "result.json", max_bytes=16 * 1024 * 1024, include=True,
                                reject_hardlinks=True, private=True)
    result = load_json_object(observed.data, "sealed worker result")
    if observed.data != canonical_json(result).encode("utf-8"):
        raise ContractError("sealed worker result must have canonical bytes")
    return result


def _result_reference(result) -> ArtifactRef:
    raw = canonical_json(result.to_dict()).encode("utf-8")
    return ArtifactRef("result", "result.json", hashlib.sha256(raw).hexdigest(), len(raw))


def _archive_phase(root: Path, destination: Path, artifacts=()) -> None:
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
            ref = expected.pop(relative.as_posix(), None)
            _copy_regular(path, destination / relative, sha256=ref.sha256 if ref else None,
                          size=ref.size_bytes if ref else None)
    if expected:
        raise ContractError("sealed evidence reference is missing")


def run_task(solver_task: SolverTask, verifier_task: VerifierTask, *, public_root: Path,
             private_root: Path, test_patch_relative_path: str, config: RuntimeConfig) -> TaskRuntimeResult:
    """Execute once, using returned patch only. Public/model runs fail admission."""
    if type(solver_task) is not SolverTask or type(verifier_task) is not VerifierTask or type(config) is not RuntimeConfig:
        raise ContractError("runtime requires exact admitted boundary types")
    solver_task.to_dict()
    verifier_task.to_dict()
    config.__post_init__()
    if ((solver_task.instance_id, solver_task.repo, solver_task.base_commit, solver_task.snapshot)
            != (verifier_task.instance_id, verifier_task.repo, verifier_task.base_commit, verifier_task.snapshot)):
        raise ContractError("solver/verifier task identity mismatch")
    if solver_task.repo != "synthetic/probe" or not solver_task.instance_id.startswith("synthetic_"):
        raise ContractError("public DEV execution requires independent audit and operator admission")
    with anchor_directory(public_root):
        pass
    with anchor_directory(private_root, private=True):
        pass
    if _overlap(config.artifact_root, public_root):
        raise ContractError("evidence and source roots must be separate")
    if _overlap(private_root, config.artifact_root):
        raise ContractError("private source and runtime evidence roots must be separate")
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
    git_head = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
    provenance = {"git_head": git_head, "solver_contract_sha256": solver_task.sha256(),
                  "task_manifest_sha256": config.task_manifest_sha256}
    common = {"schema_version": 1, "candidate": {"relative_path": "submission.zip", "sha256": E0_SHA256,
              "size_bytes": E0_SIZE}, "observation_enabled": config.observation_enabled,
              "synthetic_case": config.synthetic_case, "mode": config.mode, "provenance": provenance}
    solver_root, runtime = _prepare_worker(config, solver_task, public_root, verifier=False)
    solver = None
    try:
        request = {**common, "task": solver_task.to_dict(), "runtime": runtime}
        solver = SolverRunResult.from_dict(_execute_worker(config, request, solver_root, "solver"))
        if solver.task_id != solver_task.instance_id or solver.fingerprint is None:
            raise ContractError("sealed solver task identity mismatch")
        if solver.fingerprint.solver_contract_sha256 != solver_task.sha256() or solver.fingerprint.candidate_sha256 != E0_SHA256:
            raise ContractError("sealed solver provenance mismatch")
    finally:
        try:
            refs = (*solver.artifacts, _result_reference(solver)) if solver else ()
            _archive_phase(solver_root, run_root / "solver", refs)
        finally:
            shutil.rmtree(solver_root)
    # Private bytes are first read here, after the solver process and root are gone.
    material = load_verifier_material(verifier_task, private_root=private_root,
                                      test_patch_relative_path=test_patch_relative_path)
    verifier_root, runtime = _prepare_worker(config, solver_task, public_root, verifier=True)
    verifier = None
    try:
        if config.mode == "native_scripted":
            from tools.harness_cert._synthetic_verification import prepare_support
            support = prepare_support(REPO_ROOT, verifier_root)
            runtime["pytest_support_root"] = support["root"]
            Path(support["root"]).chmod(0o700)
        from .runtime_verifier_fixture import verification_config
        request = {**common, "task": verifier_task.to_dict(), "runtime": runtime,
                   "returned_patch": solver.returned_patch, "returned_patch_sha256": solver.returned_patch_sha256,
                   "agent_error": solver.agent_error, "test_patch": material.test_patch,
                   "verification_config": verification_config()}
        verifier = VerifierRunResult.from_dict(_execute_worker(config, request, verifier_root, "verifier"))
    finally:
        try:
            refs = (*verifier.artifacts, _result_reference(verifier)) if verifier else ()
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
