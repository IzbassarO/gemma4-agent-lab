"""Synthetic public intake admission: no vendor runtime or real task execution."""
import json
import os
from pathlib import Path

import pytest

from eval._contracts import ContractError
from eval.manifests import ManifestError
from eval.public_data import (
    PublicDataError, load_public_metadata, load_solver_tasks,
    solver_task_from_public_row, tasks_source_sha256,
)
from tools.common import partial_sha256, sha256_bytes


def make_public_dataset(root: Path):
    root.mkdir()
    row = {
        "instance_id": "fastapi_1", "repo": "fastapi/fastapi", "base_commit": "a" * 40,
        "problem_statement": " Keep whitespace\r\nUnicode: café → issue\n",
        "hints_text": "  Hint\ttext\n", "created_at": "2026-01-01T00:00:00Z",
        "patch": "REFERENCE_FIX_SENTINEL", "test_patch": "VERIFIER_TEST_SENTINEL",
        "FAIL_TO_PASS": ["VERIFIER_NODE_SENTINEL"], "PASS_TO_PASS": ["VERIFIER_PASS_SENTINEL"],
    }
    (root / "tasks.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    for directory in ("snapshots", "graphs", "embeddings"):
        (root / directory).mkdir()
    assets = {
        "snapshots/fastapi_1.tgz": b"opaque published snapshot" * 10,
        f"graphs/fastapi_{'a' * 40}.json": b"public graph" * 20,
        f"embeddings/fastapi_{'a' * 40}.npz": b"public embedding" * 20,
    }
    for relative, content in assets.items():
        (root / relative).write_bytes(content)
    return row, assets


@pytest.fixture
def published(tmp_path):
    root = tmp_path / "published"
    row, assets = make_public_dataset(root)
    return root, row, assets


def test_metadata_contains_only_non_gold_fields(published):
    root, row, _ = published
    metadata = load_public_metadata(root)
    assert len(metadata) == 1
    assert metadata[0].instance_id == row["instance_id"]
    assert not hasattr(metadata[0], "patch")
    assert not hasattr(metadata[0], "test_patch")
    assert tasks_source_sha256(root) == sha256_bytes((root / "tasks.jsonl").read_bytes())


def test_solver_view_preserves_legitimate_text_and_full_asset_identities(published):
    root, row, assets = published
    task = solver_task_from_public_row(root, row)
    assert task.problem_statement == row["problem_statement"]
    assert task.hints_text == row["hints_text"]
    for ref in (task.snapshot, task.graph, task.embedding):
        content = assets[ref.source_relative_path]
        assert ref.sha256 == sha256_bytes(content)
        assert ref.size_bytes == len(content)
    serialized = task.to_json()
    for field in ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "created_at", "partition"):
        assert field not in task.to_dict()
    for sentinel in (row["patch"], row["test_patch"], *row["FAIL_TO_PASS"], *row["PASS_TO_PASS"]):
        assert sentinel not in repr(task) and sentinel not in serialized
    assert str(root) not in serialized


def test_gold_changes_do_not_change_solver_identity(published):
    root, row, _ = published
    before = solver_task_from_public_row(root, row)
    changed = {**row, "patch": "OTHER_REFERENCE", "test_patch": "OTHER_TESTS",
               "created_at": "different", "FAIL_TO_PASS": ["different node"]}
    assert solver_task_from_public_row(root, changed).sha256() == before.sha256()
    no_gold = {k: v for k, v in row.items()
               if k not in {"patch", "test_patch", "created_at", "FAIL_TO_PASS", "PASS_TO_PASS"}}
    assert solver_task_from_public_row(root, no_gold) == before


def test_full_snapshot_hash_is_not_inventory_partial_identity(published):
    root, row, _ = published
    path = root / "snapshots/fastapi_1.tgz"
    original = b"a" * (3 * 1024 * 1024)
    path.write_bytes(original)
    inventory_before = partial_sha256(path)
    first = solver_task_from_public_row(root, row).snapshot
    changed = original[: 1536 * 1024] + b"z" + original[1536 * 1024 + 1:]
    path.write_bytes(changed)
    assert partial_sha256(path) == inventory_before
    second = solver_task_from_public_row(root, row).snapshot
    assert first.sha256 == sha256_bytes(original)
    assert second.sha256 == sha256_bytes(changed) and first.sha256 != second.sha256


@pytest.mark.parametrize("field", ["expected_result", "reference_solution", "partition", "private_path", "verifier_result"])
def test_unrecognized_source_fields_fail_closed(published, field):
    root, row, _ = published
    with pytest.raises(ContractError):
        solver_task_from_public_row(root, {**row, field: "PRIVATE_SENTINEL"})


