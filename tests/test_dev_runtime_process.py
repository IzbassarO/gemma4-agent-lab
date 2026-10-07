"""Real fresh-process and OS filesystem isolation, using no model/harness."""
import dataclasses
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from eval._contracts import ContractError
from eval import runtime
from eval.runtime import RuntimeConfig, confined_worker_argv, run_task, worker_environment
from eval.runtime_verifier_fixture import create_synthetic_fixture
from eval.worker_common import E0_SHA256, E0_SIZE
from tools.common import REPO_ROOT


@pytest.fixture
def runtime_inputs(tmp_path):
    if sys.platform != "darwin" or not (REPO_ROOT / "artifacts/submissions/e0_official_control/submission.zip").is_file():
        pytest.skip("process certification requires macOS confinement and the frozen local artifact")
    public = tmp_path / "public"
    solver, verifier, private_text = create_synthetic_fixture(public)
    private = tmp_path / "UNMOUNTED_PRIVATE_ROOT_SENTINEL"
    private.mkdir(mode=0o700)
    evidence = private / "PRIVATE_TEST_SENTINEL.patch"
    evidence.write_text(private_text)
    evidence.chmod(0o600)
    config = RuntimeConfig(Path(sys.executable).absolute(), Path(sys.prefix).resolve(),
                           REPO_ROOT / "artifacts/submissions/e0_official_control/submission.zip",
                           REPO_ROOT / "artifacts/dev_runtime" / ("test-" + uuid.uuid4().hex),
                           mode="isolation_probe")
    return public, private, evidence, solver, verifier, config


def _run(inputs):
    public, private, evidence, solver, verifier, config = inputs
    return run_task(solver, verifier, public_root=public, private_root=private,
                    test_patch_relative_path=evidence.name, config=config)


def _evidence(config, phase, name):
    runs = list(config.artifact_root.iterdir())
    assert len(runs) == 1
    return json.loads((runs[0] / phase / name).read_text())


def test_clean_exec_environment_fds_private_request_and_fresh_lifecycle(runtime_inputs, monkeypatch):
    public, private, evidence, solver, verifier, config = runtime_inputs
    sentinels = {"SWEGEMMA_SECRET_DIR": str(private), "PRIVATE_TEST_CONTENT": "PRIVATE_TEST_TEXT_SENTINEL",
                 "REFERENCE_PATCH": "REFERENCE_FIX_SENTINEL", "HOLDOUT_RESULT": "HOLDOUT_RESULT_SENTINEL",
                 "PYTHONPATH": "PRIVATE_IMPORT_SENTINEL", "HTTP_PROXY": "PRIVATE_PROXY_SENTINEL"}
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)
    fd = os.open(evidence, os.O_RDONLY)
    # Force inheritable to prove close_fds, independent of Python defaults.
    os.set_inheritable(fd, True)
    try:
        result = _run(runtime_inputs)
        os.fstat(fd)
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
    finally:
        os.close(fd)
    observation = _evidence(config, "solver", "process_observation.json")
    rendered = json.dumps(observation)
    for sentinel in (*sentinels.values(), private.name, evidence.name, verifier.test_patch.sha256,
                     verifier.verification_config_sha256, "test_patch", "fail_to_pass", "verification_config"):
        assert sentinel not in rendered
    assert fd not in observation["open_fds"]
    assert "PYTHONPATH" not in observation["environment"]
    assert observation["environment"]["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert all(name not in observation["imports"] for name in ("eval.verifier_task", "eval.verifier_data",
                                                              "eval.public_data", "eval.runtime_verifier_fixture"))
    verify_observation = _evidence(config, "verifier", "process_observation.json")
    assert observation["pid"] != verify_observation["pid"] != os.getpid()
    assert observation["cwd"] != verify_observation["cwd"]
    assert not Path(observation["cwd"]).exists()
    assert not Path(verify_observation["cwd"]).exists()
    assert result.verifier_result.returned_patch_sha256 == result.solver_result.returned_patch_sha256
    assert result.resolved is None  # a process probe never invents verification.
    assert result.fingerprint.candidate_sha256 == E0_SHA256
    assert len(result.artifacts) == 2
    events = _evidence(config, "verifier", "events.json")["events"]
    assert [event["event_type"] for event in events] == ["verifier_started", "verifier_finished"]


