"""Operator-run synthetic budget certification; no matrix promotion or real model.

Exact installed Phase 1 and Phase 2 functions are invoked directly. Evaluator
task hydration is never invoked. Imports remain stdlib-only until preflight.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tarfile
import tempfile
import time
import traceback
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

from . import run_h04_h05_h18 as dispatch
from ._probe_safety import (
    BASELINE, BUDGET_BASELINE, BUDGET_PROBE_FILES, IPv6FeatureProbe, ParentGuard,
    ProbeRefused, URLLIB3_CONNECTION_SHA256, check_repository, check_wheels,
    confined, discover_system_runtime_reads, git, wheel_candidates,
)
from ._scripted_loopback import ScriptedServer, create_client
from ._synthetic_verification import (
    TEST_CONTENT, TEST_NAME, instrument_verifier, prepare_support,
)

REPO = dispatch.REPO
CASES = ("CONTROL", "H13", "H14", "H29", "H29_BOUNDARY")
FINAL = {"role": "assistant", "content": "Synthetic budget probe complete."}
EXPECTED_REQUESTS = {"CONTROL": 3, "H13": 2, "H14": 5, "H29": 5, "H29_BOUNDARY": 3}
EXPECTED_COUNTED = {"CONTROL": 1, "H13": 1, "H14": 3, "H29": 2, "H29_BOUNDARY": 2}
EXPECTED_TOOLS = {"CONTROL": ["write_file", "submit_patch"], "H13": ["write_file"],
                  "H14": ["write_file"] * 4, "H29": ["write_file", "write_file", "get_status", "submit_patch"],
                  "H29_BOUNDARY": ["write_file"] * 2}


def declared_tools(case):
    if case not in CASES:
        raise ValueError(f"Unknown budget case: {case}")
    return (["write_file", "get_status", "submit_patch"] if case.startswith("H29")
            else ["write_file", "submit_patch"])


def scripts(case):
    declared_tools(case)  # Fail closed on unknown case names.
    write = lambda index: dispatch.tool_message("write_file", dispatch.WRITE_ARGS, f"write_{index}")
    status = dispatch.tool_message("get_status", {}, "status_at_limit")
    submit = dispatch.tool_message("submit_patch", {}, "submit_at_limit")
    if case == "CONTROL":
        return [write(1), submit, FINAL]
    if case == "H13":
        return [write(1), FINAL]
    if case == "H14":
        rejected = dispatch.tool_message("write_file", {"filepath": "app.py", "content": dispatch.INITIAL}, "rejected_write")
        return [write(1), write(2), write(3), rejected, FINAL]
    if case == "H29_BOUNDARY":
        # Available continuation lets observation, rather than script exhaustion,
        # establish whether the final-text boundary prevents another invocation.
        return [write(1), write(2), FINAL, status, submit, FINAL]
    return [write(1), write(2), status, submit, FINAL]


def budgets(case):
    declared_tools(case)
    return {"max_time_minutes": 0.05 if case == "H13" else 1,
            "max_tool_calls": 2 if case.startswith("H29") else 3, "max_turns": 8}


def tool_responses(trace):
    responses = []
    for entry in trace.get("entries", []):
        if entry.get("type") == "tool_response":
            responses.append({"tool": entry.get("tool"), "result": json.loads(entry["result"])})
    return responses


class RunnerEvidence(dispatch.Exceptions):
    """Observe the real handled timeout and fallback log without changing flow."""

    def __init__(self, source):
        super().__init__()
        self.source = str(source)
        self.recovered = []
        self.logs = []

    def emit(self, record):
        super().emit(record)
        if record.name != "swegemma.harness.agent_runner" or record.pathname != self.source:
            return
        self.logs.append({"line": record.lineno, "level": record.levelname, "message": record.getMessage()})
        if record.lineno == 746 and record.getMessage().endswith("agent timed out."):
            exc = sys.exception()
            if exc is not None:
                self.recovered.append({"class": f"{type(exc).__module__}.{type(exc).__qualname__}",
                                       "message": str(exc), "line": record.lineno,
                                       "traceback": "".join(traceback.format_exception(exc))})


def synthetic_fixture(root, guard):
    snapshot, commit = dispatch.fixture(root, guard)
    work = root / "toy_repo"
    (work / TEST_NAME).write_text(TEST_CONTENT, encoding="utf-8")
    # The official clean-baseline setup records this immutable synthetic test.
    with tarfile.open(snapshot, "w:gz") as archive:
        for path in sorted(work.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                guard.refuse(f"Special file in synthetic budget fixture: {path}")
            archive.add(path, arcname=path.relative_to(work).as_posix(), recursive=False)
    with tarfile.open(snapshot) as archive:
        for member in archive.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                guard.refuse(f"Unsafe budget snapshot entry: {member.name}")
    return snapshot, commit


async def verify_patch(label, patch, error, task, config, snapshot, root, output, wheels, guard, support):
    from swegemma.harness.verification import verify_task
    from swegemma.sandbox.subprocess import SubprocessManager

    directory = output / label
    directory.mkdir()
    manager = SubprocessManager(timeout_seconds=20, base_dir=root / label, system_site_packages=False)
    observation = instrument_verifier(manager, snapshot, wheels, guard, support)
    verification_config = replace(config, results_dir=directory)
    handler = dispatch.Exceptions()
    logger = logging.getLogger("swegemma.harness.verification")
    logger.addHandler(handler)
    try:
        result = await verify_task(manager, verification_config, task, snapshot,
                                   agent_patch=patch, agent_error=error, start_time=time.perf_counter())
    finally:
        logger.removeHandler(handler)
    evidence = {"function": "swegemma.harness.verification.verify_task", "evaluator_hydration_invoked": False,
                "input_patch_sha256": dispatch.digest(patch.encode()), "input_agent_error": error,
                "result": result.model_dump(mode="json"), "observation": observation,
                "original_exceptions": handler.records}
    dispatch.write_json(directory / "verification.json", evidence)
    if observation["workspace"]:
        for name, key in (("workspace_app.py", "app_content"), ("workspace_test_marker.py", "test_content"), ("workspace.patch", "diff")):
            (directory / name).write_text(observation["workspace"][key], encoding="utf-8")
    if observation.get("junit_xml") is not None:
        (directory / "junit.xml").write_text(observation["junit_xml"], encoding="utf-8")
    return evidence


def infrastructure_errors(case, calls, script, declared, observation, guard, http, escaped, recovered):
    invalid = list(guard.violations) + list(getattr(http, "probe_guard_violations", []))
    if escaped:
        invalid.append("Exception escaped the official phase functions")
    if not observation["workspace"] or observation["observer_errors"]:
        invalid.append("Agent workspace observer did not complete")
    if not calls:
        invalid.append("No scripted request admitted")
    for index, call in enumerate(calls):
        request, response = call["request"], call["response"]
        if (call.get("script_number") != index + 1 or index >= len(script)
                or response.get("status") != 200 or not request.get("body_complete")):
            invalid.append("Unexpected HTTP request/retry/script exhaustion")
            continue
        try:
            names = [item["function"]["name"] for item in request["json"].get("tools", [])]
            message = response["json"]["choices"][0]["message"]
        except (AttributeError, IndexError, KeyError, TypeError):
            invalid.append("Malformed recorded scripted request/response")
            continue
        if sorted(names) != sorted(declared):
            invalid.append("Advertised tools differ from exact synthetic declaration")
        if message != script[index]:
            invalid.append("Scripted response integrity mismatch")
        delivery = response.get("delivery")
        held = (case == "H13" and index == 1 and delivery == "discarded_after_release"
                and len(recovered) == 1 and recovered[0]["class"] == "builtins.TimeoutError")
        if delivery != "sent" and not held:
            invalid.append(f"Unexpected scripted response delivery: {delivery}")
    return invalid


def behavior_matches(result):
    """Predictions are checked separately from safety/infrastructure validity."""
    case, context = result["case"], result["context"]
    if (not result["infrastructure_ok"] or result["http_request_count"] != EXPECTED_REQUESTS[case]
            or context["tool_calls_used"] != EXPECTED_COUNTED[case]
            or context["llm_calls_used"] != (1 if case == "H13" else EXPECTED_REQUESTS[case])
            or result["agent_patch"] != dispatch.expected_patch()
            or result["workspace_observation"]["workspace"]["diff"] != result["agent_patch"]
            or result["workspace_observation"]["workspace"]["app_content"] != dispatch.CHANGED
            or result["original_exceptions"] or result["escaped_exception"]):
        return False
    responses = result["tool_responses"]
    expected_tools = EXPECTED_TOOLS[case]
    expected_success = len(expected_tools) - (1 if case == "H14" else 0)
    if ([item["tool"] for item in responses] != expected_tools
            or result["attempted_tool_calls"] != len(expected_tools)
            or result["successful_tool_responses"] != expected_success
            or sum(item["result"].get("status") == "ok" for item in responses) != expected_success
            or (case != "H13" and result["recovered_exceptions"])):
        return False
    writes = [item["result"] for item in responses if item["tool"] == "write_file"]
    if sum(item.get("status") == "ok" for item in writes) != EXPECTED_COUNTED[case]:
        return False
    submitted = case in {"CONTROL", "H29"}
    if bool(context["patch_submitted"]) != submitted or result["fallback_extraction_ran"] == submitted:
        return False
    if submitted:
        if context["submitted_patch"] != result["agent_patch"] or result["agent_error"] is not None or result["recovered_exceptions"]:
            return False
        if responses[-1]["result"].get("status") != "ok":
            return False
    elif context["submitted_patch"] is not None:
        return False
    if case == "H13":
        if (result["agent_error"] != "Agent exceeded session timeout (0.05 min)"
                or len(result["recovered_exceptions"]) != 1
                or result["recovered_exceptions"][0]["class"] != "builtins.TimeoutError"):
            return False
    elif case in {"H14", "H29_BOUNDARY"}:
        if result["agent_error"] != f"Agent exceeded tool call budget ({budgets(case)['max_tool_calls']} calls)":
            return False
    if case == "H14":
        if writes[-1] != {"status": "error", "error_type": "BudgetExceeded", "error_message": "Tool call budget exhausted (3 calls)"}:
            return False
    if case == "H29":
        statuses = [item["result"] for item in responses if item["tool"] == "get_status"]
        submits = [item["result"] for item in responses if item["tool"] == "submit_patch"]
        if (len(statuses) != 1 or statuses[0].get("status") != "ok"
                or statuses[0].get("tool_calls_used") != 2 or statuses[0].get("tool_calls_remaining") != 0
                or len(submits) != 1 or submits[0].get("status") != "ok"):
            return False
    if case in {"CONTROL", "H13", "H14"}:
        verified = result["verification"]
        if (not verified or not verified["result"]["resolved"] or verified["result"]["test_exit_code"] != 0
                or verified.get("original_exceptions")):
            return False
    return True


async def run_case(case, root, output, snapshot, commit, site, wheels, guard, server, support, admission):
    from adk_submission import ModelRegistry
    from google.adk.models.lite_llm import LiteLlm
    from swegemma.config import EvalConfig
    from swegemma.context import SwegemmaContext
    from swegemma.harness.agent_runner import run_agent_sandbox
    from swegemma.models.task import Task
    from swegemma.sandbox.subprocess import SubprocessManager

    case_root = root / case
    submission = case_root / "submission"
    submission.mkdir(parents=True)
    declared, script = declared_tools(case), scripts(case)
    (submission / "agent.yaml").write_text(
        f"agent_class: LlmAgent\nname: {case.lower()}_probe\nmodel: scripted\n"
        "instruction: Synthetic budget certification. Follow scripted calls.\ntools:\n"
        + "".join(f"  - {name}\n" for name in declared), encoding="utf-8")
    dispatch.write_json(output / "preflight.json", {**admission, "case": case, "budget": budgets(case),
                                                    "declared_tools": declared})
    dispatch.write_json(output / "script.json", script)
    first_call = len(server.calls)
    server.set_script(script)
    if case == "H13":
        server.withhold_response(2)
    client, http = await create_client(server.base_url)
    models = ModelRegistry()
    models.register("scripted", LiteLlm(model="openai/h23-scripted", api_base=server.base_url,
                                       api_key="H23_DUMMY", client=client, num_retries=0, timeout=10))
    config = EvalConfig(tasks_path=root / "tasks/synthetic.jsonl", snapshots_dir=root / "snapshots",
                        results_dir=output, submission_dir=submission, models=models, sandbox="subprocess", wheels_dir=None,
                        graph_dir=str(case_root / "graphs"), embeddings_dir=str(case_root / "embeddings"),
                        timeout_seconds=20, **budgets(case), display_mode="quiet", enable_sandbox_testing=True,
                        context_cache_config=None, events_compaction_config=None)
    manager = SubprocessManager(timeout_seconds=20, base_dir=case_root / "agent_sandboxes", system_site_packages=False)
    observation = dispatch.instrument_manager(manager, snapshot, dispatch.sandbox_commands(site, snapshot.name), wheels, guard)
    task = Task(instance_id=f"synthetic_{case.lower()}", repo=dispatch.SYNTHETIC_REPO, base_commit=commit,
                problem_statement='Change synthetic app.py MARKER from "before" to "after".',
                FAIL_TO_PASS=(f"{TEST_NAME}::test_marker",))
    context = SwegemmaContext(docker_manager=manager, task=task, problem_statement=task.problem_statement,
                              repo=task.repo, budget=config.budget, harness=config.harness,
                              graph_dir=config.graph_dir, embeddings_dir=config.embeddings_dir)
    registered = sorted(context.create_tools())
    if not set(declared).issubset(registered):
        guard.refuse(f"Missing standard tool registration: {declared}")
    handler = RunnerEvidence(site / "swegemma/harness/agent_runner.py")
    logger = logging.getLogger("swegemma.harness.agent_runner")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    patch, error, trace, escaped, verification, negative = "", None, None, None, None, None
    try:
        patch, error, trace = await run_agent_sandbox(manager, config, task, snapshot, context=context)
    except Exception as exc:
        escaped = {"class": f"{type(exc).__module__}.{type(exc).__qualname__}", "message": str(exc), "traceback": traceback.format_exc()}
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        await client.close()
        await http.aclose()
        if not server.release_withheld_response():
            observation["observer_errors"].append("Withheld request handler did not finish")
    trace_data = trace.to_dict(format="legacy") if trace else {"entries": []}
    calls = server.calls[first_call:]
    invalid = infrastructure_errors(case, calls, script, declared, observation, guard, http, escaped, handler.recovered)
    if not invalid and case in {"CONTROL", "H13", "H14"}:
        try:
            if case == "CONTROL":
                negative = await verify_patch("verification_negative", "", None, task, config, snapshot,
                                              case_root, output, wheels, guard, support)
            if patch:
                verification = await verify_patch("verification_positive", patch, error, task, config, snapshot,
                                                  case_root, output, wheels, guard, support)
        except Exception as exc:
            escaped = {"class": f"{type(exc).__module__}.{type(exc).__qualname__}", "message": str(exc), "traceback": traceback.format_exc()}
            invalid.append("Exception escaped official verification")
        for evidence in (negative, verification):
            if evidence and evidence["observation"]["observer_errors"]:
                invalid.append("Verification workspace/JUnit observer failed")
        invalid.extend(message for message in guard.violations if message not in invalid)
    responses = tool_responses(trace_data)
    result = {"case": case, "run_id": admission["run_id"], "scope": "synthetic local Phase 1/Phase 2 only; no hidden-scorer inference",
              "infrastructure_ok": not invalid, "infrastructure_errors": invalid,
              "declared_tools": declared, "registered_tools": registered, "budget": budgets(case),
              "advertised_tools": [[item["function"]["name"] for item in call["request"].get("json", {}).get("tools", [])] for call in calls],
              "http_request_count": len(calls), "expected_request_count": EXPECTED_REQUESTS[case],
              "tool_responses": responses, "attempted_tool_calls": sum(len(message.get("tool_calls", [])) for message in script[:len(calls)]),
              "successful_tool_responses": sum(item["result"].get("status") == "ok" for item in responses),
              "context": {name: getattr(context, name) for name in ("patch_submitted", "submitted_patch", "tool_calls_used", "llm_calls_used")},
              "agent_patch": patch, "agent_error": error, "patch_sha256": dispatch.digest(patch.encode()),
              "workspace_observation": observation, "original_exceptions": handler.records,
              "recovered_exceptions": handler.recovered, "escaped_exception": escaped, "runner_logs": handler.logs,
              "fallback_extraction_ran": any("Captured unsubmitted working tree modifications as fallback patch" in item["message"] for item in handler.logs),
              "verification": verification, "negative_verification_control": negative,
              "parent_connections": list(guard.connections), "parent_bindings": list(guard.bindings), "parent_processes": list(guard.processes),
              "process_tree_globally_firewalled": False, "competition_content_access": False,
              "evaluator_orchestration": "source-read only; direct official phase functions exercised",
              "script_sha256": dispatch.digest((output / "script.json").read_bytes())}
    result["prediction_matches"] = behavior_matches(result)
    if case == "CONTROL":
        result["control_pass"] = (result["prediction_matches"] and negative is not None
                                   and not negative["result"]["resolved"] and negative["result"]["test_exit_code"] == 1)
    dispatch.write_json(output / "result.json", result)
    dispatch.write_json(output / "http.json", calls)
    dispatch.write_json(output / "trace.json", trace_data)
    dispatch.write_json(output / "exceptions.json", {"original": handler.records, "recovered": handler.recovered, "escaped": escaped})
    (output / "agent.patch").write_text(patch, encoding="utf-8")
    (output / "submitted.patch").write_text(context.submitted_patch or "", encoding="utf-8")
    if observation["workspace"]:
        (output / "workspace.patch").write_text(observation["workspace"]["diff"], encoding="utf-8")
        (output / "workspace_app.py").write_text(observation["workspace"]["app_content"], encoding="utf-8")
    dispatch.write_json(output / "artifact_inventory.json", {
        path.relative_to(output).as_posix(): {"sha256": dispatch.digest(path.read_bytes()), "bytes": path.stat().st_size}
        for path in sorted(output.rglob("*")) if path.is_file() and path.name != "artifact_inventory.json"})
    print(f"{case}: infrastructure_ok={result['infrastructure_ok']} requests={len(calls)} "
          f"counted_tools={context.tool_calls_used} prediction_matches={result['prediction_matches']} result={output / 'result.json'}")
    return result


async def run_cases(root, outputs, snapshot, commit, site, wheels, guard, server, support, admission):
    for case in CASES:
        result = await run_case(case, root, outputs[case], snapshot, commit, site, wheels, guard, server, support, admission)
        if not result["infrastructure_ok"] or (case == "CONTROL" and not result["control_pass"]):
            print(f"STOP: {case} infrastructure/control failed; later cases not run.", file=sys.stderr)
            return 2
    print("PROBE_COMPLETE: H13/H14/H29 observations recorded; no matrix status promoted.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--h23-cpu-tmp", type=Path, default=os.environ.get("H23_CPU_TMP"))
    args = parser.parse_args(argv)
    outputs, guard = {}, None
    try:
        if args.h23_cpu_tmp is None:
            raise ProbeRefused("Set H23_CPU_TMP to the acquired Python 3.12.14 harness root")
        cpu_root = args.h23_cpu_tmp.resolve(strict=True)
        if any(not (char.isascii() and (char.isalnum() or char in "/._-")) for char in str(cpu_root)):
            raise ProbeRefused(f"H23_CPU_TMP must be ASCII shell-safe: {cpu_root}")
        original_cwd = Path.cwd().resolve()
        changes = check_repository(REPO, profile="budget")
        source_names = BUDGET_PROBE_FILES | {"tools/harness_cert/run_h04_h05_h18.py", "tools/harness_cert/__init__.py"}
        source_hashes = {name: dispatch.digest((REPO / name).read_bytes()) for name in sorted(source_names)}
        environment = dispatch.verify_environment(cpu_root, REPO)
        site = Path(environment["site_packages"])
        environment["relevant_installed_source_sha256"] = {
            name: dispatch.digest((site / name).read_bytes()) for name in (
                "swegemma/harness/agent_runner.py", "swegemma/harness/verification.py", "swegemma/evaluate.py",
                "swegemma/context.py", "swegemma/tools/base.py", "swegemma/tools/execution.py",
                "swegemma/sandbox/subprocess.py", "adk_eval_core/sandbox/subprocess_sandbox.py",
                "adk_submission/builders/llm.py", "adk_submission/resolvers/tools.py",
                "google/adk/flows/llm_flows/base_llm_flow.py", "google/adk/tools/function_tool.py")}
        root = Path(tempfile.mkdtemp(prefix="h13-h14-h29-", dir=cpu_root)).resolve()
        confined(root, cpu_root)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + root.name.rsplit("-", 1)[-1]
        protected = [REPO / "data", REPO / "agents", REPO / "snapshots", REPO / "gemma-4-developer-agent",
                     REPO.parent / "gemma-4-developer-agent", Path("/kaggle/input")]
        if os.environ.get("GEMMA4_DATASET_ROOT"):
            protected.append(Path(os.environ["GEMMA4_DATASET_ROOT"]))
        dispatch.prepare_environment(root)
        for key in list(os.environ):
            if key.startswith("PYTEST_"):
                os.environ.pop(key)
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        support = prepare_support(REPO, root)
        paths = []
        for cwd in (original_cwd, root):
            paths.extend(wheel_candidates(cwd, root / "tasks/synthetic.jsonl", root / "snapshots"))
        wheel_checks = check_wheels(paths)
        for case in ("H13", "H14", "H29"):
            base = REPO / "harness_cert/results" / case
            if base.resolve() != base or not git(REPO, "check-ignore", str(base / run_id / "result.json")).strip():
                raise ProbeRefused(f"Output root is symlinked or not gitignored: {base}")
            output = base / run_id
            output.mkdir(parents=True, exist_ok=False)
            outputs[case] = output.resolve()
        for case, category in (("CONTROL", "H13"), ("H29_BOUNDARY", "H29")):
            output = outputs[category] / case
            output.mkdir(exist_ok=False)
            outputs[case] = output
        dispatch.isolate_import_paths(Path(environment["site_packages"]))
        os.chdir(root)
        runtime_reads = discover_system_runtime_reads()
        ipv6_probe = IPv6FeatureProbe.from_site(Path(environment["site_packages"]))
        guard = ParentGuard(REPO, root, list(outputs.values()), protected,
                            read_roots=[Path(sys.prefix), Path(sys.base_prefix)], metadata_dirs=[*paths, REPO, REPO / "tools", REPO / "tools/harness_cert"],
                            system_runtime_reads=runtime_reads, ipv6_feature_probe=ipv6_probe)
        sys.addaudithook(guard.audit)
        admission = {"run_id": run_id, "git_head": BUDGET_BASELINE, "previous_dispatch_baseline": BASELINE,
                     "allowed_probe_changes": changes, "probe_source_sha256": source_hashes,
                     "environment": environment, "pytest_support": support, "wheel_checks": wheel_checks,
                     "synthetic_root": str(root), "system_site_packages": False, "pytest_plugin_autoload": False,
                     "system_runtime_read_roots": [str(runtime_reads.zoneinfo_root)] if runtime_reads.zoneinfo_root else [],
                     "ipv6_feature_detection": {"source": str(ipv6_probe.source), "sha256": URLLIB3_CONNECTION_SHA256},
                     "output_directories": {case: str(output) for case, output in outputs.items()},
                     "process_tree_globally_firewalled": False, "competition_content_access": False}
        for output in outputs.values():
            dispatch.write_json(output / "preflight.json", admission)
        import litellm
        litellm.disable_hf_tokenizer_download = True
        litellm.model_fallbacks = None
        litellm.telemetry = False
        litellm.cache = None
        for name in ("callbacks", "input_callback", "success_callback", "failure_callback", "_async_success_callback", "_async_failure_callback"):
            setattr(litellm, name, [])
        admission["auth_import_smoke"] = dispatch.smoke_auth_import()
        print("AUTHLIB_IMPORT_OK")
        snapshot, commit = synthetic_fixture(root, guard)
        admission["fixture"] = {"snapshot_sha256": dispatch.digest(snapshot.read_bytes()), "base_commit": commit,
                                "test_name": TEST_NAME, "test_sha256": dispatch.digest(TEST_CONTENT.encode()),
                                "expected_patch_sha256": dispatch.digest(dispatch.expected_patch().encode())}
        for output in outputs.values():
            dispatch.write_json(output / "preflight.json", admission)
        with ScriptedServer() as server:
            guard.port = int(server.origin.rsplit(":", 1)[1])
            return asyncio.run(run_cases(root, outputs, snapshot, commit, Path(environment["site_packages"]), paths, guard, server, support, admission))
    except Exception as exc:
        message = f"PROBE REFUSED/INVALID: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        if outputs:
            dispatch.write_json(next(iter(outputs.values())) / "launch_error.json", {
                "error": message, "traceback": traceback.format_exc(), "parent_guard_violations": guard.violations if guard else []})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
