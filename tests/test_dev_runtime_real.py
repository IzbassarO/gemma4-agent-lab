"""CPU admission/coordinator/Linux probes: no native agent or real task launch."""
import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval._contracts import ContractError
from eval._contracts import canonical_sha256
from eval import runtime, runtime_linux, worker_common
from eval.real_contracts import FROZEN_BUDGET, FROZEN_COMPACTION
from eval.public_data import solver_task_from_public_row
from eval.runtime import RuntimeConfig
from eval.runtime_result import ArtifactRef, RuntimeFingerprint, SolverRunResult, VerifierRunResult
from eval.verifier_task import PrivateTestPatchRef, VerifierTask
from test_dev_public_data import make_public_dataset
from tools.common import REPO_ROOT, sha256_bytes, tree_sha256


CANDIDATE = REPO_ROOT / "artifacts/submissions/e0_official_control/submission.zip"


def config(tmp_path, **changes):
    base = dict(python_executable=Path(sys.executable).absolute(), harness_root=Path(sys.prefix).resolve(),
                candidate_path=CANDIDATE, artifact_root=REPO_ROOT / "artifacts/dev_runtime/unit-real",
                source_wheels_root=tmp_path / "source_wheels", mode="real_public", synthetic_case=None,
                model_endpoint="http://127.0.0.1:8000/v1", budget=dict(FROZEN_BUDGET),
                compaction=dict(FROZEN_COMPACTION), worker_user="gemma_worker", preregistration_sha256="a" * 64,
                task_manifest_sha256="b" * 64)
    return RuntimeConfig(**{**base, **changes})


@pytest.mark.parametrize("change", [
    {"synthetic_case": "H05"}, {"model_endpoint": "http://remote:8000/v1"},
    {"model_endpoint": "http://user:secret@127.0.0.1:8000/v1"},
    {"model_endpoint": "http://127.0.0.1:8000/v1?api_key=secret"},
    {"worker_user": "root"}, {"worker_user": None}, {"budget": None},
    {"budget": {**FROZEN_BUDGET, "tool_calls": 11}},
    {"budget": {**FROZEN_BUDGET, "unknown": True}},
    {"compaction": {**FROZEN_COMPACTION, "token_threshold": 1000}},
    {"compaction": {}}, {"preregistration_sha256": "UNKNOWN"}, {"source_wheels_root": None},
    {"worker_timeout_seconds": 3601}, {"worker_timeout_seconds": 0}, {"worker_timeout_seconds": True},
    {"worker_parent_root": Path("relative")}, {"worker_parent_root": Path("/tmp/../elsewhere")},
    {"worker_parent_root": "/tmp"},
])
def test_real_config_refuses_missing_or_mismatched_explicit_admission(tmp_path, change):
    with pytest.raises(ContractError):
        config(tmp_path, **change)


def test_real_config_admits_frozen_candidate_and_documented_compaction(tmp_path):
    value = config(tmp_path)
    assert value.mode == "real_public" and value.synthetic_case is None
    assert config(tmp_path, compaction=None).compaction is None
    assert config(tmp_path, worker_timeout_seconds=3600).worker_timeout_seconds == 3600
    assert config(tmp_path, worker_parent_root=tmp_path).worker_parent_root == tmp_path
    wrong = tmp_path / "different.zip"
    wrong.write_bytes(b"different")
    with pytest.raises(ContractError, match="frozen E0"):
        config(tmp_path, candidate_path=wrong)


@pytest.mark.parametrize("change", [{"budget": FROZEN_BUDGET}, {"compaction": FROZEN_COMPACTION},
    {"worker_user": "gemma_worker"}, {"model_endpoint": "http://127.0.0.1:8000/v1"},
    {"preregistration_sha256": "a" * 64}, {"harness_lock_path": Path("/tmp/lock")},
    {"worker_parent_root": Path("/tmp")}])
def test_synthetic_config_rejects_new_real_only_fields(tmp_path, change):
    value = config(tmp_path)
    value = dataclasses.replace(value, mode="isolation_probe", synthetic_case="H05",
                                model_endpoint="SCRIPTED_ONLY", budget=None, compaction=None,
                                worker_user=None, preregistration_sha256="UNKNOWN", harness_lock_path=None)
    with pytest.raises(ContractError):
        dataclasses.replace(value, **change)


