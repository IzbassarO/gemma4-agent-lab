"""CPU supplement provenance and phase boundaries; no task/model execution."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from eval import runtime, runtime_provenance, runtime_supplement, worker_common
from eval._contracts import ContractError, canonical_json, canonical_sha256
from eval.public_data import solver_task_from_public_row
from test_dev_public_data import make_public_dataset
from test_dev_runtime_dependencies import _wheel
from test_dev_runtime_real import config, real_request, coordinator_fixture
from test_dev_eval_driver import setup as driver_setup, _sealed_result, _seal
from tools import dev_eval as driver
from tools.common import REPO_ROOT, tree_sha256
from tools.h23_v4.schema import PolicyError


IDENTITY = {"manifest_sha256": "a" * 64, "wheels_tree_sha256": "b" * 64,
            "supplement_id": "PUBLIC-CPU-FIXTURE-V1"}


def supplement_fixture(tmp_path, original):
    root = tmp_path / "supplement"
    root.mkdir(mode=0o700)
    wheels = root / "wheels"
    wheels.mkdir(mode=0o700)
    wheel = _wheel(wheels, "fixture-supplement", "1.0", {"fixture_supplement.py": b"PUBLIC = True\n"})
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "supplement_id": "PUBLIC-CPU-FIXTURE-V1",
                "target": {"python": "3.12", "platform": "linux_x86_64"},
                "original_wheels_tree_sha256": tree_sha256(runtime_supplement_tree(original)),
                "wheels_tree_sha256": tree_sha256({wheel.name: digest}),
                "install_requirements": ["fixture-supplement==1.0"],
                "wheels": [{"filename": wheel.name, "distribution": "fixture-supplement", "version": "1.0",
                            "sha256": digest, "requires_python": "", "requires_dist": [],
                            "source_url": "https://files.pythonhosted.org/packages/cpu-fixture/" + wheel.name,
                            "reason": "Synthetic public protocol fixture; not an acquired dependency",
                            "role": "foundation"}]}
    content = canonical_json(manifest).encode()
    (root / "manifest.json").write_bytes(content)
    (root / "manifest.json").chmod(0o600)
    return root, hashlib.sha256(content).hexdigest()


def runtime_supplement_tree(root):
    from eval.runtime_real import _tree
    return _tree(root)


@pytest.mark.parametrize("change", [
    {"supplement_root": Path("/tmp/reviewed")},
    {"supplement_manifest_sha256": "a" * 64},
    {"supplement_root": Path("relative"), "supplement_manifest_sha256": "a" * 64},
    {"supplement_root": Path("/tmp/../reviewed"), "supplement_manifest_sha256": "a" * 64},
    {"supplement_root": "/tmp/reviewed", "supplement_manifest_sha256": "a" * 64},
    {"supplement_root": Path("/tmp/reviewed"), "supplement_manifest_sha256": "not-a-hash"},
])
def test_config_refuses_incomplete_or_aliased_supplement_admission(tmp_path, change):
    with pytest.raises(ContractError):
        config(tmp_path, **change)


def test_synthetic_config_rejects_supplement_fields(tmp_path):
    with pytest.raises(ContractError, match="synthetic"):
        replace(config(tmp_path), mode="isolation_probe", synthetic_case="H05", model_endpoint="SCRIPTED_ONLY",
                budget=None, compaction=None, worker_user=None, preregistration_sha256="UNKNOWN",
                supplement_root=tmp_path, supplement_manifest_sha256="a" * 64)


@pytest.mark.parametrize("mutation", ["identity_only", "path_only", "outside", "alias", "symlink", "bad_hash", "unknown_identity"])
def test_worker_rejects_unbound_or_unsafe_supplement(tmp_path, monkeypatch, mutation):
    request, _ = real_request(tmp_path, monkeypatch)
    root = Path(request["runtime"]["worker_root"])
    supplement = root / "public/dependency_supplement"
    supplement.parent.mkdir(mode=0o700, exist_ok=True)
    supplement.mkdir(mode=0o700)
    request["runtime"]["supplement_root"] = str(supplement)
    request["provenance"]["public_identities"]["dependency_supplement"] = dict(IDENTITY)
    if mutation == "identity_only":
        del request["runtime"]["supplement_root"]
    elif mutation == "path_only":
        request["provenance"]["public_identities"].clear()
    elif mutation == "outside":
        request["runtime"]["supplement_root"] = str(tmp_path)
    elif mutation == "alias":
        request["runtime"]["supplement_root"] = str(supplement.parent) + "/./dependency_supplement"
    elif mutation == "symlink":
        supplement.rmdir()
        supplement.symlink_to(tmp_path, target_is_directory=True)
    elif mutation == "bad_hash":
        request["provenance"]["public_identities"]["dependency_supplement"]["manifest_sha256"] = "bad"
    else:
        request["provenance"]["public_identities"]["dependency_supplement"]["extra"] = True
    with pytest.raises((ContractError, PolicyError)):
        worker_common.validate_request(request)


def test_worker_accepts_bound_supplement_and_legacy_no_supplement(tmp_path, monkeypatch):
    request, _ = real_request(tmp_path, monkeypatch)
    assert worker_common.validate_request(request) is request
    root = Path(request["runtime"]["worker_root"])
    supplement = root / "public/dependency_supplement"
    supplement.parent.mkdir(mode=0o700, exist_ok=True)
    supplement.mkdir(mode=0o700)
    request["runtime"]["supplement_root"] = str(supplement)
    request["provenance"]["public_identities"]["dependency_supplement"] = dict(IDENTITY)
    assert worker_common.validate_request(request) is request


def test_solver_and_verifier_stage_separate_fresh_public_supplement_copies(tmp_path, monkeypatch):
    public = tmp_path / "public"
    row, _ = make_public_dataset(public)
    original = public / "wheels"
    original.mkdir(mode=0o700)
    _wheel(original, "fixture-original", "1.0", {"fixture_original.py": b"PUBLIC = True\n"})
    (public / "sandbox").mkdir(mode=0o700)
    (public / "sandbox/setup.py").write_text("# Public CPU fixture setup\n")
    original_identity = runtime_supplement_tree(original)
    source, manifest_sha = supplement_fixture(tmp_path, original)
    task = solver_task_from_public_row(public, row)
    value = config(tmp_path, supplement_root=source, supplement_manifest_sha256=manifest_sha,
                   source_wheels_root=REPO_ROOT / "artifacts/harness_wheels/v28", worker_parent_root=tmp_path)
    monkeypatch.setattr("eval.runtime_linux.own_worker_root", lambda *args: None)
    roots = []
    try:
        for verifier in (False, True):
            root, staged = runtime._prepare_worker(value, task, public, verifier=verifier,
                source_records=runtime_provenance.runtime_source_records(REPO_ROOT))
            roots.append(root)
            destination = root / "public/dependency_supplement"
            assert staged["supplement_root"] == str(destination)
            assert runtime_supplement.validate_supplement(destination, manifest_sha,
                original_wheels_root=root / "public/wheels")["manifest_sha256"] == manifest_sha
            assert runtime_supplement_tree(root / "public/wheels") == original_identity
            for path in destination.rglob("*"):
                if path.is_file():
                    assert path.stat().st_nlink == 1
                    assert path.stat().st_ino != (source / path.relative_to(destination)).stat().st_ino
            assert (root / "code/eval/runtime_supplement.py").is_file()
            assert (root / "code/eval/verifier_worker.py").exists() is verifier
        assert roots[0] != roots[1]
        assert runtime_supplement_tree(original) == original_identity
    finally:
        for root in roots:
            shutil.rmtree(root)


@pytest.mark.parametrize("change", [{"supplement_root": Path("/tmp/not-read")},
                                    {"supplement_manifest_sha256": "a" * 64},
                                    {"supplement_root": Path("/tmp/not-read"), "supplement_manifest_sha256": "bad"}])
def test_driver_refuses_partial_or_bad_identity_before_supplement_io(tmp_path, monkeypatch, change):
    monkeypatch.setattr(runtime_supplement, "validate_supplement", lambda *args, **kwargs: pytest.fail("source was read"))
    with pytest.raises(ContractError):
        driver._dependency_supplement_identity(tmp_path, change.get("supplement_root"),
                                             change.get("supplement_manifest_sha256"))


def test_cli_supplement_flags_are_real_execution_inputs_only():
    choices = driver._parser()._subparsers._group_actions[0].choices
    for name, parser in choices.items():
        options = {action.dest for action in parser._actions}
        assert ("supplement_root" in options) is (name in ("fingerprint", "preflight", "run"))
        assert ("supplement_manifest_sha256" in options) is (name in ("fingerprint", "preflight", "run"))


@pytest.mark.parametrize("control", [False, True])
def test_supplement_source_cannot_alias_private_root(tmp_path, monkeypatch, control):
    inputs = coordinator_fixture(tmp_path, monkeypatch)
    task, verifier, public, private, patch, value, *_ = inputs
    value = replace(value, supplement_root=private, supplement_manifest_sha256="a" * 64)
    with pytest.raises(ContractError, match="private source"):
        if control:
            runtime.run_verifier_control(task, verifier, public_root=public, private_root=private,
                test_patch_relative_path=patch.name, config=value, returned_patch="", control="no_patch")
        else:
            runtime.run_task(task, verifier, public_root=public, private_root=private,
                test_patch_relative_path=patch.name, config=value)


def test_disk_measurement_includes_phase_local_supplement_copies(tmp_path):
    public = tmp_path / "public"
    make_public_dataset(public)
    paths = {}
    for name in ("model", "harness", "vllm", "supplement"):
        paths[name] = tmp_path / name
        paths[name].mkdir(mode=0o700)
        (paths[name] / "bytes").write_bytes(b"public fixture bytes")
    kwargs = {**{name: paths[name] for name in ("model", "harness", "vllm")},
              "public": public, "private": tmp_path / "private", "tasks": (),
              "candidate": REPO_ROOT / "artifacts/submissions/e0_official_control/submission.zip"}
    original = driver.measure_footprint(**kwargs)
    with_supplement = driver.measure_footprint(**kwargs, supplement_root=paths["supplement"])
    added = driver._size(paths["supplement"])
    assert with_supplement["worker"] == original["worker"] + added
    assert with_supplement["export"] == original["export"] + added


def test_coordinator_binds_same_independent_identity_to_both_phase_receipts(tmp_path, monkeypatch):
    inputs = coordinator_fixture(tmp_path, monkeypatch)
    task, verifier, public, private, patch, value, _, _, requests, *_ = inputs
    (public / "wheels/fixture.whl").unlink()
    _wheel(public / "wheels", "fixture-original", "1.0", {"fixture_original.py": b"PUBLIC = True\n"})
    source, digest = supplement_fixture(tmp_path, public / "wheels")
    value = replace(value, supplement_root=source, supplement_manifest_sha256=digest)
    from eval.runtime_real import real_verification_config
    verification = real_verification_config(public, value.budget)
    verifier = replace(verifier, verification_config_sha256=canonical_sha256(verification))
    runtime.run_task(task, verifier, public_root=public, private_root=private,
                     test_patch_relative_path=patch.name, config=value)
    expected = runtime_supplement.validate_supplement(source, digest, original_wheels_root=public / "wheels")
    for phase in ("solver", "verifier"):
        public_identity = requests[phase]["provenance"]["public_identities"]
        assert public_identity["dependency_supplement"] == expected
        assert public_identity["public_support"] == {key: verification[key]
            for key in ("setup_py_sha256", "wheels_tree_sha256")}


@pytest.mark.parametrize("phase_identity,expected_identity", [
    (None, IDENTITY), (IDENTITY, None),
    (IDENTITY, {**IDENTITY, "manifest_sha256": "c" * 64}),
])
@pytest.mark.parametrize("control", [False, True])
def test_driver_rejects_phase_supplement_mismatch_even_with_valid_artifact_seals(
        tmp_path, phase_identity, expected_identity, control):
    _, root, _, fp = driver_setup.__wrapped__(tmp_path)
    if phase_identity is not None:
        fp["public_identities"]["dependency_supplement"] = phase_identity
    result = _sealed_result(root, "fastapi_14186", fp)
    expected = json.loads(json.dumps(fp))
    expected["public_identities"] = ({} if expected_identity is None
                                    else {"dependency_supplement": expected_identity})
    if control:
        run_root = driver._result_root(result, root)
        shutil.rmtree(run_root / "solver")
        value = {"run_root": str(run_root), "solver_started": False,
                 "verifier_result": result.verifier_result.to_dict()}
        with pytest.raises(ContractError, match="dependency supplement"):
            driver._control_observations(value, expected)
    else:
        seal = _seal(root, result, expected)
        with pytest.raises(ContractError, match="dependency supplement"):
            driver.validate_task_seal(seal, root, expected)
