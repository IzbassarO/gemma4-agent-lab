"""Deterministic evidence checks using fakes; never execute optional harness code."""

import asyncio
import copy
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tools.harness_cert import run_h13_h14_h29 as probe


TOOL_SEQUENCE = {
    "CONTROL": ["write_file", "submit_patch"],
    "H13": ["write_file"],
    "H14": ["write_file"] * 4,
    "H29": ["write_file", "write_file", "get_status", "submit_patch"],
    "H29_BOUNDARY": ["write_file", "write_file"],
}
REQUEST_COUNTS = {"CONTROL": 3, "H13": 2, "H14": 5, "H29": 5, "H29_BOUNDARY": 3}
COUNTED = {"CONTROL": 1, "H13": 1, "H14": 3, "H29": 2, "H29_BOUNDARY": 2}
ATTEMPTED = {"CONTROL": 2, "H13": 1, "H14": 4, "H29": 4, "H29_BOUNDARY": 2}
SUCCESSFUL = {"CONTROL": 2, "H13": 1, "H14": 3, "H29": 4, "H29_BOUNDARY": 2}
RUNNER_PATH = Path("/synthetic/site-packages/swegemma/harness/agent_runner.py")
FALLBACK_MESSAGE = "Task synthetic: Captured unsubmitted working tree modifications as fallback patch (134 bytes)"


def responses_for(case):
    responses = []
    for index, name in enumerate(TOOL_SEQUENCE[case]):
        payload = {"status": "ok"}
        if case == "H14" and index == 3:
            payload = {"status": "error", "error_type": "BudgetExceeded",
                       "error_message": "Tool call budget exhausted (3 calls)"}
        elif name == "get_status":
            payload.update(tool_calls_used=2, tool_calls_remaining=0, max_tool_calls=2,
                           patch_submitted=False, patch_size=0)
        elif name == "submit_patch":
            payload.update(patch_size=len(probe.dispatch.expected_patch()), files_changed=1)
        responses.append({"tool": name, "result": payload})
    return responses


def valid_result(case):
    patch = probe.dispatch.expected_patch()
    submitted = case in {"CONTROL", "H29"}
    error = None
    if case == "H13":
        error = "Agent exceeded session timeout (0.05 min)"
    elif case in {"H14", "H29_BOUNDARY"}:
        error = f"Agent exceeded tool call budget ({3 if case == 'H14' else 2} calls)"
    return {
        "case": case, "infrastructure_ok": True,
        "http_request_count": REQUEST_COUNTS[case],
        "context": {"tool_calls_used": COUNTED[case],
                    "llm_calls_used": 1 if case == "H13" else REQUEST_COUNTS[case],
                    "patch_submitted": submitted, "submitted_patch": patch if submitted else None},
        "attempted_tool_calls": ATTEMPTED[case], "successful_tool_responses": SUCCESSFUL[case],
        "agent_patch": patch, "agent_error": error,
        "workspace_observation": {"workspace": {"diff": patch, "app_content": probe.dispatch.CHANGED},
                                  "observer_errors": []},
        "original_exceptions": [], "escaped_exception": None,
        "recovered_exceptions": [{"class": "builtins.TimeoutError", "message": "", "line": 746,
                                  "traceback": "TimeoutError"}] if case == "H13" else [],
        "fallback_extraction_ran": not submitted,
        "tool_responses": responses_for(case),
        "verification": {"result": {"resolved": True, "test_exit_code": 0}}
        if case in {"CONTROL", "H13", "H14"} else None,
    }


def record(*, source=RUNNER_PATH, line=746, name="swegemma.harness.agent_runner", message="Task synthetic agent timed out.", exc_info=None):
    return logging.LogRecord(name, logging.WARNING, str(source), line, message, (), exc_info)


