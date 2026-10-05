"""Fail-closed H30 evidence tests using fakes; no harness imports or inference."""

import asyncio
import base64
import copy
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import zipfile

import pytest

from tools.harness_cert import run_h30 as probe


def valid_result(case):
    positive = case in {"H30", "DECLARED_CONTROL"}
    prompt = "Synthetic initial task.\n"
    if positive:
        prompt += probe.HEADING + "\n" + "\n".join(f"- `{name}(value)`" for name in probe.GRAPH_TOOLS)
    patch = probe.dispatch.expected_patch()
    sizes = probe.FILE_SIZES[case]
    return {"case": case, "infrastructure_ok": True, "agent_error": None, "http_request_count": 3,
            "context": {"llm_calls_used": 3, "tool_calls_used": 1, "patch_submitted": True,
                        "submitted_patch": patch},
            "agent_patch": patch,
            "workspace_observation": {"workspace": {"diff": patch, "app_content": probe.dispatch.CHANGED}},
            "tool_responses": [{"tool": "write_file", "result": {"status": "ok"}},
                               {"tool": "submit_patch", "result": {"status": "ok"}}],
            "original_exceptions": [], "escaped_exception": None, "fallback_extraction_ran": False,
            "initial_prompt": prompt, "prompt_advertises_graph_tools": positive,
            "serialized_tool_names": [probe.declared_tools(case) for _ in range(3)],
            "graph_fixture": [{"exists": size is not None, "bytes": size} for size in sizes],
            "patch_control_pass": True, "source_predicate_prediction_matches": True}


class FakeHeaders:
    def __init__(self, mapping):
        self.mapping = {key.lower(): str(value) for key, value in mapping.items()}

    def get(self, key):
        return self.mapping.get(key.lower())

    def get_list(self, key):
        value = self.get(key)
        return [] if value is None else [value]

    def multi_items(self):
        return list(self.mapping.items())


class FakeRequest:
    def __init__(self, *, url=probe.ENDPOINT, method="POST", data=None, body=None, headers=None):
        self.url, self.method = url, method
        if data is None:
            data = {"model": "h30-scripted", "messages": [{"role": "user", "content": "Synthetic task"}],
                    "stream": False,
                    "tools": [{"type": "function", "function": {"name": name}}
                              for name in probe.declared_tools("H30")]}
        self.body = body if body is not None else json.dumps(data).encode()
        self.headers = FakeHeaders({"content-length": len(self.body), **(headers or {})})

    async def aread(self):
        return self.body


@pytest.fixture
def fake_httpx(monkeypatch):
    module = ModuleType("httpx")

    class Response:
        def __init__(self, status, *, content, headers):
            self.status_code, self.content = status, content
            self.headers = FakeHeaders({"content-length": len(content), **headers})

    class AsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False

        async def aclose(self):
            self.closed = True

    module.Response = Response
    module.MockTransport = lambda handler: SimpleNamespace(handler=handler)
    module.AsyncClient = AsyncClient
    module.Timeout = lambda value: value
    monkeypatch.setitem(sys.modules, "httpx", module)
    return module


def capture_requests(fake_httpx, case="H30"):
    capture = probe.ScriptedCapture(probe.scripts(case))
    for _ in capture.script:
        assert asyncio.run(capture.handle(FakeRequest())).status_code == 200
    guard = SimpleNamespace(violations=[], connections=[], bindings=[], port=None)
    observation = {"workspace": {"diff": probe.dispatch.expected_patch()}, "observer_errors": []}
    return capture, observation, guard


def test_cases_exercise_data_conjunction_threshold_and_declaration_independence():
    assert probe.CASES == ("ABSENT", "GRAPH_ONLY", "EMBEDDING_ONLY", "THRESHOLD", "H30", "DECLARED_CONTROL")
    assert probe.FILE_SIZES == {"ABSENT": (None, None), "GRAPH_ONLY": (101, None),
                               "EMBEDDING_ONLY": (None, 101), "THRESHOLD": (100, 100),
                               "H30": (101, 101), "DECLARED_CONTROL": (101, 101)}
    for case in probe.CASES:
        script = probe.scripts(case)
        calls = [call["function"] for message in script for call in message.get("tool_calls", [])]
        assert [call["name"] for call in calls] == ["write_file", "submit_patch"]
        assert json.loads(calls[0]["arguments"]) == probe.dispatch.WRITE_ARGS
        assert json.loads(calls[1]["arguments"]) == {}
        assert script[-1]["content"]
        assert not set(probe.GRAPH_TOOLS).intersection(call["name"] for call in calls)


