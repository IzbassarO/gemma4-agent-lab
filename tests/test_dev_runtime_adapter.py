"""Native CPU scripted cases through the fresh coordinator/worker topology."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import uuid

import pytest

from eval._contracts import ContractError, canonical_json
from eval.runtime import RuntimeConfig, _archive_phase, _execute_worker, _prepare_worker, confined_worker_argv, run_task, worker_environment
from eval.runtime_adapter import RUNTIME_WHEEL_NAME, RUNTIME_WHEEL_SHA256, RuntimeAdmissionError, _admit_snapshot
from eval.runtime_fixture import CHANGED, INITIAL, expected_patch
from eval.runtime_verifier_fixture import create_synthetic_fixture, verification_config
from eval.worker_common import E0_SHA256, E0_SIZE
from tools.common import REPO_ROOT

HARNESS = Path("/private/tmp/h23-cpu.UA1C8T/harness")
CANDIDATE = REPO_ROOT / "artifacts/submissions/e0_official_control/submission.zip"
WHEELS = REPO_ROOT / "artifacts/harness_wheels/v28"


@pytest.fixture(scope="module")
def native_fixture(tmp_path_factory):
    if not (HARNESS / "bin/python").is_file() or not CANDIDATE.is_file():
        pytest.skip("exact acquired harness and frozen E0 are unavailable; native certification requires both")
    root = tmp_path_factory.mktemp("native-runtime-fixture").resolve()
    task, verifier, private = create_synthetic_fixture(root / "public")
    private_root = root / "private"
    private_root.mkdir(mode=0o700)
    patch_path = private_root / "test.patch"
    patch_path.write_text(private)
    patch_path.chmod(0o600)
    artifact_root = REPO_ROOT / "artifacts/dev_runtime" / ("pytest-" + uuid.uuid4().hex)
    artifact_root.mkdir(mode=0o700, parents=True)
    config = RuntimeConfig(HARNESS / "bin/python", HARNESS, CANDIDATE, artifact_root,
                           source_wheels_root=WHEELS)
    return task, verifier, root / "public", private_root, config


@pytest.fixture(scope="module")
def native_results(native_fixture):
    task, verifier, public_root, private_root, base = native_fixture
    results = {}
    for case in ("H05", "H04", "H18", "H13", "H14", "H29", "H29_BOUNDARY", "EMPTY_SUBMIT", "EMPTY_FINAL"):
        config = replace(base, synthetic_case=case)
        result = run_task(task, verifier, public_root=public_root, private_root=private_root,
                          test_patch_relative_path="test.patch", config=config)
        evidence = next(path.parent for path in base.artifact_root.glob("*/task_result.json")
                        if json.loads(path.read_text())["solver_result"]["started_at"] == result.solver_result.started_at)
        results[case] = result, evidence
    return results


def observations(evidence, phase):
    return json.loads((evidence / phase / "native_observation.json").read_text())


def manager_observations(evidence, phase):
    data = json.loads((evidence / phase / "native_observations.json").read_text())
    return data["manager"] if phase == "solver" else data


def test_native_write_submit_control(native_results):
    result, evidence = native_results["H05"]
    assert result.resolved is True
    assert result.solver_result.returned_patch == expected_patch()
    assert result.solver_result.submitted_patch == expected_patch()
    assert result.solver_result.counted_tool_calls == 1
    assert result.solver_result.llm_calls == 3
    assert result.solver_result.fallback_used is False
    native = observations(evidence, "solver")
    assert native["native_result"]["workspace"]["app_content"] == CHANGED
    assert native["native_result"]["sandbox_ids"] == native["native_result"]["stopped_ids"]
    imports = native["native_result"]["solver_imports"]
    assert "swegemma.harness.agent_runner" in imports
    assert not any(name.startswith("eval.verifier_") or name in (
        "eval.public_data", "eval.runtime_verifier_fixture", "swegemma.evaluate",
        "swegemma.harness.verification", "swegemma.harness.sample_verification") for name in imports)


def test_native_undeclared_tool_is_fatal(native_results):
    result, evidence = native_results["H04"]
    assert result.resolved is False
    assert result.solver_result.returned_patch == ""
    assert result.solver_result.agent_error
    assert result.solver_result.counted_tool_calls == 0
    assert result.solver_result.tool_attempts == 1
    assert observations(evidence, "solver")["native_result"]["workspace"]["app_content"] == INITIAL


def test_native_h18_returns_empty_after_workspace_edit(native_results):
    result, evidence = native_results["H18"]
    assert result.solver_result.returned_patch == ""
    assert result.solver_result.returned_patch_sha256 == hashlib.sha256(b"").hexdigest()
    assert result.solver_result.workspace_patch is None
    assert result.solver_result.counted_tool_calls == 1
    assert result.solver_result.tool_attempts == 2
    assert result.resolved is False
    solver = observations(evidence, "solver")
    assert solver["native_result"]["workspace"]["app_content"] == CHANGED
    assert solver["native_result"]["workspace"]["edited"] is True
    verifier = observations(evidence, "verifier")
    assert verifier["returned_patch_sha256"] == hashlib.sha256(b"").hexdigest()
    assert verifier["lifecycle"]["workspace"]["app_content"] == INITIAL
    assert verifier["native_result"]["agent_patch"] == ""


def test_native_timeout_preserves_native_fallback(native_results):
    result, evidence = native_results["H13"]
    assert result.solver_result.timeout is True
    assert result.solver_result.fallback_used is True
    assert result.solver_result.returned_patch == expected_patch()
    assert result.solver_result.submitted_patch is None
    assert result.solver_result.counted_tool_calls == 1
    assert result.resolved is True
    assert observations(evidence, "solver")["native_result"]["http_request_count"] == 2
    assert observations(evidence, "solver")["native_result"]["recovered_exceptions"][0]["class"] == "builtins.TimeoutError"


def test_native_budget_rejects_write_body(native_results):
    result, evidence = native_results["H14"]
    assert result.solver_result.budget_exhausted is True
    assert result.solver_result.tool_attempts == 4
    assert result.solver_result.counted_tool_calls == 3
    assert result.solver_result.fallback_used is True
    assert result.solver_result.returned_patch == expected_patch()
    assert result.resolved is True
    trace = json.loads((evidence / "solver/native_trace.json").read_text())
    responses = [json.loads(item["result"]) for item in trace["entries"]
                 if item.get("type") == "tool_response" and item.get("tool") == "write_file"]
    assert responses[-1]["error_type"] == "BudgetExceeded"
    assert observations(evidence, "solver")["native_result"]["workspace"]["app_content"] == CHANGED


def test_native_status_submit_remain_free_at_limit(native_results):
    result, evidence = native_results["H29"]
    assert result.solver_result.counted_tool_calls == 2
    assert result.solver_result.tool_attempts == 4
    assert result.solver_result.submitted_patch == expected_patch()
    assert result.solver_result.fallback_used is False
    assert result.solver_result.agent_error is None
    assert result.resolved is True
    trace = json.loads((evidence / "solver/native_trace.json").read_text())
    status = next(json.loads(item["result"]) for item in trace["entries"]
                  if item.get("type") == "tool_response" and item.get("tool") == "get_status")
    assert status["tool_calls_used"] == 2 and status["tool_calls_remaining"] == 0


def test_native_final_boundary_differs_from_free_tools(native_results):
    result, _ = native_results["H29_BOUNDARY"]
    assert result.solver_result.counted_tool_calls == 2
    assert result.solver_result.tool_attempts == 2
    assert result.solver_result.budget_exhausted is True
    assert result.solver_result.submitted_patch is None
    assert result.solver_result.fallback_used is True


def test_empty_patch_native_semantics_are_observed(native_results):
    submitted, _ = native_results["EMPTY_SUBMIT"]
    final, _ = native_results["EMPTY_FINAL"]
    assert submitted.solver_result.returned_patch == final.solver_result.returned_patch == ""
    assert submitted.solver_result.submitted_patch == ""
    assert submitted.solver_result.agent_error is None
    assert final.solver_result.submitted_patch is None
    assert final.solver_result.agent_error == "Agent completed execution without calling submit_patch."
    assert submitted.resolved is final.resolved is False


def test_native_fresh_verifier_lifecycle(native_results):
    _, evidence = native_results["H05"]
    solver = observations(evidence, "solver")
    verifier = observations(evidence, "verifier")
    assert solver["native_result"]["sandbox_ids"] != verifier["lifecycle"]["sandbox_ids"]
    assert verifier["lifecycle"]["sandbox_ids"] == verifier["lifecycle"]["stopped_ids"]
    assert verifier["lifecycle"]["workspace"]["private_test_present"] is True
    assert (evidence / "verifier/junit.xml").is_file()
    result = verifier["native_result"]
    assert result["resolved"] is True and result["test_exit_code"] == 0
    assert "1 passed" in result["test_output"]


@pytest.mark.parametrize("phase", ("solver", "verifier"))
def test_native_local_wheel_branch_never_queries_fallback_roots(phase, native_results):
    _, evidence = native_results["H05"]
    manager = manager_observations(evidence, phase)
    assert manager["local_wheel"] == {"filename": RUNTIME_WHEEL_NAME, "sha256": RUNTIME_WHEEL_SHA256}
    probes = manager["native_wheel_probes"]
    assert {probe["method"] for probe in probes} == {"exists", "glob", "scandir"}
    assert all(probe["local"] is True for probe in probes)
    assert {probe["command_index"] for probe in probes} == set(range(len(manager["commands"])))
    assert len({probe["path"] for probe in probes}) == 1
    assert Path(probes[0]["path"]).name == "wheels"
    assert all(item["command"] != "mkdir -p /wheels" for item in manager["commands"])


@pytest.mark.parametrize("case", ("H05", "H18", "H14", "H29"))
def test_native_instrumentation_preserves_outcome_and_trace(case, native_fixture, native_results):
    task, verifier, public, private, config = native_fixture
    observed, observed_evidence = native_results[case]
    plain = run_task(task, verifier, public_root=public, private_root=private, test_patch_relative_path="test.patch",
                     config=replace(config, synthetic_case=case, observation_enabled=False))
    for field in ("returned_patch", "returned_patch_sha256", "submitted_patch", "agent_error", "escaped_exception",
                  "llm_calls", "counted_tool_calls", "tool_attempts", "fallback_used", "timeout", "budget_exhausted"):
        assert getattr(plain.solver_result, field) == getattr(observed.solver_result, field)
    assert plain.resolved == observed.resolved
    plain_evidence = next(path.parent for path in config.artifact_root.glob("*/task_result.json")
                          if json.loads(path.read_text())["solver_result"]["started_at"] == plain.solver_result.started_at)

    def native_behavior(path):
        trace = json.loads((path / "solver/native_trace.json").read_text())
        result = []
        for entry in trace["entries"]:
            item = {key: value for key, value in entry.items() if key not in ("timestamp", "elapsed_s")}
            if item.get("type") == "tool_response" and item.get("tool") == "get_status":
                response = json.loads(item["result"])
                for clock in ("agent_elapsed_seconds", "time_seconds_remaining"):
                    response.pop(clock, None)
                item["result"] = response
            result.append(item)
        return result

    assert native_behavior(plain_evidence) == native_behavior(observed_evidence)
    def native_commands(path, phase):
        native = observations(path, phase)
        return [(re.sub(r"tmp[a-z0-9_]{8}\.patch|_swegemma_junit_[0-9a-f]{12}\.xml", "TEMP_ARTIFACT", item["command"]),
                 item["exit_code"]) for item in native["command_observations"]]

    for phase in ("solver", "verifier"):
        assert native_commands(plain_evidence, phase) == native_commands(observed_evidence, phase)
        def wheel_probes(path):
            return [(item["method"], item["local"], item["command_index"])
                    for item in manager_observations(path, phase)["native_wheel_probes"]]
        assert wheel_probes(plain_evidence) == wheel_probes(observed_evidence)
    assert json.loads((plain_evidence / "solver/events.json").read_text())["events"] == []


def test_native_invalid_patch_fails_application_before_tests(native_fixture):
    task, verifier, public, private, config = native_fixture
    from tools.harness_cert._synthetic_verification import prepare_support
    root, runtime = _prepare_worker(config, task, public, verifier=True)
    output = config.artifact_root / ("invalid-patch-" + uuid.uuid4().hex)
    try:
        support = prepare_support(REPO_ROOT, root)
        runtime["pytest_support_root"] = support["root"]
        Path(support["root"]).chmod(0o700)
        patch = "invalid patch\n"
        head = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              text=True, check=True, capture_output=True).stdout.strip()
        request = {"schema_version": 1, "candidate": {"relative_path": "submission.zip", "sha256": E0_SHA256,
                   "size_bytes": E0_SIZE}, "observation_enabled": True, "synthetic_case": "H05",
                   "mode": "native_scripted", "provenance": {"git_head": head,
                   "solver_contract_sha256": task.sha256(), "task_manifest_sha256": "UNKNOWN"},
                   "task": verifier.to_dict(), "runtime": runtime, "returned_patch": patch,
                   "returned_patch_sha256": hashlib.sha256(patch.encode()).hexdigest(), "agent_error": None,
                   "test_patch": (private / "test.patch").read_text(), "verification_config": verification_config()}
        result = _execute_worker(config, request, root, "verifier")
        native = json.loads((root / "evidence/native_observation.json").read_text())
        assert result["resolved"] is False
        assert native["native_result"]["agent_patch"] == patch
        assert native["native_result"]["error_message"].startswith("Failed to apply agent patch:")
        assert not any("-m pytest" in item["command"] for item in native["command_observations"])
        assert native["lifecycle"]["sandbox_ids"] == native["lifecycle"]["stopped_ids"]
        assert native["junit_ref"] is None
    finally:
        _archive_phase(root, output)
        shutil.rmtree(root)


@pytest.fixture(scope="module")
def native_junit_results(native_fixture):
    """Invoke the installed helper only in a source-admitted verifier process."""
    task, verifier, public, private, config = native_fixture
    root, runtime = _prepare_worker(config, task, public, verifier=True)
    output = config.artifact_root / ("junit-semantics-" + uuid.uuid4().hex)
    good = '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="test_marker" name="test_marker"/></testsuite></testsuites>'
    skipped = '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="1"><testcase classname="test_marker" name="test_marker"><skipped/></testcase></testsuite></testsuites>'
    failed = '<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0"><testcase classname="test_marker" name="test_marker"><failure/></testcase><testcase name="other"/></testsuite></testsuites>'
    xml = {"passing": good, "missing": "", "skipped": skipped,
           "missing_required": good.replace('name="test_marker"', 'name="other"'), "failed_required": failed}
    script = """import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from eval.runtime_adapter import _admit,_prepare,_guard,_native_namespaces
