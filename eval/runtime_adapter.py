"""Source-pinned native harness calls for fixed, model-free CPU certification.

Each entry point runs once in a newly executed worker. Optional imports happen
after explicit roots and exact source admission. Public execution is refused:
the subprocess manager is a synthetic fixture runner, not an OS security jail.
The solver import path never imports verifier contracts or test fixtures.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib.machinery import ModuleSpec
import io
import logging
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tarfile
import tempfile
import time
import traceback
from types import ModuleType, SimpleNamespace

from ._contracts import ContractError, canonical_json, canonical_sha256
from .runtime_fixture import CASES, CHANGED, INITIAL, budgets, declared_tools, expected_patch, scripts
from .solver_task import SolverTask

RUNTIME_WHEEL_NAME = "adk_eval_core-0.1.0-py3-none-any.whl"
RUNTIME_WHEEL_SHA256 = "194dd8f9aab154857bfa0db9d9ec382db856ee124529e072509634d3071d8643"


class RuntimeAdmissionError(ContractError):
    """Native execution was refused before an inference or uncontrolled task."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root(value, field: str) -> Path:
    if type(value) is not str or not value.startswith("/"):
        raise RuntimeAdmissionError(f"{field} requires an explicit absolute directory")
    path = Path(value)
    if path.as_posix() != value or path.resolve(strict=True) != path or not path.is_dir():
        raise RuntimeAdmissionError(f"{field} is missing, noncanonical or aliased")
    return path


def _admit(request: dict, task):
    from tools.harness_cert.run_h04_h05_h18 import verify_environment
    from tools.h23_v4.filesystem import anchor_directory, read_regular

    runtime = request.get("runtime")
    if type(runtime) is not dict or runtime.get("sandbox") != "subprocess" or runtime.get("model_endpoint") != "SCRIPTED_ONLY":
        raise RuntimeAdmissionError("only fixed synthetic subprocess execution with SCRIPTED_ONLY is admitted")
    case = request.get("synthetic_case")
    if case not in CASES or task.repo != "synthetic/probe":
        raise RuntimeAdmissionError("public task execution is disabled pending independent runtime audit")
    if type(request.get("observation_enabled", True)) is not bool:
        raise RuntimeAdmissionError("observation_enabled must be boolean")
    paths = {key: _root(runtime.get(key), key) for key in (
        "harness_root", "public_root", "worker_root", "evidence_root", "submission_root", "wheels_root", "setup_root")}
    if paths["harness_root"] != Path(sys.prefix).resolve():
        raise RuntimeAdmissionError("worker must use its explicitly admitted harness interpreter")
    for name in ("public_root", "evidence_root", "submission_root", "wheels_root", "setup_root"):
        if not paths[name].is_relative_to(paths["worker_root"]):
            raise RuntimeAdmissionError("synthetic execution inputs must be staged inside this worker root")
    _admit_local_wheel(paths["wheels_root"])
    setup = paths["setup_root"] / "setup.py"
    if not setup.is_file() or setup.is_symlink() or setup.read_bytes() not in (
            b"# Inert synthetic fixture; never executed.\n", b"# Inert synthetic runtime setup.\n"):
        raise RuntimeAdmissionError("synthetic setup root requires the fixed inert setup.py")
    # This source admission imports no optional package and checks all Python
    # source members of the five exact v28 distributions against source wheels.
    code_root = Path(__file__).resolve().parents[1]
    pin = verify_environment(paths["harness_root"].parent, code_root)
    # The native manager receives this one real, pinned local wheel before its
    # first exec, so its original local-wheel branch never probes fallback roots.
    candidates = [paths["wheels_root"]]
    with anchor_directory(paths["public_root"]) as descriptor:
        observed = read_regular(descriptor, task.snapshot.source_relative_path,
                                max_bytes=32 * 1024 * 1024, include=True)
    if (observed.sha256, observed.size) != (task.snapshot.sha256, task.snapshot.size_bytes):
        raise RuntimeAdmissionError("staged snapshot identity mismatch")
    _admit_snapshot(observed.data, task.base_commit)
    return paths, case, pin, candidates


