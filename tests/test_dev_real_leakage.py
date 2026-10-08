"""Trusted publication boundaries; fixtures contain no real dataset material."""
import json
import os
from pathlib import Path
import stat

import pytest

from eval import public_data, runtime
from eval._contracts import ContractError, canonical_sha256
from eval.public_data import solver_task_from_public_row
from eval.verifier_data import load_verifier_material, publish_private_test_patches, read_reference_patch
from test_dev_public_data import make_public_dataset
from tools.common import UnsafeOutputError, WriteGuard, sha256_bytes


CONFIG = {"schema_version": 1, "sandbox": "subprocess", "timeout_seconds": 60,
          "setup_py_sha256": "a" * 64, "wheels_tree_sha256": "b" * 64}


def fixture(tmp_path):
    root = tmp_path / "published"
    row, _ = make_public_dataset(root)
    return root, row, tmp_path / "private"


def test_private_publication_identity_permissions_and_solver_boundary(tmp_path):
    public, row, private = fixture(tmp_path)
    solver = solver_task_from_public_row(public, row)
    tasks = publish_private_test_patches(public, [solver.instance_id], private,
                                        guard=WriteGuard(public), verification_config=CONFIG)
    verifier = tasks[solver.instance_id]
    assert verifier.snapshot == solver.snapshot
    assert verifier.verification_config_sha256 == canonical_sha256(CONFIG)
    assert verifier.test_patch.sha256 == sha256_bytes(row["test_patch"].encode())
    assert verifier.test_patch.size_bytes == len(row["test_patch"].encode())
    assert verifier.fail_to_pass is None and verifier.pass_to_pass is None
    path = private / f"{solver.instance_id}.test.patch"
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1
    assert load_verifier_material(verifier, private_root=private,
                                  test_patch_relative_path=path.name).test_patch == row["test_patch"]
    serialized = solver.to_json()
    for forbidden in ("test_patch", '"patch"', "FAIL_TO_PASS", "PASS_TO_PASS", str(private),
                      row["test_patch"], row["patch"]):
        assert forbidden not in serialized


@pytest.mark.parametrize("existing", ["regular", "symlink", "hardlink"])
def test_private_publication_never_overwrites(tmp_path, existing):
    public, row, private = fixture(tmp_path)
    private.mkdir(mode=0o700)
    target = private / f"{row['instance_id']}.test.patch"
    sentinel = tmp_path / "existing"
    sentinel.write_text("UNCHANGED")
    if existing == "regular":
        target.write_text("UNCHANGED")
    elif existing == "symlink":
        target.symlink_to(sentinel)
    else:
        os.link(sentinel, target)
    with pytest.raises(ContractError, match="already exists"):
        publish_private_test_patches(public, [row["instance_id"]], private,
                                     guard=WriteGuard(public), verification_config=CONFIG)
    assert target.read_text() == "UNCHANGED" and sentinel.read_text() == "UNCHANGED"


@pytest.mark.parametrize("root_kind", ["public", "symlink", "mode755", "mode000"])
def test_private_root_guard_and_no_symlink_admission(tmp_path, root_kind):
    public, row, private = fixture(tmp_path)
    if root_kind == "public":
        private = public / "private"
    elif root_kind == "symlink":
        destination = tmp_path / "destination"
        destination.mkdir(mode=0o700)
        private.symlink_to(destination, target_is_directory=True)
    else:
        private.mkdir(mode=0o755 if root_kind == "mode755" else 0o000)
    with pytest.raises((ContractError, UnsafeOutputError)):
        publish_private_test_patches(public, [row["instance_id"]], private,
                                     guard=WriteGuard(public), verification_config=CONFIG)
    if root_kind == "mode000":
        private.chmod(0o700)


@pytest.mark.parametrize("ids", [[], ["missing"], ["../escape"], ["fastapi_1", "fastapi_1"], "fastapi_1"])
def test_private_publication_explicit_task_admission(tmp_path, ids):
    public, _, private = fixture(tmp_path)
    with pytest.raises(ContractError):
        publish_private_test_patches(public, ids, private, guard=WriteGuard(public), verification_config=CONFIG)
    assert not private.exists()


@pytest.mark.parametrize("change", [{"extra": True}, {"timeout_seconds": 59}, {"sandbox": "docker"},
                                      {"setup_py_sha256": "invalid"}, {"schema_version": True}])
def test_private_publication_closed_verification_config(tmp_path, change):
    public, row, private = fixture(tmp_path)
    with pytest.raises(ContractError):
        publish_private_test_patches(public, [row["instance_id"]], private,
                                     guard=WriteGuard(public), verification_config={**CONFIG, **change})
    assert not private.exists()


def test_private_source_change_between_projection_and_read_fails_closed(tmp_path, monkeypatch):
    public, row, private = fixture(tmp_path)
    original = public_data._read_rows

    def changed(fd):
        result = original(fd)
        (public / "tasks.jsonl").write_text(json.dumps({**row, "test_patch": "MUTATED"}) + "\n")
        return result

    monkeypatch.setattr(public_data, "_read_rows", changed)
    with pytest.raises(ContractError, match="changed"):
        publish_private_test_patches(public, [row["instance_id"]], private,
                                     guard=WriteGuard(public), verification_config=CONFIG)
    assert not private.exists()


def test_missing_private_evidence_is_not_an_empty_fallback(tmp_path):
    public, row, private = fixture(tmp_path)
    del row["test_patch"]
    (public / "tasks.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(ContractError, match="explicit private"):
        publish_private_test_patches(public, [row["instance_id"]], private,
                                     guard=WriteGuard(public), verification_config=CONFIG)
    assert not private.exists()


def test_reference_helper_is_trusted_only_and_does_not_write_or_print(tmp_path, capsys):
    public, row, _ = fixture(tmp_path)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert read_reference_patch(public, row["instance_id"]) == row["patch"]
    assert capsys.readouterr().out == ""
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    assert "eval/verifier_data.py" not in runtime._COMMON_CODE
    assert "eval/verifier_data.py" not in getattr(runtime, "_REAL_COMMON_CODE", ())
    with pytest.raises(ContractError):
        read_reference_patch(public, "../escape")
