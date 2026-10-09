"""Offline CPU driver acceptance boundaries; no native/model/network execution."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import io
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

from eval._contracts import ContractError, canonical_json
from eval.runtime_provenance import PACKAGES
from eval.runtime_result import ArtifactRef, RuntimeFingerprint, SolverRunResult, TaskRuntimeResult, VerifierRunResult, patch_sha256
from tools.common import REPO_ROOT, WriteGuard
from tools import dev_eval as driver


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("network calls forbidden in driver CPU tests")
    monkeypatch.setattr(driver, "build_opener", fail)


@pytest.fixture
def setup(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    artifact = tmp_path / "evidence"
    artifact.mkdir(mode=0o700)
    fp = {"schema_version": 1, "phase": "driver",
          "eval_infra_source_identity": {"git_head": "a" * 40, "source_dirty": False, "runtime_source_sha256": "b" * 64},
          "candidate_identity": {"sha256": "c" * 64, "size_bytes": 443572},
          "preregistration_identity": {"sha256": "d" * 64, "size_bytes": 100},
          "model_endpoint_identity": "http://127.0.0.1:8000/v1", "platform": "Linux", "python_version": "3.12.14",
          "package_versions": {p: None for p in PACKAGES}, "public_identities": {},
          "model_server_identity": {}, "harness_lock_sha256": "e" * 64,
          "confinement": {"status": "NOT_OBSERVED", "reason": "worker confinement is observed during preflight/run"},
          "captured_at": datetime.now(timezone.utc).isoformat(), "harness_python": "3.12.14",
          "gpu": [{"name": "A100", "memory_mib": 40960, "driver": "fixture"}],
          "model_weights": {"model.safetensors": {"sha256": "f" * 64, "size_bytes": 10}},
          "seed_forwarding": "unsupported", "tp": 1, "max_model_len": 32768}
    return public, artifact, WriteGuard(public), fp


def _raw_write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n")
    path.chmod(0o600)


def _sealed_result(root, task_id, fp, *, patch="", resolved=False, verifier_missing=False, run_name=None,
                   solver_task=None):
    run_root = root / (run_name or task_id + "_run")
    standard = RuntimeFingerprint(git_head="a" * 40, candidate_sha256="c" * 64,
                                  **({"solver_contract_sha256": solver_task.sha256()} if solver_task else {}))
    artifacts = {}
    from eval.runtime_provenance import FINGERPRINT_FIELDS
    for phase in ("solver", "verifier"):
        phase_fp = {k: fp[k] for k in FINGERPRINT_FIELDS}
        phase_fp["phase"] = phase
        if solver_task:
            phase_fp["public_identities"] = {"snapshot": solver_task.snapshot.to_dict()}
        path = run_root / phase / "real_fingerprint.json"
        _raw_write(path, phase_fp)
        info = driver._file_identity(path)
        artifacts[phase] = (ArtifactRef("real_fingerprint", "real_fingerprint.json", info["sha256"], info["size_bytes"]),)
    solver = SolverRunResult(1, task_id, "completed", "fixture UTC", 0.5, patch, patch_sha256(patch),
                             llm_calls=None, counted_tool_calls=None, artifacts=artifacts["solver"], fingerprint=standard)
    verifier = VerifierRunResult(1, task_id, "completed", patch_sha256(patch), resolved,
                                native_result_json=canonical_json({"resolved": resolved, "test_exit_code": 1}),
                                elapsed_seconds=0.1, artifacts=artifacts["verifier"], fingerprint=standard)
    phase_refs = []
    for phase, result in (("solver", solver), ("verifier", verifier)):
        path = run_root / phase / "result.json"
        _raw_write(path, result.to_dict())
        info = driver._file_identity(path)
        phase_refs.append(ArtifactRef(phase + "_result", path.relative_to(root).as_posix(), info["sha256"], info["size_bytes"]))
    result = TaskRuntimeResult(1, task_id, solver, None if verifier_missing else verifier,
                               None if verifier_missing else resolved, artifacts=tuple(phase_refs), fingerprint=standard)
    _raw_write(run_root / "task_result.json", result.to_dict())
    return result


def _seal(root, result, fp, attempt=1):
    run_root = driver._result_root(result, root)
    seal = {"schema_version": 1, "task_id": result.task_id, "attempt": attempt,
            "fingerprint_sha256": driver._digest(fp), "run_root": run_root.name,
            "task_result_sha256": driver._file_identity(run_root / "task_result.json")["sha256"]}
    _raw_write(driver._seal_path(root, result.task_id, attempt), seal)
    return seal


def test_closed_cli_only_s1_and_required_explicit_paths():
    parser = driver._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--screen", "holdout"])
    with pytest.raises(SystemExit):
        parser.parse_args(["fingerprint", "--endpoint", "http://127.0.0.1:8000/v1"])
    assert set(parser._subparsers._group_actions[0].choices) == {
        "fingerprint", "serve-argv", "preflight", "run", "leak-scan", "report"}


@pytest.mark.parametrize("field", ["candidate_identity", "preregistration_identity", "model_endpoint_identity",
                                   "eval_infra_source_identity", "harness_lock_sha256", "model_weights"])
def test_stale_identity_refused(setup, field):
    _, _, _, fp = setup
    changed = json.loads(json.dumps(fp))
    changed[field] = "different"
    with pytest.raises(ContractError):
        driver.validate_fingerprint(fp, changed)


@pytest.mark.parametrize("age", [13, -1])
def test_stale_or_future_timestamp_refused(setup, age):
    _, _, _, fp = setup
    now = datetime.now(timezone.utc)
    fp["captured_at"] = (now - timedelta(hours=age)).isoformat()
    with pytest.raises(ContractError, match="stale"):
        driver.validate_fingerprint(fp, fp, now=now)


def test_fresh_fingerprint_and_dirty_rejection(setup):
    _, _, _, fp = setup
    assert driver.validate_fingerprint(fp, fp) is fp
    fp["eval_infra_source_identity"]["source_dirty"] = True
    with pytest.raises(ContractError, match="clean"):
        driver.validate_fingerprint(fp, fp)


@pytest.mark.parametrize("memory,accepted", [(38146, False), (38147, True), (40536, True), (81920, True), (24576, False), (16384, False)])
def test_gpu_admission_boundary(memory, accepted):
    command = lambda argv: SimpleNamespace(stdout=f"fixture GPU, {memory}, 1.0\n")
    if accepted:
        assert driver.observe_gpu(command=command)[0]["memory_mib"] == memory
    else:
        with pytest.raises(ContractError, match="40 GB"):
            driver.observe_gpu(command=command)


def test_disk_each_role_shared_filesystem_aggregation(setup):
    public, _, _, _ = setup
    roles = ("model", "public", "private", "harness", "vllm", "worker", "export")
    roots = {role: public / role for role in roles}
    sizes = {role: 10 for role in roles}
    good = driver.admit_disk_space(roots, sizes, disk_usage=lambda p: SimpleNamespace(free=90), device=lambda p: "shared")
    assert len(good["filesystems"]) == 7
    assert {row["minimum_free_bytes"] for row in good["filesystems"]} == {90}
    with pytest.raises(ContractError, match="minimum"):
        driver.admit_disk_space(roots, sizes, disk_usage=lambda p: SimpleNamespace(free=89), device=lambda p: "shared")


def test_disk_missing_role_refused(setup):
    public, _, _, _ = setup
    with pytest.raises(ContractError):
        driver.admit_disk_space({"model": public}, {"model": 1})


def _test_execution_receipt(*, exit_code=1, **changes):
    return {"status": "executed", "reason": None, "command_exit_code": exit_code,
            "junit_test_count": 2, "required_test_count": 1, "required_tests_executed": True,
            **changes}


@pytest.mark.parametrize("control,resolved,apply,exit_code,passed", [
    ("known_patch", True, None, None, True), ("known_patch", False, None, None, False),
    ("no_patch", False, "not_needed", 1, True), ("no_patch", False, "UNKNOWN", 1, False),
    ("no_patch", False, "failed", 1, False), ("no_patch", True, "not_needed", 0, False),
    ("no_patch", False, "not_needed", None, False),
])
def test_exact_control_criteria(control, resolved, apply, exit_code, passed):
    result = {"resolved": resolved, "verification_observations": {
              "apply_status": apply, "apply_error": None,
              "test_execution": _test_execution_receipt(exit_code=0 if control == "known_patch" else 1)},
              "verifier_result": {"native_result_json": canonical_json({"test_exit_code": exit_code})}}
    assert driver.control_passes(control, result) is passed


def test_no_patch_control_rejects_missing_pytest_infrastructure_failure():
    result = {"resolved": False, "verification_observations": {
        "apply_status": "not_needed", "apply_error": None, "required_tests_passed": False,
        "test_execution": _test_execution_receipt(status="infrastructure_error", reason="PYTEST_UNAVAILABLE",
                                                   junit_test_count=0, required_tests_executed=False)},
        "verifier_result": {"native_result_json": canonical_json({
            "resolved": False, "test_exit_code": 1,
            "test_output": "STDOUT:\n\nSTDERR:\n/fixture/bin/python3: No module named pytest\n"})}}
    assert driver.control_passes("no_patch", result) is False


@pytest.mark.parametrize("exit_code", [1, 2])
def test_no_patch_control_accepts_proven_repository_failure_without_claiming_test_bodies_ran(exit_code):
    result = {"resolved": False, "verification_observations": {
        "apply_status": "not_needed", "apply_error": None, "required_tests_passed": False,
        "test_execution": _test_execution_receipt(exit_code=exit_code, status="repository_failure",
            reason="repository_collection_or_setup_failure", required_tests_executed=False, required_test_count=0)},
        "verifier_result": {"native_result_json": canonical_json({"resolved": False, "test_exit_code": exit_code})}}
    assert driver.control_passes("no_patch", result) is True
    assert driver.control_passes("known_patch", result) is False


@pytest.mark.parametrize("receipt_change,native", [
    ({"reason": None}, {"resolved": False, "test_exit_code": 2}),
    ({"required_tests_executed": True}, {"resolved": False, "test_exit_code": 2}),
    ({"command_exit_code": 0}, {"resolved": False, "test_exit_code": 0}),
    ({}, {"resolved": True, "test_exit_code": 2}),
    ({}, {"test_exit_code": 2}),
    ({}, {"resolved": False, "test_exit_code": 1}),
    ({}, {"resolved": False}),
])
def test_repository_failure_control_requires_unresolved_matching_native_result(receipt_change, native):
    receipt = _test_execution_receipt(exit_code=2, status="repository_failure",
        reason="repository_collection_or_setup_failure", required_tests_executed=False)
    receipt.update(receipt_change)
    result = {"resolved": False, "verification_observations": {
        "apply_status": "not_needed", "apply_error": None, "required_tests_passed": False,
        "test_execution": receipt},
        "verifier_result": {"native_result_json": canonical_json(native)}}
    assert driver.control_passes("no_patch", result) is False


@pytest.mark.parametrize("reason", ["EXPECTED_PYTEST_INVOCATION_MISSING", "PYTEST_TARGET_MISMATCH"])
def test_no_patch_control_requires_expected_test_invocation_execution(reason):
    result = {"resolved": False, "verification_observations": {
        "apply_status": "not_needed", "apply_error": None, "required_tests_passed": False,
        "test_execution": _test_execution_receipt(status="infrastructure_error", reason=reason,
                                                   required_tests_executed=False)},
        "verifier_result": {"native_result_json": canonical_json({"test_exit_code": 1})}}
    assert driver.control_passes("no_patch", result) is False


@pytest.mark.parametrize("control", ["no_patch", "known_patch"])
@pytest.mark.parametrize("receipt", [None, {}, _test_execution_receipt(junit_test_count=0),
                                    _test_execution_receipt(required_tests_executed=False),
                                    _test_execution_receipt(exit_code=5),
                                    _test_execution_receipt(status="infrastructure_error", reason="MALFORMED_JUNIT")])
def test_controls_require_nonvacuous_test_execution_evidence(control, receipt):
    result = {"resolved": control == "known_patch", "verification_observations": {
        "apply_status": "not_needed", "apply_error": None, "required_tests_passed": control == "known_patch",
        "test_execution": receipt},
        "verifier_result": {"native_result_json": canonical_json({
            "test_exit_code": 0 if control == "known_patch" else 1})}}
    assert driver.control_passes(control, result) is False


@pytest.mark.parametrize("changes", [{"command_exit_code": True}, {"junit_test_count": True},
                                    {"required_test_count": -1}, {"unexpected": "field"}])
def test_control_execution_receipt_refuses_malformed_counts_and_fields(changes):
    result = {"resolved": False, "verification_observations": {
        "apply_status": "not_needed", "apply_error": None,
        "test_execution": _test_execution_receipt(**changes)},
        "verifier_result": {"native_result_json": canonical_json({"test_exit_code": 1})}}
    assert driver.control_passes("no_patch", result) is False


@pytest.mark.parametrize("control,native_exit,command_exit", [
    ("known_patch", 1, 1), ("known_patch", 0, 1),
    ("no_patch", 1, 0), ("no_patch", 2, 1), ("no_patch", True, 1),
])
def test_control_execution_receipt_rejects_contradictory_native_exit(control, native_exit, command_exit):
    result = {"resolved": control == "known_patch", "verification_observations": {
        "apply_status": "not_needed", "apply_error": None, "required_tests_passed": control == "known_patch",
        "test_execution": _test_execution_receipt(exit_code=command_exit)},
        "verifier_result": {"native_result_json": canonical_json({"test_exit_code": native_exit})}}
    assert driver.control_passes(control, result) is False


def test_control_execution_receipt_requires_sealed_native_observation_artifact(setup):
    from eval.runtime_provenance import FINGERPRINT_FIELDS
    _, root, _, fp = setup
    run_root = root / "control_fixture"
    phase_fp = {key: fp[key] for key in FINGERPRINT_FIELDS}
    phase_fp["phase"] = "verifier"
    _raw_write(run_root / "verifier/real_fingerprint.json", phase_fp)
    info = driver._file_identity(run_root / "verifier/real_fingerprint.json")
    ref = ArtifactRef("fingerprint", "real_fingerprint.json", info["sha256"], info["size_bytes"])
    verifier = VerifierRunResult(1, driver.CONTROL_IDS[0], "completed", patch_sha256(""), False,
                                 native_result_json=canonical_json({"test_exit_code": 1}),
                                 artifacts=(ref,), fingerprint=RuntimeFingerprint())
    result = {"run_root": str(run_root), "solver_started": False, "resolved": False,
              "verifier_result": verifier.to_dict(), "verification_observations": {
                  "apply_status": "not_needed", "apply_error": None,
                  "test_execution": _test_execution_receipt()}}
    observed = driver._control_observations(result, fp)
    assert observed["verification_observations"] == {}
    assert driver.control_passes("no_patch", observed) is False


def test_phase_fingerprints_mandatory_and_crosschecked(setup):
    _, root, _, fp = setup
    result = _sealed_result(root, driver.S1_IDS[0], fp)
    seal = _seal(root, result, fp)
    assert driver.validate_task_seal(seal, root, fp) == result
    path = root / seal["run_root"] / "verifier/real_fingerprint.json"
    rich = driver._load(path)
    rich["candidate_identity"]["sha256"] = "0" * 64
    _raw_write(path, rich)
    with pytest.raises(ContractError, match="seal mismatch"):
        driver.validate_task_seal(seal, root, fp)


def test_task_result_tamper_refused(setup):
    _, root, _, fp = setup
    result = _sealed_result(root, driver.S1_IDS[0], fp)
    seal = _seal(root, result, fp)
    (root / seal["run_root"] / "task_result.json").write_text("{}")
    with pytest.raises(ContractError, match="seal mismatch"):
        driver.validate_task_seal(seal, root, fp)


def test_missing_verifier_refused(setup):
    _, root, _, fp = setup
    result = _sealed_result(root, driver.S1_IDS[0], fp, verifier_missing=True)
    seal = _seal(root, result, fp)
    with pytest.raises(ContractError, match="mandatory"):
        driver.validate_task_seal(seal, root, fp)


def test_gold_controls_excluded_from_report_and_scan(setup):
    public, root, guard, fp = setup
    _raw_write(root / "GOLD_ASSISTED_VERIFIER_ONLY.json", {"gold_assisted": True})
    with pytest.raises(ContractError, match="excluded"):
        driver.write_report(root, guard=guard, fingerprint=fp)
    with pytest.raises(ContractError, match="excluded"):
        driver.leak_scan(root, public, guard=guard)


def test_test_patch_hit_p0_reference_overlap_diagnostic(setup):
    public, root, guard, fp = setup
    private_line = b"assert very_distinct_private_test_boundary_sentinel_value == True"
    reference_line = b"return independently_producible_public_reference_fix_value_with_padding"
    result = _sealed_result(root, driver.S1_IDS[0], fp, patch=reference_line.decode())
    seal = _seal(root, result, fp)
    _raw_write(root / "run_manifest.json", {"screen": "S1", "task_ids": list(driver.S1_IDS)})
    scanner = lambda r, t: {"test_patch": [private_line], "patch": [reference_line]}
    value = driver.leak_scan(root, public, guard=guard, patch_lines=scanner)
    assert value["p0"] is False and value["patch_overlap"]
    assert value["patch_overlap_is_leakage"] is False
    (root / seal["run_root"] / "solver/process.log").write_bytes(private_line)
    value = driver.leak_scan(root, public, guard=guard, patch_lines=scanner)
    assert value["p0"] is True and len(value["test_patch_hits"]) == 1
    assert private_line.decode() not in (root / "leak_scan.json").read_text()


def test_added_lines_minimum_nonwhitespace_and_headers():
    assert driver._added_lines("+++ " + "x" * 100 + "\n+short\n+" + " " * 80) == []
    assert driver._added_lines("+" + "x" * 40) == [b"x" * 40]


def test_unknown_remains_null_and_unknown(setup):
    _, root, _, fp = setup
    never = driver.forensic_record(driver.S1_IDS[0], index=0)
    assert never["outcome"]["resolved"] is None
    assert never["tools"]["counted"] is None
    assert never["forensics"]["primary"] == "UNKNOWN"
    assert never["model"]["finish_reasons"]["reason"] == "not started"
    result = _sealed_result(root, driver.S1_IDS[0], fp, patch="public fix", resolved=False)
    record = driver.forensic_record(result.task_id, index=0, result=result)
    assert record["forensics"]["primary"] == "UNKNOWN"
    resolved = replace(result, verifier_result=replace(result.verifier_result, resolved=True), resolved=True)
    assert driver.forensic_record(result.task_id, index=0, result=resolved)["forensics"]["primary"] is None


@pytest.mark.parametrize("solver_failure,expected", [(None, "ENVIRONMENT_VERIFICATION_ARTIFACT"),
                                                    ("timeout", "AGENT_TIMEOUT"),
                                                    ("budget_exhausted", "TOOL_BUDGET_EXHAUSTED")])
def test_verifier_infrastructure_failure_is_not_empty_patch_agent_quality(setup, solver_failure, expected):
    _, root, _, fp = setup
    result = _sealed_result(root, driver.S1_IDS[0], fp, patch="")
    solver = replace(result.solver_result, **({solver_failure: True} if solver_failure else {}))
    verifier = replace(result.verifier_result, runtime_status="error", resolved=None,
                       error="verification infrastructure error: missing_or_empty_junit")
    observed = replace(result, solver_result=solver, verifier_result=verifier, resolved=None)
    record = driver.forensic_record(observed.task_id, index=0, result=observed)
    assert record["forensics"]["primary"] == expected
    assert record["outcome"]["resolved"] is None


def test_generic_verifier_error_is_not_guessed_to_be_infrastructure(setup):
    _, root, _, fp = setup
    result = _sealed_result(root, driver.S1_IDS[0], fp, patch="")
    verifier = replace(result.verifier_result, runtime_status="error", resolved=None, error="unspecified error")
    observed = replace(result, verifier_result=verifier, resolved=None)
    record = driver.forensic_record(observed.task_id, index=0, result=observed)
    assert record["forensics"]["primary"] == "NO_PATCH"


def test_s1_only_and_attempt_contract(setup):
    public, root, guard, fp = setup
    kwargs = dict(public_root=public, private_root=public, config=SimpleNamespace(artifact_root=root), guard=guard,
                  fingerprint=fp, controls_validator=lambda *a: None)
    with pytest.raises(ContractError, match="S1"):
        driver.run_s1((SimpleNamespace(instance_id="holdout_1"),), {}, **kwargs)
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    with pytest.raises(ContractError, match="diagnosed"):
        driver.run_s1(tasks, {}, attempt=2, **kwargs)
    with pytest.raises(ContractError, match="initial attempt"):
        driver.run_s1(tasks, {}, attempt=3, **kwargs)


def test_sequential_resume_attempts_and_p0_stop(setup):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    calls = []
    def runner(task, verifier, **kwargs):
        calls.append(task.instance_id)
        return _sealed_result(root, task.instance_id, fp, resolved=task.instance_id == driver.S1_IDS[0])
    def scanner():
        return {"p0": len(calls) == 2}
    kwargs = dict(public_root=public, private_root=public, config=SimpleNamespace(artifact_root=root), guard=guard,
                  fingerprint=fp, task_runner=runner, scanner=scanner, controls_validator=lambda *a: None)
    summary = driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, **kwargs)
    assert calls == list(driver.S1_IDS[:2])
    assert summary["assigned"] == 12 and summary["started"] == 2 and summary["verified"] == 2
    assert summary["resolved"] == 1 and summary["unresolved"] == 1 and summary["indeterminate"] == 10
    assert summary["stopped"] is True
    with pytest.raises(ContractError, match="P0"):
        driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, **kwargs)
    # A P0 run must remain halted even when its existing seals validate.
    records = [json.loads(row) for row in (root / "records.jsonl").read_text().splitlines()]
    assert records[2]["outcome"]["resolved"] is None


def test_resume_skips_only_trustworthy_seals(setup):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    calls = []
    def runner(task, verifier, **kwargs):
        calls.append(task.instance_id)
        return _sealed_result(root, task.instance_id, fp)
    kwargs = dict(public_root=public, private_root=public, config=SimpleNamespace(artifact_root=root), guard=guard,
                  fingerprint=fp, task_runner=runner, scanner=lambda: {"p0": False}, controls_validator=lambda *a: None)
    driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, **kwargs)
    assert calls == list(driver.S1_IDS)
    calls.clear()
    driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, **kwargs)
    assert calls == []
    latest = driver._load(driver._seal_path(root, driver.S1_IDS[0], 1))
    latest["fingerprint_sha256"] = "0" * 64
    _raw_write(driver._seal_path(root, driver.S1_IDS[0], 1), latest)
    with pytest.raises(ContractError, match="fingerprint"):
        driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, **kwargs)


def test_unsealed_start_refuses_silent_resume(setup):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    driver._append_ledger(root, {"task_id": driver.S1_IDS[0], "attempt": 1, "status": "started"}, guard)
    with pytest.raises(ContractError, match="unsealed"):
        driver.run_s1(tasks, {}, public_root=public, private_root=public,
                      config=SimpleNamespace(artifact_root=root), guard=guard, fingerprint=fp,
                      controls_validator=lambda *a: None)


def test_secrets_rejected_before_evidence_write(setup):
    _, root, guard, _ = setup
    with pytest.raises(ContractError, match="authentication"):
        driver._write(root / "bad.json", {"authorization": "Bearer never_print_me"}, guard)
    assert not (root / "bad.json").exists()


def test_serve_argv_native_explicit_executable_no_launch(setup, monkeypatch):
    import eval.runtime_real as real
    public, root, guard, _ = setup
    candidate = root / "fixture.zip"
    candidate.write_bytes(b"fixture")
    venv = root / "vllm"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin/python"
    python.write_bytes(b"executable fixture")
    python.chmod(0o700)
    monkeypatch.setattr(real, "validate_candidate", lambda p: {})
    monkeypatch.setattr(real, "extract_candidate", lambda p, dest: dest.mkdir(mode=0o700))
    class Config:
        def __init__(self, **kwargs):
            assert kwargs["max_model_len"] == 32768 and kwargs["tensor_parallel_size"] == 1
    class Server:
        def __init__(self, config, adapter_manifest):
            pass
        def build_cmd(self):
            return ["/official/harness/python", "-m", "vllm.entrypoints.openai.api_server", "--model", "/public/model"]
    value = driver.build_serve_argv(candidate=candidate, model_path=root, vllm_root=venv, out=root,
                                   guard=guard, native=(lambda p: {}, Config, Server))
    assert value["executed_argv"] == [str(python), *value["original_argv"][1:]]
    assert value["server_started"] is False
    assert Path(value["candidate_dir"]).exists()
    with pytest.raises(ContractError, match="overwrite"):
        driver.build_serve_argv(candidate=candidate, model_path=root, vllm_root=venv, out=root,
                                guard=guard, native=(lambda p: {}, Config, Server))


def test_model_server_observation_identity_only_and_no_network():
    calls = []
    def fetch(url):
        calls.append(url)
        if url.endswith("/version"):
            return b'{"version":"fixture"}'
        return json.dumps({"data": [{"id": name} for name in
                          ("gemma-4-31b-it-qat-w4a16-ct", "main_lora", "tool_lora")]}).encode()
    value = driver.observe_model_server("http://127.0.0.1:8000/v1", fetch=fetch)
    assert calls == ["http://127.0.0.1:8000/version", "http://127.0.0.1:8000/v1/models"]
    assert value["version"]["body"] == {"version": "fixture"}
    with pytest.raises(ContractError, match="credentials"):
        driver.observe_model_server("http://user:neverprint@127.0.0.1:8000/v1", fetch=fetch)


def test_model_server_missing_adapter_refused():
    with pytest.raises(ContractError, match="adapter"):
        driver.observe_model_server("http://127.0.0.1:8000/v1",
                                    fetch=lambda url: b'{"version":"fixture"}' if url.endswith("/version") else b'{"data":[]}')


def test_fingerprint_capture_observations_offline(setup, monkeypatch):
    from eval import runtime_provenance as provenance
    from eval.runtime_provenance import FINGERPRINT_FIELDS
    public, root, _, fp = setup
    tasks = public / "tasks.jsonl"
    tasks.write_bytes(b"fixture source identity")
    monkeypatch.setattr(driver, "TASKS_SHA256", driver._file_identity(tasks)["sha256"])
    core = {key: fp[key] for key in FINGERPRINT_FIELDS}
    def build(*args, **kwargs):
        value = json.loads(json.dumps(core))
        value["package_versions"] = kwargs["package_versions"]
        value["model_server_identity"] = kwargs["model_server_identity"]
        return value
    monkeypatch.setattr(provenance, "build_real_fingerprint", build)
    model = root / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"deterministic weight fixture")
    lock = root / "lock"
    lock.write_bytes(b"lock fixture")
    candidate = REPO_ROOT / "artifacts/submissions/e0_official_control/submission.zip"
    observed = driver.capture_fingerprint(
        repo_root=REPO_ROOT, candidate=candidate,
        prereg=REPO_ROOT / "docs/experiments/DEV_E0_FORENSICS_V1_S1_PREREG.md", public_root=public,
        endpoint="http://127.0.0.1:8000/v1", python_executable=root / "python", harness_lock=lock,
        model_path=model, server_observer=lambda endpoint: {"version": {"body": {"version": "fixture"}}},
        harness_observer=lambda p: {"packages": {name: None for name in PACKAGES}, "python_version": "3.12.14"},
        gpu_observer=lambda: [{"name": "A100", "memory_mib": 40536}], vllm_version="fixture")
    assert observed["model_weights"]["model.safetensors"]["sha256"] == hashlib.sha256(b"deterministic weight fixture").hexdigest()
    assert observed["package_versions"]["vllm"] == "fixture"


def test_all_eight_controls_sealed_gold_exclusion_and_no_solver(setup):
    from eval.runtime_provenance import FINGERPRINT_FIELDS
    public, root, guard, fp = setup
    @dataclass
    class Config:
        artifact_root: Path
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.CONTROL_IDS)
    reference_calls = []
    def reference(root, task_id):
        reference_calls.append(task_id)
        return "public reference control fixture"
    def run_control(task, verifier, **kwargs):
        control = kwargs["control"]
        run_root = kwargs["config"].artifact_root / task.instance_id
        phase_fp = {key: fp[key] for key in FINGERPRINT_FIELDS}
        phase_fp["phase"] = "verifier"
        _raw_write(run_root / "verifier/real_fingerprint.json", phase_fp)
        _raw_write(run_root / "verifier/native_observations.json", {
            "apply_status": "not_needed" if control == "no_patch" else "applied",
            "apply_error": None, "required_tests_passed": control == "known_patch",
            "test_execution": _test_execution_receipt(exit_code=0 if control == "known_patch" else 1)})
        refs = []
        for filename in ("real_fingerprint.json", "native_observations.json"):
            info = driver._file_identity(run_root / "verifier" / filename)
            refs.append(ArtifactRef("observation", filename, info["sha256"], info["size_bytes"]))
        observed = VerifierRunResult(1, task.instance_id, "completed", patch_sha256(kwargs["returned_patch"]),
                                     resolved=control == "known_patch",
                                     native_result_json=canonical_json({"test_exit_code": 1 if control == "no_patch" else 0}),
                                     artifacts=tuple(refs), fingerprint=RuntimeFingerprint())
        result = {"schema_version": 1, "control": control, "task_id": task.instance_id,
                  "verifier_result": observed.to_dict(), "resolved": observed.resolved,
                  "run_root": str(run_root), "gold_assisted": control == "known_patch", "solver_started": False}
        _raw_write(run_root / "control_result.json", result)
        return result
    for control in ("no_patch", "known_patch"):
        value = driver.run_controls(tasks, {i: None for i in driver.CONTROL_IDS}, control=control,
                                    public_root=public, private_root=public, config=Config(root),
                                    guard=guard, fingerprint=fp, run_control=run_control, reference_reader=reference)
        assert value["passed"] is True
    assert reference_calls == list(driver.CONTROL_IDS)
    driver.validate_controls(root, fp)
    gold_root = root / "controls/known_patch"
    assert (gold_root / "GOLD_ASSISTED_VERIFIER_ONLY.json").exists()
    with pytest.raises(ContractError, match="excluded"):
        driver.write_report(gold_root, guard=guard, fingerprint=fp)
    first = root / "controls/no_patch" / driver.CONTROL_IDS[0] / "verifier/native_observations.json"
    _raw_write(first, {"apply_status": "failed"})
    with pytest.raises(ContractError, match="seal mismatch"):
        driver.validate_controls(root, fp)


def test_diagnosed_second_attempt_retains_first_and_only_selected_tasks(setup):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    calls = []
    def first(task, verifier, **kwargs):
        return _sealed_result(root, task.instance_id, fp)
    kwargs = dict(public_root=public, private_root=public, config=SimpleNamespace(artifact_root=root), guard=guard,
                  fingerprint=fp, scanner=lambda: {"p0": False}, controls_validator=lambda *a: None)
    driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, task_runner=first, **kwargs)
    first_seal = driver._read(driver._seal_path(root, driver.S1_IDS[0], 1))
    def repair(task, verifier, **kwargs):
        calls.append(task.instance_id)
        return _sealed_result(root, task.instance_id, fp, resolved=True, run_name=task.instance_id + "_repair")
    driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, task_runner=repair, attempt=2,
                  retry_ids=(driver.S1_IDS[0],), retry_diagnosis="ENVIRONMENT_VERIFICATION_ARTIFACT", **kwargs)
    assert calls == [driver.S1_IDS[0]]
    assert driver._read(driver._seal_path(root, driver.S1_IDS[0], 1)) == first_seal
    assert driver._seal_path(root, driver.S1_IDS[0], 2).exists()
    assert any(row.get("retry_diagnosis") == "ENVIRONMENT_VERIFICATION_ARTIFACT" for row in driver._ledger(root))


def test_unsealed_solver_archive_scanned_and_cannot_hide_p0(setup):
    public, root, guard, fp = setup
    sentinel = b"private_test_patch_added_line_boundary_sentinel_with_long_padding"
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    def broken(task, verifier, **kwargs):
        phase = root / "partial_archive/solver"
        phase.mkdir(parents=True)
        (phase / "process.log").write_bytes(sentinel)
        raise ContractError("fixture process failed before sealing")
    scanner = lambda: driver.leak_scan(root, public, guard=guard,
                                       patch_lines=lambda *a: {"test_patch": [sentinel], "patch": []})
    summary = driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, task_runner=broken,
                            public_root=public, private_root=public, config=SimpleNamespace(artifact_root=root),
                            guard=guard, fingerprint=fp, scanner=scanner, controls_validator=lambda *a: None)
    assert summary["started"] == 1 and summary["verified"] == 0 and summary["indeterminate"] == 12
    assert driver._load(root / "leak_scan.json")["p0"] is True
    assert driver._ledger(root)[-1]["unsealed_run_roots"] == ["partial_archive"]


def test_unattributed_solver_archive_refuses_clean_scan(setup):
    public, root, guard, _ = setup
    _raw_write(root / "run_manifest.json", {"screen": "S1", "task_ids": list(driver.S1_IDS)})
    (root / "unattributed/solver").mkdir(parents=True)
    with pytest.raises(ContractError, match="unattributed"):
        driver.leak_scan(root, public, guard=guard, patch_lines=lambda *a: {})


@pytest.mark.parametrize("error,category", [
    ("ValueError: Tool 'invented' not found.\nAvailable tools: []", "INVALID_TOOL_FATAL"),
    ("SubmissionValidationError: malformed submission", "HARNESS_COMPILE_VALIDATION"),
    ("ServerStartupError: unavailable local model server", "MODEL_SERVER_START_FAILURE"),
    ("unexpected failure", "UNKNOWN"),
])
def test_process_classification_requires_exact_native_evidence(setup, error, category):
    _, root, _, fp = setup
    result = _sealed_result(root, driver.S1_IDS[0], fp, patch="public patch")
    result = replace(result, solver_result=replace(result.solver_result, escaped_exception=error))
    record = driver.forensic_record(result.task_id, index=0, result=result)
    record = driver._enrich_record(record, result, root / (result.task_id + "_run"))
    assert record["forensics"]["primary"] == category


def test_footprint_measures_expansion_and_reserves_unpublished_private_bytes(setup):
    from eval.solver_task import PublicAssetRef, SolverTask
    public, root, _, _ = setup
    task_id = driver.S1_IDS[0]
    (public / "snapshots").mkdir()
    snapshot = public / "snapshots" / (task_id + ".tgz")
    with tarfile.open(snapshot, "w:gz") as archive:
        member = tarfile.TarInfo("public_repo/file.py")
        member.size = 100
        archive.addfile(member, io.BytesIO(b"x" * 100))
    (public / "tasks.jsonl").write_bytes(b"source")
    ref = PublicAssetRef("snapshot", task_id, "snapshots/" + task_id + ".tgz",
                         hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot.stat().st_size)
    task = SolverTask(1, task_id, "fastapi/fastapi", "1" * 40, "public problem", "", ref)
    model, harness, vllm = root / "model", root / "harness", root / "vllm"
    for path in (model, harness, vllm):
        path.mkdir()
        (path / "file").write_bytes(b"x" * 10)
    (harness / "interpreter_link").symlink_to("/usr/bin/python3")
    candidate = root / "candidate.zip"
    candidate.write_bytes(b"candidate")
    footprint = driver.measure_footprint(model=model, public=public, private=root / "private",
                                         harness=harness, vllm=vllm, tasks=(task,), candidate=candidate)
    assert footprint["private"] >= len(b"source") + len(task.to_json().encode())
    assert footprint["worker"] == 100 + footprint["public"] + candidate.stat().st_size
    assert footprint["harness"] > 10


def test_private_publication_resume_keeps_contract_and_does_not_reopen_private_bytes(setup, monkeypatch):
    from eval.solver_task import PublicAssetRef, SolverTask
    from eval.verifier_task import PrivateTestPatchRef, VerifierTask
    from eval import verifier_data
    public, root, guard, _ = setup
    (public / "tasks.jsonl").write_bytes(b"trusted source fixture")
    task_id = driver.S1_IDS[0]
    ref = PublicAssetRef("snapshot", task_id, "snapshots/" + task_id + ".tgz", "1" * 64, 10)
    solver = SolverTask(1, task_id, "fastapi/fastapi", "1" * 40, "public problem", "", ref)
    verification = {"schema_version": 1, "sandbox": "subprocess", "timeout_seconds": 60,
                    "setup_py_sha256": "2" * 64, "wheels_tree_sha256": "3" * 64}
    private = root / "private"
    patch = b"private fixture"
    verifier = VerifierTask(1, task_id, solver.repo, solver.base_commit, ref,
                            PrivateTestPatchRef(hashlib.sha256(patch).hexdigest(), len(patch)),
                            driver._digest(verification), None, None)
    def publish(*args, **kwargs):
        private.mkdir(mode=0o700)
        dest = private / (task_id + ".test.patch")
        dest.write_bytes(patch)
        dest.chmod(0o600)
        return {task_id: verifier}
    monkeypatch.setattr(verifier_data, "publish_private_test_patches", publish)
    first = driver._private_publication(public, (task_id,), private, guard, verification, (solver,))
    original_read = driver._read
    def read(path):
        assert not str(path).endswith(".test.patch"), "private bytes must wait until solver exit"
        return original_read(path)
    monkeypatch.setattr(driver, "_read", read)
    monkeypatch.setattr(verifier_data, "publish_private_test_patches", lambda *a, **k: pytest.fail("resume cannot overwrite publication"))
    assert driver._private_publication(public, (task_id,), private, guard, verification, (solver,)) == first
    (private / (task_id + ".test.patch")).chmod(0o644)
    with pytest.raises(ContractError, match="metadata"):
        driver._private_publication(public, (task_id,), private, guard, verification, (solver,))


def test_interrupted_solver_archive_identity_associates_to_started_ledger(setup):
    public, root, guard, fp = setup
    task_id = driver.S1_IDS[0]
    _raw_write(root / "fingerprint.json", fp)
    _raw_write(root / "run_manifest.json", {"screen": "S1", "task_ids": list(driver.S1_IDS),
                                            "fingerprint_sha256": driver._digest(fp)})
    phase = root / "interrupted/solver"
    phase.mkdir(parents=True)
    sentinel = b"private_long_line_boundary_sentinel_for_interrupted_solver_phase"
    (phase / "process.log").write_bytes(sentinel)
    _raw_write(root / "interrupted/run_identity.json", {"task_id": task_id,
               "candidate_sha256": fp["candidate_identity"]["sha256"],
               "preregistration_sha256": fp["preregistration_identity"]["sha256"]})
    driver._append_ledger(root, {"task_id": task_id, "attempt": 1, "status": "started",
                                "fingerprint_sha256": driver._digest(fp)}, guard)
    value = driver.leak_scan(root, public, guard=guard, patch_lines=lambda *a: {"test_patch": [sentinel], "patch": []})
    assert value["p0"] is True and value["test_patch_hits"][0]["task_id"] == task_id


@pytest.mark.parametrize("sealed", [True, False])
def test_json_escaped_private_lines_are_p0_reference_overlap_diagnostic(setup, sealed):
    public, root, guard, fp = setup
    task_id = driver.S1_IDS[0]
    private_line = 'assert "private\\test sentinel with escaped quotes"\t== "a sufficiently long private assertion"'
    reference_line = 'return "independently produced\\public fix with quotes"\t+ "padding to exceed forty characters"'
    if sealed:
        result = _sealed_result(root, task_id, fp)
        seal = _seal(root, result, fp)
        solver = root / seal["run_root"] / "solver"
    else:
        solver = root / "partial/solver"
        solver.mkdir(parents=True)
        driver._append_ledger(root, {"task_id": task_id, "attempt": 1, "status": "failed",
                                    "unsealed_run_roots": ["partial"]}, guard)
    _raw_write(root / "run_manifest.json", {"screen": "S1", "task_ids": list(driver.S1_IDS)})
    trace = solver / "native_trace.json"
    _raw_write(trace, {"entries": [{"content": private_line}, {"content": reference_line}]})
    assert private_line.encode() not in trace.read_bytes()
    assert reference_line.encode() not in trace.read_bytes()
    scanner = lambda *args: {"test_patch": [private_line.encode()], "patch": [reference_line.encode()]}
    value = driver.leak_scan(root, public, guard=guard, patch_lines=scanner)
    assert value["p0"] is True
    assert len(value["test_patch_hits"]) == 1 and value["test_patch_hits"][0]["count"] == 1
    assert len(value["patch_overlap"]) == 1 and value["patch_overlap"][0]["count"] == 1
    assert value["patch_overlap_is_leakage"] is False
    exported = (root / "leak_scan.json").read_text()
    assert private_line not in exported and reference_line not in exported


def test_json_string_views_decode_keys_nested_payloads_and_jsonl_without_duplicate_views():
    sentinel = 'private "quoted"\\assertion\twith more than forty nonwhitespace sentinel characters'
    payload = json.dumps({sentinel: "other", "native_result_json": json.dumps({"result": sentinel})}).encode()
    views = tuple(driver._json_string_views(payload))
    assert sum(view.count(sentinel.encode()) for view in views) == 2
    jsonl = json.dumps({"message": sentinel}).encode() + b"\nplain log text\n" + json.dumps({"message": sentinel}).encode()
    assert sum(view.count(sentinel.encode()) for view in driver._json_string_views(jsonl)) == 2


def test_scan_raw_and_decoded_json_hit_count_not_added_twice(setup):
    public, root, guard, fp = setup
    sentinel = b"private_plain_assertion_without_escaping_and_more_than_forty_chars"
    result = _sealed_result(root, driver.S1_IDS[0], fp)
    seal = _seal(root, result, fp)
    _raw_write(root / "run_manifest.json", {"screen": "S1", "task_ids": list(driver.S1_IDS)})
    _raw_write(root / seal["run_root"] / "solver/native_trace.json", {"message": sentinel.decode()})
    value = driver.leak_scan(root, public, guard=guard,
                            patch_lines=lambda *args: {"test_patch": [sentinel], "patch": []})
    assert value["p0"] is True and value["test_patch_hits"][0]["count"] == 1


def test_json_duplicate_keys_do_not_hide_escaped_private_strings():
    sentinel = 'private "quoted" long assertion with more than forty sentinel characters'
    raw = ('{"message":' + json.dumps(sentinel) + ',"message":"public"}').encode()
    assert sentinel.encode() not in raw
    assert sum(view.count(sentinel.encode()) for view in driver._json_string_views(raw)) == 1


@pytest.mark.parametrize("repaired", [False, True])
def test_attempt1_resume_preserves_terminal_failure_and_reaches_later_tasks(setup, repaired):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    first_id, failed_id = driver.S1_IDS[:2]
    _seal(root, _sealed_result(root, first_id, fp), fp)
    driver._append_ledger(root, {"task_id": first_id, "attempt": 1, "status": "completed"}, guard)
    driver._append_ledger(root, {"task_id": failed_id, "attempt": 1, "status": "started"}, guard)
    failure = {"task_id": failed_id, "attempt": 1, "status": "failed",
               "error": "UNSEALED_RUNTIME_FAILURE", "unsealed_run_roots": []}
    driver._append_ledger(root, failure, guard)
    if repaired:
        repair = _sealed_result(root, failed_id, fp, resolved=True, run_name=failed_id + "_repair")
        _seal(root, repair, fp, attempt=2)
        driver._append_ledger(root, {"task_id": failed_id, "attempt": 2, "status": "completed"}, guard)
    calls = []
    def runner(task, verifier, **kwargs):
        calls.append(task.instance_id)
        return _sealed_result(root, task.instance_id, fp)
    driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, public_root=public, private_root=public,
                  config=SimpleNamespace(artifact_root=root), guard=guard, fingerprint=fp,
                  task_runner=runner, scanner=lambda: {"p0": False}, controls_validator=lambda *a: None)
    assert calls == list(driver.S1_IDS[2:])
    assert failure in driver._ledger(root)
    assert not driver._seal_path(root, failed_id, 1).exists()
    assert sum(row.get("task_id") == failed_id and row.get("attempt") == 1
               and row.get("status") == "started" for row in driver._ledger(root)) == 1
    assert driver._seal_path(root, failed_id, 2).exists() is repaired


def test_attempt1_resume_dangling_start_still_refuses(setup):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    _seal(root, _sealed_result(root, driver.S1_IDS[0], fp), fp)
    driver._append_ledger(root, {"task_id": driver.S1_IDS[1], "attempt": 1, "status": "started"}, guard)
    with pytest.raises(ContractError, match="unsealed"):
        driver.run_s1(tasks, {}, public_root=public, private_root=public,
                      config=SimpleNamespace(artifact_root=root), guard=guard, fingerprint=fp,
                      task_runner=lambda *a, **k: pytest.fail("dangling attempt cannot run"),
                      scanner=lambda: {"p0": False}, controls_validator=lambda *a: None)


def test_attempt1_resume_dangling_start_with_valid_later_seal_skips(setup):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    _seal(root, _sealed_result(root, driver.S1_IDS[0], fp), fp)
    repaired_id = driver.S1_IDS[1]
    driver._append_ledger(root, {"task_id": repaired_id, "attempt": 1, "status": "started"}, guard)
    _seal(root, _sealed_result(root, repaired_id, fp, run_name=repaired_id + "_repair"), fp, attempt=2)
    calls = []
    def runner(task, verifier, **kwargs):
        calls.append(task.instance_id)
        return _sealed_result(root, task.instance_id, fp)
    driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, public_root=public, private_root=public,
                  config=SimpleNamespace(artifact_root=root), guard=guard, fingerprint=fp,
                  task_runner=runner, scanner=lambda: {"p0": False}, controls_validator=lambda *a: None)
    assert calls == list(driver.S1_IDS[2:])
    assert not driver._seal_path(root, repaired_id, 1).exists()


def test_attempt1_resume_refuses_invalid_later_attempt_seal(setup):
    public, root, guard, fp = setup
    tasks = tuple(SimpleNamespace(instance_id=i) for i in driver.S1_IDS)
    _seal(root, _sealed_result(root, driver.S1_IDS[0], fp), fp)
    task_id = driver.S1_IDS[1]
    driver._append_ledger(root, {"task_id": task_id, "attempt": 1, "status": "started"}, guard)
    driver._append_ledger(root, {"task_id": task_id, "attempt": 1, "status": "failed"}, guard)
    seal = _seal(root, _sealed_result(root, task_id, fp, run_name=task_id + "_repair"), fp, attempt=2)
    seal["fingerprint_sha256"] = "0" * 64
    _raw_write(driver._seal_path(root, task_id, 2), seal)
    with pytest.raises(ContractError, match="fingerprint"):
        driver.run_s1(tasks, {}, public_root=public, private_root=public,
                      config=SimpleNamespace(artifact_root=root), guard=guard, fingerprint=fp,
                      task_runner=lambda *a, **k: pytest.fail("tampered repair cannot permit traversal"),
                      scanner=lambda: {"p0": False}, controls_validator=lambda *a: None)


def _public_snapshot_scan_fixture(setup, monkeypatch, *, public_lines, private_lines, evidence):
    from eval.solver_task import PublicAssetRef, SolverTask
    public, root, guard, fp = setup
    task_id = driver.S1_IDS[0]
    snapshot = public / "snapshots" / (task_id + ".tgz")
    snapshot.parent.mkdir()
    body = b"\n".join(public_lines) + b"\n"
    with tarfile.open(snapshot, "w:gz") as archive:
        member = tarfile.TarInfo("tests/public_fixture.py")
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    ref = PublicAssetRef("snapshot", task_id, "snapshots/" + task_id + ".tgz",
                         hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot.stat().st_size)
    task = SolverTask(1, task_id, "fastapi/fastapi", "1" * 40, "public fixture problem", "", ref)
    source = driver._jsonl_row({"instance_id": task_id,
                               "test_patch": "\n".join("+" + s.decode() for s in private_lines),
                               "patch": ""}).encode()
    (public / "tasks.jsonl").write_bytes(source)
    monkeypatch.setattr(driver, "TASKS_SHA256", hashlib.sha256(source).hexdigest())
    result = _sealed_result(root, task_id, fp, solver_task=task)
    seal = _seal(root, result, fp)
    _raw_write(root / "run_manifest.json", {"screen": "S1", "task_ids": list(driver.S1_IDS),
                                            "solver_contracts": {task_id: task.to_dict()}})
    _raw_write(root / seal["run_root"] / "run_identity.json", {"task_id": task_id,
                                                              "solver_contract_sha256": task.sha256()})
    _raw_write(root / seal["run_root"] / "solver/native_trace.json", {"message": evidence.decode()})
    return public, root, guard, task, snapshot


def test_private_line_absent_from_public_snapshot_still_triggers_p0(setup, monkeypatch):
    private = b"assert truly_private_fixture_assertion_with_long_padding == True"
    public, root, guard, task, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[b"public fixture"], private_lines=[private], evidence=private)
    value = driver.leak_scan(root, public, guard=guard)
    assert value["p0"] is True and value["test_patch_hits"][0]["task_id"] == task.instance_id
    assert private.decode() not in (root / "leak_scan.json").read_text()


def test_public_snapshot_line_is_excluded_from_private_leak_p0(setup, monkeypatch):
    line = b"assert legitimately_public_fixture_assertion_with_long_padding == True"
    public, root, guard, _, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[line], private_lines=[line], evidence=line)
    value = driver.leak_scan(root, public, guard=guard)
    assert value["p0"] is False
    counts = value["private_needle_diagnostics"][0]
    assert counts["total_candidate_private_needle_count"] == 1
    assert counts["excluded_public_needle_count"] == 1
    assert counts["effective_private_needle_count"] == 0
    assert counts["scan_sensitivity"] == "none"
    assert line.decode() not in (root / "leak_scan.json").read_text()


@pytest.mark.parametrize("private_hit", [False, True])
def test_mixed_public_private_needles_only_private_evidence_triggers_p0(setup, monkeypatch, private_hit):
    public_line = b"assert public_fixture_assertion_with_more_than_forty_characters == True"
    private_line = b"assert private_fixture_assertion_with_more_than_forty_characters == True"
    public, root, guard, _, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[public_line], private_lines=[public_line, private_line],
        evidence=private_line if private_hit else public_line)
    value = driver.leak_scan(root, public, guard=guard)
    assert value["p0"] is private_hit
    counts = value["private_needle_diagnostics"][0]
    assert counts["total_candidate_private_needle_count"] == 2
    assert counts["excluded_public_needle_count"] == 1
    assert counts["effective_private_needle_count"] == 1
    assert counts["scan_sensitivity"] == "exact_private_lines"
    assert all(hit["line_sha256"] == hashlib.sha256(private_line).hexdigest() for hit in value["test_patch_hits"])
    exported = (root / "leak_scan.json").read_text()
    assert public_line.decode() not in exported and private_line.decode() not in exported


def test_all_candidate_private_needles_public_records_no_sensitivity(setup, monkeypatch):
    lines = [b"assert first_public_fixture_assertion_with_long_padding == True",
             b"assert second_public_fixture_assertion_with_long_padding == True"]
    public, root, guard, _, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=lines, private_lines=lines, evidence=b"\n".join(lines))
    value = driver.leak_scan(root, public, guard=guard)
    assert value["p0"] is False and value["test_patch_hits"] == []
    counts = value["private_needle_diagnostics"][0]
    assert counts["excluded_public_needle_count"] == counts["total_candidate_private_needle_count"] == 2
    assert counts["effective_private_needle_count"] == 0 and counts["scan_sensitivity"] == "none"


def test_snapshot_filter_refuses_bytes_changed_from_admitted_contract(setup, monkeypatch):
    line = b"assert private_fixture_assertion_with_more_than_forty_characters == True"
    public, root, guard, _, snapshot = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[b"public fixture"], private_lines=[line], evidence=line)
    with tarfile.open(snapshot, "w:gz") as archive:
        member = tarfile.TarInfo("tests/changed.py")
        member.size = len(line)
        archive.addfile(member, io.BytesIO(line))
    with pytest.raises(ContractError, match="snapshot.*authority"):
        driver.leak_scan(root, public, guard=guard)


def test_snapshot_filter_refuses_changed_admission_contract_map(setup, monkeypatch):
    line = b"assert private_fixture_assertion_with_more_than_forty_characters == True"
    public, root, guard, task, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[b"public fixture"], private_lines=[line], evidence=line)
    manifest = driver._load(root / "run_manifest.json")
    manifest["solver_contracts"][task.instance_id]["snapshot"]["sha256"] = "9" * 64
    _raw_write(root / "run_manifest.json", manifest)
    with pytest.raises(ContractError, match="contract.*admission|admission.*contract"):
        driver.leak_scan(root, public, guard=guard)


def test_public_snapshot_needle_crossing_read_chunks_is_excluded(setup, monkeypatch):
    line = b"assert public_assertion_spanning_scan_chunks_with_long_padding == True"
    prefix = b"x" * (1024 * 1024 - 10)
    public, root, guard, _, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[prefix + line], private_lines=[line], evidence=line)
    value = driver.leak_scan(root, public, guard=guard)
    assert value["p0"] is False
    assert value["private_needle_diagnostics"][0]["excluded_public_needle_count"] == 1


@pytest.mark.parametrize("private_hit", [False, True])
def test_unsealed_archive_filters_original_admitted_snapshot(setup, monkeypatch, private_hit):
    public_line = b"assert public_unsealed_fixture_assertion_with_long_padding == True"
    private_line = b"assert private_unsealed_fixture_assertion_with_long_padding == True"
    public, root, guard, task, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[public_line], private_lines=[public_line, private_line],
        evidence=private_line if private_hit else public_line)
    seal_path = driver._seal_path(root, task.instance_id, 1)
    run_root = root / driver._load(seal_path)["run_root"]
    seal_path.unlink()
    (run_root / "task_result.json").unlink()
    driver._append_ledger(root, {"task_id": task.instance_id, "attempt": 1, "status": "started"}, guard)
    driver._append_ledger(root, {"task_id": task.instance_id, "attempt": 1, "status": "failed",
                                "unsealed_run_roots": [run_root.name]}, guard)
    value = driver.leak_scan(root, public, guard=guard)
    assert value["p0"] is private_hit
    assert value["private_needle_diagnostics"][0]["effective_private_needle_count"] == 1


def test_filtered_private_lines_preserve_nested_json_and_duplicate_key_scan(setup, monkeypatch):
    public_line = b"assert public_fixture_assertion_with_more_than_forty_characters == True"
    private_line = b'assert "private\\quoted fixture assertion with enough padding"\t== True'
    public, root, guard, task, _ = _public_snapshot_scan_fixture(
        setup, monkeypatch, public_lines=[public_line], private_lines=[public_line, private_line],
        evidence=b"public evidence")
    seal = driver._load(driver._seal_path(root, task.instance_id, 1))
    nested = json.dumps({"message": private_line.decode()})
    payload = ('{"message":' + json.dumps(nested) + ',"message":"public"}').encode()
    (root / seal["run_root"] / "solver/native_trace.json").write_bytes(payload)
    assert private_line not in payload
    value = driver.leak_scan(root, public, guard=guard)
    assert value["p0"] is True and value["test_patch_hits"][0]["count"] == 1
    assert value["test_patch_hits"][0]["line_sha256"] == hashlib.sha256(private_line).hexdigest()
    assert private_line.decode() not in (root / "leak_scan.json").read_text()


def test_run_s1_persists_original_solver_contracts_before_worker_launch(setup):
    from eval.solver_task import PublicAssetRef, SolverTask
    public, root, guard, fp = setup
    repos = {"fastapi": "fastapi/fastapi", "httpx": "encode/httpx",
             "requests": "psf/requests", "rich": "Textualize/rich"}
    tasks = tuple(SolverTask(1, task_id, repos[task_id.split("_")[0]], "1" * 40,
                             "public fixture problem", "",
                             PublicAssetRef("snapshot", task_id, "snapshots/" + task_id + ".tgz", "2" * 64, 1))
                  for task_id in driver.S1_IDS)
    expected = {task.instance_id: task.to_dict() for task in tasks}
    def runner(task, verifier, **kwargs):
        assert driver._load(root / "run_manifest.json")["solver_contracts"] == expected
        return _sealed_result(root, task.instance_id, fp, solver_task=task)
    driver.run_s1(tasks, {i: None for i in driver.S1_IDS}, public_root=public, private_root=public,
                  config=SimpleNamespace(artifact_root=root), guard=guard, fingerprint=fp,
                  task_runner=runner, scanner=lambda: {"p0": False}, controls_validator=lambda *a: None)
    assert driver._load(root / "run_manifest.json")["solver_contracts"] == expected


def _run_cli_args():
    args = ["run"]
    for flag in ("public-root", "candidate", "prereg", "harness-python", "harness-root", "harness-lock",
                 "source-wheels-root", "model-path", "vllm-root", "private-root", "artifact-root",
                 "worker-root", "export-root", "compaction"):
        args += ["--" + flag, "/fixture/" + flag]
    return args + ["--endpoint", "http://127.0.0.1:8000/v1", "--worker-user", "fixture_worker", "--screen", "S1"]


def test_real_s1_cli_requires_explicit_3600_infrastructure_timeout():
    parser = driver._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(_run_cli_args())
    with pytest.raises(SystemExit):
        parser.parse_args(_run_cli_args() + ["--worker-timeout-seconds", "600"])
    args = parser.parse_args(_run_cli_args() + ["--worker-timeout-seconds", "3600"])
    assert args.worker_timeout_seconds == 3600
    from eval.real_contracts import FROZEN_BUDGET
    assert FROZEN_BUDGET == {"time_minutes": 1.0, "tool_calls": 10, "turns": 50,
                             "command_timeout_seconds": 60}


def test_solver_dependency_setup_is_environment_failure_and_not_test_execution(setup):
    _, root, _, fingerprint = setup
    result = _sealed_result(root, driver.S1_IDS[0], fingerprint)
    run_root = root / (result.task_id + "_run")
    path = run_root / "solver/native_observations.json"
    _raw_write(path, {"manager": {
        "setup_error": {"phase": "dependency provisioning", "exception_type": "RealAdmissionError"},
        "commands": [{"purpose": "dependency_setup", "command": "python3 -I -m pytest --version", "exit_code": 0},
                     {"purpose": "native_test", "command": "python3 -m pytest test_public_fixture.py", "exit_code": 1},
                     {"purpose": "native", "command": "git diff --binary", "exit_code": 0}]}})
    identity = driver._file_identity(path)
    reference = ArtifactRef("native_observations", path.name, identity["sha256"], identity["size_bytes"])
    result = replace(result, solver_result=replace(result.solver_result, runtime_status="worker_error",
                     escaped_exception="eval.runtime_real.RealAdmissionError: sandbox dependency setup failed",
                     artifacts=(*result.solver_result.artifacts, reference)))
    record = driver._enrich_record(driver.forensic_record(result.task_id, index=0, result=result), result, run_root)
    assert record["forensics"]["primary"] == "ENVIRONMENT_VERIFICATION_ARTIFACT"
    assert record["tests"]["commands"]["value"] == [{"artifact": path.name, "command_index": 1}]
    assert record["tests"]["exit_codes"]["value"] == [1]
