"""P1-2 locked holdout split. Synthetic tasks only; the frozen v1 check uses committed files, not the dataset."""
import hashlib
import json
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import pytest

from tools import build_split, common

REPO = Path(__file__).resolve().parent.parent
INPUTS = ("instance_id", "repo", "base_commit")  # literal, not imported from tools.build_split


def synth_tasks(with_gold=True, gold="A"):
    """129 tasks shaped like the public set: 67/48/13/1, with two shared-commit pairs (one rich, one requests)."""
    rows = []
    for repo, n in (("fastapi/fastapi", 67), ("Textualize/rich", 48), ("psf/requests", 13), ("encode/httpx", 1)):
        short = repo.split("/")[1]
        for i in range(n):
            commit = hashlib.sha1(f"{repo}{i}".encode()).hexdigest()
            if short in ("rich", "requests") and i == 1:
                commit = hashlib.sha1(f"{repo}0".encode()).hexdigest()  # shares base_commit with i == 0
            row = {"instance_id": f"{short}_{i}", "repo": repo, "base_commit": commit,
                   "problem_statement": f"ps {i}", "hints_text": "", "created_at": "2024-01-01T00:00:00Z"}
            if with_gold:
                row |= {"patch": f"{gold}-patch-{i}", "test_patch": f"{gold}-test-{i}"}
            rows.append(row)
    return rows


def write_tasks(root: Path, rows) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tasks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return root


def split_text(tmp_path, rows) -> str:
    root = write_tasks(tmp_path / f"ds{len(list(tmp_path.iterdir()))}", rows)
    return common.dumps(build_split.build_split(build_split.load_inputs(root / "tasks.jsonl")))


def test_split_independent_of_gold_and_other_fields(tmp_path):
    base = split_text(tmp_path, synth_tasks(gold="A"))
    assert split_text(tmp_path, synth_tasks(gold="TOTALLY-DIFFERENT")) == base
    assert split_text(tmp_path, synth_tasks(with_gold=False)) == base
    mutated = [r | {"problem_statement": "x", "created_at": "1999-01-01T00:00:00Z", "hints_text": "h"} for r in synth_tasks()]
    assert split_text(tmp_path, mutated) == base


def test_load_inputs_projects_to_allowed_keys(tmp_path):
    root = write_tasks(tmp_path / "ds", synth_tasks())
    rows = build_split.load_inputs(root / "tasks.jsonl")
    assert all(set(r) == {"instance_id", "repo", "base_commit"} for r in rows)  # literal: the only allowed inputs
    assert "patch" not in common.dumps(build_split.build_split(rows))


def test_split_shape_grouping_and_singletons(tmp_path):
    s = json.loads(split_text(tmp_path, synth_tasks()))
    assert s["counts"]["dev"]["total"] == 80 and s["counts"]["holdout"]["total"] == 49
    assert s["holdout_quota_by_repo"] == {"Textualize/rich": 18, "encode/httpx": 0, "fastapi/fastapi": 26, "psf/requests": 5}
    sides = defaultdict(set)
    for t in s["tasks"]:
        sides[t["base_commit"]].add(t["split"])
    assert all(len(v) == 1 for v in sides.values()) and s["base_commits_crossing_split"] == []
    assert [t["split"] for t in s["tasks"] if t["repo"] == "encode/httpx"] == ["dev"]
    assert all(set(t) == {"instance_id", "repo", "base_commit", "split"} for t in s["tasks"])


def test_split_order_independent_and_seed_sensitive(tmp_path):
    rows = synth_tasks()
    a = build_split.build_split([{k: r[k] for k in INPUTS} for r in rows])
    b = build_split.build_split([{k: r[k] for k in INPUTS} for r in reversed(rows)])
    assert common.dumps(a) == common.dumps(b)
    c = build_split.build_split([{k: r[k] for k in INPUTS} for r in rows], seed="other")
    assert [t["split"] for t in c["tasks"]] != [t["split"] for t in a["tasks"]]


def test_group_that_would_overshoot_goes_to_dev():
    rows = [{"instance_id": f"t{i}", "repo": "r/r", "base_commit": c} for i, c in enumerate(["x", "x", "y"])]
    s = build_split.build_split(rows, fraction=Fraction(1, 3))  # quota 1: the pair can never be holdout
    assert {t["instance_id"]: t["split"] for t in s["tasks"]} == {"t0": "dev", "t1": "dev", "t2": "holdout"}


def test_quota_apportionment():
    assert build_split.holdout_quotas({"a": 67, "b": 48, "c": 13, "d": 1}, Fraction(49, 129)) == {"a": 26, "b": 18, "c": 5, "d": 0}


def test_cli_writes_once_then_refuses_changes(tmp_path):
    root = write_tasks(tmp_path / "ds", synth_tasks())
    out = tmp_path / "splits"
    build_split.main(["--dataset-root", str(root), "--out-dir", str(out)])
    first = (out / "v1.json").read_bytes()
    assert (out / "v1.sha256").read_text() == f"{hashlib.sha256(first).hexdigest()}  v1.json\n"
    build_split.main(["--dataset-root", str(root), "--out-dir", str(out), "--check"])  # unchanged: ok
    write_tasks(root, synth_tasks()[:-5])  # dataset changed -> regenerated split differs
    with pytest.raises(build_split.FrozenSplitError):
        build_split.main(["--dataset-root", str(root), "--out-dir", str(out)])
    assert (out / "v1.json").read_bytes() == first


def test_committed_v1_is_self_consistent():
    d = REPO / "eval" / "splits"
    raw = (d / "v1.json").read_bytes()
    digest, name = (d / "v1.sha256").read_text().split()
    assert name == "v1.json" and hashlib.sha256(raw).hexdigest() == digest
    s = json.loads(raw)
    assert s["base_commits_crossing_split"] == [] and s["inputs_used"] == ["instance_id", "repo", "base_commit"]
    assert Counter(t["split"] for t in s["tasks"]) == {"dev": s["counts"]["dev"]["total"], "holdout": s["counts"]["holdout"]["total"]}
    assert all(set(t) == {"instance_id", "repo", "base_commit", "split"} for t in s["tasks"])