def _admit_local_wheel(root: Path) -> None:
    from tools.h23_v4.filesystem import anchor_directory, read_regular
    if {path.name for path in root.iterdir()} != {RUNTIME_WHEEL_NAME}:
        raise RuntimeAdmissionError("synthetic wheel root must contain only the pinned local runtime wheel")
    with anchor_directory(root) as descriptor:
        observed = read_regular(descriptor, RUNTIME_WHEEL_NAME, max_bytes=32 * 1024 * 1024,
                                include=False, reject_hardlinks=True)
    if observed.sha256 != RUNTIME_WHEEL_SHA256:
        raise RuntimeAdmissionError("local native runtime wheel identity mismatch")


def _admit_snapshot(data: bytes, base_commit: str) -> None:
    """Only the inert synthetic repository may reach native host subprocesses."""
    found = set()
    git_directories = {".git", ".git/hooks", ".git/branches", ".git/info", ".git/objects", ".git/objects/info",
                       ".git/objects/pack", ".git/refs", ".git/refs/heads", ".git/refs/tags",
                       ".git/logs", ".git/logs/refs", ".git/logs/refs/heads"}
    git_files = {".git/config", ".git/HEAD", ".git/COMMIT_EDITMSG", ".git/refs/heads/main",
                 ".git/logs/HEAD", ".git/logs/refs/heads/main"}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if (path.is_absolute() or ".." in path.parts or member.name in found
                    or not (member.isfile() or member.isdir()) or member.size > 4 * 1024 * 1024
                    or (member.mode & (stat.S_ISUID | stat.S_ISGID))):
                raise RuntimeAdmissionError("unsafe synthetic snapshot entry")
            found.add(member.name)
            if path.parts[0] != ".git":
                if member.name != "app.py" or not member.isfile() or archive.extractfile(member).read() != INITIAL.encode():
                    raise RuntimeAdmissionError("snapshot is not the fixed synthetic public fixture")
                continue
            if member.name == ".git/index":
                raise RuntimeAdmissionError("synthetic snapshots must omit stale Git indexes")
            if len(path.parts) >= 3 and path.parts[1] == "hooks":
                raise RuntimeAdmissionError("synthetic Git hooks must be empty")
            if member.isdir() and member.name not in git_directories and re.fullmatch(r"\.git/objects/[0-9a-f]{2}", member.name) is None:
                raise RuntimeAdmissionError("unadmitted synthetic Git directory")
            if member.isfile() and member.name not in git_files and re.fullmatch(r"\.git/objects/[0-9a-f]{2}/[0-9a-f]{38}", member.name) is None:
                raise RuntimeAdmissionError("unadmitted synthetic Git file")
            if member.name == ".git/config":
                content = archive.extractfile(member).read().decode()
                expected_config = ("[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n"
                                   "\tlogallrefupdates = true\n\tignorecase = true\n\tprecomposeunicode = true\n"
                                   "\thooksPath = .git/hooks\n\tabbrev = 7\n\tautocrlf = false\n\tfsmonitor = false\n"
                                   "[user]\n\tname = Synthetic runtime fixture\n\temail = synthetic@example.invalid\n"
                                   "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n")
                # Darwin's two filesystem flags are optional on other hosts.
                configurations = {expected_config, expected_config.replace("\tignorecase = true\n", "").replace("\tprecomposeunicode = true\n", "")}
                if content not in configurations:
                    raise RuntimeAdmissionError("unsafe synthetic Git configuration")
            if member.name == ".git/HEAD" and archive.extractfile(member).read() != b"ref: refs/heads/main\n":
                raise RuntimeAdmissionError("unsafe synthetic Git HEAD")
            if member.name == ".git/refs/heads/main":
                if archive.extractfile(member).read().decode().strip() != base_commit:
                    raise RuntimeAdmissionError("synthetic base commit mismatch")
    if not {"app.py", ".git/config", ".git/HEAD", ".git/refs/heads/main"}.issubset(found):
        raise RuntimeAdmissionError("incomplete synthetic snapshot")