def real_request(tmp_path, monkeypatch):
    public = tmp_path / "published"
    row, _ = make_public_dataset(public)
    task = solver_task_from_public_row(public, row)
    root = tmp_path / "worker"
    root.mkdir(mode=0o700)
    runtime_values = {}
    for key in ("public_root", "evidence_root", "submission_root", "wheels_root", "setup_root", "source_wheels_root"):
        path = root / ("evidence" if key == "evidence_root" else key)
        path.mkdir(mode=0o700)
        runtime_values[key] = str(path)
    candidate = Path(runtime_values["submission_root"]) / "submission.zip"
    shutil.copyfile(CANDIDATE, candidate)
    candidate.chmod(0o600)
    (root / "code").mkdir(mode=0o700)
    code = root / "code/eval/probe.py"
    code.parent.mkdir(mode=0o700, parents=True)
    code.write_text("PUBLIC_RUNTIME_SOURCE\n")
    code.chmod(0o600)
    records = {"eval/probe.py": {"sha256": hashlib.sha256(code.read_bytes()).hexdigest(),
                                 "size_bytes": code.stat().st_size}}
    worker_common.seal_json(Path(runtime_values["evidence_root"]) / "runtime_sources.json",
                           {"schema_version": 1, "sources": [{"relative_path": key, **value}
                                                               for key, value in records.items()]})
    runtime_values.update(harness_root=str(tmp_path), worker_root=str(root), sandbox="subprocess", image="UNKNOWN",
                          model_endpoint="http://127.0.0.1:8000/v1", pytest_support_root=None,
                          budget=dict(FROZEN_BUDGET), compaction=dict(FROZEN_COMPACTION), worker_user="gemma_worker",
                          preregistration_sha256="a" * 64)
    provenance = {"git_head": "b" * 40, "solver_contract_sha256": task.sha256(), "task_manifest_sha256": "c" * 64,
                  "eval_infra_source_identity": {"git_head": "b" * 40, "source_dirty": True,
                      "runtime_source_sha256": tree_sha256({key: value["sha256"] for key, value in records.items()})},
                  "candidate_identity": worker_common.candidate_identity(),
                  "preregistration_identity": {"sha256": "a" * 64, "size_bytes": 100},
                  "model_endpoint_identity": runtime_values["model_endpoint"], "public_identities": {},
                  "harness_lock_sha256": "d" * 64, "confinement": {"backend": "mock-user-separation"},
                  "runtime_sources": records, "staged_source_paths": list(records)}
    request = {"schema_version": 1, "task": task.to_dict(), "runtime": runtime_values,
               "candidate": {"relative_path": "submission.zip", **worker_common.candidate_identity()},
               "observation_enabled": True, "synthetic_case": None, "mode": "real_public", "provenance": provenance}
    monkeypatch.chdir(root)
    return request, code


def test_real_worker_closed_request_validates_authoritative_sources_before_native_imports(tmp_path, monkeypatch):
    request, _ = real_request(tmp_path, monkeypatch)
    assert worker_common.validate_request(request) is request


@pytest.mark.parametrize("mutation", ["synthetic_case", "private_field", "unknown_runtime", "unknown_provenance",
                                      "forged_endpoint", "forged_candidate", "forged_prereg", "forged_head",
                                      "source_digest", "code_mutation"])