@pytest.mark.parametrize("name", ["", "unknown", "H28", "CONTROL", "h30", None])
def test_unknown_cases_are_refused(name):
    for function in (probe.declared_tools, probe.scripts):
        with pytest.raises(ValueError, match="Unknown graph case"):
            function(name)


@pytest.mark.parametrize("size", [100, 101])
def test_availability_payloads_are_valid_exact_size_stat_only_containers(size):
    graph = probe.availability_payload("graph", size)
    assert len(graph) == size
    assert json.loads(graph) == {"synthetic_stat_only": True}
    embedding = probe.availability_payload("embedding", size)
    assert len(embedding) == size
    with zipfile.ZipFile(io.BytesIO(embedding)) as archive:
        assert archive.namelist() == []
        assert archive.comment.startswith(b"SYNTHETIC STAT-ONLY NPZ")


@pytest.mark.parametrize("kind,size", [("graph", 1), ("embedding", 1), ("unknown", 101), ("graph", True)])
def test_invalid_availability_payloads_refused(kind, size):
    with pytest.raises(ValueError):
        probe.availability_payload(kind, size)


@pytest.mark.parametrize("case", probe.CASES)
def test_graph_fixture_preserves_recognized_names_sizes_and_exact_archived_bytes(tmp_path, case):
    root, output = tmp_path / "runtime", tmp_path / "evidence"
    root.mkdir()
    output.mkdir()
    commit = "d" * 40
    entries = probe.graph_fixture(case, root, output, commit)
    for entry, expected_size in zip(entries, probe.FILE_SIZES[case]):
        path = Path(entry["path"])
        assert path.name == f"probe_{commit}.{'json' if entry['kind'] == 'graph' else 'npz'}"
        assert path.exists() == entry["exists"] == (expected_size is not None)
        if expected_size is not None:
            data = path.read_bytes()
            assert len(data) == entry["bytes"] == expected_size
            assert probe.dispatch.digest(data) == entry["sha256"]
            archive = output / f"fixture_{entry['kind']}.{'json' if entry['kind'] == 'graph' else 'npz'}"
            assert archive.read_bytes() == data
    assert json.loads((output / "graph_fixture.json").read_text()) == entries


@pytest.mark.parametrize("commit", ["", "d" * 39, "d" * 41, "../escape", "D" * 40, None])
def test_graph_fixture_refuses_commit_path_injection_before_creating_files(tmp_path, commit):
    with pytest.raises(probe.safety.ProbeRefused, match="exact SHA1"):
        probe.graph_fixture("H30", tmp_path, tmp_path, commit)
    assert list(tmp_path.iterdir()) == []


def test_capture_serializes_bounded_scripted_request_response_bytes_without_network(fake_httpx):
    capture, observation, guard = capture_requests(fake_httpx)
    assert len(capture.calls) == capture.position == 3
    assert probe.infrastructure_errors(capture, observation, guard, None) == []
    for index, call in enumerate(capture.calls):
        assert call["script_number"] == index + 1
        assert call["request"]["url"] == probe.ENDPOINT
        assert call["response"]["json"]["choices"][0]["message"] == capture.script[index]
        for record in (call["request"], call["response"]):
            body = base64.b64decode(record["body_base64"])
            assert body.decode() == record["body_text"]
            assert json.loads(body) == record["json"]
            assert probe.dispatch.digest(body) == record["body_sha256"]


@pytest.mark.parametrize("url", [
    "http://localhost:8765/v1/chat/completions", "http://127.0.0.2:8765/v1/chat/completions",
    "https://127.0.0.1:8765/v1/chat/completions", "http://127.0.0.1:9999/v1/chat/completions",
    "http://127.0.0.1:8765/v1/other", "http://127.0.0.1:8765/v1/chat/completions?x=1",
    "http://127.0.0.1:8765/v1/../chat/completions", "http://user@127.0.0.1:8765/v1/chat/completions",
    "http://example.invalid/v1/chat/completions",
])
def test_capture_refuses_any_other_origin_or_endpoint_before_consuming_script(fake_httpx, url):
    capture = probe.ScriptedCapture(probe.scripts("H30"))
    response = asyncio.run(capture.handle(FakeRequest(url=url)))
    assert response.status_code == 400
    assert capture.position == 0
    assert capture.violations
    assert capture.calls[0]["script_number"] is None