def wire_calls(case):
    script = probe.scripts(case)
    declared = probe.declared_tools(case)
    calls = [{"script_number": index + 1,
              "request": {"body_complete": True, "json": {"tools": [
                  {"type": "function", "function": {"name": name}} for name in declared]}},
              "response": {"status": 200, "delivery": "sent",
                           "json": {"choices": [{"message": copy.deepcopy(message)}]}}}
             for index, message in enumerate(script[:REQUEST_COUNTS[case]])]
    recovered = []
    if case == "H13":
        calls[1]["response"]["delivery"] = "discarded_after_release"
        recovered = [{"class": "builtins.TimeoutError"}]
    return SimpleNamespace(case=case, calls=calls, script=script, declared=declared,
                           observation={"workspace": {"diff": "synthetic"}, "observer_errors": []},
                           guard=SimpleNamespace(violations=[]),
                           http=SimpleNamespace(probe_guard_violations=[]), escaped=None, recovered=recovered)


def infra(evidence):
    return probe.infrastructure_errors(evidence.case, evidence.calls, evidence.script, evidence.declared,
                                       evidence.observation, evidence.guard, evidence.http,
                                       evidence.escaped, evidence.recovered)


def install_module(monkeypatch, name, **exports):
    module = ModuleType(name)
    for key, value in exports.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_registered_cases_keep_control_first_and_boundary_last():
    assert probe.CASES == ("CONTROL", "H13", "H14", "H29", "H29_BOUNDARY")
    assert probe.EXPECTED_REQUESTS == REQUEST_COUNTS
    assert probe.EXPECTED_COUNTED == COUNTED


@pytest.mark.parametrize("case", probe.CASES)
def test_declared_tools_and_budgets_are_minimal_and_scripted(case):
    expected = ["write_file", "get_status", "submit_patch"] if case.startswith("H29") else ["write_file", "submit_patch"]
    assert probe.declared_tools(case) == expected
    assert probe.budgets(case) == {
        "max_time_minutes": 0.05 if case == "H13" else 1,
        "max_tool_calls": 2 if case.startswith("H29") else 3, "max_turns": 8,
    }
    script = probe.scripts(case)
    requested = [call["function"]["name"] for message in script for call in message.get("tool_calls", [])]
    expected_calls = list(TOOL_SEQUENCE[case])
    if case == "H29_BOUNDARY":
        expected_calls += ["get_status", "submit_patch"]
    assert requested == expected_calls
    assert script[-1]["role"] == "assistant" and script[-1]["content"]
    assert len({call["id"] for message in script for call in message.get("tool_calls", [])}) == len(requested)
    for message in script:
        for call in message.get("tool_calls", []):
            assert call["function"]["name"] in expected
            arguments = json.loads(call["function"]["arguments"])
            if call["function"]["name"] == "write_file":
                assert arguments["filepath"] == "app.py"
                expected_content = probe.dispatch.INITIAL if call["id"] == "rejected_write" else probe.dispatch.CHANGED
                assert arguments["content"] == expected_content
            else:
                assert arguments == {}


@pytest.mark.parametrize("name", ["H04", "H05", "H18", "H30", "H28", "unknown", ""])
def test_unregistered_cases_refused_by_declaration_script_and_budget(name):
    for function in (probe.declared_tools, probe.scripts, probe.budgets):
        with pytest.raises(ValueError, match="Unknown budget case"):
            function(name)


def test_h29_boundary_has_unused_continuation_after_final_text():
    script = probe.scripts("H29_BOUNDARY")
    assert "tool_calls" not in script[2]
    assert script[3]["tool_calls"][0]["function"]["name"] == "get_status"
    assert script[4]["tool_calls"][0]["function"]["name"] == "submit_patch"
    assert len(script) > REQUEST_COUNTS["H29_BOUNDARY"]


@pytest.mark.parametrize("case", probe.CASES)
def test_predictions_accept_complete_coherent_evidence(case):
    assert probe.behavior_matches(valid_result(case)) is True