def _prepare(paths):
    for name in ("home", "tmp", "hf_cache"):
        (paths["worker_root"] / name).mkdir(exist_ok=True)
    # This worker is disposable. Replace, rather than augment, inherited model
    # endpoints, private-root variables, credentials and discovery variables.
    os.environ.clear()
    os.environ.update({
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(paths["worker_root"] / "home"),
        "TMPDIR": str(paths["worker_root"] / "tmp"), "HF_HOME": str(paths["worker_root"] / "hf_cache"),
        "KAGGLE_SANDBOX_DIR": str(paths["setup_root"]), "OPENAI_API_KEY": "SYNTHETIC_DUMMY",
        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1", "OTEL_SDK_DISABLED": "true",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_MODE": "PRODUCTION", "DO_NOT_TRACK": "1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    sys.dont_write_bytecode = True
    tempfile.tempdir = str(paths["worker_root"] / "tmp")
    os.chdir(paths["worker_root"])


def _guard(paths, pin, candidates):
    from tools.harness_cert._probe_safety import IPv6FeatureProbe, ParentGuard, discover_system_runtime_reads

    site = Path(pin["site_packages"])
    guard = ParentGuard(paths["worker_root"] / "inert_guard_anchor", paths["worker_root"],
                        [paths["evidence_root"]], [],
                        read_roots=[Path(sys.prefix), Path(sys.base_prefix)],
                        metadata_dirs=candidates,
                        system_runtime_reads=discover_system_runtime_reads(),
                        ipv6_feature_probe=IPv6FeatureProbe.from_site(site))
    # Use the original direct hook: its pinned urllib3 feature-probe and null
    # sink checks rely on the exact immediate caller code identity.
    sys.addaudithook(guard.audit)
    return guard


def _native_namespaces(site: Path) -> None:
    """Bypass only eager public re-export aggregators, preserving native leaves.

    The admitted swegemma/__init__.py imports Evaluator and sample-verification;
    harness/__init__.py imports both phases. Namespace package paths ensure a
    solver loads unchanged agent_runner.py without loading those broad modules.
    """
    for name, relative in (("swegemma", "swegemma"), ("swegemma.harness", "swegemma/harness")):
        if name in sys.modules:
            raise RuntimeAdmissionError("native package was imported before source admission")
        package = ModuleType(name)
        package.__package__ = name
        package.__path__ = [str(site / relative)]
        package.__spec__ = ModuleSpec(name, loader=None, is_package=True)
        package.__spec__.submodule_search_locations = package.__path__
        sys.modules[name] = package
    # Retain the admitted root aggregator's logging-only telemetry suppression.
    logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)


def _artifact(path: Path, data: bytes) -> dict:
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"relative_path": path.name, "sha256": _sha(data), "size_bytes": len(data)}


def _source_script(site: Path, name: str) -> str:
    source = ast.parse((site / "swegemma/harness/container_setup.py").read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return next(ast.literal_eval(node.value) for node in ast.walk(function)
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "script"
                                                       for target in node.targets))


