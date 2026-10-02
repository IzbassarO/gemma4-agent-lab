"""P1-1 output-path guard, P2-1 canonical manifests, P2-2 symlink handling, P2-4 gitignore. Temp fixtures only."""
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import snapshot_state
from tools import build_split, common, inventory_dataset, preserve_sample, wheelhouse

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    # never let the real $GEMMA4_DATASET_ROOT leak into fixture tests
    monkeypatch.delenv(common.DATASET_ENV, raising=False)


# ---- P1-1: guard ------------------------------------------------------------------------------

def test_guard_allows_normal_output(fake_dataset, tmp_path):
    out = tmp_path / "out" / "m.json"
    common.write_json(out, {"a": 1}, fake_dataset)
    assert out.read_text() == '{\n  "a": 1\n}\n'
    assert not list(out.parent.glob("*.tmp"))  # atomic write leaves no temp files


@pytest.mark.parametrize("rel", [".", "sub/dir/m.json", "sub/../m.json", "wheels"])
def test_guard_rejects_dataset_and_nested(fake_dataset, rel):
    with pytest.raises(common.UnsafeOutputError):
        common.guard_output(fake_dataset / rel, fake_dataset)


def test_guard_rejects_symlink_into_dataset(fake_dataset, tmp_path):
    (tmp_path / "innocent").symlink_to(fake_dataset)
    (tmp_path / "file_link.json").symlink_to(fake_dataset / "tasks.jsonl")
    for dest in (tmp_path / "innocent" / "m.json", tmp_path / "innocent", tmp_path / "file_link.json"):
        with pytest.raises(common.UnsafeOutputError):
            common.guard_output(dest, fake_dataset)


def test_guard_protects_env_dataset_even_with_other_root(fake_dataset, tmp_path, monkeypatch):
    monkeypatch.setenv(common.DATASET_ENV, str(fake_dataset))
    with pytest.raises(common.UnsafeOutputError):
        common.guard_output(fake_dataset / "x.json", tmp_path / "some_other_root")


def test_guard_catches_case_alias_on_case_insensitive_fs(fake_dataset):
    alias = fake_dataset.parent / fake_dataset.name.upper() / "x.json"
    if not alias.parent.exists():
        pytest.skip("case-sensitive filesystem")
    with pytest.raises(common.UnsafeOutputError):
        common.guard_output(alias, fake_dataset)


def _cli_calls(ds: Path, target: Path):
    """Every writing CLI pointed at `target` (a file path, or the dir for --out-dir tools)."""
    d = target if target.suffix == "" else target.parent
    return [
        lambda: wheelhouse.main(["--dataset-root", str(ds), "--out", str(target)]),
        lambda: inventory_dataset.main(["--dataset-root", str(ds), "--out-dir", str(d)]),
        lambda: build_split.main(["--dataset-root", str(ds), "--out-dir", str(d)]),
        lambda: preserve_sample.main(["--dataset-root", str(ds), "--dest", str(d / "copy")]),
    ]


@pytest.mark.parametrize("target_rel", ["tasks.jsonl", "HARNESS_README.md", "new_dir/deeper/out.json", "graphs"])
def test_every_cli_refuses_dataset_targets_without_touching_it(fake_dataset, target_rel):
    before = snapshot_state(fake_dataset)
    for call in _cli_calls(fake_dataset, fake_dataset / target_rel):
        with pytest.raises(common.UnsafeOutputError):
            call()
    assert snapshot_state(fake_dataset) == before  # no file opened/truncated, no directory created


def test_cli_refuses_symlinked_out_dir(fake_dataset, tmp_path):
    (tmp_path / "looks_safe").symlink_to(fake_dataset / "graphs")
    before = snapshot_state(fake_dataset)
    for call in _cli_calls(fake_dataset, tmp_path / "looks_safe" / "m.json"):
        with pytest.raises(common.UnsafeOutputError):
            call()
    assert snapshot_state(fake_dataset) == before


def test_atomic_write_does_not_modify_hardlinked_target(tmp_path):
    a = tmp_path / "a.json"
    a.write_text("original")
    os.link(a, tmp_path / "b.json")
    common.write_text_safe(tmp_path / "b.json", "new")
    assert a.read_text() == "original" and (tmp_path / "b.json").read_text() == "new"