@pytest.mark.parametrize("case", probe.CASES)
@pytest.mark.parametrize("mutation", [
    "infrastructure", "http_count", "counted_tools", "llm_count", "attempted_count", "successful_count",
    "returned_patch", "workspace_diff", "workspace_content", "original_exception", "escaped_exception",
    "submission_flag", "submission_state", "fallback", "missing_tool_response", "extra_tool_response",
])
def test_prediction_rejects_inconsistent_counts_state_and_execution(case, mutation):
    result = valid_result(case)
    if mutation == "infrastructure":
        result["infrastructure_ok"] = False
    elif mutation == "http_count":
        result["http_request_count"] += 1
    elif mutation == "counted_tools":
        result["context"]["tool_calls_used"] += 1
    elif mutation == "llm_count":
        result["context"]["llm_calls_used"] += 1
    elif mutation == "attempted_count":
        result["attempted_tool_calls"] += 1
    elif mutation == "successful_count":
        result["successful_tool_responses"] += 1
    elif mutation == "returned_patch":
        result["agent_patch"] = ""
    elif mutation == "workspace_diff":
        result["workspace_observation"]["workspace"]["diff"] = ""
    elif mutation == "workspace_content":
        result["workspace_observation"]["workspace"]["app_content"] = probe.dispatch.INITIAL
    elif mutation == "original_exception":
        result["original_exceptions"] = [{"class": "builtins.ValueError"}]
    elif mutation == "escaped_exception":
        result["escaped_exception"] = {"class": "builtins.RuntimeError"}
    elif mutation == "submission_flag":
        result["context"]["patch_submitted"] = not result["context"]["patch_submitted"]
    elif mutation == "submission_state":
        result["context"]["submitted_patch"] = "unexpected"
    elif mutation == "fallback":
        result["fallback_extraction_ran"] = not result["fallback_extraction_ran"]
    elif mutation == "missing_tool_response":
        result["tool_responses"].pop()
    else:
        result["tool_responses"].append({"tool": "run_command", "result": {"status": "ok"}})
    assert probe.behavior_matches(result) is False


@pytest.mark.parametrize("case", probe.CASES)
def test_prediction_requires_exact_expected_agent_error(case):
    result = valid_result(case)
    result["agent_error"] = "different termination path"
    assert probe.behavior_matches(result) is False


@pytest.mark.parametrize("case", ["CONTROL", "H13", "H14"])
@pytest.mark.parametrize("mutation", ["missing", "unresolved", "nonzero_exit"])
def test_recovery_prediction_requires_actual_positive_verification(case, mutation):
    result = valid_result(case)
    if mutation == "missing":
        result["verification"] = None
    elif mutation == "unresolved":
        result["verification"]["result"]["resolved"] = False
    else:
        result["verification"]["result"]["test_exit_code"] = 1
    assert probe.behavior_matches(result) is False


@pytest.mark.parametrize("mutation", ["missing", "wrong_class", "multiple"])
def test_h13_requires_one_real_recovered_timeout(mutation):
    result = valid_result("H13")
    if mutation == "missing":
        result["recovered_exceptions"] = []
    elif mutation == "wrong_class":
        result["recovered_exceptions"][0]["class"] = "builtins.ValueError"
    else:
        result["recovered_exceptions"] *= 2
    assert probe.behavior_matches(result) is False


@pytest.mark.parametrize("case", ["CONTROL", "H14", "H29", "H29_BOUNDARY"])
def test_non_timeout_cases_reject_recovered_timeout(case):
    result = valid_result(case)
    result["recovered_exceptions"] = [{"class": "builtins.TimeoutError"}]
    assert probe.behavior_matches(result) is False


def test_h14_requires_exact_budget_response_without_rejected_write_execution():
    result = valid_result("H14")
    result["tool_responses"][-1]["result"]["error_message"] = "Turn budget exhausted (8 turns)"
    assert probe.behavior_matches(result) is False
    result = valid_result("H14")
    result["tool_responses"][-1]["result"] = {"status": "ok"}
    assert probe.behavior_matches(result) is False


@pytest.mark.parametrize("field,value", [("status", "error"), ("tool_calls_used", 1), ("tool_calls_remaining", 1)])
def test_h29_requires_status_observed_at_exact_exhaustion(field, value):
    result = valid_result("H29")
    result["tool_responses"][2]["result"][field] = value
    assert probe.behavior_matches(result) is False


@pytest.mark.parametrize("case,index", [("CONTROL", 1), ("H29", 3)])
def test_explicit_submission_requires_successful_submit_response(case, index):
    result = valid_result(case)
    result["tool_responses"][index]["result"]["status"] = "error"
    assert probe.behavior_matches(result) is False


