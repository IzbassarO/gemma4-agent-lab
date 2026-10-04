"""Safe synthetic H05 control, then observational H04/H18 certification probes.

Run only with the acquired Python 3.12.14 harness interpreter. This module is
stdlib-only until strict preflight has passed. It never loads competition tasks,
uses Evaluator, installs task wheels, or starts a model/vLLM server.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib.metadata
import json
import logging
import os
import shlex
import sys
import sysconfig
import tarfile
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from ._probe_safety import (
    PROBE_FILES, REVIEWED_SUPPORT_DOCUMENTS, URLLIB3_CONNECTION_SHA256, IPv6FeatureProbe, ParentGuard, ProbeRefused,
    check_repository, check_wheels, confined,
    discover_system_runtime_reads, git, wheel_candidates,
)
from ._scripted_loopback import ScriptedServer, create_client

REPO = Path(__file__).resolve().parents[2]
INITIAL = 'MARKER = "before"\n'
CHANGED = 'MARKER = "after"\n'
WRITE_ARGS = {"filepath": "app.py", "content": CHANGED}
SYNTHETIC_REPO = "synthetic/probe"
WHEELS = {
    "adk_submission-0.2.12-py3-none-any.whl": "077c438c426e625b9f722081694e1d32856e6f7e932ef625002fc4a11aabdc10",
    "google_adk-1.36.1-py3-none-any.whl": "1a2f6868c509e3151fb0de3575a7d18b45c338be86f420924dad74e7193631a0",
    "google_genai-2.11.0-py3-none-any.whl": "5bc8186100e1d34d691fbe0cba392b7e04e98d286ca952323a6672d054accf95",
    "adk_eval_core-0.1.0-py3-none-any.whl": "194dd8f9aab154857bfa0db9d9ec382db856ee124529e072509634d3071d8643",
    "swegemma-0.2.7-py3-none-any.whl": "27a2f60f8db46c8fef5defc16df722dac0402446c9a6252e7e6b4c280e843c81",
}
VERSIONS = {
    "adk-submission": "0.2.12", "google-adk": "1.36.1", "google-genai": "2.11.0",
    "adk-eval-core": "0.1.0", "swegemma": "0.2.7", "litellm": "1.83.14",
    "urllib3": "2.8.0",
    "authlib": "1.6.6",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def verify_environment(cpu_root: Path, repo: Path) -> dict:
    """Read metadata/ZIP members only; never import the installed packages."""
    import zipfile

    if sys.version_info[:3] != (3, 12, 14) or Path(sys.prefix).resolve() != cpu_root / "harness":
        raise ProbeRefused(f"use {cpu_root}/harness/bin/python (exact Python 3.12.14 required)")
    prefixes = ("swegemma", "adk_submission", "adk_eval_core", "google.adk", "litellm", "openai", "httpx")
    if any(name == prefix or name.startswith(prefix + ".") for name in sys.modules for prefix in prefixes):
        raise ProbeRefused("harness/transport packages were imported before preflight")
    site = confined(Path(sysconfig.get_path("purelib")), cpu_root / "harness")
    versions = {name: importlib.metadata.version(name) for name in VERSIONS}
    if versions != VERSIONS:
        raise ProbeRefused(f"installed versions differ from the audited environment: {versions}")
    sources = []
    for filename, expected in WHEELS.items():
        wheel = confined(repo / "artifacts/harness_wheels/v28" / filename, repo / "artifacts/harness_wheels/v28")
        if not wheel.is_file() or digest(wheel.read_bytes()) != expected:
            raise ProbeRefused(f"source wheel missing or changed: {wheel}")
        count = 0
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.namelist():
                if not member.endswith(".py"):
                    continue
                installed = confined(site / member, site)
                if installed.read_bytes() != archive.read(member):
                    raise ProbeRefused(f"installed source differs from v28: {installed}")
                count += 1
        sources.append({"wheel": filename, "sha256": expected, "matching_python_files": count})
    # LiteLLM's import-time tiktoken initialization must have its bundled assets.
    cache = site / "litellm/litellm_core_utils/tokenizers"
    for filename, expected in {
        "9b5ad71b2ce5302211f9c61530b329a4922fc6a4": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
        "fb374d419588a4632f3f557e76b4b70aebbca790": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    }.items():
        if digest((cache / filename).read_bytes()) != expected:
            raise ProbeRefused(f"bundled tokenizer missing or changed: {cache / filename}")
    return {"versions": versions, "site_packages": str(site), "source_wheels": sources}


def smoke_auth_import() -> dict:
    """Construct the previously failing AutoFlow path; never run inference."""
    for name in ("google-adk", "authlib"):
        actual = importlib.metadata.version(name)
        if actual != VERSIONS[name]:
            raise ProbeRefused(f"auth import smoke requires {name}=={VERSIONS[name]}, found {actual}")
    from authlib.integrations.requests_client import OAuth2Session
    from authlib.oauth2.rfc6749 import OAuth2Token
    from google.adk.auth import oauth2_credential_util

    if (oauth2_credential_util.OAuth2Session is not OAuth2Session
            or oauth2_credential_util.OAuth2Token is not OAuth2Token):
        raise ProbeRefused("ADK OAuth utility uses unexpected Authlib bindings")
    from google.adk.flows.llm_flows.auto_flow import AutoFlow
    AutoFlow()
    return {"module": "google.adk.auth.oauth2_credential_util",
            "google-adk": VERSIONS["google-adk"], "authlib": VERSIONS["authlib"],
            "result": "AUTHLIB_IMPORT_OK"}


def prepare_environment(root: Path):
    for key in list(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"} or key.startswith(("OTEL_", "GIT_")):
            os.environ.pop(key)
    for key in ("CUSTOM_TIKTOKEN_CACHE_DIR", "LITELLM_DISABLE_LAZY_LOADING", "GEMMA4_DATASET_ROOT"):
        os.environ.pop(key, None)
    os.environ.pop("PYTHONPATH", None)
    for key in ("MODEL_PROXY_URL", "MODEL_PROXY_API_KEY", "LITELLM_API_BASE", "LITELLM_API_KEY",
                "LOCAL_INFERENCE_URL", "LOCAL_API_KEY", "OPENAI_BASE_URL"):
        os.environ.pop(key, None)
    os.environ.update({
        "LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_MODE": "PRODUCTION",
        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHON_DOTENV_DISABLED": "1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "OTEL_SDK_DISABLED": "true", "DO_NOT_TRACK": "1", "OPENAI_API_KEY": "H23_DUMMY",
        "PIP_NO_INDEX": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"), "HF_HOME": str(root / "hf_cache"),
        "KAGGLE_SANDBOX_DIR": str(root / "sandbox_setup"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    })
    for name in ("home", "tmp", "hf_cache", "sandbox_setup"):
        (root / name).mkdir()
    # Import-time setup-script discovery stops here; SubprocessManager never runs it.
    (root / "sandbox_setup/setup.py").write_text("# Inert synthetic fixture; never executed.\n", encoding="utf-8")
    sys.dont_write_bytecode = True
    tempfile.tempdir = str(root / "tmp")


def isolate_import_paths(site: Path):
    """Use the acquired venv and stdlib, excluding cwd/PYTHONPATH shadows."""
    trusted = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    sys.path[:] = [entry for entry in sys.path if entry and any(
        Path(entry).resolve().is_relative_to(base) for base in trusted
    )]
    if str(site) not in sys.path:
        sys.path.append(str(site))


def expected_patch() -> str:
    def blob(text):
        data = text.encode()
        return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()[:7]
    return (f"diff --git a/app.py b/app.py\nindex {blob(INITIAL)}..{blob(CHANGED)} 100644\n"
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n"
            f"-{INITIAL}+{CHANGED}")


def fixture(root: Path, guard: ParentGuard) -> tuple[Path, str]:
    work = root / "toy_repo"
    work.mkdir()
    template = root / "empty_git_template"
    template.mkdir()

    def local_git(*args):
        argv = ["/usr/bin/git", "-C", str(work), *args]
        with guard.processes_allowed("fixture_git", argv):
            return git(work, *args)

    local_git("init", "--template=" + str(template), "--initial-branch=main", "--object-format=sha1")
    for name, value in {
        "user.name": "Synthetic H certification", "user.email": "synthetic@example.invalid",
        "core.hooksPath": ".git/hooks", "core.abbrev": "7", "core.autocrlf": "false",
        "core.fsmonitor": "false", "commit.gpgsign": "false", "tag.gpgsign": "false",
    }.items():
        local_git("config", "--local", name, value)
    (work / ".git/hooks").mkdir()
    (work / "app.py").write_text(INITIAL, encoding="utf-8")
    local_git("add", "app.py")
    local_git("commit", "-m", "synthetic initial", "--quiet")
    if local_git("remote").strip() or list((work / ".git/hooks").iterdir()):
        raise ProbeRefused("synthetic Git repository has a remote or hook")
    commit = local_git("rev-parse", "HEAD").strip()
    snapshot = root / "synthetic.tar.gz"
    with tarfile.open(snapshot, "w:gz") as archive:
        for path in sorted(work.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ProbeRefused(f"special file in synthetic snapshot: {path}")
            archive.add(path, arcname=path.relative_to(work).as_posix(), recursive=False)
    with tarfile.open(snapshot) as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ProbeRefused(f"unsafe synthetic archive member: {member.name}")
    return snapshot, commit


def sandbox_commands(site: Path, snapshot_name: str) -> set[str]:
    """Literal commands from the already byte-verified v28 source, never eval."""
    tree = ast.parse((site / "swegemma/harness/container_setup.py").read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    script = next(ast.literal_eval(node.value) for node in ast.walk(functions["setup_workspace_test_config"])
                  if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "script" for t in node.targets))
    literals = set()
    for name in ("setup_git_exclude", "extract_snapshot"):
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "exec":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                    literals.add(node.args[1].value)
    return literals | {
        "ls /wheels/*.whl 2>/dev/null | wc -l",
        f"(tar --no-same-owner -I pigz -xf /tmp/{snapshot_name} -C /workspace 2>/dev/null || "
        f"tar --no-same-owner -xzf /tmp/{snapshot_name} -C /workspace) && rm -f /tmp/{snapshot_name}",
        f"python3 -S -c {shlex.quote(script)} /workspace {SYNTHETIC_REPO} 0",
        'cd /workspace && git add -A && git commit -m "baseline" --allow-empty -q && git tag -f _swegemma_baseline',
        'cd /workspace && find . -maxdepth 3 -not -path "./.git/*" -not -name "*.pyc" -not -name "__pycache__" | sort | head -150',
        "mkdir -p /workspace",
        "cd /workspace && git add -N .",
        "cd /workspace && (git diff --binary _swegemma_baseline 2>/dev/null || git diff --binary HEAD)",
    }


def instrument_manager(manager, snapshot: Path, commands: set[str], wheel_paths: list[Path], guard: ParentGuard):
    """Instance wrappers preserve the exact class-name installation skips."""
    observation = {"commands": [], "copies": [], "workspace": None, "observer_errors": []}
    original_start, original_exec = manager.start, manager.exec
    original_copy, original_stop = manager.copy_to, manager.stop
    snapshot_sha = digest(snapshot.read_bytes())

    def start():
        try:
            check_wheels(wheel_paths)
        except ProbeRefused as exc:
            guard.refuse(str(exc))
        with guard.processes_allowed("venv"):
            sandbox_id = original_start()
        paths = manager.sandboxes[sandbox_id]
        if any(paths["wheels"].iterdir()):
            guard.refuse(f"sandbox wheels are not empty: {paths['wheels']}")
        return sandbox_id

    def execute(sandbox_id, command, timeout=None):
        try:
            check_wheels(wheel_paths)
        except ProbeRefused as exc:
            guard.refuse(str(exc))
        if sandbox_id not in manager.sandboxes or command not in commands:
            guard.refuse(f"unapproved sandbox command: {command!r}")
        if any(manager.sandboxes[sandbox_id]["wheels"].iterdir()):
            guard.refuse("sandbox wheel staging detected")
        with guard.processes_allowed("shell"):
            result = original_exec(sandbox_id, command, timeout=timeout)
        observation["commands"].append({"command": command, "exit_code": result.exit_code,
                                        "stdout": result.stdout, "stderr": result.stderr})
        if command == "ls /wheels/*.whl 2>/dev/null | wc -l" and result.stdout.strip() != "0":
            guard.refuse("wheel check did not confirm zero wheels")
        return result

    def copy_to(sandbox_id, source, destination):
        try:
            source = confined(Path(source), guard.run_root)
        except ProbeRefused as exc:
            guard.refuse(str(exc))
        actual = digest(source.read_bytes()) if source.is_file() else None
        accepted = (source == snapshot and destination == "/tmp" and actual == snapshot_sha)
        accepted |= (destination == "/workspace/" and source.name == "app.py" and actual == digest(CHANGED.encode()))
        if not accepted or sandbox_id not in manager.sandboxes:
            guard.refuse(f"unapproved sandbox copy: {source} -> {destination}")
        observation["copies"].append({"source": str(source), "destination": destination, "sha256": actual})
        return original_copy(sandbox_id, source, destination)

    def stop(sandbox_id):
        try:
            workspace = manager.sandboxes[sandbox_id]["workspace"]
            content = (workspace / "app.py").read_bytes()
            diff = execute(sandbox_id, "cd /workspace && (git diff --binary _swegemma_baseline 2>/dev/null || git diff --binary HEAD)")
            config = (workspace / ".git/config").read_text()
            if '[remote ' in config or list((workspace / ".git/hooks").iterdir()):
                guard.refuse("remote or hooks appeared in synthetic workspace")
            observation["workspace"] = {
                "app_content": content.decode(), "app_sha256": digest(content),
                "edited": content != INITIAL.encode(), "diff": diff.stdout,
                "diff_sha256": digest(diff.stdout.encode()), "diff_exit_code": diff.exit_code,
                "git_remotes": [], "git_hooks": [], "path": str(workspace),
            }
        except Exception as exc:
            observation["observer_errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            original_stop(sandbox_id)

    manager.start, manager.exec, manager.copy_to, manager.stop = start, execute, copy_to, stop
    return observation


class Exceptions(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        if record.exc_info:
            cls, exception, tb = record.exc_info
            self.records.append({
                "class": f"{cls.__module__}.{cls.__qualname__}", "message": str(exception),
                "traceback": "".join(traceback.format_exception(cls, exception, tb)),
                "logger": record.name,
                "adk_get_tool_in_traceback": any(frame.name == "_get_tool" for frame in traceback.extract_tb(tb)),
            })


def tool_message(name: str, args: dict, identifier: str) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)},
    }]}


def scripts(case: str) -> list[dict]:
    write = tool_message("write_file", WRITE_ARGS, "call_write")
    submit = tool_message("submit_patch", {}, "call_submit")
    final = {"role": "assistant", "content": "Synthetic probe complete."}
    # Continuation responses deliberately permit observing recovery, rather than
    # encoding H04/H18 fatality as an expectation or exhausting the server.
    if case == "H18":
        return [write, tool_message("bash", {"command": "echo synthetic"}, "call_unknown"), submit, final]
    return [write, submit, final]


def control_ok(result: dict) -> bool:
    return (result["infrastructure_ok"] and result["agent_error"] is None
            and result["write_file_succeeded"] and result["http_request_count"] == 3
            and result["agent_patch"] == expected_patch() and result["context"]["patch_submitted"]
            and result["context"]["submitted_patch"] == result["agent_patch"])


def write_file_results(trace_data: dict) -> list:
    # SessionTrace's legacy JSON renames dataclass fields to type/tool/result.
    return [json.loads(entry["result"]) for entry in trace_data["entries"]
            if entry.get("type") == "tool_response" and entry.get("tool") == "write_file"]


async def run_case(case, root, output, snapshot, commit, site, wheel_paths, guard, server):
    # All optional/harness imports occur only after admission and audit guards.
    from adk_submission import ModelRegistry
    from google.adk.models.lite_llm import LiteLlm
    from swegemma.config import EvalConfig
    from swegemma.context import SwegemmaContext
    from swegemma.harness.agent_runner import run_agent_sandbox
    from swegemma.models.task import Task
    from swegemma.sandbox.subprocess import SubprocessManager

    case_root = root / case
    case_root.mkdir()
    submission = case_root / "submission"
    submission.mkdir()
    declared = ["submit_patch"] if case == "H04" else ["write_file", "submit_patch"]
    (submission / "agent.yaml").write_text(
        f"agent_class: LlmAgent\nname: {case.lower()}_probe\nmodel: scripted\n"
        "instruction: Synthetic tool-dispatch certification only. Follow the scripted tool calls.\n"
        "tools:\n" + "".join(f"  - {name}\n" for name in declared), encoding="utf-8",
    )
    first_call = len(server.calls)
    server.set_script(scripts(case))
    client, http = await create_client(server.base_url)
    models = ModelRegistry()
    models.register("scripted", LiteLlm(model="openai/h23-scripted", api_base=server.base_url,
                                       api_key="H23_DUMMY", client=client, num_retries=0, timeout=10))
    config = EvalConfig(
        tasks_path=root / "tasks/synthetic.jsonl", snapshots_dir=root / "snapshots",
        results_dir=output, submission_dir=submission, models=models, sandbox="subprocess", wheels_dir=None,
        graph_dir=str(case_root / "graphs"), embeddings_dir=str(case_root / "embeddings"),
        timeout_seconds=20, max_time_minutes=1, max_tool_calls=8, max_turns=6,
        display_mode="quiet", enable_sandbox_testing=True,
        context_cache_config=None, events_compaction_config=None,
    )
    manager = SubprocessManager(timeout_seconds=20, base_dir=case_root / "sandboxes", system_site_packages=False)
    observation = instrument_manager(manager, snapshot, sandbox_commands(site, snapshot.name), wheel_paths, guard)
    task = Task(instance_id=f"synthetic_{case.lower()}", repo=SYNTHETIC_REPO, base_commit=commit,
                problem_statement='Change app.py MARKER from "before" to "after". Synthetic fixture only.')
    context = SwegemmaContext(docker_manager=manager, task=task, problem_statement=task.problem_statement,
                              repo=task.repo, budget=config.budget, harness=config.harness,
                              graph_dir=config.graph_dir, embeddings_dir=config.embeddings_dir)
    registered = sorted(context.create_tools())
    if "write_file" not in registered or "bash" in registered:
        raise ProbeRefused(f"unexpected standard tool registry: {registered}")
    handler = Exceptions()
    logger = logging.getLogger("swegemma.harness.agent_runner")
    logger.addHandler(handler)
    patch, error, trace = "", None, None
    escaped = None
    try:
        patch, error, trace = await run_agent_sandbox(manager, config, task, snapshot, context=context)
    except Exception as exc:
        escaped = {"class": f"{type(exc).__module__}.{type(exc).__qualname__}",
                   "message": str(exc), "traceback": traceback.format_exc()}
    finally:
        logger.removeHandler(handler)
        await client.close()
        await http.aclose()
    trace_data = trace.to_dict(format="legacy") if trace is not None else {"entries": []}
    calls = server.calls[first_call:]
    tool_results = write_file_results(trace_data)
    schemas = [(call["request"].get("json") or {}).get("tools", []) for call in calls]
    advertised = [[schema["function"]["name"] for schema in group] for group in schemas]
    invalid = list(guard.violations) + list(getattr(http, "probe_guard_violations", []))
    if any(call["response"]["status"] != 200 for call in calls):
        invalid.append("scripted HTTP error/retry/infrastructure failure")
    if any(sorted(names) != sorted(declared) for names in advertised):
        invalid.append("advertised tools differ from the synthetic declaration")
    if not observation["workspace"] or observation["observer_errors"]:
        invalid.append("workspace observer did not complete")
    if escaped:
        invalid.append("exception escaped the direct runner")
    if not calls:
        invalid.append("no scripted HTTP completion occurred")
    result = {
        "case": case, "scope": "REPRODUCED-LOCAL synthetic only; no hidden-scorer inference",
        "infrastructure_ok": not invalid, "infrastructure_errors": invalid,
        "declared_tools": declared, "registered_tools": registered, "advertised_tools": advertised,
        "http_request_count": len(calls), "next_turn_after_write": len(calls) > 1,
        "next_turn_after_missing_tool": len(calls) > (2 if case == "H18" else 1),
        "write_file_results": tool_results,
        "write_file_succeeded": any(isinstance(item, dict) and item.get("status") == "ok" for item in tool_results),
        "agent_patch": patch, "agent_error": error, "patch_sha256": digest(patch.encode()),
        "original_exceptions": handler.records, "escaped_exception": escaped,
        "context": {name: getattr(context, name) for name in (
            "patch_submitted", "submitted_patch", "tool_calls_used", "llm_calls_used")},
        "workspace_observation": observation,
        "prior_edit_in_returned_patch": CHANGED.strip() in patch,
        "parent_connections": list(guard.connections),
        "parent_bindings": list(guard.bindings),
        "parent_processes": list(guard.processes),
        "process_tree_globally_firewalled": False,
        "competition_content_access": False,
        "wheel_discovery_checks": "metadata only; both resolver sets checked before execution and each command",
    }
    if case == "H05":
        result["control_pass"] = control_ok(result)
    # Evidence artifacts remain raw/ignored; no hypothesis status promotion.
    write_json(output / "result.json", result)
    write_json(output / "http.json", calls)
    write_json(output / "trace.json", trace_data)
    write_json(output / "exceptions.json", handler.records)
    (output / "agent.patch").write_text(patch, encoding="utf-8")
    if observation["workspace"]:
        (output / "workspace.patch").write_text(observation["workspace"]["diff"], encoding="utf-8")
        (output / "workspace_app.py").write_text(observation["workspace"]["app_content"], encoding="utf-8")
    print(f"{case}: infrastructure_ok={result['infrastructure_ok']} requests={len(calls)} "
          f"patch_bytes={len(patch.encode())} result={output / 'result.json'}")
    return result


async def run_cases(root, outputs, snapshot, commit, site, wheel_paths, guard, server):
    for case in ("H05", "H04", "H18"):
        result = await run_case(case, root, outputs[case], snapshot, commit, site, wheel_paths, guard, server)
        if not result["infrastructure_ok"] or (case == "H05" and not result["control_pass"]):
            print(f"STOP: {case} infrastructure/control failed; no remaining cases interpreted.", file=sys.stderr)
            return 2
        if case == "H05":
            print("H05_CONTROL_PASS")
    print("PROBE_COMPLETE: H04/H18 observations recorded; no matrix status promoted.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--h23-cpu-tmp", type=Path, default=os.environ.get("H23_CPU_TMP"),
                        help="acquired harness root (default $H23_CPU_TMP)")
    args = parser.parse_args(argv)
    guard = None
    outputs = {}
    try:
        if args.h23_cpu_tmp is None:
            raise ProbeRefused("set H23_CPU_TMP to the acquired temporary harness root")
        cpu_root = args.h23_cpu_tmp.resolve(strict=True)
        if any(not (char.isascii() and (char.isalnum() or char in "/._-")) for char in str(cpu_root)):
            raise ProbeRefused(f"harness shell substitution requires an ASCII shell-safe H23_CPU_TMP: {cpu_root}")
        original_cwd = Path.cwd().resolve()
        changes = check_repository(REPO)
        source_hashes = {name: digest((REPO / name).read_bytes())
                         for name in sorted(PROBE_FILES | REVIEWED_SUPPORT_DOCUMENTS.keys())
                         if (REPO / name).is_file()}
        environment = verify_environment(cpu_root, REPO)
        root = Path(tempfile.mkdtemp(prefix="h04-h05-h18-", dir=cpu_root)).resolve()
        confined(root, cpu_root)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + root.name.rsplit("-", 1)[-1]
        paths = []
        for cwd in (original_cwd, root):
            paths.extend(wheel_candidates(cwd, root / "tasks/synthetic.jsonl", root / "snapshots"))
        wheel_checks = check_wheels(paths)
        for case in ("H05", "H04", "H18"):
            base = REPO / "harness_cert/results" / case
            if base.resolve() != base:
                raise ProbeRefused(f"artifact directory contains a symlink: {base}")
            if not git(REPO, "check-ignore", str(base / run_id / "result.json")).strip():
                raise ProbeRefused(f"artifact path is not gitignored: {base}")
            output = base / run_id
            output.mkdir(parents=True, exist_ok=False)
            outputs[case] = output.resolve()
        protected = [REPO / "data", REPO / "agents", REPO / "snapshots",
                     REPO / "gemma-4-developer-agent", REPO.parent / "gemma-4-developer-agent", Path("/kaggle/input")]
        if os.environ.get("GEMMA4_DATASET_ROOT"):
            protected.append(Path(os.environ["GEMMA4_DATASET_ROOT"]))
        prepare_environment(root)
        isolate_import_paths(Path(environment["site_packages"]))
        os.chdir(root)
        libraries = [Path(sys.prefix), Path(sys.base_prefix)]
        system_runtime_reads = discover_system_runtime_reads()
        ipv6_feature_probe = IPv6FeatureProbe.from_site(Path(environment["site_packages"]))
        guard = ParentGuard(REPO, root, list(outputs.values()), protected, read_roots=libraries,
                            metadata_dirs=[*paths, REPO, REPO / "tools", REPO / "tools/harness_cert"],
                            system_runtime_reads=system_runtime_reads, ipv6_feature_probe=ipv6_feature_probe)
        sys.addaudithook(guard.audit)
        admission = {"git_head": "ea5b857487ae9e146e94a88e108962742fcbdef8", "allowed_probe_changes": changes,
                     "probe_source_sha256": source_hashes,
                     "environment": environment, "wheel_checks": wheel_checks, "synthetic_root": str(root),
                     "system_runtime_read_roots": ([str(system_runtime_reads.zoneinfo_root)]
                                                   if system_runtime_reads.zoneinfo_root else []),
                     "ipv6_feature_detection": {"source": str(ipv6_feature_probe.source),
                                                "sha256": URLLIB3_CONNECTION_SHA256,
                                                "purpose": "urllib3 import-time capability detection only"},
                     "output_directories": {key: str(value) for key, value in outputs.items()},
                     "process_tree_globally_firewalled": False, "competition_content_access": False,
                     "data_access_policy": "Synthetic files and installed libraries only; wheel candidate checks read directory metadata only."}
        write_json(outputs["H05"] / "preflight.json", admission)
        import litellm
        litellm.disable_hf_tokenizer_download = True
        litellm.model_fallbacks = None
        litellm.telemetry = False
        litellm.cache = None
        for name in ("callbacks", "input_callback", "success_callback", "failure_callback",
                     "_async_success_callback", "_async_failure_callback"):
            setattr(litellm, name, [])
        admission["auth_import_smoke"] = smoke_auth_import()
        write_json(outputs["H05"] / "preflight.json", admission)
        print("AUTHLIB_IMPORT_OK")
        snapshot, commit = fixture(root, guard)
        with ScriptedServer() as server:
            guard.port = int(server.origin.rsplit(":", 1)[1])
            return asyncio.run(run_cases(root, outputs, snapshot, commit, Path(environment["site_packages"]), paths, guard, server))
    except Exception as exc:
        message = f"PROBE REFUSED/INVALID: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        if outputs:
            write_json(outputs["H05"] / "launch_error.json", {
                "error": message, "traceback": traceback.format_exc(),
                "parent_bindings": list(guard.bindings) if guard else [],
                "guard_violations": guard.violations if guard else [], "evidence_valid": False,
            })
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
