"""Tri-state git evidence in provenance verification (final-audit P1). Temporary git repositories only.

Expected values are literals: VERIFIED / VERIFIED_WITH_UNVERIFIABLE_EVIDENCE / FAILED for the overall status, and
VERIFIED / FAILED / UNVERIFIABLE / NOT_CLAIMED for git evidence.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import build_submission, hash_submission
from tools.common import WriteGuard, sha256_file

REPO = Path(__file__).resolve().parent.parent
TOOLS = "".join(f"  - {t}\n" for t in ["run_command", "submit_patch", "get_status", "read_file", "edit_file",
                                        "write_file", "get_code_neighbors", "search_similar_code", "get_code_subgraph"])
AGENT = "name: root\nmodel: gemma-4-31b-it-qat-w4a16-ct\ninstruction: !include prompts/system.md\ntools:\n" + TOOLS


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("GEMMA4_DATASET_ROOT", raising=False)


def git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def new_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(path, "config", k, v)
    (path / "README").write_text("r\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def env(tmp_path):
    """Fake dataset + matching repository metadata, so that a fully checkable artifact can be plain VERIFIED."""
    ds = tmp_path / "dataset"
    ds.mkdir()
    (ds / "tasks.jsonl").write_text("{}\n")
    (ds / "HARNESS_README.md").write_text("# fake\n")
    meta = tmp_path / "meta"
    shutil.copytree(REPO / "vendor_meta", meta / "vendor_meta")
    shutil.copytree(REPO / "eval" / "splits", meta / "eval" / "splits")
    dm = json.loads((meta / "vendor_meta/dataset_manifest.json").read_text())
    dm["key_file_sha256"] = {"tasks.jsonl": sha256_file(ds / "tasks.jsonl"), "HARNESS_README.md": sha256_file(ds / "HARNESS_README.md")}
    (meta / "vendor_meta/dataset_manifest.json").write_text(json.dumps(dm))
    repo = new_repo(tmp_path / "repo")
    src = repo / "agents" / "c1"
    (src / "prompts").mkdir(parents=True)
    (src / "agent.yaml").write_text(AGENT)
    (src / "prompts/system.md").write_text("Fix it.\n")
    git(repo, "add", "agents")
    git(repo, "commit", "-qm", "c1")
    return {"ds": ds, "meta": meta, "repo": repo, "src": src, "tmp": tmp_path}


def build(e, out: str, **kw):
    return build_submission.build(e["src"], guard=WriteGuard(e["ds"]), candidate_id="c1", out_dir=e["tmp"] / out,
                                  meta_root=e["meta"], require_clean=True, **kw)


def verify(e, out: str, git_repo=None, dataset=True, meta=None):
    return hash_submission.verify(e["tmp"] / out, meta_root=meta or e["meta"], git_repo=git_repo or e["repo"],
                                  dataset_root=e["ds"] if dataset else None)


def edit_prov(e, out: str, fn):
    p = e["tmp"] / out / "provenance.json"
    d = json.loads(p.read_text())
    fn(d)
    p.write_text(json.dumps(d))


def origin_status(r) -> dict:
    return {i["check"]: i["status"] for i in r["checks"] if i["check"].startswith("origin:")}


# CASE A -----------------------------------------------------------------------------------------
def test_case_a_commit_available_and_matching_is_verified(env):
    build(env, "a")
    r = verify(env, "a")
    assert r["status"] == "VERIFIED" and r["git_evidence"] == "VERIFIED"
    assert r["mismatches"] == [] and r["unverifiable"] == []
    assert origin_status(r) == {"origin:agent.yaml": "ok", "origin:prompts/system.md": "ok"}


# CASE B -----------------------------------------------------------------------------------------
def test_case_b_commit_available_but_source_differs_fails(env):
    c1 = git(env["repo"], "rev-parse", "HEAD")
    (env["src"] / "prompts/system.md").write_text("Different.\n")
    git(env["repo"], "commit", "-qam", "c2")
    build(env, "b")  # honestly built at c2
    edit_prov(env, "b", lambda p: p.update(git_commit=c1))  # claims c1, which exists locally but holds other bytes
    r = verify(env, "b")
    assert r["status"] == "FAILED" and r["git_evidence"] == "FAILED"
    assert origin_status(r) == {"origin:agent.yaml": "ok", "origin:prompts/system.md": "mismatch"}


# CASE C -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["other_repo", "not_a_repo"])
def test_case_c_commit_unavailable_is_unverifiable_not_failed(env, where):
    build(env, "c")
    other = new_repo(env["tmp"] / "other") if where == "other_repo" else (env["tmp"] / "plain")
    other.mkdir(exist_ok=True)
    r = verify(env, "c", git_repo=other)
    assert r["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE"
    assert r["git_evidence"] == "UNVERIFIABLE"
    assert r["mismatches"] == []
    assert origin_status(r) == {"origin:agent.yaml": "unverifiable", "origin:prompts/system.md": "unverifiable"}
    assert {i["check"] for i in r["unverifiable"]} == {"origin:agent.yaml", "origin:prompts/system.md", "source_in_git"}
    p = json.loads((env["tmp"] / "c/provenance.json").read_text())
    assert p["source_in_git"] is True  # the field is never rewritten


def test_case_c_partial_objects_classified_per_file(env):
    """Commit and root trees exist but the prompts/ subtree object is missing: per-file, never collapsed."""
    build(env, "cp")
    subtree = git(env["repo"], "rev-parse", "HEAD:agents/c1/prompts")
    (env["repo"] / ".git" / "objects" / subtree[:2] / subtree[2:]).unlink()
    r = verify(env, "cp")
    assert origin_status(r) == {"origin:agent.yaml": "ok", "origin:prompts/system.md": "unverifiable"}
    assert r["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE" and r["git_evidence"] == "UNVERIFIABLE"


# CASE D -----------------------------------------------------------------------------------------
def test_case_d_observable_contradiction_with_partial_objects_still_fails(env):
    c1 = git(env["repo"], "rev-parse", "HEAD")
    (env["src"] / "agent.yaml").write_text(AGENT.replace("name: root", "name: changed"))
    git(env["repo"], "commit", "-qam", "c2")
    build(env, "d")
    edit_prov(env, "d", lambda p: p.update(git_commit=c1))
    subtree = git(env["repo"], "rev-parse", f"{c1}:agents/c1/prompts")
    (env["repo"] / ".git" / "objects" / subtree[:2] / subtree[2:]).unlink()
    r = verify(env, "d")
    assert origin_status(r) == {"origin:agent.yaml": "mismatch", "origin:prompts/system.md": "unverifiable"}
    assert r["status"] == "FAILED" and r["git_evidence"] == "FAILED"  # the observable mismatch is not hidden


@pytest.mark.parametrize("name,fn", [
    ("wrong source path", lambda p: p.update(source_path_in_repo="agents/elsewhere")),
    ("origin vs git_status", lambda p: p["source_origins"]["agent.yaml"].update(git_status="modified")),
    ("unproven origin under source_in_git=true", lambda p: p["source_origins"]["agent.yaml"].update(origin="unproven", git_status="untracked")),
    ("source_in_git false while all origins git-tracked", lambda p: p.update(source_in_git=False)),
    ("source_in_git not boolean", lambda p: p.update(source_in_git="yes")),
    ("commit removed", lambda p: p.update(git_commit=None)),
])
def test_case_d_commit_available_contradiction_fails(env, name, fn):
    build(env, "d2")
    edit_prov(env, "d2", fn)
    r = verify(env, "d2")
    assert r["status"] == "FAILED", name


@pytest.mark.parametrize("fn", [
    lambda p: p["source_origins"]["agent.yaml"].update(origin="unproven", git_status="untracked"),
    lambda p: p["source_origins"]["agent.yaml"].update(git_status="modified"),
])
def test_case_d_internal_contradiction_fails_even_when_commit_unavailable(env, fn):
    build(env, "d3")
    edit_prov(env, "d3", fn)
    r = verify(env, "d3", git_repo=new_repo(env["tmp"] / "elsewhere"))
    assert r["status"] == "FAILED"  # observable from provenance itself: never downgraded to UNVERIFIABLE


# CASE E -----------------------------------------------------------------------------------------
def test_case_e_external_metadata_unavailable_zip_local_facts_verify(env):
    build(env, "e")
    empty_meta = env["tmp"] / "empty_meta"
    empty_meta.mkdir()
    r = verify(env, "e", dataset=False, meta=empty_meta)
    assert r["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE" and r["mismatches"] == []
    unver = {i["check"] for i in r["unverifiable"]}
    assert {"live_dataset.tasks_jsonl_sha256", "live_dataset.harness_readme_sha256",
            "environment.split_sha256", "environment.dataset_manifest_sha256"} <= unver
    assert r["git_evidence"] == "VERIFIED"  # git evidence is still independently available
    zip_local = {i["check"]: i["status"] for i in r["checks"]}
    assert all(zip_local[k] == "ok" for k in ("zip_sha256", "archive_members_and_hashes", "unpacked_bytes",
                                                "source_tree_sha256", "validation_structural", "adapter_hashes"))


@pytest.mark.parametrize("fn", [lambda p: p.update(unpacked_bytes=-1), lambda p: p.update(lora_present=True),
                                lambda p: p["validation"].update(structural="STRUCTURAL_INVALID")])
def test_case_e_unavailable_environment_does_not_hide_recomputable_failure(env, fn):
    build(env, "e2")
    edit_prov(env, "e2", fn)
    empty_meta = env["tmp"] / "empty_meta2"
    empty_meta.mkdir()
    r = verify(env, "e2", dataset=False, meta=empty_meta, git_repo=env["tmp"])
    assert r["status"] == "FAILED" and r["unverifiable"]  # failure reported alongside the unverifiable facts


# CLI semantics ---------------------------------------------------------------------------------
def test_cli_exit_codes(env, capsys):
    # built against the real repository metadata, which the CLI verifies against by default
    build_submission.build(env["src"], guard=WriteGuard(env["ds"]), candidate_id="c1", out_dir=env["tmp"] / "cli")
    out = str(env["tmp"] / "cli")
    assert hash_submission.main([out]) == 0                       # VERIFIED_WITH_UNVERIFIABLE_EVIDENCE (no dataset)
    assert "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE" in capsys.readouterr().out
    assert hash_submission.main([out, "--strict"]) == 2
    edit_prov(env, "cli", lambda p: p.update(unpacked_bytes=-1))
    assert hash_submission.main([out]) == 1
