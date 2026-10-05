"""Observe the installed H30 prompt gate using synthetic, network-free requests.

The official submission compiler and agent runner are exercised unchanged.
Real ADK/LiteLLM/OpenAI request serialization reaches an in-process MockTransport;
no socket listener, model server, graph query, evaluator or competition data runs.
Observations and source-predicate predictions are recorded separately. This
operator probe does not promote the matrix's broader file-existence hypothesis.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import io
import json
import logging
import os
import re
import sys
import tempfile
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import _probe_safety as safety
from . import run_h04_h05_h18 as dispatch
from ._scripted_loopback import validate_loopback_url

REPO = dispatch.REPO
CASES = ("ABSENT", "GRAPH_ONLY", "EMBEDDING_ONLY", "THRESHOLD", "H30", "DECLARED_CONTROL")
GRAPH_TOOLS = ("search_similar_code", "get_code_neighbors", "get_code_subgraph")
FILE_SIZES = {"ABSENT": (None, None), "GRAPH_ONLY": (101, None),
              "EMBEDDING_ONLY": (None, 101), "THRESHOLD": (100, 100),
              "H30": (101, 101), "DECLARED_CONTROL": (101, 101)}
HEADING = "## Code Intelligence Tools"
BASE_URL = "http://127.0.0.1:8765/v1"
ENDPOINT = BASE_URL + "/chat/completions"
MAX_REQUEST_BYTES = 64 * 1024
FINAL = {"role": "assistant", "content": "Synthetic graph advertisement observation complete."}


def declared_tools(case):
    if case not in CASES:
        raise ValueError(f"Unknown graph case: {case}")
    return ["write_file", "submit_patch"] + (list(GRAPH_TOOLS) if case == "DECLARED_CONTROL" else [])


def scripts(case):
    declared_tools(case)
    return [dispatch.tool_message("write_file", dispatch.WRITE_ARGS, "synthetic_write"),
            dispatch.tool_message("submit_patch", {}, "synthetic_submit"), copy.deepcopy(FINAL)]


def availability_payload(kind, size):
    """Valid containers only: the official prompt reads metadata, not contents."""
    if kind == "graph":
        marker = b'{"synthetic_stat_only":true}'
        if type(size) is not int or size < len(marker):
            raise ValueError("Graph fixture is too short for its provenance marker")
        return marker + b" " * (size - len(marker))
    if kind != "embedding" or type(size) is not int or size < 22:
        raise ValueError("Unsupported availability fixture")
    # An empty NPZ is a ZIP container with no arrays. Nothing loads it here.
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        marker = b"SYNTHETIC STAT-ONLY NPZ; NO ARRAYS; "
        archive.comment = (marker + b"_" * size)[:size - 22]
    data = stream.getvalue()
    if len(data) != size:
        raise ValueError("Unexpected empty ZIP container size")
    return data


def graph_fixture(case, case_root, output, commit):
    declared_tools(case)
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise safety.ProbeRefused("Synthetic commit must be an exact SHA1 identifier")
    metadata = []
    for kind, directory, suffix, size in zip(
        ("graph", "embedding"), ("graphs", "embeddings"), ("json", "npz"), FILE_SIZES[case]
    ):
        parent = case_root / directory
        parent.mkdir()
        path = parent / f"probe_{commit}.{suffix}"
        entry = {"kind": kind, "path": str(path), "filename": path.name,
                 "exists": size is not None, "bytes": size, "sha256": None,
                 "semantics": "stat-only availability fixture; no graph handler executes"}
        if size is not None:
            data = availability_payload(kind, size)
            path.write_bytes(data)
            actual = path.read_bytes()
            if actual != data or path.stat().st_size != size:
                raise safety.ProbeRefused("Synthetic availability file changed during fixture creation")
            entry.update(exists=path.exists(), bytes=path.stat().st_size, sha256=dispatch.digest(actual))
            # Preserve exact bytes outside the disposable runtime directory.
            (output / f"fixture_{kind}.{suffix}").write_bytes(data)
        metadata.append(entry)
    dispatch.write_json(output / "graph_fixture.json", metadata)
    return metadata


class ScriptedCapture:
    """Bounded, fixed-origin in-process transport; it never opens a socket."""

    def __init__(self, script):
        if (not isinstance(script, list) or not script
                or any(not isinstance(message, dict) or message.get("role") != "assistant"
                       or not ("content" in message or "tool_calls" in message) for message in script)):
            raise ValueError("Script must contain assistant messages")
        json.dumps(script, allow_nan=False)
        self.script = copy.deepcopy(script)
        self.calls = []
        self.violations = []
        self.position = 0

    async def handle(self, request):
        import httpx

        body = await request.aread()
        request_json, number, message, reason = None, None, None, None
        try:
            validate_loopback_url(str(request.url), expected_origin=BASE_URL.rsplit("/", 1)[0])
            if request.method != "POST" or str(request.url) != ENDPOINT:
                raise ValueError("Only the fixed synthetic completion endpoint is accepted")
            lengths = request.headers.get_list("content-length")
            if (request.headers.get("transfer-encoding") or lengths != [str(len(body))]
                    or not 0 < len(body) <= MAX_REQUEST_BYTES):
                raise ValueError("Incomplete or oversized synthetic request body")
            request_json = json.loads(body)
            if (not isinstance(request_json, dict) or request_json.get("stream", False) is not False
                    or request_json.get("model") != "h30-scripted"
                    or not isinstance(request_json.get("messages"), list)):
                raise ValueError("Only nonstreaming synthetic model requests are accepted")
            if self.position >= len(self.script):
                raise ValueError("Script exhausted; no alternate endpoint or fallback is permitted")
            message = copy.deepcopy(self.script[self.position])
            self.position += 1
            number = self.position
        except (ValueError, TypeError, UnicodeError) as exc:
            reason = str(exc)
            self.violations.append(reason)
        if reason is None:
            payload = {"id": f"h30-scripted-{number:04d}", "object": "chat.completion", "created": 0,
                       "model": request_json["model"],
                       "choices": [{"index": 0, "message": message,
                                    "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                       "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
            status = 200
        else:
            payload = {"error": {"type": "synthetic_transport_refusal", "message": reason}}
            status = 400
        response_body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        response = httpx.Response(status, content=response_body, headers={"content-type": "application/json"})
        self.calls.append({"script_number": number, "transport": "httpx.MockTransport; no network",
                           "request": {"method": request.method, "url": str(request.url),
                                       "headers": list(request.headers.multi_items()),
                                       "body_text": body.decode("utf-8", errors="replace"),
                                       "body_base64": base64.b64encode(body).decode("ascii"),
                                       "body_sha256": dispatch.digest(body),
                                       "body_complete": request.headers.get_list("content-length") == [str(len(body))],
                                       "json": request_json},
                           "response": {"status": status, "headers": list(response.headers.multi_items()),
                                        "body_text": response_body.decode(),
                                        "body_base64": base64.b64encode(response_body).decode("ascii"),
                                        "body_sha256": dispatch.digest(response_body), "json": payload,
                                        "delivery": "in_process_mock_transport"}})
        return response


async def create_client(capture):
    """Construct real SDK clients with an exclusively in-process transport."""
    import httpx
    from openai import AsyncOpenAI

    class SharedTransportClient(AsyncOpenAI):
        def __deepcopy__(self, memo):
            memo[id(self)] = self
            return self

    http = httpx.AsyncClient(transport=httpx.MockTransport(capture.handle), trust_env=False,
                            follow_redirects=False, timeout=httpx.Timeout(10.0))
    try:
        client = SharedTransportClient(base_url=BASE_URL, api_key="H30_SCRIPTED_DUMMY", max_retries=0,
                                       timeout=10.0, http_client=http)
        # Avoid the optional Darwin platform header's unapproved `uname` launch.
        client._platform = "Unknown"
    except BaseException:
        await http.aclose()
        raise
    return client, http


def first_user_prompt(calls):
    if not calls:
        raise ValueError("No completion request captured")
    messages = calls[0]["request"]["json"]["messages"]
    users = [message for message in messages if message.get("role") == "user"]
    if len(users) != 1:
        raise ValueError("The first request must contain exactly one task user message")
    content = users[0].get("content")
    if isinstance(content, str):
        return content
    if (isinstance(content, list) and content
            and all(isinstance(part, dict) and part.get("type") == "text"
                    and isinstance(part.get("text"), str) for part in content)):
        return "".join(part["text"] for part in content)
    raise ValueError("Task user content is not recorded text")


def schemas_from(calls):
    groups = [call["request"]["json"].get("tools", []) for call in calls]
    names = [[schema["function"]["name"] for schema in group] for group in groups]
    if any(not isinstance(name, str) or not name for group in names for name in group):
        raise ValueError("Malformed serialized tool names")
    return groups, names


def infrastructure_errors(capture, observation, guard, escaped):
    invalid = list(guard.violations) + list(capture.violations)
    if escaped:
        invalid.append("Exception escaped the official runner")
    if not observation["workspace"] or observation["observer_errors"]:
        invalid.append("Synthetic workspace observer failed")
    if guard.port is not None or guard.connections:
        invalid.append("The network-free probe admitted a socket connection")
    if any(binding["purpose"] != "urllib3_ipv6_detection" for binding in guard.bindings):
        invalid.append("The network-free probe created a socket listener")
    if not capture.calls:
        invalid.append("No genuine SDK completion request captured")
    for index, call in enumerate(capture.calls):
        request, response = call["request"], call["response"]
        if (index >= len(capture.script) or call["script_number"] != index + 1
                or request["method"] != "POST" or request["url"] != ENDPOINT
                or not request["body_complete"] or response["status"] != 200
                or response["delivery"] != "in_process_mock_transport"):
            invalid.append("Unexpected SDK request, retry, transport refusal or script exhaustion")
            continue
        try:
            for record in (request, response):
                body = base64.b64decode(record["body_base64"], validate=True)
                if (body.decode() != record["body_text"] or dispatch.digest(body) != record["body_sha256"]
                        or json.loads(body) != record["json"]):
                    raise ValueError("Recorded serialized bytes differ from JSON evidence")
            if response["json"]["choices"][0]["message"] != capture.script[index]:
                raise ValueError("Response bytes differ from the admitted script")
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
            invalid.append("Malformed or inconsistent serialized request/response evidence")
    return invalid


def patch_control_ok(result):
    context = result["context"]
    responses = result["tool_responses"]
    return (result["infrastructure_ok"] and result["agent_error"] is None
            and result["http_request_count"] == 3 and context["llm_calls_used"] == 3
            and context["tool_calls_used"] == 1 and context["patch_submitted"]
            and context["submitted_patch"] == result["agent_patch"] == dispatch.expected_patch()
            and result["workspace_observation"]["workspace"]["diff"] == result["agent_patch"]
            and result["workspace_observation"]["workspace"]["app_content"] == dispatch.CHANGED
            and [item["tool"] for item in responses] == ["write_file", "submit_patch"]
            and all(item["result"].get("status") == "ok" for item in responses)
            and not result["original_exceptions"] and not result["escaped_exception"]
            and not result["fallback_extraction_ran"])


def source_prediction_matches(result):
    declared = declared_tools(result["case"])
    return (result["patch_control_pass"]
            and result["prompt_advertises_graph_tools"] == (result["case"] in {"H30", "DECLARED_CONTROL"})
            and (not result["prompt_advertises_graph_tools"]
                 or all(f"`{name}(" in result["initial_prompt"] for name in GRAPH_TOOLS))
            and len(result["serialized_tool_names"]) == 3
            and all(sorted(names) == sorted(declared) for names in result["serialized_tool_names"]))


class RunnerEvidence(dispatch.Exceptions):
    def __init__(self, source):
        super().__init__()
        self.source = str(source)
        self.logs = []

    def emit(self, record):
        super().emit(record)
        if record.name == "swegemma.harness.agent_runner" and record.pathname == self.source:
            self.logs.append({"line": record.lineno, "level": record.levelname, "message": record.getMessage()})


def write_inventory(output):
    inventory = output / "artifact_inventory.json"
    dispatch.write_json(inventory, {
        path.relative_to(output).as_posix(): {"sha256": dispatch.digest(path.read_bytes()), "bytes": path.stat().st_size}
        for path in sorted(output.rglob("*")) if path.is_file() and path != inventory})


async def run_case(case, root, output, snapshot, commit, site, wheels, guard, admission):
    # Exact installed imports occur only after operator admission and audit guard.
    from adk_submission import ModelRegistry
    from google.adk.models.lite_llm import LiteLlm
    from swegemma.config import EvalConfig
    from swegemma.context import SwegemmaContext
    from swegemma.harness.agent_runner import run_agent_sandbox
    from swegemma.models.task import Task
    from swegemma.sandbox.subprocess import SubprocessManager

    declared = declared_tools(case)
    case_root = root / case
    case_root.mkdir()
    files = graph_fixture(case, case_root, output, commit)
    submission = case_root / "submission"
    submission.mkdir()
    yaml_text = (f"agent_class: LlmAgent\nname: {case.lower()}_probe\nmodel: scripted\n"
                 "instruction: Synthetic graph-advertisement observation only. Follow the scripted tool calls.\n"
                 "tools:\n" + "".join(f"  - {name}\n" for name in declared))
    (submission / "agent.yaml").write_text(yaml_text, encoding="utf-8")
    (output / "agent.yaml").write_text(yaml_text, encoding="utf-8")
    script = scripts(case)
    dispatch.write_json(output / "script.json", script)
    capture = ScriptedCapture(script)
    client, http = await create_client(capture)
    models = ModelRegistry()
    models.register("scripted", LiteLlm(model="openai/h30-scripted", api_base=BASE_URL,
                                       api_key="H30_SCRIPTED_DUMMY", client=client, num_retries=0, timeout=10))
    config = EvalConfig(tasks_path=root / "tasks/synthetic.jsonl", snapshots_dir=root / "snapshots",
                        results_dir=output, submission_dir=submission, models=models, sandbox="subprocess", wheels_dir=None,
                        graph_dir=str(case_root / "graphs"), embeddings_dir=str(case_root / "embeddings"),
                        timeout_seconds=20, max_time_minutes=1, max_tool_calls=8, max_turns=6,
                        display_mode="quiet", enable_sandbox_testing=True,
                        context_cache_config=None, events_compaction_config=None)
    manager = SubprocessManager(timeout_seconds=20, base_dir=case_root / "sandboxes", system_site_packages=False)
    observation = dispatch.instrument_manager(manager, snapshot, dispatch.sandbox_commands(site, snapshot.name), wheels, guard)
    task = Task(instance_id=f"synthetic_{case.lower()}", repo=dispatch.SYNTHETIC_REPO, base_commit=commit,
                problem_statement='Change synthetic app.py MARKER from "before" to "after". Synthetic fixture only.')
    context = SwegemmaContext(docker_manager=manager, task=task, problem_statement=task.problem_statement,
                              repo=task.repo, budget=config.budget, harness=config.harness,
                              graph_dir=config.graph_dir, embeddings_dir=config.embeddings_dir)
    registered = sorted(context.create_tools())
    if not set(declared).issubset(registered):
        guard.refuse("Declared synthetic tools are not present in the standard registry")
    handler = RunnerEvidence(site / "swegemma/harness/agent_runner.py")
    logger = logging.getLogger("swegemma.harness.agent_runner")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    patch, error, trace, escaped = "", None, None, None
    try:
        patch, error, trace = await run_agent_sandbox(manager, config, task, snapshot, context=context)
    except Exception as exc:
        escaped = {"class": f"{type(exc).__module__}.{type(exc).__qualname__}",
                   "message": str(exc), "traceback": traceback.format_exc()}
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        await client.close()
        await http.aclose()
    trace_data = trace.to_dict(format="legacy") if trace is not None else {"entries": []}
    invalid = infrastructure_errors(capture, observation, guard, escaped)
    prompt, schemas, names, responses = "", [], [], []
    try:
        prompt = first_user_prompt(capture.calls)
        schemas, names = schemas_from(capture.calls)
        responses = [{"tool": entry.get("tool"), "result": json.loads(entry["result"])}
                     for entry in trace_data["entries"] if entry.get("type") == "tool_response"]
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        invalid.append(f"Observation parsing failed: {type(exc).__name__}: {exc}")
    result = {"case": case, "run_id": admission["run_id"],
              "scope": "synthetic local prompt/request serialization only; no graph execution or hidden-scorer inference",
              "infrastructure_ok": not invalid, "infrastructure_errors": invalid,
              "declared_tools": declared, "registered_tools": registered,
              "graph_fixture": files, "initial_prompt": prompt, "initial_prompt_sha256": dispatch.digest(prompt.encode()),
              "prompt_advertises_graph_tools": HEADING in prompt,
              "serialized_tool_names": names, "http_request_count": len(capture.calls),
              "tool_responses": responses, "agent_patch": patch, "agent_error": error,
              "patch_sha256": dispatch.digest(patch.encode()),
              "context": {name: getattr(context, name) for name in
                          ("patch_submitted", "submitted_patch", "tool_calls_used", "llm_calls_used")},
              "workspace_observation": observation, "original_exceptions": handler.records,
              "escaped_exception": escaped, "runner_logs": handler.logs,
              "fallback_extraction_ran": any("Captured unsubmitted working tree modifications as fallback patch"
                                             in entry["message"] for entry in handler.logs),
              "parent_connections": list(guard.connections), "parent_bindings": list(guard.bindings),
              "parent_processes": list(guard.processes), "port_admitted": guard.port,
              "process_tree_globally_firewalled": False, "competition_content_access": False,
              "transport": "real AsyncOpenAI + httpx.MockTransport; serialized bytes, no socket traffic",
              "evaluator_orchestration": "not invoked",
              "graph_tools_executed": any(entry["tool"] in GRAPH_TOOLS for entry in responses)}
    result["patch_control_pass"] = patch_control_ok(result)
    result["source_predicate_prediction_matches"] = source_prediction_matches(result)
    dispatch.write_json(output / "result.json", result)
    dispatch.write_json(output / "http.json", capture.calls)
    dispatch.write_json(output / "tool_schemas.json", schemas)
    dispatch.write_json(output / "trace.json", trace_data)
    dispatch.write_json(output / "exceptions.json", {"original": handler.records, "escaped": escaped})
    (output / "initial_prompt.txt").write_text(prompt, encoding="utf-8")
    (output / "agent.patch").write_text(patch, encoding="utf-8")
    (output / "submitted.patch").write_text(context.submitted_patch or "", encoding="utf-8")
    if observation["workspace"]:
        (output / "workspace.patch").write_text(observation["workspace"]["diff"], encoding="utf-8")
        (output / "workspace_app.py").write_text(observation["workspace"]["app_content"], encoding="utf-8")
    write_inventory(output)
    print(f"{case}: infrastructure_ok={result['infrastructure_ok']} requests={len(capture.calls)} "
          f"patch_control_pass={result['patch_control_pass']} graph_prompt={result['prompt_advertises_graph_tools']} "
          f"prediction_matches={result['source_predicate_prediction_matches']} result={output / 'result.json'}")
    return result


def summarize(results):
    complete = len(results) == len(CASES) and [result["case"] for result in results] == list(CASES)
    valid = complete and all(result["infrastructure_ok"] and result["patch_control_pass"] for result in results)
    by_case = {result["case"]: result for result in results}
    counterexample = False
    independence = False
    if valid:
        threshold = by_case["THRESHOLD"]
        omitted = by_case["H30"]
        declared = by_case["DECLARED_CONTROL"]
        counterexample = (len(threshold["graph_fixture"]) == 2
                          and all(entry["exists"] and entry["bytes"] == 100 for entry in threshold["graph_fixture"])
                          and not threshold["prompt_advertises_graph_tools"]
                          and omitted["prompt_advertises_graph_tools"])
        independence = (omitted["prompt_advertises_graph_tools"] and declared["prompt_advertises_graph_tools"]
                        and omitted["initial_prompt"] == declared["initial_prompt"]
                        and all(not set(GRAPH_TOOLS).intersection(names) for names in omitted["serialized_tool_names"])
                        and all(set(GRAPH_TOOLS).issubset(names) for names in declared["serialized_tool_names"]))
    return {"evidence_valid": valid, "cases_completed": [result["case"] for result in results],
            "source_predicate_prediction_matches": valid and all(result["source_predicate_prediction_matches"] for result in results),
            "literal_file_existence_counterexample": counterexample,
            "declaration_independence_observed": independence,
            "matrix_status_changed": False,
            "interpretation_boundary": "Recognized filenames and size >100 are tested; no unconditional-existence PASS is inferred."}


async def run_cases(root, output, snapshot, commit, site, wheels, guard, admission):
    results = []
    for case in CASES:
        directory = output / case
        directory.mkdir()
        dispatch.write_json(directory / "preflight.json", admission)
        result = await run_case(case, root, directory, snapshot, commit, site, wheels, guard, admission)
        results.append(result)
        if not result["infrastructure_ok"] or not result["patch_control_pass"]:
            print(f"STOP: {case} infrastructure/patch control failed; remaining cases not executed.", file=sys.stderr)
            break
    summary = summarize(results)
    dispatch.write_json(output / "summary.json", summary)
    # Capture the final child inventories; no later case rewrites any child.
    write_inventory(output)
    print("PROBE_COMPLETE: observations recorded; no matrix status promoted.")
    return 0 if summary["evidence_valid"] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--h23-cpu-tmp", type=Path, default=os.environ.get("H23_CPU_TMP"))
    args = parser.parse_args(argv)
    guard, output = None, None
    try:
        if args.h23_cpu_tmp is None:
            raise safety.ProbeRefused("Set H23_CPU_TMP to the acquired temporary harness root")
        cpu_root = args.h23_cpu_tmp.resolve(strict=True)
        if any(not (char.isascii() and (char.isalnum() or char in "/._-")) for char in str(cpu_root)):
            raise safety.ProbeRefused("H23_CPU_TMP must be ASCII shell-safe")
        original_cwd = Path.cwd().resolve()
        changes = safety.check_repository(REPO, profile="graph")
        source_names = safety.GRAPH_PROBE_FILES | {
            "tools/harness_cert/run_h04_h05_h18.py", "tools/harness_cert/_scripted_loopback.py",
            "tools/harness_cert/__init__.py"}
        source_hashes = {name: dispatch.digest((REPO / name).read_bytes()) for name in sorted(source_names)}
        environment = dispatch.verify_environment(cpu_root, REPO)
        site = Path(environment["site_packages"])
        environment["relevant_installed_source_sha256"] = {
            name: dispatch.digest((site / name).read_bytes()) for name in (
                "swegemma/harness/agent_runner.py", "swegemma/context.py", "swegemma/tools/__init__.py",
                "adk_submission/builders/llm.py", "adk_submission/resolvers/tools.py",
                "google/adk/flows/llm_flows/base_llm_flow.py", "google/adk/tools/function_tool.py")}
        root = Path(tempfile.mkdtemp(prefix="h30-", dir=cpu_root)).resolve()
        safety.confined(root, cpu_root)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + root.name.split("-", 1)[1]
        paths = []
        for cwd in (original_cwd, root):
            paths.extend(safety.wheel_candidates(cwd, root / "tasks/synthetic.jsonl", root / "snapshots"))
        wheel_checks = safety.check_wheels(paths)
        base = REPO / "harness_cert/results/H30"
        if base.resolve() != base or not safety.git(REPO, "check-ignore", str(base / run_id / "result.json")).strip():
            raise safety.ProbeRefused("H30 output root must be unsymlinked and gitignored")
        output = base / run_id
        output.mkdir(parents=True, exist_ok=False)
        protected = [REPO / "data", REPO / "agents", REPO / "snapshots", REPO / "gemma-4-developer-agent",
                     REPO.parent / "gemma-4-developer-agent", Path("/kaggle/input")]
        if os.environ.get("GEMMA4_DATASET_ROOT"):
            protected.append(Path(os.environ["GEMMA4_DATASET_ROOT"]))
        dispatch.prepare_environment(root)
        dispatch.isolate_import_paths(site)
        os.chdir(root)
        runtime_reads = safety.discover_system_runtime_reads()
        ipv6_probe = safety.IPv6FeatureProbe.from_site(site)
        guard = safety.ParentGuard(REPO, root, [output], protected,
                                   read_roots=[Path(sys.prefix), Path(sys.base_prefix)],
                                   metadata_dirs=[*paths, REPO, REPO / "tools", REPO / "tools/harness_cert"],
                                   system_runtime_reads=runtime_reads, ipv6_feature_probe=ipv6_probe)
        # port remains None: even the numeric URL cannot cause socket traffic.
        sys.addaudithook(guard.audit)
        admission = {"run_id": run_id, "git_head": safety.GRAPH_BASELINE,
                     "previous_dispatch_baseline": safety.BASELINE, "previous_budget_baseline": safety.BUDGET_BASELINE,
                     "allowed_probe_changes": changes, "probe_source_sha256": source_hashes,
                     "environment": environment, "wheel_checks": wheel_checks, "synthetic_root": str(root),
                     "output_directory": str(output), "system_site_packages": False,
                     "system_runtime_read_roots": [str(runtime_reads.zoneinfo_root)] if runtime_reads.zoneinfo_root else [],
                     "ipv6_feature_detection": {"source": str(ipv6_probe.source), "sha256": safety.URLLIB3_CONNECTION_SHA256},
                     "transport": "httpx.MockTransport; no listener or socket connection admitted",
                     "port_admitted": None, "competition_content_access": False, "process_tree_globally_firewalled": False}
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
        snapshot, commit = dispatch.fixture(root, guard)
        admission["fixture"] = {"snapshot_sha256": dispatch.digest(snapshot.read_bytes()), "base_commit": commit,
                                "expected_patch_sha256": dispatch.digest(dispatch.expected_patch().encode())}
        dispatch.write_json(output / "preflight.json", admission)
        return asyncio.run(run_cases(root, output, snapshot, commit, site, paths, guard, admission))
    except Exception as exc:
        message = f"PROBE REFUSED/INVALID: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        if output is not None:
            dispatch.write_json(output / "launch_error.json", {
                "error": message, "traceback": traceback.format_exc(),
                "parent_guard_violations": guard.violations if guard else [], "evidence_valid": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