@pytest.mark.parametrize("mutation", ["method", "length", "transfer", "oversize", "malformed", "stream", "model", "messages"])
def test_capture_refuses_unexpected_request_shape(fake_httpx, mutation):
    kwargs = {}
    data = {"model": "h30-scripted", "messages": [], "stream": False}
    if mutation == "method":
        kwargs["method"] = "GET"
    elif mutation == "length":
        kwargs["headers"] = {"content-length": 1}
    elif mutation == "transfer":
        kwargs["headers"] = {"transfer-encoding": "chunked"}
    elif mutation == "oversize":
        kwargs["body"] = b"x" * (probe.MAX_REQUEST_BYTES + 1)
    elif mutation == "malformed":
        kwargs["body"] = b"not JSON"
    elif mutation == "stream":
        data["stream"] = True
    elif mutation == "model":
        data["model"] = "real-model"
    elif mutation == "messages":
        data["messages"] = "invalid"
    capture = probe.ScriptedCapture(probe.scripts("H30"))
    assert asyncio.run(capture.handle(FakeRequest(data=data, **kwargs))).status_code == 400
    assert capture.position == 0
    assert capture.violations


def test_script_exhaustion_cannot_silently_fall_back(fake_httpx):
    capture = probe.ScriptedCapture([probe.FINAL])
    assert asyncio.run(capture.handle(FakeRequest())).status_code == 200
    assert asyncio.run(capture.handle(FakeRequest())).status_code == 400
    assert capture.position == 1
    assert "Script exhausted" in capture.violations[0]


@pytest.mark.parametrize("script", [[], None, [{}], [{"role": "user", "content": "bad"}],
                                   [{"role": "assistant", "content": float("nan")}]] )
def test_invalid_scripts_refused(script):
    with pytest.raises((ValueError, TypeError)):
        probe.ScriptedCapture(script)


def test_sdk_client_uses_only_mock_transport_and_preserves_it_through_compiler_copies(monkeypatch, fake_httpx):
    module = ModuleType("openai")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    module.AsyncOpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    capture = probe.ScriptedCapture(probe.scripts("H30"))
    client, http = asyncio.run(probe.create_client(capture))
    assert client.kwargs["base_url"] == probe.BASE_URL
    assert client.kwargs["api_key"] == "H30_SCRIPTED_DUMMY"
    assert client.kwargs["max_retries"] == 0
    assert client.kwargs["http_client"] is http
    assert client._platform == "Unknown"
    assert http.kwargs["transport"].handler == capture.handle
    assert http.kwargs["trust_env"] is False
    assert http.kwargs["follow_redirects"] is False
    assert copy.deepcopy(client) is client
    asyncio.run(http.aclose())
    assert http.closed


@pytest.mark.parametrize("mutation", ["guard", "transport", "observer", "escaped", "port", "connection", "listener",
                                     "script_number", "status", "delivery", "body_sha", "body_text", "body_json", "response_script"])
def test_infrastructure_validation_rejects_invalid_evidence_without_using_prompt_prediction(fake_httpx, mutation):
    capture, observation, guard = capture_requests(fake_httpx)
    escaped = None
    if mutation == "guard":
        guard.violations.append("refused")
    elif mutation == "transport":
        capture.violations.append("refused")
    elif mutation == "observer":
        observation["observer_errors"].append("failed")
    elif mutation == "escaped":
        escaped = {"class": "RuntimeError"}
    elif mutation == "port":
        guard.port = 8765
    elif mutation == "connection":
        guard.connections.append({"host": "127.0.0.1", "port": 8765})
    elif mutation == "listener":
        guard.bindings.append({"purpose": "scripted_listener"})
    elif mutation == "script_number":
        capture.calls[0]["script_number"] = 2
    elif mutation == "status":
        capture.calls[0]["response"]["status"] = 500
    elif mutation == "delivery":
        capture.calls[0]["response"]["delivery"] = "sent_over_socket"
    elif mutation == "body_sha":
        capture.calls[0]["request"]["body_sha256"] = "0" * 64
    elif mutation == "body_text":
        capture.calls[0]["request"]["body_text"] = "changed"
    elif mutation == "body_json":
        capture.calls[0]["request"]["json"]["model"] = "changed"
    elif mutation == "response_script":
        capture.script[0]["content"] = "changed"
    assert probe.infrastructure_errors(capture, observation, guard, escaped)