@pytest.mark.parametrize("field,value", [
    ("instance_id", "../secret"), ("instance_id", 1), ("repo", "../fastapi"),
    ("base_commit", "A" * 40), ("problem_statement", None), ("hints_text", []),
    ("patch", {}), ("test_patch", False), ("FAIL_TO_PASS", "['node']"),
    ("PASS_TO_PASS", [1]),
])
def test_bad_published_values_are_not_coerced(published, field, value):
    root, row, _ = published
    with pytest.raises((ContractError, ManifestError)):
        solver_task_from_public_row(root, {**row, field: value})


def test_duplicate_ids_fail_before_asset_admission(published):
    root, row, _ = published
    (root / "tasks.jsonl").write_text((json.dumps(row) + "\n") * 2)
    with pytest.raises(PublicDataError, match="duplicate"):
        load_public_metadata(root)


@pytest.mark.parametrize("source", [
    b'{"instance_id":"fastapi_1","instance_id":"fastapi_2"}\n',
    b'{"instance_id":NaN}\n', b'not json\n', b'\xff\n', b'', b'\n',
])
def test_invalid_jsonl_fails(published, source):
    root, _, _ = published
    (root / "tasks.jsonl").write_bytes(source)
    with pytest.raises(ContractError):
        load_public_metadata(root)


@pytest.mark.parametrize("kind", ["snapshot", "graph", "embedding"])
def test_missing_required_asset_fails_without_fallback(published, kind):
    root, row, assets = published
    directories = {"snapshot": "snapshots/", "graph": "graphs/", "embedding": "embeddings/"}
    relative = next(p for p in assets if p.startswith(directories[kind]))
    (root / relative).unlink()
    with pytest.raises(PublicDataError):
        solver_task_from_public_row(root, row)


def test_explicit_graph_omission_has_no_auto_discovery(published):
    root, row, assets = published
    for relative in assets:
        if not relative.startswith("snapshots/"):
            (root / relative).unlink()
    task = solver_task_from_public_row(root, row, include_code_intelligence=False)
    assert task.graph is None and task.embedding is None
    with pytest.raises(PublicDataError):
        solver_task_from_public_row(root, row)
    with pytest.raises(PublicDataError):
        solver_task_from_public_row(root, row, include_code_intelligence=0)


@pytest.mark.parametrize("kind,size", [("snapshot", 0), ("graph", 0), ("graph", 100), ("embedding", 100)])
def test_unavailable_assets_fail(published, kind, size):
    root, row, assets = published
    prefix = {"snapshot": "snapshots/", "graph": "graphs/", "embedding": "embeddings/"}[kind]
    (root / next(p for p in assets if p.startswith(prefix))).write_bytes(b"x" * size)
    with pytest.raises(PublicDataError):
        solver_task_from_public_row(root, row)


@pytest.mark.parametrize("target", ["tasks.jsonl", "snapshots/fastapi_1.tgz"])
def test_symlinks_rejected_and_only_public_assets_allow_hardlinks(published, tmp_path, target):
    root, row, _ = published
    path = root / target
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    action = (lambda: load_public_metadata(root)) if target == "tasks.jsonl" else (lambda: solver_task_from_public_row(root, row))
    with pytest.raises(PublicDataError):
        action()
    path.unlink()
    os.link(outside, path)
    if target == "tasks.jsonl":
        with pytest.raises(PublicDataError):
            action()
    else:
        assert action().snapshot.sha256 == sha256_bytes(outside.read_bytes())


def test_symlink_root_and_asset_parent_are_rejected(published, tmp_path):
    root, row, _ = published
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(PublicDataError):
        load_public_metadata(alias)
    moved = tmp_path / "moved_snapshots"
    (root / "snapshots").rename(moved)
    (root / "snapshots").symlink_to(moved, target_is_directory=True)
    with pytest.raises(PublicDataError):
        solver_task_from_public_row(root, row)


def test_fifo_source_is_rejected_without_blocking(published):
    root, _, _ = published
    (root / "tasks.jsonl").unlink()
    os.mkfifo(root / "tasks.jsonl")
    with pytest.raises(PublicDataError):
        load_public_metadata(root)


def test_roots_are_explicit_paths_not_descriptors_or_environment(published, monkeypatch):
    root, _, _ = published
    monkeypatch.setenv("GEMMA4_DATASET_ROOT", str(root))
    with (root / "tasks.jsonl").open("rb") as stream:
        for wrong in (None, stream, stream.fileno(), str(root), Path("published")):
            with pytest.raises(PublicDataError):
                load_public_metadata(wrong)


def test_routine_solver_intake_requires_validated_screening(published):
    root, _, _ = published
    with pytest.raises(PublicDataError):
        load_solver_tasks(root, {})