def test_timeout_observer_captures_actual_exception_and_original_traceback():
    evidence = probe.RunnerEvidence(RUNNER_PATH)
    try:
        raise TimeoutError("observed session deadline")
    except TimeoutError:
        evidence.emit(record())
    assert len(evidence.recovered) == 1
    found = evidence.recovered[0]
    assert found["class"] == "builtins.TimeoutError"
    assert found["message"] == "observed session deadline" and found["line"] == 746
    assert "test_timeout_observer_captures_actual_exception_and_original_traceback" in found["traceback"]
    assert "TimeoutError: observed session deadline" in found["traceback"]
    assert evidence.records == []


@pytest.mark.parametrize("options", [
    {"name": "unrelated.logger"}, {"source": Path("/other/agent_runner.py")},
    {"line": 587}, {"line": 745}, {"message": "Task synthetic completed normally."},
])
def test_timeout_observer_requires_exact_source_logger_and_recovery_site(options):
    evidence = probe.RunnerEvidence(RUNNER_PATH)
    try:
        raise TimeoutError("real but wrong observation site")
    except TimeoutError:
        evidence.emit(record(**options))
    assert evidence.recovered == []


def test_timeout_observer_does_not_invent_an_exception_outside_except():
    evidence = probe.RunnerEvidence(RUNNER_PATH)
    evidence.emit(record())
    assert evidence.recovered == []
    assert evidence.logs[0]["message"] == "Task synthetic agent timed out."


def test_timeout_observer_preserves_exception_cause_and_fallback_log():
    evidence = probe.RunnerEvidence(RUNNER_PATH)
    try:
        try:
            raise asyncio.CancelledError("cancelled awaited response")
        except asyncio.CancelledError as cancelled:
            raise TimeoutError from cancelled
    except TimeoutError:
        evidence.emit(record())
    evidence.emit(record(line=771, message=FALLBACK_MESSAGE))
    assert "CancelledError: cancelled awaited response" in evidence.recovered[0]["traceback"]
    assert "direct cause" in evidence.recovered[0]["traceback"]
    assert evidence.logs[-1]["message"] == FALLBACK_MESSAGE
    assert evidence.records == []


@pytest.mark.parametrize("case", probe.CASES)
def test_infrastructure_accepts_exact_script_and_intentional_timeout_delivery(case):
    assert infra(wire_calls(case)) == []


@pytest.mark.parametrize("mutation", ["wrong_case", "no_timeout", "wrong_exception", "duplicate_timeout", "wrong_index", "expired_bound"])
def test_held_http_response_requires_exact_h13_recovery(mutation):
    evidence = wire_calls("H13")
    if mutation == "wrong_case":
        evidence.case = "H14"
    elif mutation == "no_timeout":
        evidence.recovered = []
    elif mutation == "wrong_exception":
        evidence.recovered[0]["class"] = "builtins.RuntimeError"
    elif mutation == "duplicate_timeout":
        evidence.recovered *= 2
    elif mutation == "wrong_index":
        evidence.calls[0]["response"]["delivery"] = "discarded_after_release"
        evidence.calls[1]["response"]["delivery"] = "sent"
    else:
        evidence.calls[1]["response"]["delivery"] = "hold_bound_expired"
    assert any("Unexpected scripted response delivery" in error for error in infra(evidence))


@pytest.mark.parametrize("mutation,expected", [
    ("guard", "synthetic parent refusal"), ("http_guard", "synthetic transport refusal"),
    ("escaped", "Exception escaped"), ("no_workspace", "workspace observer"),
    ("observer_error", "workspace observer"), ("no_requests", "No scripted request"),
    ("retry", "Unexpected HTTP"), ("status", "Unexpected HTTP"), ("incomplete", "Unexpected HTTP"),
    ("advertised", "Advertised tools"), ("integrity", "integrity mismatch"),
    ("delivery", "Unexpected scripted response delivery"),
])
def test_infrastructure_refuses_guard_observer_and_transport_failures(mutation, expected):
    evidence = wire_calls("CONTROL")
    if mutation == "guard":
        evidence.guard.violations.append(expected)
    elif mutation == "http_guard":
        evidence.http.probe_guard_violations.append(expected)
    elif mutation == "escaped":
        evidence.escaped = {"class": "builtins.RuntimeError"}
    elif mutation == "no_workspace":
        evidence.observation["workspace"] = None
    elif mutation == "observer_error":
        evidence.observation["observer_errors"].append("missing workspace")
    elif mutation == "no_requests":
        evidence.calls = []
    elif mutation == "retry":
        evidence.calls[1]["script_number"] = 1
    elif mutation == "status":
        evidence.calls[0]["response"]["status"] = 500
    elif mutation == "incomplete":
        evidence.calls[0]["request"]["body_complete"] = False
    elif mutation == "advertised":
        evidence.calls[0]["request"]["json"]["tools"].append({"function": {"name": "bash"}})
    elif mutation == "integrity":
        evidence.calls[0]["response"]["json"]["choices"][0]["message"] = {"role": "assistant", "content": "changed"}
    else:
        evidence.calls[0]["response"]["delivery"] = "withheld"
    assert any(expected in error for error in infra(evidence))