def test_reviewed_import_feature_bind_is_permitted_evidence_and_empty_requests_fail(fake_httpx):
    capture, observation, guard = capture_requests(fake_httpx)
    guard.bindings.append({"purpose": "urllib3_ipv6_detection"})
    assert probe.infrastructure_errors(capture, observation, guard, None) == []
    capture.calls.clear()
    assert probe.infrastructure_errors(capture, observation, guard, None)


@pytest.mark.parametrize("content,expected", [
    ("Synthetic task", "Synthetic task"),
    ([{"type": "text", "text": "Synthetic "}, {"type": "text", "text": "task"}], "Synthetic task"),
])
def test_first_user_prompt_comes_from_actual_first_serialized_request(content, expected):
    calls = [{"request": {"json": {"messages": [{"role": "system", "content": "policy"},
                                                 {"role": "user", "content": content}]}}}]
    assert probe.first_user_prompt(calls) == expected


@pytest.mark.parametrize("messages", [[], [{"role": "assistant", "content": "none"}],
                                     [{"role": "user", "content": "one"}, {"role": "user", "content": "two"}],
                                     [{"role": "user", "content": None}],
                                     [{"role": "user", "content": [{"type": "image_url", "image_url": "bad"}]}]])
def test_malformed_or_ambiguous_first_user_prompt_is_refused(messages):
    with pytest.raises(ValueError):
        probe.first_user_prompt([{"request": {"json": {"messages": messages}}}])


@pytest.mark.parametrize("case", probe.CASES)
def test_patch_controls_and_source_predictions_accept_coherent_evidence(case):
    result = valid_result(case)
    assert probe.patch_control_ok(result)
    assert probe.source_prediction_matches(result)


@pytest.mark.parametrize("mutation", ["infrastructure", "error", "request_count", "llm_count", "tool_count", "submitted_flag",
                                     "submitted_patch", "returned_patch", "workspace_diff", "workspace_content", "tool_sequence",
                                     "tool_error", "original_exception", "escaped_exception", "fallback"])
def test_patch_control_rejects_failed_runner_behaviour_independently_of_advertisement(mutation):
    result = valid_result("H30")
    if mutation == "infrastructure":
        result["infrastructure_ok"] = False
    elif mutation == "error":
        result["agent_error"] = "failed"
    elif mutation == "request_count":
        result["http_request_count"] = 2
    elif mutation == "llm_count":
        result["context"]["llm_calls_used"] = 2
    elif mutation == "tool_count":
        result["context"]["tool_calls_used"] = 2
    elif mutation == "submitted_flag":
        result["context"]["patch_submitted"] = False
    elif mutation == "submitted_patch":
        result["context"]["submitted_patch"] = "changed"
    elif mutation == "returned_patch":
        result["agent_patch"] = "changed"
    elif mutation == "workspace_diff":
        result["workspace_observation"]["workspace"]["diff"] = "changed"
    elif mutation == "workspace_content":
        result["workspace_observation"]["workspace"]["app_content"] = "changed"
    elif mutation == "tool_sequence":
        result["tool_responses"][0]["tool"] = probe.GRAPH_TOOLS[0]
    elif mutation == "tool_error":
        result["tool_responses"][0]["result"]["status"] = "error"
    elif mutation == "original_exception":
        result["original_exceptions"].append({"class": "RuntimeError"})
    elif mutation == "escaped_exception":
        result["escaped_exception"] = {"class": "RuntimeError"}
    elif mutation == "fallback":
        result["fallback_extraction_ran"] = True
    assert not probe.patch_control_ok(result)


@pytest.mark.parametrize("mutation", ["block_absent", "block_unexpected", "missing_advertised_name", "extra_schema",
                                     "missing_schema", "duplicate_schema", "missing_request", "patch_control"])
def test_source_predictions_do_not_convert_counterevidence_into_pass(mutation):
    case = "ABSENT" if mutation == "block_unexpected" else "H30"
    result = valid_result(case)
    if mutation == "block_absent":
        result["prompt_advertises_graph_tools"] = False
    elif mutation == "block_unexpected":
        result["prompt_advertises_graph_tools"] = True
    elif mutation == "missing_advertised_name":
        result["initial_prompt"] = probe.HEADING
    elif mutation == "extra_schema":
        result["serialized_tool_names"][0].append(probe.GRAPH_TOOLS[0])
    elif mutation == "missing_schema":
        result["serialized_tool_names"][0].pop()
    elif mutation == "duplicate_schema":
        result["serialized_tool_names"][0].append("write_file")
    elif mutation == "missing_request":
        result["serialized_tool_names"].pop()
    elif mutation == "patch_control":
        result["patch_control_pass"] = False
    assert not probe.source_prediction_matches(result)


