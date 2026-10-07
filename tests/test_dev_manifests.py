"""Frozen split admission and tune-only nested screens; no tasks or agents run."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from eval import manifests as m
from tools.common import WriteGuard, dumps

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def frozen_data():
    return json.loads((REPO / "eval/splits/v1.json").read_text())


@pytest.fixture
def metadata(frozen_data):
    # This fixture uses only committed ID/repo/base_commit fields. Actual public
    # source admission is separately integrated with the trusted intake module.
    return tuple(m.TaskMetadata(row["instance_id"], row["repo"], row["base_commit"])
                 for row in frozen_data["tasks"])


@pytest.fixture
def split(metadata):
    return m.verify_frozen_split(metadata, tasks_sha256=m.TASKS_SHA256)


@pytest.fixture
def screen_data(split):
    return m.build_screen_manifest(split)


def publish_fixture(tmp_path, data, name="screens_v1.json"):
    target = tmp_path.resolve() / name
    target.write_text(dumps(data))
    target.with_suffix(".sha256").write_text(
        f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n")
    return target


@pytest.mark.parametrize("field,value", [
    ("instance_id", "../fastapi_1"), ("instance_id", "requests_1"),
    ("instance_id", 1), ("instance_id", True), ("instance_id", "fastapi_1\n"),
    ("repo", "elsewhere/repo"), ("repo", None),
    ("base_commit", "A" * 40), ("base_commit", "a" * 39), ("base_commit", 42),
])
def test_metadata_is_closed_and_strict(field, value):
    fields = {"instance_id": "fastapi_1", "repo": "fastapi/fastapi", "base_commit": "a" * 40}
    fields[field] = value
    with pytest.raises(m.ManifestError):
        m.TaskMetadata(**fields)


def test_metadata_is_frozen_slots_without_gold():
    task = m.TaskMetadata("fastapi_1", "fastapi/fastapi", "a" * 40)
    assert [field.name for field in dataclasses.fields(task)] == ["instance_id", "repo", "base_commit"]
    assert not hasattr(task, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.instance_id = "fastapi_2"
    with pytest.raises(TypeError):
        m.TaskMetadata("fastapi_1", "fastapi/fastapi", "a" * 40, patch="GOLD")


def test_frozen_split_exact_pin_and_read_only(metadata):
    path = REPO / "eval/splits/v1.json"
    sidecar = path.with_suffix(".sha256")
    before = (path.read_bytes(), sidecar.read_bytes())
    verified = m.verify_frozen_split(tuple(reversed(metadata)), tasks_sha256=m.TASKS_SHA256)
    assert verified.split_sha256 == "420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12"
    assert verified.input_projection_sha256 == "5a6188365392b2febde6d3067f5fea9e8c5dddcd7389a6e02a87e4333245df30"
    assert len(verified.tune_tasks) == 80
    assert not hasattr(verified, "holdout_tasks")
    assert (path.read_bytes(), sidecar.read_bytes()) == before


@pytest.mark.parametrize("kind", ["duplicate", "missing", "wrong_commit", "wrong_sha", "list"])
def test_source_identity_fail_closed(metadata, kind):
    source, sha = metadata, m.TASKS_SHA256
    if kind == "duplicate":
        source = metadata[:-1] + (metadata[0],)
    elif kind == "missing":
        source = metadata[:-1]
    elif kind == "wrong_commit":
        source = (dataclasses.replace(metadata[0], base_commit="a" * 40),) + metadata[1:]
    elif kind == "wrong_sha":
        sha = "0" * 64
    else:
        source = list(metadata)
    with pytest.raises(m.ManifestError):
        m.verify_frozen_split(source, tasks_sha256=sha)


def test_frozen_byte_mutation_rejected_even_with_updated_sidecar(tmp_path, metadata, frozen_data):
    changed = copy.deepcopy(frozen_data)
    changed["tasks"][0]["split"] = "holdout"
    path = publish_fixture(tmp_path, changed, "v1.json")
    with pytest.raises(m.ManifestError, match="frozen split SHA256 mismatch"):
        m.verify_frozen_split(metadata, tasks_sha256=m.TASKS_SHA256, split_path=path)


def test_frozen_sidecar_must_match_exact_format(tmp_path, metadata, frozen_data):
    path = publish_fixture(tmp_path, frozen_data, "v1.json")
    path.with_suffix(".sha256").write_text(f"{m.SPLIT_SHA256} *v1.json\n")
    with pytest.raises(m.ManifestError, match="sidecar"):
        m.verify_frozen_split(metadata, tasks_sha256=m.TASKS_SHA256, split_path=path)


@pytest.mark.parametrize("mutation", ["root_unknown", "row_gold", "count_bool", "quota", "seed", "duplicate", "crossing"])
def test_structural_frozen_checks_are_independent(frozen_data, metadata, mutation):
    # Exercise deeper pure checks separately: byte pinning would first reject
    # each modified file, and cannot substitute for testing the invariants.
    data = copy.deepcopy(frozen_data)
    if mutation == "root_unknown":
        data["gold"] = "SENTINEL"
    elif mutation == "row_gold":
        data["tasks"][0]["patch"] = "SENTINEL"
    elif mutation == "count_bool":
        data["counts"]["dev"]["total"] = True
    elif mutation == "quota":
        data["holdout_quota_by_repo"]["encode/httpx"] = 1
    elif mutation == "seed":
        data["seed"] = "different"
    elif mutation == "duplicate":
        data["tasks"][-1] = copy.deepcopy(data["tasks"][0])
    else:
        next(row for row in data["tasks"] if row["instance_id"] == "requests_6629")["split"] = "holdout"
    with pytest.raises(m.ManifestError):
        m._validate_frozen_data(data, metadata, m.TASKS_SHA256)


def test_screen_seed_membership_matches_committed_plan(split, screen_data):
    plan = (REPO / "docs/experiments/COMPETITIVE_OPTIMIZATION_PROGRAM_2026-10-06.md").read_text()
    manifest = m.validate_screen_manifest(screen_data, split)
    for stage, title in (("S1", "S1 — 12 tasks"), ("S2", "S2 — 40 tasks"), ("S3", "Full tune — 80 tasks")):
        block = re.search(r"\*\*" + re.escape(title) + r"\*\*\s+```text\n(.*?)```", plan, re.S)
        assert block is not None
        expected = tuple(sorted(block.group(1).split()))
        assert tuple(t.instance_id for t in m.select_screen(manifest, stage)) == expected


def test_screens_are_tune_only_nested_whole_groups_with_incremental_cohorts(split, screen_data):
    manifest = m.validate_screen_manifest(screen_data, split)
    sets = [{t.instance_id for t in m.select_screen(manifest, stage)} for stage in ("S1", "S2", "S3")]
    assert [len(ids) for ids in sets] == [12, 40, 80]
    assert sets[0] < sets[1] < sets[2] == {t.instance_id for t in split.tune_tasks}
    assert [len(m.incremental_screen(manifest, stage)) for stage in ("S1", "S2", "S3")] == [12, 28, 40]
    assert all(row["partition"] == "tune" for row in screen_data["tasks"])
    assert set(screen_data["stages"]) == {"S1", "S2", "S3"}
    text = dumps(screen_data)
    assert "holdout" not in text and "patch" not in text and "problem_statement" not in text


@pytest.mark.parametrize("stage", ["S4", "holdout", "dev", "", None, 1])
def test_no_normal_holdout_screen_api(split, screen_data, stage):
    manifest = m.validate_screen_manifest(screen_data, split)
    with pytest.raises(m.ManifestError, match="only tune screens"):
        m.select_screen(manifest, stage)


@pytest.mark.parametrize("cls", [m.VerifiedSplit, m.ScreeningManifest])
def test_unvalidated_constructors_are_unavailable(cls):
    with pytest.raises(TypeError):
        cls()
    forged = object.__new__(cls)
    with pytest.raises(m.ManifestError):
        m.build_screen_manifest(forged) if cls is m.VerifiedSplit else m.select_screen(forged)


def test_admitted_objects_cannot_be_replaced_or_mutated(split, screen_data):
    manifest = m.validate_screen_manifest(screen_data, split)
    with pytest.raises(dataclasses.FrozenInstanceError):
        manifest.sha256 = "0" * 64
    with pytest.raises(TypeError):
        dataclasses.replace(manifest, sha256="0" * 64)
    with pytest.raises(m.ManifestError):
        m.build_screen_manifest(screen_data)


@pytest.mark.parametrize("mutation", [
    "unknown", "gold", "timestamp", "schema_bool", "algorithm", "seed", "parent", "source",
    "holdout_stage", "holdout_row", "holdout_id", "nesting", "duplicate", "split_group",
    "wrong_count", "wrong_composition", "wrong_group_key", "wrong_task_commit", "reordered_ids",
    "changed_seeded_ids", "missing_task",
])
def test_screen_mutations_fail_closed(split, screen_data, frozen_data, mutation):
    data = copy.deepcopy(screen_data)
    if mutation == "unknown":
        data["unknown"] = "value"
    elif mutation == "gold":
        data["tasks"][0]["patch"] = "SENTINEL"
    elif mutation == "timestamp":
        data["created_at"] = "2026-10-06"
    elif mutation == "schema_bool":
        data["schema_version"] = True
    elif mutation == "algorithm":
        data["algorithm_version"] = 2
    elif mutation == "seed":
        data["seed"] = "other"
    elif mutation == "parent":
        data["parent_split"]["sha256"] = "0" * 64
    elif mutation == "source":
        data["source_dataset"]["tasks_sha256"] = "0" * 64
    elif mutation == "holdout_stage":
        data["stages"]["S4"] = copy.deepcopy(data["stages"]["S1"])
    elif mutation == "holdout_row":
        data["tasks"][0]["partition"] = "holdout"
    elif mutation == "holdout_id":
        iid = next(row["instance_id"] for row in frozen_data["tasks"] if row["split"] == "holdout")
        data["stages"]["S1"]["task_ids"][0] = iid
    elif mutation == "nesting":
        s1 = set(data["stages"]["S1"]["task_ids"])
        ids = data["stages"]["S2"]["task_ids"]
        removed = next(i for i in ids if i in s1)
        replacement = next(t.instance_id for t in split.tune_tasks if t.instance_id not in ids and t.repo == "fastapi/fastapi")
        ids[ids.index(removed)] = replacement
        ids.sort()
    elif mutation == "duplicate":
        ids = data["stages"]["S1"]["task_ids"]
        ids[0] = ids[1]
    elif mutation == "split_group":
        ids = data["stages"]["S1"]["task_ids"]
        ids[ids.index("requests_7502")] = "requests_6589"
        ids.sort()
    elif mutation == "wrong_count":
        data["stages"]["S1"]["count"] = 13
    elif mutation == "wrong_composition":
        data["stages"]["S1"]["by_repo"]["psf/requests"] = True
    elif mutation == "wrong_group_key":
        data["tasks"][0]["group_key"] = "wrong"
    elif mutation == "wrong_task_commit":
        data["tasks"][0]["base_commit"] = "a" * 40
    elif mutation == "reordered_ids":
        data["stages"]["S1"]["task_ids"].reverse()
    elif mutation == "changed_seeded_ids":
        ids = data["stages"]["S1"]["task_ids"]
        ids[ids.index("rich_3468")] = "rich_3472"  # S2 member, same repo/count/groups; wrong seeded identity
        ids.sort()
    else:
        data["tasks"].pop()
    with pytest.raises(m.ManifestError):
        m.validate_screen_manifest(data, split)


def test_screen_load_checks_digest_and_canonical_bytes(tmp_path, split, screen_data):
    path = publish_fixture(tmp_path, screen_data)
    loaded = m.load_screen_manifest(split, path=path)
    assert loaded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".sha256").write_text(f"{'0' * 64}  {path.name}\n")
    with pytest.raises(m.ManifestError, match="sidecar"):
        m.load_screen_manifest(split, path=path)
    path.write_text(json.dumps(screen_data))
    path.with_suffix(".sha256").write_text(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    with pytest.raises(m.ManifestError, match="canonically"):
        m.load_screen_manifest(split, path=path)


@pytest.mark.parametrize("raw", [b'{"key":1,"key":2}', b'{"key":NaN}', b'{"key":Infinity}', b'\xff'])
def test_closed_json_parser_rejects_ambiguous_or_invalid_bytes(raw):
    with pytest.raises(m.ManifestError):
        m._parse(raw)


def test_screen_writer_guards_and_refuses_overwrite(tmp_path, split, screen_data):
    dataset = tmp_path.resolve() / "published"
    dataset.mkdir()
    guard = WriteGuard(dataset)
    with pytest.raises(Exception):
        m.write_screen_manifest(split, guard=guard, path=dataset / "screens_v1.json")
    path = tmp_path.resolve() / "output/screens_v1.json"
    created = m.write_screen_manifest(split, guard=guard, path=path)
    before = path.read_bytes(), path.with_suffix(".sha256").read_bytes()
    assert m.write_screen_manifest(split, guard=guard, path=path).sha256 == created.sha256
    assert before == (path.read_bytes(), path.with_suffix(".sha256").read_bytes())
    changed = copy.deepcopy(screen_data)
    changed["seed"] = "changed"
    path.write_text(dumps(changed))
    path.with_suffix(".sha256").write_text(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    with pytest.raises(m.ManifestError):
        m.write_screen_manifest(split, guard=guard, path=path)
    assert path.read_text() == dumps(changed)


def test_screen_writer_cannot_replace_frozen_split(tmp_path, split):
    dataset = tmp_path.resolve() / "published"
    dataset.mkdir()
    with pytest.raises(Exception):
        m.write_screen_manifest(split, guard=WriteGuard(dataset), path=m.SPLIT_PATH)


def test_incomplete_screen_publication_fails_closed(tmp_path, split, screen_data):
    dataset = tmp_path.resolve() / "published"
    dataset.mkdir()
    path = tmp_path.resolve() / "screens_v1.json"
    path.write_text(dumps(screen_data))
    with pytest.raises(m.ManifestError, match="incomplete"):
        m.write_screen_manifest(split, guard=WriteGuard(dataset), path=path)
    assert not path.with_suffix(".sha256").exists()


@pytest.mark.parametrize("kind", ["parent_symlink", "file_symlink", "fifo", "hardlink", "oversized"])
def test_manifest_reads_refuse_unsafe_paths_without_loading(tmp_path, metadata, kind):
    root = tmp_path.resolve()
    real = root / "real"
    real.mkdir()
    path = real / "v1.json"
    path.write_bytes(m.SPLIT_PATH.read_bytes())
    path.with_suffix(".sha256").write_text(f"{m.SPLIT_SHA256}  v1.json\n")
    if kind == "parent_symlink":
        (root / "alias").symlink_to(real, target_is_directory=True)
        path = root / "alias/v1.json"
    elif kind == "file_symlink":
        (real / "aliased.json").symlink_to(path)
        path = real / "aliased.json"
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "hardlink":
        os.link(path, real / "second.json")
    else:
        path.write_bytes(b" " * (512 * 1024 + 1))
    with pytest.raises(m.ManifestError, match="safely"):
        m.verify_frozen_split(metadata, tasks_sha256=m.TASKS_SHA256, split_path=path)


def test_committed_screen_hash_and_membership(split):
    manifest = m.load_screen_manifest(split)
    assert len(m.select_screen(manifest, "S1")) == 12
    assert len(m.select_screen(manifest, "S2")) == 40
    assert len(m.select_screen(manifest, "S3")) == 80