def _wrap_manager(manager, paths, snapshot, site, guard, candidates, *, observing, verifier=False, support=None,
                  returned_patch=None, private_patch=None):
    """Instance command/copy admission is active even with observation OFF."""
    from tools.harness_cert.run_h04_h05_h18 import sandbox_commands

    commands = sandbox_commands(site, snapshot.name)
    patch_prefix = "python3 -S -c " + shlex.quote(_source_script(site, "apply_patch_in_container")) + " /workspace "
    if verifier:
        reset = "test_marker.py conftest.py pytest.ini sitecustomize.py usercustomize.py _swegemma_stubs.py"
        overwrite = "python3 -S -c " + shlex.quote(_source_script(site, "setup_workspace_test_config")) + " /workspace synthetic/probe 1"
        commands |= {
            overwrite,
            'cd /workspace && git add -A && git commit -m "eval_baseline" --allow-empty -q && git tag -f _swegemma_baseline',
            "cd /workspace && (git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null) || true",
            f"cd /workspace && git checkout HEAD -- {reset} 2>/dev/null || true",
            f"cd /workspace && git clean -f -- {reset} 2>/dev/null || true",
        }
    original_start, original_exec, original_copy, original_stop = manager.start, manager.exec, manager.copy_to, manager.stop
    result = {"commands": [], "copies": [], "workspace": None, "junit_xml": None, "workspace_patch": None,
              "sandbox_ids": [], "stopped_ids": [], "native_wheel_probes": [],
              "local_wheel": {"filename": RUNTIME_WHEEL_NAME, "sha256": RUNTIME_WHEEL_SHA256}}
    admitted_patches = set()
    junit_paths = set()

    def start():
        with guard.processes_allowed("venv"):
            identifier = original_start()
        if result["sandbox_ids"]:
            guard.refuse("only one fresh sandbox is admitted per worker")
        result["sandbox_ids"].append(identifier)
        # Installation is skipped natively for exact SubprocessManager. This
        # byte copy controls only root discovery and occurs before any native
        # setup command or agent activity, identically with observation ON/OFF.
        original_copy(identifier, paths["wheels_root"] / RUNTIME_WHEEL_NAME, "/wheels/")
        local_wheels = manager.sandboxes[identifier]["wheels"]
        if ({path.name for path in local_wheels.iterdir()} != {RUNTIME_WHEEL_NAME}
                or _sha((local_wheels / RUNTIME_WHEEL_NAME).read_bytes()) != RUNTIME_WHEEL_SHA256):
            guard.refuse("native local wheel copy identity mismatch")
        if verifier:
            from tools.harness_cert._synthetic_verification import stage_support
            stage_support(manager, identifier, support, guard)
        return identifier

    def execute(identifier, command, timeout=None):
        if identifier not in manager.sandboxes:
            guard.refuse("unadmitted native synthetic sandbox")
        local_wheels = manager.sandboxes[identifier]["wheels"]
        if ({path.name for path in local_wheels.iterdir()} != {RUNTIME_WHEEL_NAME}
                or _sha((local_wheels / RUNTIME_WHEEL_NAME).read_bytes()) != RUNTIME_WHEEL_SHA256):
            guard.refuse("native local wheel changed before command")
        accepted = command in commands
        if verifier:
            if command.startswith(patch_prefix) and command[len(patch_prefix):] in admitted_patches:
                accepted = True
            if command.startswith("rm -f "):
                target = command[6:]
                accepted |= target.rstrip("*") in admitted_patches
                if re.fullmatch(r"/tmp/_swegemma_junit_[0-9a-f]{12}\.xml", target):
                    junit_paths.add(target)
                    accepted = True
            if command.startswith("cat "):
                accepted |= any(command == f"cat {path} 2>/dev/null || true" for path in junit_paths)
            for path in junit_paths:
                accepted |= command == (f"cd /workspace && PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 python3 -s -m pytest test_marker.py "
                                        f"--junitxml={path} -p no:anyio -o timeout=0 -o python_classes=\"Test* *Test\" -q")
        if not accepted or identifier not in manager.sandboxes:
            guard.refuse("unadmitted native synthetic command")
        # A thread-local passive profiler observes the original manager's exact
        # pathlib exists/glob/scandir calls. It delegates no method, performs no
        # filesystem lookup, and runs identically with observation ON or OFF.
        # This proves that the local-wheel branch does not even query fallback
        # roots, rather than merely relying on filesystem denial of those roots.
        native_code = original_exec.__func__.__code__
        probe_codes = {Path.exists.__code__: "exists", Path.glob.__code__: "glob",
                       Path._scandir.__code__: "scandir"}

        def observe_wheel_probe(frame, event, _arg):
            if event != "call" or frame.f_code not in probe_codes:
                return
            caller = frame.f_back
            if frame.f_code is not Path._scandir.__code__:
                if caller is None or caller.f_code is not native_code:
                    return
            else:
                while caller is not None and caller.f_code is not native_code:
                    caller = caller.f_back
                if caller is None:
                    return
            result["native_wheel_probes"].append({"method": probe_codes[frame.f_code],
                                                   "path": os.fspath(frame.f_locals["self"]),
                                                   "local": frame.f_locals["self"] == local_wheels,
                                                   "command_index": len(result["commands"])})

        if sys.getprofile() is not None:
            guard.refuse("native synthetic worker has an unexpected active profiler")
        sys.setprofile(observe_wheel_probe)
        try:
            with guard.processes_allowed("shell"):
                observed = original_exec(identifier, command, timeout=timeout)
        finally:
            sys.setprofile(None)
        # Command admission evidence is always retained, allowing ON/OFF order
        # equivalence without executing an additional command in either mode.
        result["commands"].append({"command": command, "exit_code": observed.exit_code,
                                    "stdout_sha256": _sha(observed.stdout.encode()),
                                    "stderr_sha256": _sha(observed.stderr.encode())})
        if observing:
            if command.startswith("cat /tmp/_swegemma_junit_"):
                result["junit_xml"] = observed.stdout
            if command == "cd /workspace && (git diff --binary _swegemma_baseline 2>/dev/null || git diff --binary HEAD)":
                result["workspace_patch"] = observed.stdout
        return observed

    def copy_to(identifier, source, destination):
        source = Path(source)
        if source.is_symlink() or identifier not in manager.sandboxes:
            guard.refuse("unadmitted synthetic copy")
        if not source.is_file() or source.resolve() != source or not source.is_relative_to(paths["worker_root"]):
            guard.refuse("copy source must be staged inside this worker")
        content = source.read_bytes()
        accepted = source == snapshot and destination == "/tmp" and _sha(content) == task_snapshot_sha
        accepted |= (not verifier and source.name == "app.py" and destination == "/workspace/" and content == CHANGED.encode())
        if (verifier and source.parent == paths["worker_root"] / "tmp" and destination == "/tmp/"
                and re.fullmatch(r"tmp[a-z0-9_]{8}\.patch", source.name)
                and content in {((returned_patch or "") + ("" if (returned_patch or "").endswith("\n") else "\n")).encode(),
                                private_patch.encode()}):
            accepted = True
            admitted_patches.add(f"/tmp/{source.name}")
        if not accepted:
            guard.refuse("unadmitted synthetic copy bytes or destination")
        observed = original_copy(identifier, source, destination)
        if source == snapshot:
            staged = manager.sandboxes[identifier]["tmp"] / source.name
            if _sha(staged.read_bytes()) != task_snapshot_sha:
                guard.refuse("native snapshot copy changed before extraction")
        if observing:
            result["copies"].append({"source_name": source.name, "destination": destination, "sha256": _sha(content)})
        return observed

    def stop(identifier):
        try:
            if observing and identifier in manager.sandboxes:
                workspace = manager.sandboxes[identifier]["workspace"]
                app = (workspace / "app.py").read_bytes()
                result["workspace"] = {"app_sha256": _sha(app), "app_content": app.decode(),
                                         "edited": app != INITIAL.encode(), "path": str(workspace)}
                if verifier:
                    result["workspace"]["private_test_present"] = (workspace / "test_marker.py").is_file()
        finally:
            original_stop(identifier)
            result["stopped_ids"].append(identifier)

    task_snapshot_sha = _sha(snapshot.read_bytes())
    manager.start, manager.exec, manager.copy_to, manager.stop = start, execute, copy_to, stop
    return result