def test_real_worker_closed_request_refuses_forged_identity_and_code(tmp_path, monkeypatch, mutation):
    request, code = real_request(tmp_path, monkeypatch)
    if mutation == "synthetic_case":
        request["synthetic_case"] = "H05"
    elif mutation == "private_field":
        request["test_patch"] = "PRIVATE_SENTINEL"
    elif mutation == "unknown_runtime":
        request["runtime"]["unknown"] = True
    elif mutation == "unknown_provenance":
        request["provenance"]["unknown"] = True
    elif mutation == "forged_endpoint":
        request["provenance"]["model_endpoint_identity"] = "http://127.0.0.1:8001/v1"
    elif mutation == "forged_candidate":
        request["provenance"]["candidate_identity"]["sha256"] = "1" * 64
    elif mutation == "forged_prereg":
        request["provenance"]["preregistration_identity"]["sha256"] = "1" * 64
    elif mutation == "forged_head":
        request["provenance"]["eval_infra_source_identity"]["git_head"] = "1" * 40
    elif mutation == "source_digest":
        request["provenance"]["eval_infra_source_identity"]["runtime_source_sha256"] = "1" * 64
    else:
        code.write_text("MUTATED_RUNTIME_SOURCE\n")
    with pytest.raises(ContractError):
        worker_common.validate_request(request)


def linux_mocks(monkeypatch):
    monkeypatch.setattr(runtime_linux.sys, "platform", "linux")
    monkeypatch.setattr(runtime_linux.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime_linux, "worker_account", lambda user: SimpleNamespace(pw_uid=1001, pw_gid=1001))
    monkeypatch.setattr(runtime_linux, "worker_pids", lambda uid: set())
    monkeypatch.setattr(runtime_linux, "enable_subreaper", lambda: None)


@pytest.mark.parametrize("backend", ["runuser", "sudo", "missing"])
def test_linux_user_prefix_uses_explicit_runuser_and_only_documented_fallback(monkeypatch, backend):
    linux_mocks(monkeypatch)
    monkeypatch.setattr(runtime_linux.shutil, "which",
                        lambda command, **kwargs: f"/usr/bin/{command}" if command == backend else None)
    if backend == "missing":
        with pytest.raises(ContractError):
            runtime_linux.linux_prefix("gemma_worker")
    else:
        argv, observed = runtime_linux.linux_prefix("gemma_worker")
        assert argv == [f"/usr/bin/{backend}", "-u", "gemma_worker", "--"] and observed == backend


def test_linux_worker_prefix_requires_root_coordinator(monkeypatch):
    linux_mocks(monkeypatch)
    monkeypatch.setattr(runtime_linux.os, "geteuid", lambda: 1000)
    with pytest.raises(ContractError, match="privileged"):
        runtime_linux.linux_prefix("gemma_worker")


def test_coordinator_linux_argv_preserves_literal_arguments_without_shell(tmp_path, monkeypatch):
    linux_mocks(monkeypatch)
    monkeypatch.setattr(runtime_linux, "linux_prefix",
                        lambda user: (["/usr/sbin/runuser", "-u", user, "--"], "runuser"))
    value = config(tmp_path)
    args = ["-c", "print('literal;$')", "argument with spaces"]
    argv = runtime.confined_worker_argv(value.python_executable, tmp_path, args, config=value)
    assert argv[:4] == ["/usr/sbin/runuser", "-u", "gemma_worker", "--"]
    assert argv[4] == str(value.python_executable)
    assert argv[-3:] == args


@pytest.mark.parametrize("outcome", ["pass", "protected_readable", "probe_failed", "interpreter_failed", "invalid_json", "timeout"])
def test_linux_confinement_probe_fails_closed_without_privileged_execution(tmp_path, monkeypatch, outcome):
    linux_mocks(monkeypatch)
    monkeypatch.setattr(runtime_linux, "linux_prefix", lambda user: (["/mock/runuser", "-u", user, "--"], "runuser"))
    roots = {}
    for name in ("public", "private", "artifacts", "repository_git"):
        root = tmp_path / name
        root.mkdir()
        roots[name] = root
    config_value = SimpleNamespace(worker_user="gemma_worker", python_executable=Path("/mock/python"))
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert argv[:4] == ["/mock/runuser", "-u", "gemma_worker", "--"]
        assert kwargs["close_fds"] is True
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        if len(calls) == 2:
            return SimpleNamespace(returncode=1 if outcome == "interpreter_failed" else 0, stdout="")
        evidence = {"protected_roots": dict.fromkeys(roots, "PermissionError"), "interpreter_readable": True}
        if outcome == "protected_readable":
            evidence["protected_roots"]["private"] = "OPENED"
        return SimpleNamespace(returncode=1 if outcome == "probe_failed" else 0,
                               stdout="invalid" if outcome == "invalid_json" else json.dumps(evidence))

    monkeypatch.setattr(runtime_linux.subprocess, "run", fake_run)
    if outcome == "pass":
        evidence = runtime_linux.verify_worker_confinement(config_value, roots)
        assert evidence["backend"] == "runuser"
        assert "filesystem permissions only" in evidence["limitations"]
    else:
        with pytest.raises(ContractError):
            runtime_linux.verify_worker_confinement(config_value, roots)