from eval.verifier_task import VerifierTask
payload=json.load(sys.stdin)
task=VerifierTask.from_dict(payload['task'])
paths,case,pin,candidates=_admit(payload,task)
_prepare(paths)
guard=_guard(paths,pin,candidates)
_native_namespaces(Path(pin['site_packages']))
from swegemma.harness.verification import _validate_junit_xml
result={key:_validate_junit_xml(value,fail_to_pass=('test_marker.py::test_marker',)) for key,value in payload['xml'].items()}
guard.read_roots=None
(paths['evidence_root']/'source_pin.json').write_text(json.dumps(pin,sort_keys=True))
print(json.dumps(result,sort_keys=True))
"""
    payload = {"task": verifier.to_dict(), "runtime": runtime, "synthetic_case": "H05",
               "observation_enabled": False, "xml": xml}
    try:
        result = subprocess.run(confined_worker_argv(config.python_executable, root, ["-c", script, str(root / "code")]),
                                cwd=root, env=worker_environment(root), input=canonical_json(payload),
                                text=True, capture_output=True, timeout=120, check=True)
        (root / "evidence/process.log").write_text(result.stderr)
        return json.loads(result.stdout)
    finally:
        _archive_phase(root, output)
        shutil.rmtree(root)


@pytest.mark.parametrize("case", ("passing", "missing", "skipped", "missing_required", "failed_required"))
def test_native_junit_requires_passing_required_nodes(case, native_junit_results):
    valid, error = native_junit_results[case]
    assert valid is (case == "passing")
    assert (error is None) is (case == "passing")


def test_native_import_admission_refuses_public_tasks(native_fixture):
    task, verifier, public, private, config = native_fixture
    task = replace(task, instance_id="public_task", repo="real/project")
    verifier = replace(verifier, instance_id="public_task", repo="real/project")
    with pytest.raises(ContractError, match="public DEV"):
        run_task(task, verifier, public_root=public, private_root=private, test_patch_relative_path="test.patch", config=config)


def test_snapshot_without_stale_index_is_admitted(native_fixture):
    task, _, public, _, _ = native_fixture
    data = (public / task.snapshot.source_relative_path).read_bytes()
    import io
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        names = archive.getnames()
    assert ".git/index" not in names
    assert any(re.fullmatch(r"\.git/objects/[0-9a-f]{2}/[0-9a-f]{38}", name) for name in names)
    _admit_snapshot(data, task.base_commit)


def test_snapshot_refuses_hooks_and_external_git_discovery(native_fixture):
    task, _, public, _, _ = native_fixture
    data = (public / task.snapshot.source_relative_path).read_bytes()
    import io
    for name in (".git/hooks/pre-commit", ".git/objects/info/alternates", ".git/index"):
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(data)) as original, tarfile.open(fileobj=output, mode="w:gz") as changed:
            for member in original:
                changed.addfile(member, original.extractfile(member) if member.isfile() else None)
            member = tarfile.TarInfo(name)
            content = b"/private/secret\n"
            member.size = len(content)
            changed.addfile(member, io.BytesIO(content))
        with pytest.raises(RuntimeAdmissionError):
            _admit_snapshot(output.getvalue(), task.base_commit)