class _RunnerEvidence(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []
        self.exceptions = []
        self.recovered_exceptions = []

    def emit(self, record):
        self.logs.append({"elapsed_seconds": time.perf_counter() - self.started,
                          "level": record.levelname, "line": record.lineno, "message": record.getMessage()})
        if record.exc_info:
            self.exceptions.append("".join(traceback.format_exception(*record.exc_info)))
        if record.getMessage().endswith("agent timed out."):
            recovered = sys.exception()
            if isinstance(recovered, TimeoutError):
                self.recovered_exceptions.append({"class": f"{type(recovered).__module__}.{type(recovered).__qualname__}",
                                                  "line": record.lineno, "message": str(recovered)})


def _config(paths, models, submission, case):
    from swegemma.config import EvalConfig
    return EvalConfig(tasks_path=paths["worker_root"] / "tasks/unopened.jsonl",
                      snapshots_dir=paths["public_root"] / "snapshots", results_dir=paths["evidence_root"],
                      submission_dir=submission, models=models, sandbox="subprocess", wheels_dir=paths["wheels_root"],
                      image="SYNTHETIC_SUBPROCESS_NO_IMAGE", graph_dir=str(paths["public_root"] / "graphs"),
                      embeddings_dir=str(paths["public_root"] / "embeddings"), timeout_seconds=20,
                      display_mode="quiet", enable_sandbox_testing=True, context_cache_config=None,
                      events_compaction_config=None, **budgets(case))


def solver_execute(request: dict) -> dict:
    task = SolverTask.from_dict(request["task"])
    paths, case, pin, candidates = _admit(request, task)
    _prepare(paths)
    guard = _guard(paths, pin, candidates)
    _native_namespaces(Path(pin["site_packages"]))
    # Optional dependencies import only after the exact CPU/source gate.
    import litellm
    litellm.disable_hf_tokenizer_download = True
    litellm.telemetry = False
    litellm.cache = None
    from tools.harness_cert._scripted_loopback import ScriptedServer, create_client
    from adk_submission import ModelRegistry
    from google.adk.models.lite_llm import LiteLlm
    from swegemma.context import SwegemmaContext
    from swegemma.harness.agent_runner import run_agent_sandbox
    from swegemma.sandbox.subprocess import SubprocessManager

    observing = request.get("observation_enabled", True)
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    submission = paths["worker_root"] / "synthetic_submission"
    submission.mkdir()
    tools = declared_tools(case)
    (submission / "agent.yaml").write_text(
        "agent_class: LlmAgent\nname: synthetic_runtime\nmodel: scripted\n"
        "instruction: Synthetic native runtime certification. Follow scripted calls.\ntools:\n"
        + "".join(f"  - {tool}\n" for tool in tools))
    public = SimpleNamespace(**{name: getattr(task, name) for name in (
        "instance_id", "repo", "base_commit", "problem_statement", "hints_text")})
    manager = SubprocessManager(timeout_seconds=20, base_dir=paths["worker_root"] / "sandboxes", system_site_packages=False)
    snapshot = paths["public_root"] / task.snapshot.source_relative_path
    observations = _wrap_manager(manager, paths, snapshot, Path(pin["site_packages"]), guard, candidates, observing=observing)
    evidence = _RunnerEvidence()
    evidence.started = started
    logger = logging.getLogger("swegemma.harness.agent_runner")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(evidence)
    patch, error, trace, escaped = "", None, None, None
    context = None
    calls = []

    async def run(server):
        nonlocal context
        client, http = await create_client(server.base_url)
        models = ModelRegistry()
        models.register("scripted", LiteLlm(model="openai/runtime-scripted", api_base=server.base_url,
                                            api_key="SYNTHETIC_DUMMY", client=client, num_retries=0, timeout=10))
        config = _config(paths, models, submission, case)
        context = SwegemmaContext(docker_manager=manager, task=public, problem_statement=public.problem_statement,
                                  hints_text=public.hints_text, repo=public.repo, budget=config.budget,
                                  harness=config.harness, graph_dir=config.graph_dir, embeddings_dir=config.embeddings_dir)
        try:
            return await run_agent_sandbox(manager, config, public, snapshot, context=context)
        finally:
            await client.close()
            await http.aclose()
            if http.probe_guard_violations:
                guard.refuse("scripted client attempted to leave its loopback endpoint")

    try:
        with ScriptedServer() as server:
            guard.port = int(server.origin.rsplit(":", 1)[1])
            server.set_script(scripts(case))
            if case == "H13":
                server.withhold_response(2)
            try:
                patch, error, trace = asyncio.run(run(server))
            finally:
                if not server.release_withheld_response():
                    guard.refuse("scripted timeout response did not terminate")
                calls = server.calls
    except Exception as exc:
        escaped = f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}"
    finally:
        logger.removeHandler(evidence)
        logger.setLevel(old_level)
        manager.cleanup_all()
    if guard.violations:
        raise RuntimeAdmissionError("synthetic guard refused execution: " + "; ".join(guard.violations))
    if any(call["response"]["status"] != 200 for call in calls):
        raise RuntimeAdmissionError("scripted server exhausted or failed; no endpoint fallback is allowed")
    forbidden_imports = ("eval.verifier_task", "eval.verifier_data", "eval.runtime_verifier_fixture",
                         "swegemma.evaluate", "swegemma.harness.verification", "swegemma.harness.sample_verification")
    if any(module in sys.modules for module in forbidden_imports):
        raise RuntimeAdmissionError("solver imported a forbidden verification/orchestration boundary")
    # Native execution has stopped and its sandbox is gone. Trusted sealing
    # opens lexical ancestor directories via no-follow descriptors. The OS
    # profile continues to confine all file contents; this Python hook still
    # gates mutations, sockets and process launches for the worker lifetime.
    guard.read_roots = None
    elapsed = time.perf_counter() - started
    trace_data = trace.to_dict(format="legacy") if trace else {"entries": []}
    trace_ref = _artifact(paths["evidence_root"] / "native_trace.json", canonical_json(trace_data).encode())
    native_ref = _artifact(paths["evidence_root"] / "native_observations.json", canonical_json({
        "manager": observations, "runner_logs": evidence.logs, "exceptions": evidence.exceptions,
        "recovered_exceptions": evidence.recovered_exceptions,
        "http_request_count": len(calls), "http_script_numbers": [call["script_number"] for call in calls],
    }).encode())
    events = _events(task.instance_id, "solver", trace_data, trace_ref, elapsed, observing)
    timeout = bool(error and error.startswith("Agent exceeded session timeout"))
    exhausted = bool(error and ("budget" in error))
    fallback = any("Captured unsubmitted working tree modifications as fallback patch" in item["message"] for item in evidence.logs)
    status = "worker_error" if escaped else "timeout" if timeout else "budget_exhausted" if exhausted else "error" if error else "completed"
    return {"schema_version": 1, "task_id": task.instance_id, "runtime_status": status,
            "started_at": started_at, "elapsed_seconds": elapsed, "returned_patch": patch,
            "returned_patch_sha256": _sha(patch.encode()), "submitted_patch": context.submitted_patch if context else None,
            "workspace_patch": observations["workspace_patch"], "agent_error": error, "escaped_exception": escaped,
            "llm_calls": context.llm_calls_used if context else None,
            "counted_tool_calls": context.tool_calls_used if context else None,
            "tool_attempts": sum(len(call["response"]["json"]["choices"][0]["message"].get("tool_calls", [])) for call in calls),
            "fallback_used": fallback, "timeout": timeout, "budget_exhausted": exhausted,
            "terminal_reason": error or "native_runner_returned", "native_trace_ref": trace_ref,
            "workspace_diagnostics_ref": native_ref, "events": events, "source_pin": pin,
            "native_result": {"patch_submitted": context.patch_submitted if context else None,
                              "http_request_count": len(calls), "workspace": observations["workspace"],
                              "recovered_exceptions": evidence.recovered_exceptions,
                              "native_function": "swegemma.harness.agent_runner.run_agent_sandbox",
                              "forbidden_imports_absent": list(forbidden_imports),
                              "solver_imports": sorted(sys.modules),
                              "sandbox_ids": observations["sandbox_ids"], "stopped_ids": observations["stopped_ids"]},
            "effective_configuration": {**request["runtime"], "executed_submission_root": str(submission),
                                         "budget": budgets(case), "system_site_packages": False},
            "command_observations": observations["commands"]}