def test_linux_worker_account_refuses_root_group_and_bad_user_names(monkeypatch):
    for name in (None, "root", "bad user", "-argument", "../escape"):
        with pytest.raises(ContractError):
            runtime_linux.validate_worker_user(name)
    monkeypatch.setattr(runtime_linux.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=1001, pw_gid=0))
    with pytest.raises(ContractError):
        runtime_linux.worker_account("gemma_worker")


def test_worker_root_ownership_walk_keeps_private_mode_and_no_follow(tmp_path, monkeypatch):
    root = tmp_path / "worker"
    root.mkdir(mode=0o700)
    (root / "subdir").mkdir(mode=0o700)
    (root / "subdir/public.py").write_text("public")
    (root / "subdir/public.py").chmod(0o600)
    account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    monkeypatch.setattr(runtime_linux, "worker_account", lambda user: account)
    observed = []

    def chown(path, uid, gid, *, follow_symlinks):
        assert (uid, gid) == (account.pw_uid, account.pw_gid)
        assert follow_symlinks is False
        observed.append(path)

    monkeypatch.setattr(runtime_linux.os, "chown", chown)
    runtime_linux.own_worker_root(root, "gemma_worker")
    assert set(observed) == {root, root / "subdir", root / "subdir/public.py"}
    assert root.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_worker_ownership_refuses_aliased_files(tmp_path, monkeypatch, alias):
    root = tmp_path / "worker"
    root.mkdir(mode=0o700)
    target = tmp_path / "source"
    target.write_text("public fixture")
    if alias == "symlink":
        (root / "alias").symlink_to(target)
    else:
        os.link(target, root / "alias")
    monkeypatch.setattr(runtime_linux, "worker_account", lambda user: SimpleNamespace(pw_uid=1001, pw_gid=1001))
    monkeypatch.setattr(runtime_linux.os, "chown", lambda *a, **k: pytest.fail("aliased inode ownership changed"))
    with pytest.raises(ContractError):
        runtime_linux.own_worker_root(root, "gemma_worker")