# ---- P2-1: canonical manifest ---------------------------------------------------------------

class _StatProxy:
    def __init__(self, st, blocks):
        self._st, self.st_blocks = st, blocks

    def __getattr__(self, name):
        return getattr(self._st, name)


def test_canonical_manifest_ignores_allocation_and_timestamps(fake_dataset, monkeypatch):
    m1, h1 = inventory_dataset.build_manifest(fake_dataset)
    rng = random.Random(0)
    real_stat, real_lstat = os.stat, os.lstat
    with monkeypatch.context() as mp:
        mp.setattr(os, "stat", lambda *a, **k: _StatProxy(real_stat(*a, **k), rng.randrange(1, 10**6)))
        mp.setattr(os, "lstat", lambda *a, **k: _StatProxy(real_lstat(*a, **k), rng.randrange(1, 10**6)))
        assert os.stat(fake_dataset / "tasks.jsonl").st_blocks != os.stat(fake_dataset / "tasks.jsonl").st_blocks
        m2, h2 = inventory_dataset.build_manifest(fake_dataset)
    for p in fake_dataset.rglob("*"):
        os.utime(p, (1, 1), follow_symlinks=False)  # different mtimes, same bytes
    m3, h3 = inventory_dataset.build_manifest(fake_dataset)
    assert common.dumps(m1) == common.dumps(m2) == common.dumps(m3)
    assert common.dumps(h1) == common.dumps(h2) == common.dumps(h3)


def test_canonical_manifest_identical_for_copied_dataset(fake_dataset, tmp_path):
    copy = tmp_path / "elsewhere" / "copy"
    shutil.copytree(fake_dataset, copy)
    assert common.dumps(inventory_dataset.build_manifest(fake_dataset)) == common.dumps(inventory_dataset.build_manifest(copy))


# ---- P2-2: symlinks -------------------------------------------------------------------------

@pytest.fixture
def outside(tmp_path):
    o = tmp_path / "outside"
    o.mkdir()
    (o / "secret.txt").write_text("EXTERNAL-BYTES")
    (o / "evil-1.0-py3-none-any.whl").write_bytes(b"EXTERNAL-BYTES")
    return o


def test_scan_tree_reports_but_never_follows_symlinks(fake_dataset, outside):
    (fake_dataset / "docker" / "file_link").symlink_to(outside / "secret.txt")
    (fake_dataset / "docker" / "dir_link").symlink_to(outside, target_is_directory=True)
    (fake_dataset / "sandbox" / "internal_link").symlink_to(fake_dataset / "tasks.jsonl")
    files, links = common.scan_tree(fake_dataset)
    assert {p.name for p in links} == {"file_link", "dir_link", "internal_link"}
    assert all(not p.is_symlink() for p in files)
    assert not any("outside" in str(p.resolve()) for p in files)
    with pytest.raises(common.SymlinkError):
        common.iter_files(fake_dataset)


def test_hashing_refuses_symlinked_file(outside, tmp_path):
    link = tmp_path / "l"
    link.symlink_to(outside / "secret.txt")
    with pytest.raises(OSError):
        common.sha256_file(link)
    with pytest.raises(OSError):
        common.partial_sha256(link)


def test_inventory_lists_symlinks_as_metadata_only(fake_dataset, outside):
    (fake_dataset / "snapshots" / "fastapi_9.tgz").symlink_to(outside / "secret.txt")
    (fake_dataset / "graphs" / "dir_link").symlink_to(outside, target_is_directory=True)
    (fake_dataset / "docker" / "internal").symlink_to(fake_dataset / "tasks.jsonl")
    (fake_dataset / "wheels" / "evil-1.0-py3-none-any.whl").symlink_to(outside / "evil-1.0-py3-none-any.whl")
    m, hashes = inventory_dataset.build_manifest(fake_dataset)
    text = common.dumps(m) + common.dumps(hashes)
    assert set(m["symlinks_and_special_files"]) == {"snapshots/fastapi_9.tgz", "graphs/dir_link", "docker/internal",
                                                    "wheels/evil-1.0-py3-none-any.whl"}
    assert "fastapi_9.tgz" not in m["snapshots"]["files"] and "docker/internal" not in hashes["files"]
    assert m["wheels"]["count"] == 2
    assert common.sha256_bytes(b"EXTERNAL-BYTES") not in text
    wm = wheelhouse.build_manifest(fake_dataset / "wheels")
    assert wm["anomalies"]["symlinks_or_special_not_read"] == ["evil-1.0-py3-none-any.whl"]
    assert "evil" not in wm["project_versions"]


