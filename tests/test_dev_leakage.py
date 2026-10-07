"""Sentinel and access-observation checks on the implemented data boundary."""
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from eval._contracts import ContractError
from eval import manifests, public_data
from eval.public_data import (
    PublicDataError, load_public_metadata, load_solver_tasks,
    solver_task_from_public_row, tasks_source_sha256,
)
from eval.solver_task import SolverTask, solver_task_dict, solver_task_json
from eval.verifier_data import load_verifier_material
from eval.verifier_task import PrivateTestPatchRef, VerifierTask
from test_dev_public_data import make_public_dataset
from tools.common import dumps, sha256_bytes


def make_private_evidence(tmp_path, solver):
    root = tmp_path / "private_verifier"
    root.mkdir(mode=0o700)
    evidence = "PRIVATE_TEST_EVIDENCE_SENTINEL\n"
    path = root / "explicit_test_patch.txt"
    path.write_text(evidence)
    path.chmod(0o600)
    task = VerifierTask(1, solver.instance_id, solver.repo, solver.base_commit, solver.snapshot,
                        PrivateTestPatchRef(sha256_bytes(evidence.encode()), len(evidence.encode())),
                        "f" * 64, ("PRIVATE_REQUIRED_NODE",), None)
    return root, path, evidence, task


def test_solver_helpers_cannot_receive_verifier_objects_or_private_serialization(tmp_path):
    root = tmp_path / "published"
    row, _ = make_public_dataset(root)
    solver = solver_task_from_public_row(root, row)
    private_root, path, evidence, verifier = make_private_evidence(tmp_path, solver)
    material = load_verifier_material(verifier, private_root=private_root,
                                      test_patch_relative_path=path.name)
    assert material.test_patch == evidence
    for wrong in (verifier, material, verifier.to_dict(), verifier.to_json()):
        with pytest.raises(ContractError):
            solver_task_json(wrong)
        with pytest.raises(ContractError):
            solver_task_dict(wrong)
    with pytest.raises(ContractError):
        SolverTask.from_dict(verifier.to_dict())
    with pytest.raises(ContractError):
        VerifierTask.from_dict(solver.to_dict())
    serialized = solver_task_json(solver)
    for forbidden in (evidence.strip(), path.name, str(private_root), "PRIVATE_REQUIRED_NODE",
                      verifier.test_patch.sha256, verifier.verification_config_sha256):
        assert forbidden not in serialized
    with pytest.raises(dataclasses.FrozenInstanceError):
        solver.test_patch = evidence


@pytest.mark.parametrize("relative", ["../private_verifier/explicit_test_patch.txt", "/tmp/test_patch", "a/../test_patch", "a\\test_patch"])
def test_verifier_evidence_path_is_explicit_and_traversal_safe(tmp_path, relative):
    public = tmp_path / "published"
    row, _ = make_public_dataset(public)
    solver = solver_task_from_public_row(public, row)
    root, _, _, verifier = make_private_evidence(tmp_path, solver)
    with pytest.raises(ContractError):
        load_verifier_material(verifier, private_root=root, test_patch_relative_path=relative)


def test_private_evidence_must_match_identity_and_permissions(tmp_path):
    public = tmp_path / "published"
    row, _ = make_public_dataset(public)
    solver = solver_task_from_public_row(public, row)
    root, path, evidence, verifier = make_private_evidence(tmp_path, solver)
    path.write_text(evidence + "changed")
    with pytest.raises(ContractError, match="identity"):
        load_verifier_material(verifier, private_root=root, test_patch_relative_path=path.name)
    path.write_text(evidence)
    path.chmod(0o644)
    with pytest.raises(ContractError):
        load_verifier_material(verifier, private_root=root, test_patch_relative_path=path.name)
    path.chmod(0o600)
    root.chmod(0o755)
    with pytest.raises(ContractError):
        load_verifier_material(verifier, private_root=root, test_patch_relative_path=path.name)
    with pytest.raises(ContractError):
        load_verifier_material(solver, private_root=root, test_patch_relative_path=path.name)


@pytest.mark.parametrize("missing_public", [False, True])
def test_no_sibling_ancestor_secret_or_reference_file_is_opened(tmp_path, monkeypatch, missing_public):
    published = tmp_path / "root" / "nested" / "published"
    published.parent.mkdir(parents=True)
    row, _ = make_public_dataset(published)
    private_files = []
    for directory in (published.parent / "secret", published.parent.parent / "secret",
                      published.parent / "reference_fixes", published / "unadmitted_tests"):
        directory.mkdir()
        for name in ("solution.jsonl", "reference_fix.patch", "test_patch.txt"):
            path = directory / name
            path.write_text("SECRET_FILESYSTEM_SENTINEL")
            private_files.append(path)
    monkeypatch.setenv("GEMMA4_DATASET_ROOT", str(published.parent / "secret"))
    monkeypatch.setenv("KAGGLE_SANDBOX_DIR", str(published.parent.parent / "secret"))
    monkeypatch.setenv("SWEGEMMA_SECRET_DIR", str(published.parent / "reference_fixes"))
    if missing_public:
        (published / "snapshots/fastapi_1.tgz").unlink()
    opened = []
    original_open = os.open
    forbidden_names = {p.name for p in private_files} | {"secret", "reference_fixes", "unadmitted_tests"}

    def observed_open(path, flags, *args, **kwargs):
        raw = os.fspath(path)
        assert Path(raw).name not in forbidden_names, "forbidden discovery attempted"
        fd = original_open(path, flags, *args, **kwargs)
        assert not os.get_inheritable(fd)
        opened.append((raw, fd))
        return fd

    monkeypatch.setattr(os, "open", observed_open)
    assert load_public_metadata(published)[0].instance_id == row["instance_id"]
    if missing_public:
        with pytest.raises(PublicDataError):
            solver_task_from_public_row(published, row)
    else:
        task = solver_task_from_public_row(published, row)
        assert "SECRET_FILESYSTEM_SENTINEL" not in solver_task_json(task)
    assert any(name == "tasks.jsonl" for name, _ in opened)
    # All descriptors belonging to intake were closed, including failure paths.
    for _, fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_existing_private_descriptor_is_never_consumed_by_public_intake(tmp_path, monkeypatch):
    public = tmp_path / "published"
    row, _ = make_public_dataset(public)
    private = tmp_path / "private.txt"
    private.write_text("INHERITED_DESCRIPTOR_SENTINEL")
    with private.open("rb") as stream:
        private_fd = stream.fileno()
        real_read = os.read

        def observed_read(fd, size):
            assert fd != private_fd, "public loader consumed private descriptor"
            return real_read(fd, size)

        monkeypatch.setattr(os, "read", observed_read)
        task = solver_task_from_public_row(public, row)
        assert "INHERITED_DESCRIPTOR_SENTINEL" not in solver_task_json(task)
        assert stream.tell() == 0