def coordinator_fixture(tmp_path, monkeypatch, *, failure=None):
    """Exercise real coordinator ordering with only deterministic sealed stubs."""
    from eval import runtime_provenance, runtime_real

    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    (repository / ".git").mkdir(mode=0o700)
    public = tmp_path / "published"
    row, _ = make_public_dataset(public)
    (public / "wheels").mkdir()
    (public / "wheels/fixture.whl").write_bytes(b"fixture wheel")
    (public / "sandbox").mkdir()
    (public / "sandbox/setup.py").write_text("# official fixture setup\n")
    solver = solver_task_from_public_row(public, row)
    verification = runtime_real.real_verification_config(public, FROZEN_BUDGET)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    test_patch = "PRIVATE_VERIFIER_FIXTURE\n"
    patch_path = private / "explicit.test.patch"
    patch_path.write_text(test_patch)
    patch_path.chmod(0o600)
    verifier = VerifierTask(1, solver.instance_id, solver.repo, solver.base_commit, solver.snapshot,
        PrivateTestPatchRef(sha256_bytes(test_patch.encode()), len(test_patch.encode())), canonical_sha256(verification))
    monkeypatch.setattr(runtime, "REPO_ROOT", repository)
    monkeypatch.setattr(sys, "platform", "linux")
    config_value = config(tmp_path, artifact_root=repository / "artifacts/dev_runtime/unit-real",
        preregistration_sha256=sha256_bytes(runtime._PREREGISTRATION.read_bytes()))
    records = {"eval/probe.py": {"sha256": "e" * 64, "size_bytes": 7}}
    source = {"git_head": "f" * 40, "source_dirty": True,
              "runtime_source_sha256": tree_sha256({key: value["sha256"] for key, value in records.items()})}
    monkeypatch.setattr(runtime_provenance, "runtime_source_identity", lambda repo: copy.deepcopy(source))
    monkeypatch.setattr(runtime_provenance, "runtime_source_records", lambda repo: copy.deepcopy(records))
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="f" * 40))
    monkeypatch.setattr(runtime, "_interpreter_roots", lambda *a: ())
    monkeypatch.setattr(runtime, "verify_worker_confinement",
                        lambda config, roots: {"backend": "mock-runuser", "confinement": "linux_user_separation"})
    events, roots, requests = [], {}, {}

    def prepare(config, task, public_root, *, verifier, source_records):
        assert source_records == records
        phase = "verifier" if verifier else "solver"
        if verifier:
            assert events[-1] == "private_read" and not roots["solver"].exists()
        root = tmp_path / f"{phase}-worker"
        root.mkdir(mode=0o700)
        (root / "evidence").mkdir(mode=0o700)
        roots[phase] = root
        events.append(f"prepare_{phase}")
        return root, {"worker_root": str(root), "evidence_root": str(root / "evidence"),
                      "model_endpoint": config.model_endpoint}

    def execute(config, request, root, phase):
        requests[phase] = request
        events.append(f"execute_{phase}")
        fp = runtime_provenance.real_fingerprint_from_request(request, phase=phase,
            model_server_identity={"served_model": "gemma-4-31b-it-qat-w4a16-ct"})
        if failure == f"mismatch_{phase}":
            fp["model_endpoint_identity"] = "http://127.0.0.1:8001/v1"
        if failure == f"source_{phase}":
            fp["eval_infra_source_identity"] = {**source, "runtime_source_sha256": "1" * 64}
        artifacts = ()
        if failure != f"missing_{phase}":
            worker_common.seal_json(root / "evidence/real_fingerprint.json", fp)
            raw = (root / "evidence/real_fingerprint.json").read_bytes()
            artifacts = (ArtifactRef("fingerprint", "real_fingerprint.json", sha256_bytes(raw), len(raw)),)
        base_fp = RuntimeFingerprint(git_head=source["git_head"], candidate_sha256=worker_common.E0_SHA256,
                                     solver_contract_sha256=solver.sha256())
        if failure == f"base_{phase}":
            base_fp = None
        if phase == "solver":
            assert "test_patch" not in request and "returned_patch" not in request
            assert str(private) not in json.dumps(request)
            result = SolverRunResult(1, solver.instance_id, "completed", "2026-10-07T00:00:00Z", 0.1,
                "NATIVE_RETURNED_PATCH\n", worker_common.patch_sha256("NATIVE_RETURNED_PATCH\n"),
                workspace_patch="MUTABLE_WORKSPACE_PATCH\n", artifacts=artifacts, fingerprint=base_fp)
        else:
            assert not roots["solver"].exists()
            assert request["returned_patch"] == "NATIVE_RETURNED_PATCH\n"
            assert request["test_patch"] == test_patch
            result = VerifierRunResult(1, solver.instance_id, "completed", request["returned_patch_sha256"],
                                       resolved=False, artifacts=artifacts, fingerprint=base_fp)
        worker_common.seal_json(root / "evidence/result.json", result.to_dict())
        return result.to_dict()

    original_private = runtime.load_verifier_material

    def private_read(*args, **kwargs):
        assert events == ["prepare_solver", "execute_solver"]
        assert not roots["solver"].exists()
        run_root, = config_value.artifact_root.iterdir()
        assert (run_root / "solver/result.json").is_file()
        events.append("private_read")
        return original_private(*args, **kwargs)

    monkeypatch.setattr(runtime, "_prepare_worker", prepare)
    monkeypatch.setattr(runtime, "_execute_worker", execute)
    monkeypatch.setattr(runtime, "load_verifier_material", private_read)
    return solver, verifier, public, private, patch_path, config_value, events, roots, requests, source, records