def test_required_path_symlink_rejected(fake_dataset, tmp_path):
    real = tmp_path / "real_tasks.jsonl"
    (fake_dataset / "tasks.jsonl").rename(real)
    (fake_dataset / "tasks.jsonl").symlink_to(real)
    assert inventory_dataset.check_required(fake_dataset) == ["symlink not allowed: tasks.jsonl"]


@pytest.mark.parametrize("kind", ["file_out", "dir_out", "internal"])
def test_preserve_rejects_symlinks_in_source_and_copies_nothing(fake_dataset, outside, tmp_path, kind):
    src = fake_dataset / "sample_submission"
    link = src / "prompts" / "leak.md"
    if kind == "file_out":
        link.symlink_to(outside / "secret.txt")
    elif kind == "dir_out":
        link.symlink_to(outside, target_is_directory=True)
    else:
        link.symlink_to(src / "agent.yaml")
    dest = tmp_path / "copy"
    with pytest.raises(common.SymlinkError):
        preserve_sample.preserve(src, dest)
    assert not dest.exists()


def test_preserve_never_writes_through_dest_symlink(fake_dataset, outside, tmp_path):
    src, dest = fake_dataset / "sample_submission", tmp_path / "copy"
    (dest / "prompts").mkdir(parents=True)
    (dest / "prompts" / "system.md").symlink_to(outside / "victim.md")  # dangling: would create outside/victim.md
    with pytest.raises(common.SymlinkError):
        preserve_sample.preserve(src, dest)
    assert not (outside / "victim.md").exists()
    assert not (dest / "agent.yaml").exists()


def test_preserve_refuses_symlinked_dest_parent(fake_dataset, outside, tmp_path):
    src, dest = fake_dataset / "sample_submission", tmp_path / "copy"
    dest.mkdir()
    (dest / "prompts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(common.SymlinkError):
        preserve_sample.preserve(src, dest)
    assert sorted(p.name for p in outside.iterdir()) == ["evil-1.0-py3-none-any.whl", "secret.txt"]


# ---- P2-4: gitignore ------------------------------------------------------------------------

def _ignored(path: str) -> bool:
    r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "--no-index", path], capture_output=True)
    if r.returncode not in (0, 1):
        pytest.skip(f"git check-ignore unavailable: {r.stderr!r}")
    return r.returncode == 0


@pytest.mark.parametrize("path,ignored", [
    ("harness_cert/results/H01/trace.json", True),
    ("harness_cert/results/H01/SUMMARY.md", True),          # raw results are never committable, even .md
    ("harness_cert/results/git_semantics/probe_output.txt", True),
    ("harness_cert/reports/git_semantics.md", False),
    ("adapters/x/adapter_model.safetensors", True),
    ("agents/candidates/c1/adapters/a/adapter_model.safetensors", True),
    ("agents/baseline_v0_official/adapters/main_lora/adapter_model.safetensors", True),  # D003: not yet approved
    ("model.safetensors", True),
    (".env", True),
    (".env.local", True),
    ("snapshots/fastapi_1.tgz", True),
    ("graphs/x.json", True),
    ("embeddings/x.npz", True),
    ("wheels/x.whl", True),
    ("gemma-4-developer-agent/tasks.jsonl", True),
    ("artifacts/local_runs/run1/summary.json", True),
    ("eval/splits/v1.json", False),
    ("docs/competition/official_facts.md", False),
])
def test_gitignore(path, ignored):
    if not (REPO / ".git").exists():
        pytest.skip("not a git work tree")
    assert _ignored(path) is ignored