def test_summary_distinguishes_conditional_independence_from_literal_existence_hypothesis():
    results = [valid_result(case) for case in probe.CASES]
    summary = probe.summarize(results)
    assert summary["evidence_valid"]
    assert summary["source_predicate_prediction_matches"]
    assert summary["literal_file_existence_counterexample"]
    assert summary["declaration_independence_observed"]
    assert summary["matrix_status_changed"] is False
    assert "PASS" in summary["interpretation_boundary"]  # Explicitly says no unconditional PASS is inferred.


@pytest.mark.parametrize("mutation", ["incomplete", "order", "invalid", "failed_control", "threshold_advertised",
                                     "threshold_missing", "threshold_wrong_size", "different_prompt", "omitted_schema_leak",
                                     "declared_schema_missing"])
def test_summary_requires_exact_comparable_runtime_evidence(mutation):
    results = [valid_result(case) for case in probe.CASES]
    if mutation == "incomplete":
        results.pop()
    elif mutation == "order":
        results.reverse()
    elif mutation == "invalid":
        results[0]["infrastructure_ok"] = False
    elif mutation == "failed_control":
        results[0]["patch_control_pass"] = False
    elif mutation == "threshold_advertised":
        results[3]["prompt_advertises_graph_tools"] = True
    elif mutation == "threshold_missing":
        results[3]["graph_fixture"] = []
    elif mutation == "threshold_wrong_size":
        results[3]["graph_fixture"][0]["bytes"] = 99
    elif mutation == "different_prompt":
        results[-1]["initial_prompt"] += "Different task"
    elif mutation == "omitted_schema_leak":
        results[-2]["serialized_tool_names"][0].append(probe.GRAPH_TOOLS[0])
    elif mutation == "declared_schema_missing":
        results[-1]["serialized_tool_names"][0].remove(probe.GRAPH_TOOLS[0])
    summary = probe.summarize(results)
    if mutation in {"incomplete", "order", "invalid", "failed_control"}:
        assert not summary["evidence_valid"]
        assert not summary["literal_file_existence_counterexample"]
        assert not summary["declaration_independence_observed"]
    elif mutation.startswith("threshold_"):
        assert not summary["literal_file_existence_counterexample"]
    else:
        assert not summary["declaration_independence_observed"]
    assert summary["matrix_status_changed"] is False


def test_inventory_records_final_children_and_excludes_only_itself(tmp_path):
    child = tmp_path / "H30"
    child.mkdir()
    (child / "result.json").write_text("{}\n")
    probe.write_inventory(child)
    (tmp_path / "summary.json").write_text("{}\n")
    probe.write_inventory(tmp_path)
    inventory = json.loads((tmp_path / "artifact_inventory.json").read_text())
    assert set(inventory) == {"H30/result.json", "H30/artifact_inventory.json", "summary.json"}
    for name, metadata in inventory.items():
        data = (tmp_path / name).read_bytes()
        assert metadata == {"bytes": len(data), "sha256": probe.dispatch.digest(data)}


def test_repository_admission_failure_prevents_optional_imports_and_runtime_side_effects(monkeypatch, tmp_path):
    def refuse(repo, *, profile):
        assert repo == probe.REPO
        assert profile == "graph"
        raise probe.safety.ProbeRefused("fixed baseline mismatch")

    def unexpected(*args, **kwargs):
        pytest.fail("Environment verification and fixture allocation must follow repository admission")

    monkeypatch.setattr(probe.safety, "check_repository", refuse)
    monkeypatch.setattr(probe.dispatch, "verify_environment", unexpected)
    monkeypatch.setattr(probe.tempfile, "mkdtemp", unexpected)
    before = set(sys.modules)
    assert probe.main(["--h23-cpu-tmp", str(tmp_path)]) == 2
    assert set(sys.modules) == before
    assert list(tmp_path.iterdir()) == []


def test_missing_harness_argument_is_refused_before_repository_admission(monkeypatch):
    monkeypatch.delenv("H23_CPU_TMP", raising=False)
    monkeypatch.setattr(probe.safety, "check_repository", lambda *args, **kwargs: pytest.fail("No repository admission"))
    assert probe.main([]) == 2