def _events(task_id, phase, trace_data, trace_ref, elapsed, enabled):
    from .runtime_events import EventRecorder
    recorder = EventRecorder(task_id, phase, enabled=enabled)
    recorder.append(f"{phase}_started", elapsed_seconds=0)
    recorder.capture_native_trace(trace_data, trace_reference=trace_ref["relative_path"])
    recorder.append(f"{phase}_finished", elapsed_seconds=elapsed)
    return [event.to_dict() for event in recorder.events]


def verifier_execute(request: dict) -> dict:
    # Private contracts, test fixture material and native verification are
    # imported solely in this separately executed verifier entry point.
    from .verifier_task import VerifierTask
    from .runtime_verifier_fixture import test_patch, verification_config
    from tools.harness_cert._synthetic_verification import SUPPORT_ROOTS, SUPPORT_VERSIONS, _collect

    task = VerifierTask.from_dict(request["task"])
    configuration = request.get("verification_config")
    if configuration != verification_config() or canonical_sha256(configuration) != task.verification_config_sha256:
        raise RuntimeAdmissionError("verification configuration identity mismatch")
    private = request.get("test_patch")
    if (type(private) is not str or private != test_patch()
            or (_sha(private.encode()), len(private.encode())) != (task.test_patch.sha256, task.test_patch.size_bytes)):
        raise RuntimeAdmissionError("private synthetic test identity mismatch")
    patch = request.get("returned_patch")
    if type(patch) is not str or patch not in ("", expected_patch(), "invalid patch\n"):
        raise RuntimeAdmissionError("synthetic verification accepts only fixed fixture patch outcomes")
    if _sha(patch.encode()) != request.get("returned_patch_sha256"):
        raise RuntimeAdmissionError("sealed returned patch identity mismatch")
    paths, case, pin, candidates = _admit(request, task)
    support_root = _root(request["runtime"].get("pytest_support_root"), "pytest_support_root")
    if not support_root.is_relative_to(paths["worker_root"]):
        raise RuntimeAdmissionError("private pytest support must be staged in the verifier worker")
    support = {"root": str(support_root), "manifest": _collect(support_root, caches=False),
               "manifest_sha256": configuration["pytest_support_manifest_sha256"], "versions": SUPPORT_VERSIONS}
    if set(path.name for path in support_root.iterdir()) != set(SUPPORT_ROOTS):
        raise RuntimeAdmissionError("unexpected private pytest support entry")
    _prepare(paths)
    guard = _guard(paths, pin, candidates)
    _native_namespaces(Path(pin["site_packages"]))
    from adk_submission import ModelRegistry
    from swegemma.models.task import Task
    from swegemma.harness.verification import verify_task
    from swegemma.sandbox.subprocess import SubprocessManager

    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    observing = request.get("observation_enabled", True)
    manager = SubprocessManager(timeout_seconds=20, base_dir=paths["worker_root"] / "sandboxes", system_site_packages=False)
    snapshot = paths["public_root"] / task.snapshot.source_relative_path
    observations = _wrap_manager(manager, paths, snapshot, Path(pin["site_packages"]), guard, candidates,
                                 observing=observing, verifier=True, support=support, returned_patch=patch, private_patch=private)
    native_task = Task(instance_id=task.instance_id, repo=task.repo, base_commit=task.base_commit,
                       problem_statement="", test_patch=private, FAIL_TO_PASS=task.fail_to_pass or (), PASS_TO_PASS=task.pass_to_pass or ())
    config = _config(paths, ModelRegistry(), paths["submission_root"], case)
    try:
        native = asyncio.run(verify_task(manager, config, native_task, snapshot, fast_path=True,
                                        agent_patch=patch, agent_error=request.get("agent_error"), start_time=started))
    finally:
        manager.cleanup_all()
    if guard.violations:
        raise RuntimeAdmissionError("synthetic verification guard refused execution: " + "; ".join(guard.violations))
    guard.read_roots = None
    elapsed = time.perf_counter() - started
    native_data = native.model_dump(mode="json")
    native_ref = _artifact(paths["evidence_root"] / "native_verification.json", canonical_json(native_data).encode())
    observations_ref = _artifact(paths["evidence_root"] / "native_observations.json", canonical_json(observations).encode())
    junit_ref = None
    if observations["junit_xml"] is not None:
        junit_ref = _artifact(paths["evidence_root"] / "junit.xml", observations["junit_xml"].encode())
    events = _events(task.instance_id, "verifier", {"entries": []}, native_ref, elapsed, observing)
    return {"schema_version": 1, "task_id": task.instance_id, "runtime_status": "completed",
            "started_at": started_at, "elapsed_seconds": elapsed, "resolved": native.resolved,
            "returned_patch_sha256": _sha(patch.encode()), "test_exit_code": native.test_exit_code,
            "error": native.error_message, "native_result": native_data, "native_result_ref": native_ref,
            "workspace_diagnostics_ref": observations_ref, "junit_ref": junit_ref, "events": events,
            "source_pin": pin, "effective_configuration": {**request["runtime"], "verification_config": configuration},
            "command_observations": observations["commands"],
            "lifecycle": {"sandbox_ids": observations["sandbox_ids"], "stopped_ids": observations["stopped_ids"],
                          "workspace": observations["workspace"]}}