@pytest.mark.parametrize("mutation", ["request_json", "choices", "response_json"])
def test_malformed_wire_evidence_is_invalid_infrastructure_not_an_escaped_validator_error(mutation):
    evidence = wire_calls("CONTROL")
    if mutation == "request_json":
        evidence.calls[0]["request"]["json"] = None
    elif mutation == "choices":
        evidence.calls[0]["response"]["json"]["choices"] = []
    else:
        evidence.calls[0]["response"]["json"] = None
    assert infra(evidence)


@pytest.mark.parametrize("failure,expected_cases", [
    ("control_behavior", ["CONTROL"]), ("control_infrastructure", ["CONTROL"]),
    ("h13_infrastructure", ["CONTROL", "H13"]),
    ("h14_behavior", list(probe.CASES)),
])
def test_run_cases_stops_only_on_invalid_infrastructure_or_failed_control(monkeypatch, tmp_path, capsys, failure, expected_cases):
    executed = []

    async def fake_case(case, *args):
        executed.append(case)
        return {"infrastructure_ok": not ((failure == "control_infrastructure" and case == "CONTROL")
                                          or (failure == "h13_infrastructure" and case == "H13")),
                "control_pass": failure != "control_behavior",
                "prediction_matches": not (failure == "h14_behavior" and case == "H14")}

    monkeypatch.setattr(probe, "run_case", fake_case)
    outputs = {case: tmp_path / case for case in probe.CASES}
    status = asyncio.run(probe.run_cases(tmp_path, outputs, None, None, None, [], None, None, {}, {}))
    assert executed == expected_cases
    assert status == (0 if failure == "h14_behavior" else 2)
    captured = capsys.readouterr()
    if status == 0:
        assert "no matrix status promoted" in captured.out
    else:
        assert "later cases not run" in captured.err


def test_verify_patch_uses_official_verifier_with_dataclass_config_and_preserves_inputs(monkeypatch, tmp_path):
    @dataclass
    class Config:
        results_dir: Path

    config = Config(tmp_path / "old_results")
    output = tmp_path / "outputs"
    output.mkdir()
    calls = []
    observation = {"workspace": {}, "observer_errors": []}

    class Manager:
        def __init__(self, **kwargs):
            assert kwargs["system_site_packages"] is False
            calls.append(("manager", kwargs))

    async def fake_verifier(manager, chosen_config, task, snapshot, **kwargs):
        assert chosen_config is not config and chosen_config.results_dir == output / "positive"
        assert kwargs["agent_patch"] == "synthetic patch" and kwargs["agent_error"] == "budget error"
        assert isinstance(kwargs["start_time"], float) and kwargs["start_time"] > 0
        calls.append(("verify", task, snapshot))
        return SimpleNamespace(model_dump=lambda **kwargs: {"resolved": True, "test_exit_code": 0})

    install_module(monkeypatch, "swegemma.harness.verification", verify_task=fake_verifier)
    install_module(monkeypatch, "swegemma.sandbox.subprocess", SubprocessManager=Manager)
    monkeypatch.setattr(probe, "instrument_verifier", lambda *args: observation)
    result = asyncio.run(probe.verify_patch("positive", "synthetic patch", "budget error", "synthetic task", config,
                                           tmp_path / "snapshot", tmp_path, output, [], None, {}))
    assert config.results_dir == tmp_path / "old_results"
    assert result["evaluator_hydration_invoked"] is False
    assert result["input_agent_error"] == "budget error"
    assert result["input_patch_sha256"] == probe.dispatch.digest(b"synthetic patch")
    assert result["observation"] is observation
    assert json.loads((output / "positive/verification.json").read_text()) == result
    assert calls[-1][0] == "verify"