def test_importing_solver_intake_never_imports_vendor_or_verifier_modules():
    code = (
        "import sys; import eval.public_data; "
        "assert not any(n == 'swegemma' or n.startswith('swegemma.') for n in sys.modules); "
        "assert 'eval.verifier_data' not in sys.modules; assert 'eval.verifier_task' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_data_errors_do_not_echo_gold_or_private_fields(tmp_path):
    root = tmp_path / "published"
    row, _ = make_public_dataset(root)
    row["unknown"] = "/private/UNADMITTED_PRIVATE_PATH_SENTINEL"
    try:
        solver_task_from_public_row(root, row)
    except ContractError as exc:
        rendered = str(exc)
        for sentinel in (row["patch"], row["test_patch"], row["unknown"]):
            assert sentinel not in rendered
    else:
        pytest.fail("unknown task fields admitted")


def test_nested_screen_intake_never_exports_gold_or_selects_holdout(tmp_path, monkeypatch):
    """All paths exercised together with synthetic rows/assets and known v1 IDs."""
    public = tmp_path / "published"
    public.mkdir()
    frozen = json.loads(manifests.SPLIT_PATH.read_text())
    for directory in ("snapshots", "graphs", "embeddings"):
        (public / directory).mkdir()
    rows = []
    sentinels = ("SCREEN_REFERENCE_SENTINEL", "SCREEN_TEST_SENTINEL", "SCREEN_NODE_SENTINEL")
    for record in frozen["tasks"]:
        row = {key: record[key] for key in ("instance_id", "repo", "base_commit")}
        row.update(problem_statement="Legitimate published issue", hints_text="  Exact hint\n",
                   patch=sentinels[0], test_patch=sentinels[1], FAIL_TO_PASS=[sentinels[2]])
        rows.append(row)
        # Publish only tune assets. Selecting a holdout would fail even if an
        # incorrect selector tried to use it.
        if record["split"] == "dev":
            (public / "snapshots" / f"{record['instance_id']}.tgz").write_bytes(b"public snapshot")
            stem = f"{record['repo'].split('/')[1]}_{record['base_commit']}"
            (public / "graphs" / f"{stem}.json").write_bytes(b"public graph" * 20)
            (public / "embeddings" / f"{stem}.npz").write_bytes(b"public embedding" * 20)
    (public / "tasks.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    metadata = load_public_metadata(public)
    source_sha = tasks_source_sha256(public)
    # The source pin is intentionally fixture-local. Production remains pinned
    # to the published source SHA; no real gold/source file is used in this test.
    monkeypatch.setattr(manifests, "TASKS_SHA256", source_sha)
    split = manifests.verify_frozen_split(metadata, tasks_sha256=source_sha)
    control_data = manifests.build_screen_manifest(split)
    screen = manifests.validate_screen_manifest(control_data, split)
    observed_reads = []
    original_read = public_data.read_regular

    def observed_read(descriptor, relative, **kwargs):
        observed_reads.append(relative)
        return original_read(descriptor, relative, **kwargs)

    monkeypatch.setattr(public_data, "read_regular", observed_read)
    default = load_solver_tasks(public, screen)
    assert len(default) == 12
    for stage, expected in (("S1", 12), ("S2", 40), ("S3", 80)):
        observed_reads.clear()
        tasks = load_solver_tasks(public, screen, stage)
        selected = manifests.select_screen(screen, stage)
        assert len(tasks) == expected
        assert tuple(task.instance_id for task in tasks) == tuple(task.instance_id for task in selected)
        solver_facing_manifest = dumps([dataclasses.asdict(task) for task in selected])
        for sentinel in sentinels:
            assert sentinel not in solver_facing_manifest and sentinel not in dumps(control_data)
            assert all(sentinel not in solver_task_json(task) for task in tasks)
        assert "partition" not in solver_facing_manifest
        assert "test_patch" not in solver_facing_manifest
        expected_reads = {"tasks.jsonl"} | {
            ref.source_relative_path for task in tasks for ref in (task.snapshot, task.graph, task.embedding)
        }
        assert set(observed_reads) == expected_reads
    with pytest.raises(manifests.ManifestError):
        load_solver_tasks(public, screen, "S4")
    # A source drift is rejected before any selected asset is opened.
    (public / "tasks.jsonl").write_text("".join(json.dumps(row) + "\n" for row in reversed(rows)))
    observed_reads.clear()
    with pytest.raises(PublicDataError, match="source identity"):
        load_solver_tasks(public, screen)
    assert observed_reads == ["tasks.jsonl"]