def fake_git(monkeypatch, repo, *, head=None, status=""):
    replies = {("rev-parse", "--show-toplevel"): str(repo) + "\n",
               ("rev-parse", "HEAD"): (head or probe.safety.GRAPH_BASELINE) + "\n",
               ("status", "--porcelain=v1", "-z", "--untracked-files=all"): status}
    calls = []

    def invoke(candidate, *args):
        assert candidate == repo
        calls.append(args)
        return replies[args]

    monkeypatch.setattr(probe.safety, "git", invoke)
    return calls


def test_graph_admission_has_separate_fixed_baseline_and_exact_change_surface(monkeypatch, tmp_path):
    assert probe.safety.GRAPH_BASELINE == "81ac5b150c719cafc671b884dce7f525219e4ea0"
    assert probe.safety.GRAPH_PROBE_FILES == frozenset({
        "tools/harness_cert/_probe_safety.py", "tools/harness_cert/run_h30.py",
        "tools/harness_cert/README.md", "tools/harness_cert/H30_DESIGN.md",
        "tests/test_harness_graph_probe.py"})
    assert probe.safety.BASELINE == "ea5b857487ae9e146e94a88e108962742fcbdef8"
    assert probe.safety.BUDGET_BASELINE == "12c319fa8bcf5b313175f80cee3ce06a77a59619"
    calls = fake_git(monkeypatch, tmp_path)
    assert probe.safety.check_repository(tmp_path, profile="graph") == []
    assert calls == [("rev-parse", "--show-toplevel"), ("rev-parse", "HEAD"),
                     ("status", "--porcelain=v1", "-z", "--untracked-files=all")]


@pytest.mark.parametrize("head", [probe.safety.BASELINE, probe.safety.BUDGET_BASELINE, "0" * 40,
                                   "81ac5b150c719cafc671b884dce7f525219e4ea0-extra"])
def test_graph_profile_refuses_other_baselines_before_inspecting_changes(monkeypatch, tmp_path, head):
    calls = fake_git(monkeypatch, tmp_path, head=head)
    with pytest.raises(probe.safety.ProbeRefused, match="reviewed baseline"):
        probe.safety.check_repository(tmp_path, profile="graph")
    assert calls == [("rev-parse", "--show-toplevel"), ("rev-parse", "HEAD")]


@pytest.mark.parametrize("profile", ["dispatch", "budget"])
def test_historical_profiles_still_refuse_graph_baseline(monkeypatch, tmp_path, profile):
    fake_git(monkeypatch, tmp_path)
    with pytest.raises(probe.safety.ProbeRefused, match="reviewed baseline"):
        probe.safety.check_repository(tmp_path, profile=profile)


@pytest.mark.parametrize("status", ["??", " M", "M ", "MM", "A ", "AM"])
def test_graph_profile_admits_only_its_exact_registered_paths(monkeypatch, tmp_path, status):
    paths = sorted(probe.safety.GRAPH_PROBE_FILES)
    fake_git(monkeypatch, tmp_path, status="".join(f"{status} {path}\0" for path in paths))
    assert probe.safety.check_repository(tmp_path, profile="graph") == paths


@pytest.mark.parametrize("path", [
    "tools/harness_cert/run_h04_h05_h18.py", "tools/harness_cert/run_h13_h14_h29.py",
    "tools/harness_cert/_scripted_loopback.py", "tools/harness_cert/run_h30.py.evil",
    "harness_cert/matrix.yaml", "harness_cert/reports/H30_PACKAGING_READINESS_2026-10-04.md",
    "harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md", "README.md",
    "agents/baseline_v0_official/agent.yaml", "tests/test_harness_budget_admission.py",
])
def test_graph_profile_refuses_other_tranches_agents_reports_and_matrix(monkeypatch, tmp_path, path):
    fake_git(monkeypatch, tmp_path, status=f" M {path}\0")
    with pytest.raises(probe.safety.ProbeRefused, match="dirty outside the probe"):
        probe.safety.check_repository(tmp_path, profile="graph")


@pytest.mark.parametrize("record", [" D tools/harness_cert/run_h30.py\0", "UU tools/harness_cert/run_h30.py\0",
                                     "R  tools/harness_cert/run_h30.py\0tools/old.py\0", "X\0"])
def test_graph_profile_refuses_deletions_conflicts_renames_and_malformed_status(monkeypatch, tmp_path, record):
    fake_git(monkeypatch, tmp_path, status=record)
    with pytest.raises(probe.safety.ProbeRefused, match="unreviewed Git change"):
        probe.safety.check_repository(tmp_path, profile="graph")