def invoke_coordinator(inputs):
    solver, verifier, public, private, patch, config_value, *_ = inputs
    return runtime.run_task(solver, verifier, public_root=public, private_root=private,
                            test_patch_relative_path=patch.name, config=config_value)


def test_real_coordinator_native_patch_authority_and_private_lifecycle(tmp_path, monkeypatch):
    inputs = coordinator_fixture(tmp_path, monkeypatch)
    result = invoke_coordinator(inputs)
    *_, config_value, events, roots, requests, source, _records = inputs
    assert result.resolved is False and result.solver_result.returned_patch == "NATIVE_RETURNED_PATCH\n"
    assert events == ["prepare_solver", "execute_solver", "private_read", "prepare_verifier", "execute_verifier"]
    assert not roots["solver"].exists() and not roots["verifier"].exists()
    run_root, = config_value.artifact_root.iterdir()
    assert (run_root / "task_result.json").is_file()
    for phase in ("solver", "verifier"):
        fp = json.loads((run_root / phase / "real_fingerprint.json").read_text())
        assert fp["eval_infra_source_identity"] == source
        assert requests[phase]["mode"] == "real_public" and requests[phase]["synthetic_case"] is None


@pytest.mark.parametrize("failure", ["missing_solver", "missing_verifier", "base_solver", "base_verifier",
                                      "mismatch_solver", "mismatch_verifier", "source_solver", "source_verifier"])
def test_real_coordinator_requires_each_phase_fingerprint_and_admitted_identity(tmp_path, monkeypatch, failure):
    inputs = coordinator_fixture(tmp_path, monkeypatch, failure=failure)
    with pytest.raises(ContractError):
        invoke_coordinator(inputs)
    *_, config_value, events, roots, _requests, _source, _records = inputs
    assert all(not root.exists() for root in roots.values())
    if failure.endswith("solver"):
        assert "private_read" not in events and "prepare_verifier" not in events
    run_root, = config_value.artifact_root.iterdir()
    assert not (run_root / "task_result.json").exists()


def test_real_coordinator_source_inventory_must_match_authoritative_digest(tmp_path, monkeypatch):
    from eval import runtime_provenance
    inputs = coordinator_fixture(tmp_path, monkeypatch)
    records = inputs[-1]
    monkeypatch.setattr(runtime_provenance, "runtime_source_records",
                        lambda repo: {key: {**value, "sha256": "1" * 64} for key, value in records.items()})
    with pytest.raises(ContractError, match="source changed"):
        invoke_coordinator(inputs)
    assert inputs[6] == []


@pytest.mark.parametrize("private_nodes", [("fail_to_pass", ("PRIVATE_NODE",)), ("pass_to_pass", ())])
def test_real_coordinator_refuses_verifier_private_nodes_before_any_worker(tmp_path, monkeypatch, private_nodes):
    inputs = list(coordinator_fixture(tmp_path, monkeypatch))
    field, nodes = private_nodes
    inputs[1] = dataclasses.replace(inputs[1], **{field: nodes})
    with pytest.raises(ContractError, match="private node"):
        invoke_coordinator(inputs)
    assert inputs[6] == []


def test_real_coordinator_refuses_secret_ancestor_before_any_worker(tmp_path, monkeypatch):
    inputs = coordinator_fixture(tmp_path, monkeypatch)
    (tmp_path / "secret").mkdir()
    with pytest.raises(ContractError, match="secret-discovery"):
        invoke_coordinator(inputs)
    assert inputs[6] == []