def test_main_persists_static_baselines_source_hashes_and_synthetic_only_preflight(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cpu = tmp_path / "h23-cpu"
    cpu.mkdir()
    site = cpu / "site-packages"
    site.mkdir()
    installed_sources = (
        "swegemma/harness/agent_runner.py", "swegemma/harness/verification.py", "swegemma/evaluate.py",
        "swegemma/context.py", "swegemma/tools/base.py", "swegemma/tools/execution.py",
        "swegemma/sandbox/subprocess.py", "adk_eval_core/sandbox/subprocess_sandbox.py",
        "adk_submission/builders/llm.py", "adk_submission/resolvers/tools.py",
        "google/adk/flows/llm_flows/base_llm_flow.py", "google/adk/tools/function_tool.py",
    )
    for name in installed_sources:
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Pinned installed source stand-in\n")
    names = probe.BUDGET_PROBE_FILES | {"tools/harness_cert/run_h04_h05_h18.py", "tools/harness_cert/__init__.py"}
    for name in names:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Synthetic source identity\n")
    monkeypatch.setattr(probe, "REPO", repo)
    # The fake launch must not alter pytest's or the operator's environment.
    monkeypatch.setattr(probe.os, "environ", dict(probe.os.environ))
    monkeypatch.setenv("GEMMA4_DATASET_ROOT", str(tmp_path / "forbidden_dataset"))
    monkeypatch.setattr(probe, "check_repository", lambda candidate, **kwargs: [] if candidate == repo and kwargs == {"profile": "budget"} else pytest.fail("wrong repository profile"))
    monkeypatch.setattr(probe, "git", lambda *args: "ignored-result\n")
    monkeypatch.setattr(probe.dispatch, "verify_environment", lambda *args: {"site_packages": str(site), "versions": {"swegemma": "0.2.7"}})
    monkeypatch.setattr(probe.dispatch, "prepare_environment", lambda *args: None)
    monkeypatch.setattr(probe.dispatch, "isolate_import_paths", lambda *args: None)
    monkeypatch.setattr(probe.dispatch, "smoke_auth_import", lambda: {"result": "AUTHLIB_IMPORT_OK"})
    monkeypatch.setattr(probe, "prepare_support", lambda *args: {"root": "synthetic pytest support"})
    monkeypatch.setattr(probe, "check_wheels", lambda *args: [{"top_level_wheels": []}])
    monkeypatch.setattr(probe, "discover_system_runtime_reads", lambda: SimpleNamespace(zoneinfo_root=None))
    monkeypatch.setattr(probe, "IPv6FeatureProbe", SimpleNamespace(from_site=lambda candidate: SimpleNamespace(source=candidate / "urllib3/util/connection.py")))
    monkeypatch.setattr(probe.sys, "addaudithook", lambda *args: None)
    monkeypatch.setattr(probe.os, "chdir", lambda *args: None)
    install_module(monkeypatch, "litellm")
    guards = []

    class Guard:
        def __init__(self, candidate, root, outputs, protected, **kwargs):
            self.run_root = root
            self.audit = lambda *args: None
            self.violations = []
            self.protected = protected
            guards.append(self)

    class Server:
        origin = "http://127.0.0.1:31337"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fixture(root, guard):
        snapshot = root / "synthetic.tar.gz"
        snapshot.write_bytes(b"Synthetic snapshot")
        return snapshot, "synthetic_commit"

    def fake_cases(root, outputs, snapshot, commit, candidate_site, wheels, guard, server, support, admission):
        async def complete():
            assert set(outputs) == set(probe.CASES)
            assert len(set(outputs.values())) == len(probe.CASES)
            assert outputs["CONTROL"] == outputs["H13"] / "CONTROL"
            assert outputs["H29_BOUNDARY"] == outputs["H29"] / "H29_BOUNDARY"
            assert all(output.is_dir() for output in outputs.values())
            assert admission["git_head"] == "12c319fa8bcf5b313175f80cee3ce06a77a59619"
            assert admission["previous_dispatch_baseline"] == "ea5b857487ae9e146e94a88e108962742fcbdef8"
            assert set(admission["probe_source_sha256"]) == names
            assert set(admission["environment"]["relevant_installed_source_sha256"]) == set(installed_sources)
            assert admission["system_site_packages"] is False
            assert admission["competition_content_access"] is False
            assert admission["process_tree_globally_firewalled"] is False
            assert admission["pytest_plugin_autoload"] is False
            assert admission["fixture"]["test_sha256"] == probe.dispatch.digest(probe.TEST_CONTENT.encode())
            assert admission["fixture"]["expected_patch_sha256"] == probe.dispatch.digest(probe.dispatch.expected_patch().encode())
            assert tmp_path / "forbidden_dataset" in guard.protected
            for output in outputs.values():
                assert json.loads((output / "preflight.json").read_text()) == admission
            return 0
        return complete()

    monkeypatch.setattr(probe, "ParentGuard", Guard)
    monkeypatch.setattr(probe, "ScriptedServer", Server)
    monkeypatch.setattr(probe, "synthetic_fixture", fixture)
    monkeypatch.setattr(probe, "run_cases", fake_cases)
    assert probe.main(["--h23-cpu-tmp", str(cpu)]) == 0
    assert len(guards) == 1
    assert not (repo / "harness_cert/matrix.yaml").exists()


@pytest.mark.parametrize("case", probe.CASES)
def test_run_case_preserves_raw_artifacts_counters_and_scope_without_optional_runtime(monkeypatch, tmp_path, case):
    """Exercise artifact wiring while every optional runtime boundary is faked."""
    output = tmp_path / "output"
    output.mkdir()
    site = tmp_path / "site-packages"
    source = site / "swegemma/harness/agent_runner.py"
    expected = valid_result(case)
    clients = []
    contexts = []
    verifications = []
    observation = expected["workspace_observation"]
    observation.update(commands=[], copies=[], observer_errors=[])

    class Registry:
        def register(self, name, model):
            assert name == "scripted" and model.model == "openai/h23-scripted"

    class Config(SimpleNamespace):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.budget = SimpleNamespace(tool_calls=kwargs["max_tool_calls"], turns=kwargs["max_turns"],
                                          time_minutes=kwargs["max_time_minutes"])
            self.harness = SimpleNamespace(command_timeout_seconds=kwargs["timeout_seconds"])

    class Context:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.patch_submitted = False
            self.submitted_patch = None
            self.tool_calls_used = 0
            self.llm_calls_used = 0
            contexts.append(self)

        def create_tools(self):
            return {name: object() for name in probe.declared_tools(case)}

    class Manager:
        def __init__(self, **kwargs):
            assert kwargs["system_site_packages"] is False
            assert kwargs["base_dir"].is_relative_to(tmp_path)

    class Trace:
        def to_dict(self, **kwargs):
            assert kwargs == {"format": "legacy"}
            return {"entries": [{"type": "tool_response", "tool": response["tool"],
                                 "result": json.dumps(response["result"])}
                                for response in responses_for(case)]}

    class Client:
        closed = False

        async def close(self):
            self.closed = True

    class Http:
        probe_guard_violations = []
        closed = False

        async def aclose(self):
            self.closed = True

    class Server:
        base_url = "http://127.0.0.1:31337/v1"

        def __init__(self):
            self.calls = []
            self.withheld = None
            self.released = False

        def set_script(self, script):
            self.script = copy.deepcopy(script)

        def withhold_response(self, number):
            assert case == "H13" and number == 2
            self.withheld = number

        def release_withheld_response(self):
            self.released = True
            return True

    server = Server()

    async def create_client(url):
        assert url == server.base_url
        client, http = Client(), Http()
        clients.extend((client, http))
        return client, http

    async def official_agent(manager, config, task, snapshot, *, context):
        assert task.FAIL_TO_PASS == ("test_marker.py::test_marker",)
        assert not hasattr(task, "test_files")
        for name, value in expected["context"].items():
            setattr(context, name, value)
        server.calls.extend(wire_calls(case).calls)
        logger = logging.getLogger("swegemma.harness.agent_runner")
        if case == "H13":
            try:
                raise TimeoutError
            except TimeoutError:
                logger.handle(record(source=source))
        if case in {"H13", "H14", "H29_BOUNDARY"}:
            logger.handle(record(source=source, line=771, message=FALLBACK_MESSAGE))
        return expected["agent_patch"], expected["agent_error"], Trace()

    async def verification(label, patch, error, task, config, snapshot, root, chosen_output, *args):
        assert chosen_output == output
        assert root.is_relative_to(tmp_path)
        negative = label == "verification_negative"
        assert patch == ("" if negative else expected["agent_patch"])
        assert error == (None if negative else expected["agent_error"])
        verifications.append(label)
        return {"result": {"resolved": not negative, "test_exit_code": 1 if negative else 0},
                "observation": {"observer_errors": []}}

    install_module(monkeypatch, "adk_submission", ModelRegistry=Registry)
    install_module(monkeypatch, "google.adk.models.lite_llm", LiteLlm=lambda **kwargs: SimpleNamespace(**kwargs))
    install_module(monkeypatch, "swegemma.config", EvalConfig=Config)
    install_module(monkeypatch, "swegemma.context", SwegemmaContext=Context)
    install_module(monkeypatch, "swegemma.harness.agent_runner", run_agent_sandbox=official_agent)
    install_module(monkeypatch, "swegemma.models.task", Task=lambda **kwargs: SimpleNamespace(**kwargs))
    install_module(monkeypatch, "swegemma.sandbox.subprocess", SubprocessManager=Manager)
    monkeypatch.setattr(probe, "create_client", create_client)
    monkeypatch.setattr(probe, "verify_patch", verification)
    monkeypatch.setattr(probe.dispatch, "instrument_manager", lambda *args: observation)
    monkeypatch.setattr(probe.dispatch, "sandbox_commands", lambda *args: set())
    guard = SimpleNamespace(violations=[], connections=[], bindings=[], processes=[])
    admission = {"run_id": "synthetic-test-run", "git_head": probe.BUDGET_BASELINE,
                 "previous_dispatch_baseline": probe.BASELINE}
    result = asyncio.run(probe.run_case(case, tmp_path, output, tmp_path / "snapshot", "synthetic_commit",
                                       site, [], guard, server, {}, admission))
    assert result["prediction_matches"] is True
    if case == "CONTROL":
        assert result["control_pass"] is True
        assert verifications == ["verification_negative", "verification_positive"]
    elif case in {"H13", "H14"}:
        assert verifications == ["verification_positive"]
    else:
        assert verifications == []
    assert result["attempted_tool_calls"] == ATTEMPTED[case]
    assert result["successful_tool_responses"] == SUCCESSFUL[case]
    assert result["run_id"] == "synthetic-test-run"
    assert result["competition_content_access"] is False
    assert "source-read only" in result["evaluator_orchestration"]
    assert "no hidden-scorer inference" in result["scope"]
    assert result["process_tree_globally_firewalled"] is False
    assert all(client.closed for client in clients) and server.released
    assert server.withheld == (2 if case == "H13" else None)
    assert len(contexts) == 1
    assert (output / "agent.patch").read_text() == expected["agent_patch"]
    assert (output / "workspace.patch").read_text() == expected["agent_patch"]
    assert (output / "workspace_app.py").read_text() == probe.dispatch.CHANGED
    assert (output / "submitted.patch").read_text() == (expected["agent_patch"] if case in {"CONTROL", "H29"} else "")
    assert json.loads((output / "result.json").read_text()) == result
    assert json.loads((output / "preflight.json").read_text())["git_head"] == probe.BUDGET_BASELINE
    inventory = json.loads((output / "artifact_inventory.json").read_text())
    assert {"preflight.json", "script.json", "result.json", "trace.json", "http.json", "exceptions.json",
            "agent.patch", "submitted.patch", "workspace.patch", "workspace_app.py"}.issubset(inventory)
    for name, evidence in inventory.items():
        data = (output / name).read_bytes()
        assert evidence == {"sha256": probe.dispatch.digest(data), "bytes": len(data)}
