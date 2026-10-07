"""CPU-only runtime contracts and passive native trace evidence."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
from importlib import metadata
import json
import math

import pytest

from eval._contracts import ContractError, canonical_json
from eval.runtime_events import EventRecorder, RuntimeEvent
from eval.runtime_result import (
    UNKNOWN, ArtifactRef, RuntimeFingerprint, SolverRunResult, TaskRuntimeResult,
    VerifierRunResult, capture_runtime_fingerprint, patch_sha256,
)


def artifact():
    return ArtifactRef("native_trace", "trace/native.json", "a" * 64, 42)


def solver(**changes):
    values = dict(schema_version=1, task_id="toy_1", runtime_status="completed",
                  started_at="2026-10-06T10:00:00Z", elapsed_seconds=0.25,
                  returned_patch="", returned_patch_sha256=patch_sha256(""),
                  artifacts=(artifact(),), fingerprint=RuntimeFingerprint())
    values.update(changes)
    return SolverRunResult(**values)


def verifier(**changes):
    values = dict(schema_version=1, task_id="toy_1", runtime_status="completed",
                  returned_patch_sha256=patch_sha256(""), resolved=False,
                  native_result_json=canonical_json({"resolved": False, "reason": "native"}))
    values.update(changes)
    return VerifierRunResult(**values)


def result():
    return TaskRuntimeResult(1, "toy_1", solver(), verifier(), False)


@pytest.mark.parametrize("factory", [artifact, RuntimeFingerprint, solver, verifier, result])
def test_results_are_frozen_slotted_closed_and_round_trip(factory):
    value = factory()
    assert not hasattr(value, "__dict__")
    assert type(value).from_dict(value.to_dict()) == value
    assert type(value).from_json(value.to_json()) == value
    assert type(value).from_json(value.to_json().encode()) == value
    assert value.identity_sha256() == hashlib.sha256(value.to_json().encode()).hexdigest()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        setattr(value, fields(value)[0].name, "changed")
    with pytest.raises(ContractError, match="unknown fields"):
        type(value).from_dict(value.to_dict() | {"private_path": "PRIVATE_SENTINEL"})


def test_nested_results_and_artifacts_have_no_mutable_aliases():
    value = result()
    data = value.to_dict()
    data["solver_result"]["artifacts"][0]["sha256"] = "b" * 64
    data["solver_result"]["fingerprint"]["python_version"] = "changed"
    assert value.solver_result.artifacts[0] == artifact()
    assert value.solver_result.fingerprint.python_version == UNKNOWN
    with pytest.raises(ContractError, match="tuple"):
        replace(value.solver_result, artifacts=[artifact()])
    with pytest.raises(ContractError, match="unknown fields"):
        corrupted = value.to_dict()
        corrupted["solver_result"]["artifacts"][0]["private_root"] = "PRIVATE_SENTINEL"
        TaskRuntimeResult.from_dict(corrupted)


@pytest.mark.parametrize("factory", [solver, verifier, result])
@pytest.mark.parametrize("second_digest", ["a" * 64, "b" * 64])
def test_artifact_paths_are_unique_even_when_duplicate_hashes_agree(factory, second_digest):
    value = factory()
    first = artifact()
    duplicate = replace(first, sha256=second_digest)
    with pytest.raises(ContractError, match="duplicate relative_path"):
        replace(value, artifacts=(first, duplicate))
    serialized = value.to_dict() | {"artifacts": [first.to_dict(), duplicate.to_dict()]}
    with pytest.raises(ContractError, match="duplicate relative_path"):
        type(value).from_dict(serialized)
    with pytest.raises(ContractError, match="duplicate relative_path"):
        type(value).from_json(canonical_json(serialized))
    distinct = replace(duplicate, relative_path="trace/other.json")
    assert replace(value, artifacts=(first, distinct)).artifacts == (first, distinct)


def test_exact_result_types_cannot_be_replaced_with_duck_types_or_subclasses():
    class Derived(SolverRunResult):
        pass

    with pytest.raises(ContractError, match="exact contract"):
        Derived(**solver().to_dict())
    with pytest.raises(ContractError, match="exact SolverRunResult"):
        TaskRuntimeResult(1, "toy_1", solver().to_dict())


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2), ("task_id", "../private"),
    ("runtime_status", "semantic_failure"), ("elapsed_seconds", True),
    ("elapsed_seconds", -0.1), ("elapsed_seconds", math.inf), ("elapsed_seconds", math.nan),
    ("llm_calls", True), ("counted_tool_calls", -1), ("tool_attempts", 1.0),
    ("fallback_used", 0), ("timeout", "false"), ("budget_exhausted", 1),
    ("returned_patch_sha256", "a" * 64), ("agent_error", {}),
    ("escaped_exception", {"message": "error"}), ("returned_patch", "\ud800"),
])
def test_solver_result_rejects_invalid_observations(field, value):
    with pytest.raises(ContractError):
        solver(**{field: value})


@pytest.mark.parametrize("path", ["/private/trace", "../trace", "trace/../secret", "trace//native",
                                   "trace/./native", "trace/", "C:\\secret", "native\x00.json"])
def test_artifact_refs_are_relative_to_phase_evidence_root(path):
    with pytest.raises(ContractError, match="relative"):
        replace(artifact(), relative_path=path)


def test_unknown_observations_are_not_fabricated_zero_or_false():
    value = solver()
    for name in ("submitted_patch", "workspace_patch", "agent_error", "escaped_exception",
                 "llm_calls", "counted_tool_calls", "tool_attempts", "fallback_used",
                 "timeout", "budget_exhausted", "terminal_reason"):
        assert getattr(value, name) is None
        assert value.to_dict()[name] is None
    assert set(RuntimeFingerprint().to_dict().values()) == {UNKNOWN}


def test_empty_returned_patch_remains_authoritative_after_workspace_edit_and_fatal_error():
    value = solver(runtime_status="error", workspace_patch="WORKSPACE_DIAGNOSTIC_PATCH",
                   escaped_exception="Tool not found: undeclared_tool", agent_error="fatal")
    combined = TaskRuntimeResult(1, "toy_1", value, verifier(), False)
    assert combined.solver_result.returned_patch == ""
    assert combined.verifier_result.returned_patch_sha256 == patch_sha256("")
    with pytest.raises(ContractError, match="authoritative returned patch"):
        TaskRuntimeResult(1, "toy_1", value,
                          verifier(returned_patch_sha256=patch_sha256(value.workspace_patch)), False)


def test_fallback_budget_and_free_tools_observations_are_independent():
    recovered_patch = "NATIVE_FALLBACK_PATCH"
    timed = solver(runtime_status="timeout", returned_patch=recovered_patch,
                   returned_patch_sha256=patch_sha256(recovered_patch), fallback_used=True,
                   timeout=True, agent_error="native session timeout")
    rejected = solver(runtime_status="budget_exhausted", counted_tool_calls=3,
                      tool_attempts=4, budget_exhausted=True, agent_error="native budget rejection")
    free = solver(counted_tool_calls=2, tool_attempts=4, submitted_patch="", fallback_used=False)
    assert timed.returned_patch == recovered_patch and timed.workspace_patch is None
    assert rejected.tool_attempts > rejected.counted_tool_calls
    assert free.counted_tool_calls == 2 and free.tool_attempts == 4
    assert free.budget_exhausted is None


def test_task_result_identity_and_resolution_are_native_verifier_only():
    with pytest.raises(ContractError, match="solver task identity"):
        TaskRuntimeResult(1, "other", solver())
    with pytest.raises(ContractError, match="verifier task identity"):
        TaskRuntimeResult(1, "toy_1", solver(), verifier(task_id="other"), False)
    with pytest.raises(ContractError, match="resolved must equal"):
        TaskRuntimeResult(1, "toy_1", solver(), verifier(), True)
    with pytest.raises(ContractError, match="without a verifier"):
        TaskRuntimeResult(1, "toy_1", solver(), resolved=False)
    assert TaskRuntimeResult(1, "toy_1", solver()).resolved is None


@pytest.mark.parametrize("native_json", ["{}", '{"resolved":true,"resolved":false}',
                                        '{"resolved": NaN}', "[]"])
def test_verifier_retains_closed_canonical_native_result_json(native_json):
    with pytest.raises(ContractError):
        verifier(native_result_json=native_json)
    original = {"resolved": False, "test_counts": {"fail": 2, "pass": 5}, "reason": "patch apply failure"}
    retained = verifier(native_result_json=canonical_json(original))
    assert json.loads(retained.native_result_json) == original


def test_fingerprint_captures_explicit_provenance_and_actual_metadata_without_imports(monkeypatch):
    observed = []

    def version(name):
        observed.append(name)
        if name == "litellm":
            raise metadata.PackageNotFoundError(name)
        return "1.2.3"

    monkeypatch.setattr(metadata, "version", version)
    value = capture_runtime_fingerprint(
        git_head="a" * 40, candidate_sha256="b" * 64, solver_contract_sha256="c" * 64,
        task_manifest_sha256="d" * 64, sandbox_image="python:3.13-slim@sha256:" + "e" * 64,
        model_endpoint_identity="UNCONFIGURED",
    )
    assert observed == ["swegemma", "adk-submission", "adk-eval-core", "google-adk", "google-genai", "litellm"]
    assert value.litellm_version == UNKNOWN and value.swegemma_version == "1.2.3"
    assert value.python_version != UNKNOWN and value.platform != UNKNOWN
    assert value.candidate_sha256 == "b" * 64
    with pytest.raises(ContractError, match="credential-free"):
        replace(value, model_endpoint_identity="https://user:SECRET@model.example/v1?token=SECRET")


def native_trace():
    return {"entries": [
        {"type": "task_prompt", "elapsed_s": 0.1, "author": "harness", "content": "HUGE_PROMPT_SENTINEL"},
        {"type": "tool_call", "elapsed_s": 0.2, "author": "agent", "tool": "write_file",
         "args": {"content": "HUGE_EDIT_SENTINEL", "filepath": "toy.py"}},
        {"type": "tool_response", "elapsed_s": 0.3, "author": "agent", "tool": "write_file",
         "result": '{"status":"ok"}'},
        {"type": "tool_call", "elapsed_s": 0.4, "author": "agent", "tool": "get_status", "args": {}},
        {"type": "tool_response", "elapsed_s": 0.5, "author": "agent", "tool": "get_status",
         "result": '{"status":"ok","tool_calls_used":2,"tool_calls_remaining":0,"max_tool_calls":2,"patch_size":13}'},
        {"type": "tool_call", "elapsed_s": 0.6, "author": "agent", "tool": "submit_patch", "args": {}},
        {"type": "final", "elapsed_s": 0.7, "author": "agent", "content": "HUGE_FINAL_SENTINEL"},
    ]}


def test_native_trace_projection_is_append_only_passive_and_keeps_only_references():
    trace = native_trace()
    before = deepcopy(trace)
    recorder = EventRecorder("toy_1", "solver")
    recorder.append("solver_started", elapsed_seconds=0)
    snapshot = recorder.events
    recorder.capture_native_trace(trace, trace_reference="sealed/native_trace.json")
    recorder.append("solver_finished", elapsed_seconds=1)
    assert trace == before
    assert len(snapshot) == 1
    assert tuple(event.sequence for event in recorder.events) == tuple(range(11))
    assert recorder.events[0].elapsed_origin == "worker"
    assert recorder.events[2].elapsed_origin == "native_trace"
    assert recorder.events[2].event_type == "tool_attempt"
    assert recorder.events[3].event_type == "tool_result"
    status_result = next(e for e in recorder.events if e.event_type == "tool_result" and e.tool_name == "get_status")
    assert status_result.budget_state_json == canonical_json({
        "tool_calls_used": 2, "tool_calls_remaining": 0, "max_tool_calls": 2,
    })
    assert any(e.event_type == "status_call" and e.tool_name == "get_status" for e in recorder.events)
    assert any(e.event_type == "submit_patch" and e.tool_name == "submit_patch" for e in recorder.events)
    serialized = recorder.to_json()
    assert "HUGE_" not in serialized
    entry = recorder.events[2]
    assert entry.native_reference == "sealed/native_trace.json#/entries/1"
    assert entry.native_sha256 == hashlib.sha256(canonical_json(trace["entries"][1]).encode()).hexdigest()
    assert not set(e.event_type for e in recorder.events) & {"model_request", "tool_started", "timeout", "fallback"}
    assert RuntimeEvent.from_json(entry.to_json()) == entry
    assert not hasattr(entry, "__dict__")
    with pytest.raises(FrozenInstanceError):
        entry.event_type = "timeout"


def test_absent_native_elapsed_time_stays_unknown_and_error_text_is_not_classified():
    recorder = EventRecorder("toy_1", "solver")
    recorder.capture_native_trace({"entries": [{"type": "tool_response", "tool": "write_file",
        "result": "timeout / budget exhausted / fallback happened"}]})
    assert recorder.events[0].elapsed_seconds is None
    assert recorder.events[0].elapsed_origin == "UNKNOWN"
    assert recorder.events[0].event_type == "tool_result"
    assert recorder.events[0].budget_state_json is None


def test_instrumentation_on_and_off_preserves_scripted_outcome_and_call_order():
    def run(enabled):
        recorder = EventRecorder("toy_1", "solver", enabled=enabled)
        recorder.append("solver_started", elapsed_seconds=0)
        # A deterministic stand-in for a native function: observation runs only
        # after it returns, with no callback or workspace access inside it.
        trace = native_trace()
        native_result = ("RETURNED_PATCH", None, trace)
        recorder.capture_native_trace(native_result[2])
        recorder.append("solver_finished", elapsed_seconds=1)
        return native_result, recorder.events

    off, off_events = run(False)
    on, on_events = run(True)
    assert off == on
    assert [e["tool"] for e in off[2]["entries"] if e["type"] == "tool_call"] == [
        "write_file", "get_status", "submit_patch"]
    assert off_events == () and len(on_events) == 11


def test_native_structured_budget_rejection_is_observed_without_inventing_body_start():
    recorder = EventRecorder("toy_1", "solver")
    recorder.capture_native_trace({"entries": [
        {"type": "tool_call", "elapsed_s": 0.1, "tool": "write_file", "args": {}},
        {"type": "tool_response", "elapsed_s": 0.2, "tool": "write_file",
         "result": '{"status":"error","error_type":"BudgetExceeded","error_message":"native message"}'},
        {"type": "tool_response", "elapsed_s": 0.3, "tool": "other",
         "result": '{"status":"error","error_message":"BudgetExceeded"}'},
        {"type": "tool_response", "elapsed_s": 0.4, "tool": "another",
         "result": "BudgetExceeded"},
    ]})
    assert [e.event_type for e in recorder.events] == [
        "tool_attempt", "tool_result", "tool_rejected", "tool_result", "tool_result"]
    returned = recorder.events[1]
    rejected = recorder.events[2]
    assert (returned.native_reference, returned.native_sha256) == (rejected.native_reference, rejected.native_sha256)
    assert rejected.elapsed_seconds == 0.2 and rejected.tool_name == "write_file"
    assert rejected.budget_state_json is None
    assert "tool_started" not in {e.event_type for e in recorder.events}


@pytest.mark.parametrize("changes", [{"sequence": True}, {"elapsed_seconds": math.inf},
    {"elapsed_seconds": True}, {"worker_phase": "private"}, {"event_type": "semantic_failure"},
    {"native_sha256": "invalid"}, {"budget_state_json": '{"used":1}'}, {"agent": {}},
    {"event_type": []}, {"worker_phase": {}}])
def test_events_reject_invalid_or_mutable_observations(changes):
    values = dict(sequence=0, elapsed_seconds=0, task_id="toy_1", worker_phase="solver",
                  event_type="solver_started")
    with pytest.raises(ContractError):
        RuntimeEvent(**(values | changes))


def test_event_json_is_closed_and_duplicate_keys_fail():
    event = RuntimeEvent(0, 0, "toy_1", "solver", "solver_started")
    with pytest.raises(ContractError, match="unknown fields"):
        RuntimeEvent.from_dict(event.to_dict() | {"model_prompt": "SENTINEL"})
    with pytest.raises(ContractError, match="duplicate"):
        SolverRunResult.from_json('{"task_id":"toy_1","task_id":"other"}')