@pytest.mark.parametrize("control, returned_patch", [("no_patch", ""), ("known_patch", "REFERENCE_CONTROL_SENTINEL\n")])
def test_verifier_only_controls_launch_no_solver_and_visibly_exclude_gold_roots(tmp_path, monkeypatch, capsys,
                                                                                control, returned_patch):
    from eval import runtime_provenance
    from eval.verifier_data import load_verifier_material
    inputs = coordinator_fixture(tmp_path, monkeypatch)
    solver, verifier, public, private, patch_path, config_value, events, roots, requests, source, records = inputs
    monkeypatch.setattr(runtime, "load_verifier_material", load_verifier_material)

    def prepare(config, task, public_root, *, verifier, source_records):
        assert verifier is True and source_records == records
        assert not events and not roots
        root = tmp_path / "control-verifier"
        root.mkdir(mode=0o700)
        (root / "evidence").mkdir(mode=0o700)
        roots["verifier"] = root
        events.append("prepare_verifier")
        return root, {"worker_root": str(root), "evidence_root": str(root / "evidence")}

    def execute(config, request, root, phase):
        assert phase == "verifier" and request["returned_patch"] == returned_patch
        assert request["agent_error"] is None
        assert events == ["prepare_verifier"]
        events.append("execute_verifier")
        requests[phase] = request
        fp = runtime_provenance.real_fingerprint_from_request(request, phase="verifier",
            model_server_identity={"served_model": "gemma-4-31b-it-qat-w4a16-ct"})
        worker_common.seal_json(root / "evidence/real_fingerprint.json", fp)
        observations = {"apply_status": "applied" if returned_patch else "skipped",
                        "apply_error": None, "required_tests_passed": False, "test_exit_code": 1}
        worker_common.seal_json(root / "evidence/native_observation.json", observations)
        artifacts = []
        for name in ("real_fingerprint.json", "native_observation.json"):
            data = (root / "evidence" / name).read_bytes()
            artifacts.append(ArtifactRef("evidence", name, sha256_bytes(data), len(data)))
        base_fp = RuntimeFingerprint(git_head=source["git_head"], candidate_sha256=worker_common.E0_SHA256,
                                     solver_contract_sha256=solver.sha256())
        result = VerifierRunResult(1, solver.instance_id, "completed", request["returned_patch_sha256"],
                                   resolved=False, artifacts=tuple(artifacts), fingerprint=base_fp)
        worker_common.seal_json(root / "evidence/result.json", result.to_dict())
        return result.to_dict()

    monkeypatch.setattr(runtime, "_prepare_worker", prepare)
    monkeypatch.setattr(runtime, "_execute_worker", execute)
    result = runtime.run_verifier_control(solver, verifier, public_root=public, private_root=private,
        test_patch_relative_path=patch_path.name, config=config_value, returned_patch=returned_patch, control=control)
    assert events == ["prepare_verifier", "execute_verifier"] and "solver" not in roots
    assert not roots["verifier"].exists()
    assert result["solver_started"] is False and result["gold_assisted"] is (control == "known_patch")
    assert result["verification_observations"]["test_exit_code"] == 1
    assert result["verification_observations"]["required_tests_passed"] is False
    run_root = Path(result["run_root"])
    manifest = json.loads((run_root / "run_manifest.json").read_text())
    assert manifest["role"] == "verifier_only_control" and manifest["solver_started"] is False
    for boundary in ("normal_reports", "candidate_comparison", "designer_inspection", "solver_inspection"):
        assert manifest[f"excluded_from_{boundary}"] is True
    assert (run_root / "GOLD_ASSISTED_VERIFIER_ONLY.json").exists() is (control == "known_patch")
    assert not (run_root / "solver").exists() and not (run_root / "task_result.json").exists()
    assert (run_root / "control_result.json").is_file()
    if returned_patch:
        assert returned_patch.strip() not in (run_root / "run_manifest.json").read_text()
        assert returned_patch.strip() not in (run_root / "control_result.json").read_text()
        assert returned_patch.strip() not in capsys.readouterr().out


@pytest.mark.parametrize("control, returned_patch", [("unknown", ""), ("no_patch", "unadmitted patch")])
def test_control_schema_refuses_unknown_controls_and_patch_on_no_patch_before_any_worker(tmp_path, monkeypatch,
                                                                                         control, returned_patch):
    inputs = coordinator_fixture(tmp_path, monkeypatch)
    solver, verifier, public, private, patch_path, config_value, events, *_ = inputs
    with pytest.raises(ContractError):
        runtime.run_verifier_control(solver, verifier, public_root=public, private_root=private,
            test_patch_relative_path=patch_path.name, config=config_value, returned_patch=returned_patch, control=control)
    assert events == []