def test_os_sandbox_denies_guessed_private_path_and_inherited_shell(runtime_inputs, tmp_path):
    *_, config = runtime_inputs
    root = tmp_path / "worker"
    root.mkdir(mode=0o700)
    for name in ("home", "tmp", "hf_cache", "setup"):
        (root / name).mkdir(mode=0o700)
    private_root = tmp_path / "unmounted-private"
    private_root.mkdir(mode=0o700)
    private = private_root / "PRIVATE_FILE_SENTINEL"
    private.write_text("PRIVATE_CONTENT_SENTINEL")
    script = (
        "import pathlib,subprocess,sys,os; p=pathlib.Path(sys.argv[1]); "
        "\ntry: p.read_text(); raise AssertionError('private contents readable')"
        "\nexcept PermissionError: pass"
        "\ntry: p.parent.iterdir().__next__(); raise AssertionError('private directory list readable')"
        "\nexcept PermissionError: pass"
        "\ntry: os.link(p,'private-hardlink'); raise AssertionError('private link accepted')"
        "\nexcept PermissionError: pass"
        "\nr=subprocess.run(['/bin/cat',str(p)],capture_output=True); "
        "assert r.returncode != 0 and b'PRIVATE_CONTENT_SENTINEL' not in r.stdout; "
        "pathlib.Path('allowed').write_text('public'); print('ISOLATED')"
    )
    result = subprocess.run(confined_worker_argv(config.python_executable, root, ["-c", script, str(private)]),
                            cwd=root, env=worker_environment(root), close_fds=True,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ISOLATED"
    assert (root / "allowed").read_text() == "public"


def test_private_staging_occurs_only_after_solver_exit_and_root_cleanup(runtime_inputs, monkeypatch):
    observed = []
    original_execute = runtime._execute_worker
    original_private = runtime.load_verifier_material

    def execute(config, request, root, phase):
        result = original_execute(config, request, root, phase)
        observed.append((phase, root))
        return result

    def private(*args, **kwargs):
        assert [phase for phase, _ in observed] == ["solver"]
        assert not observed[0][1].exists()
        return original_private(*args, **kwargs)

    monkeypatch.setattr(runtime, "_execute_worker", execute)
    monkeypatch.setattr(runtime, "load_verifier_material", private)
    _run(runtime_inputs)
    assert [phase for phase, _ in observed] == ["solver", "verifier"]


@pytest.mark.parametrize("field,value", [("repo", "fastapi/fastapi"), ("instance_id", "fastapi_9555")])
def test_public_solver_execution_is_refused_before_any_worker(runtime_inputs, monkeypatch, field, value):
    public, private, evidence, solver, verifier, config = runtime_inputs
    solver = dataclasses.replace(solver, **{field: value})
    verifier = dataclasses.replace(verifier, **{field: value})
    monkeypatch.setattr(runtime, "_prepare_worker", lambda *a, **k: pytest.fail("worker launched"))
    with pytest.raises(ContractError, match="operator admission"):
        run_task(solver, verifier, public_root=public, private_root=private,
                 test_patch_relative_path=evidence.name, config=config)


def test_task_disagreement_fails_before_staging(runtime_inputs, monkeypatch):
    public, private, evidence, solver, verifier, config = runtime_inputs
    verifier = dataclasses.replace(verifier, base_commit="f" * 40)
    monkeypatch.setattr(runtime, "_prepare_worker", lambda *a, **k: pytest.fail("worker launched"))
    with pytest.raises(ContractError, match="identity mismatch"):
        run_task(solver, verifier, public_root=public, private_root=private,
                 test_patch_relative_path=evidence.name, config=config)


def test_wrong_frozen_candidate_fails_without_launch(runtime_inputs, tmp_path, monkeypatch):
    public, private, evidence, solver, verifier, config = runtime_inputs
    wrong = tmp_path / "submission.zip"
    wrong.write_bytes(b"not frozen E0")
    config = dataclasses.replace(config, candidate_path=wrong)
    monkeypatch.setattr(runtime, "_execute_worker", lambda *a, **k: pytest.fail("worker launched"))
    with pytest.raises(ContractError, match="identity mismatch"):
        run_task(solver, verifier, public_root=public, private_root=private,
                 test_patch_relative_path=evidence.name, config=config)


def test_cleanup_even_when_evidence_archive_fails(runtime_inputs, monkeypatch):
    roots = []
    original = runtime._prepare_worker

    def prepare(*args, **kwargs):
        result = original(*args, **kwargs)
        roots.append(result[0])
        return result

    monkeypatch.setattr(runtime, "_prepare_worker", prepare)
    monkeypatch.setattr(runtime, "_archive_phase", lambda *a, **k: (_ for _ in ()).throw(ContractError("archive refused")))
    with pytest.raises(ContractError, match="archive refused"):
        _run(runtime_inputs)
    assert len(roots) == 1 and not roots[0].exists()


def test_worker_code_staging_excludes_private_and_trusted_intake(runtime_inputs, tmp_path):
    root = tmp_path / "worker"
    root.mkdir()
    runtime._stage_code(root, verifier=False)
    names = {p.relative_to(root / "code").as_posix() for p in (root / "code").rglob("*") if p.is_file()}
    assert "eval/solver_worker.py" in names
    for forbidden in ("eval/public_data.py", "eval/verifier_task.py", "eval/verifier_data.py",
                      "eval/verifier_worker.py", "eval/runtime_verifier_fixture.py", "tools/harness_cert/_synthetic_verification.py"):
        assert forbidden not in names


def test_runtime_evidence_root_must_be_ignored(runtime_inputs, tmp_path):
    *_, config = runtime_inputs
    with pytest.raises(ContractError, match="artifacts/dev_runtime"):
        dataclasses.replace(config, artifact_root=tmp_path / "tracked-looking-results")


@pytest.mark.parametrize("case_alias", (False, True))
def test_private_evidence_overlap_refused_without_writing_source(runtime_inputs, monkeypatch, case_alias):
    public, _, evidence, solver, verifier, config = runtime_inputs
    private = config.artifact_root
    private.mkdir(mode=0o700, parents=True)
    source = private / evidence.name
    source.write_bytes(evidence.read_bytes())
    source.chmod(0o600)
    artifact_parent = private.with_name(private.name.swapcase()) if case_alias else private
    try:
        if case_alias and (not artifact_parent.exists() or not artifact_parent.samefile(private)):
            pytest.skip("filesystem does not admit case aliases")
        config = dataclasses.replace(config, artifact_root=artifact_parent / "runtime-output")
        monkeypatch.setattr(runtime, "_prepare_worker", lambda *a, **k: pytest.fail("worker launched"))
        before = source.read_bytes()
        with pytest.raises(ContractError, match="private source and runtime evidence"):
            run_task(solver, verifier, public_root=public, private_root=private,
                     test_patch_relative_path=source.name, config=config)
        assert list(private.iterdir()) == [source]
        assert source.read_bytes() == before
    finally:
        shutil.rmtree(private)


@pytest.mark.parametrize("field", ("test_patch", "reference_patch", "verifier_task", "holdout_result", "partition", "secret_root"))
def test_solver_protocol_rejects_private_fields_before_paths(field):
    from eval.worker_common import validate_request
    request = {"schema_version": 1, "task": {}, "runtime": {}, "candidate": {}, "observation_enabled": True,
               "synthetic_case": "H05", "mode": "isolation_probe", "provenance": {}, field: "PRIVATE_SENTINEL"}
    with pytest.raises(ContractError, match="unknown fields"):
        validate_request(request)


def test_archival_rejects_evidence_changed_after_result_seal(tmp_path):
    from eval.runtime_result import ArtifactRef
    import hashlib
    root = tmp_path / "worker"
    (root / "evidence").mkdir(parents=True)
    path = root / "evidence/native_trace.json"
    original = b"sealed native evidence"
    path.write_bytes(original)
    reference = ArtifactRef("native_trace", path.name, hashlib.sha256(original).hexdigest(), len(original))
    path.write_bytes(b"changed native evidence")
    with pytest.raises(ContractError, match="identity mismatch"):
        runtime._archive_phase(root, tmp_path / "archive", (reference,))


def test_archival_rejects_conflicting_duplicate_refs_before_publication(tmp_path):
    from eval.runtime_result import ArtifactRef
    first = ArtifactRef("native_trace", "native_trace.json", "a" * 64, 10)
    second = dataclasses.replace(first, sha256="b" * 64)
    destination = tmp_path / "archive"
    with pytest.raises(ContractError, match="duplicate"):
        runtime._archive_phase(tmp_path, destination, (first, second))
    assert not destination.exists()


def test_library_overlap_refused_before_solver(runtime_inputs, monkeypatch):
    public, private, evidence, solver, verifier, config = runtime_inputs
    monkeypatch.setattr(runtime, "_interpreter_roots", lambda *a: (private,))
    monkeypatch.setattr(runtime, "_prepare_worker", lambda *a, **k: pytest.fail("solver started with readable private root"))
    with pytest.raises(ContractError, match="overlaps"):
        _run(runtime_inputs)


def test_archived_private_evidence_cannot_overlap_runtime_library_access(runtime_inputs, monkeypatch):
    *_, config = runtime_inputs
    monkeypatch.setattr(runtime, "_interpreter_roots", lambda *a: (config.artifact_root.parent,))
    monkeypatch.setattr(runtime, "_prepare_worker", lambda *a, **k: pytest.fail("prior verifier evidence readable"))
    with pytest.raises(ContractError, match="overlaps"):
        _run(runtime_inputs)
    assert not config.artifact_root.exists()


def test_library_overlap_case_alias_refused_before_solver(runtime_inputs, monkeypatch):
    public, private, evidence, solver, verifier, config = runtime_inputs
    alias = private.with_name(private.name.swapcase())
    if not alias.exists() or not alias.samefile(private):
        pytest.skip("filesystem does not admit case aliases")
    assert not private.is_relative_to(alias)
    monkeypatch.setattr(runtime, "_interpreter_roots", lambda *a: (alias,))
    monkeypatch.setattr(runtime, "_prepare_worker", lambda *a, **k: pytest.fail("case alias escaped private-root check"))
    with pytest.raises(ContractError, match="overlaps"):
        _run(runtime_inputs)


def test_prefix_probe_does_not_execute_venv_pth(tmp_path):
    import venv
    root = tmp_path / "library-venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(root)
    executable = root / "bin/python"
    site = root / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    marker = tmp_path / "PTH_EXECUTION_SENTINEL"
    (site / "test_probe.pth").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')\n")
    subprocess.run([str(executable), "-I", "-B", "-c", "pass"], check=True, timeout=10)
    assert marker.read_text() == "executed"
    marker.unlink()
    roots = runtime._interpreter_roots(executable, tmp_path)
    assert root in roots
    assert not marker.exists()


def test_result_seal_cannot_change_between_read_and_archival(runtime_inputs, monkeypatch):
    original = runtime._execute_worker
    roots = []

    def execute(config, request, root, phase):
        assert phase == "solver"
        result = original(config, request, root, phase)
        roots.append(root)
        (root / "evidence/result.json").write_text('{}\n')
        return result

    monkeypatch.setattr(runtime, "_execute_worker", execute)
    monkeypatch.setattr(runtime, "load_verifier_material", lambda *a, **k: pytest.fail("private bytes staged after seal drift"))
    with pytest.raises(ContractError, match="identity mismatch"):
        _run(runtime_inputs)
    assert len(roots) == 1 and not roots[0].exists()
