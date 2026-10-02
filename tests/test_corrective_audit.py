"""Adversarial regression tests for the submission re-audit (2 P0 / 3 P1 / 3 P2).

Every acceptance-critical expectation here is a LITERAL taken from the local HARNESS_README contract or from the
audit; nothing is imported from the implementation's accepted enums/ranges/constants. Temp fixtures only;
$GEMMA4_DATASET_ROOT is deliberately unset for every test.
"""
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import pytest

from conftest import snapshot_state
from tools import build_submission, hash_submission, inspect_submission, package_official_control, safetensors_check
from tools import validate_submission as vs
from tools.common import UnsafeOutputError, WriteGuard, sha256_file, tree_sha256

REPO = Path(__file__).resolve().parent.parent
MODEL = "gemma-4-31b-it-qat-w4a16-ct"
NINE_TOOLS = ["run_command", "submit_patch", "get_status", "read_file", "edit_file", "write_file",
              "get_code_neighbors", "search_similar_code", "get_code_subgraph"]
SPLIT_V1_SHA = "420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12"
TOOLS_YAML = "".join(f"  - {t}\n" for t in NINE_TOOLS)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("GEMMA4_DATASET_ROOT", raising=False)


def st_bytes(entries=(("w", "F32", [2, 2]),), pad=b"") -> bytes:
    header, off = {}, 0
    for name, dtype, shape in entries:
        n = {"F32": 4, "BF16": 2}[dtype]
        for dim in shape:
            n *= dim
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [off, off + n]}
        off += n
    h = json.dumps(header).encode() + pad
    return struct.pack("<Q", len(h)) + h + b"\x00" * off


def raw_st(header: dict, payload: int) -> bytes:
    h = json.dumps(header).encode()
    return struct.pack("<Q", len(h)) + h + b"\x00" * payload


def write_tree(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    root.mkdir(parents=True, exist_ok=True)
    return root


def agent_yaml(extra: str = "", name="root") -> str:
    return f"name: {name}\nmodel: {MODEL}\ninstruction: !include prompts/system.md\ntools:\n{TOOLS_YAML}{extra}"


def bundle(tmp_path, files=None, name="b") -> Path:
    return write_tree(tmp_path / name, {"agent.yaml": agent_yaml(), "prompts/system.md": "Fix it.\n"} | (files or {}))


def errs(src) -> set[str]:
    return {i["code"] for i in vs.validate(src).to_dict()["errors"]}


# =============================== P0-1: explicit dataset root survives every delegation ===============

def official_dataset(tmp_path) -> Path:
    """A fake dataset whose sample_submission is a valid bundle with a (container-valid) adapter."""
    ds = tmp_path / "dataset"
    write_tree(ds / "sample_submission", {
        "agent.yaml": agent_yaml("adapter: main\n"), "prompts/system.md": "Official.\n",
        "adapters/main/adapter_config.json": '{"r": 4}', "adapters/main/adapter_model.safetensors": st_bytes()})
    return ds  # no tasks.jsonl/HARNESS_README: live-dataset checks are UNVERIFIABLE, not faked


def control_manifest(ds: Path, tmp_path: Path) -> Path:
    files = {p.relative_to(ds / "sample_submission").as_posix(): sha256_file(p)
             for p in sorted((ds / "sample_submission").rglob("*")) if p.is_file()}
    m = tmp_path / "control_manifest.json"
    m.write_text(json.dumps({"tree_sha256": tree_sha256(files), "files": {k: {"sha256": v} for k, v in files.items()}}))
    return m


@pytest.mark.parametrize("target", ["", "e0", "deep/nested/e0"])
def test_e0_cli_explicit_root_rejects_outputs_inside_it_with_env_unset(tmp_path, monkeypatch, target):
    ds = official_dataset(tmp_path)
    control = tmp_path / "control"  # never the real agents/baseline_v0_official, even if this test regresses
    monkeypatch.setattr(package_official_control, "CONTROL_DIR", control)
    monkeypatch.setattr(package_official_control, "CONTROL_MANIFEST", control_manifest(ds, tmp_path))
    before = snapshot_state(ds)
    rc = package_official_control.main(["--dataset-root", str(ds), "--out-dir", str(ds / target)])
    assert rc == 1
    assert snapshot_state(ds) == before
    assert not control.exists()  # refused before restoring anything


def test_e0_package_rejects_symlink_and_case_alias_into_dataset(tmp_path):
    ds = official_dataset(tmp_path)
    (tmp_path / "innocent").symlink_to(ds, target_is_directory=True)
    targets = [tmp_path / "innocent" / "e0"]
    alias = ds.parent / ds.name.upper()
    if alias.exists():  # case-insensitive filesystem
        targets.append(alias / "e0")
    before = snapshot_state(ds)
    for out in targets:
        with pytest.raises(UnsafeOutputError):
            package_official_control.package(WriteGuard(ds), out, control_dir=tmp_path / "control",
                                             control_manifest=control_manifest(ds, tmp_path))
        assert not (tmp_path / "control").exists()  # refused before restoring the control
    assert snapshot_state(ds) == before


def test_e0_delegation_positive_path_records_external_origin(tmp_path):
    ds = official_dataset(tmp_path)
    before = snapshot_state(ds)
    res = package_official_control.package(WriteGuard(ds), tmp_path / "out", control_dir=tmp_path / "control",
                                           control_manifest=control_manifest(ds, tmp_path))
    p = res["provenance"]
    assert p["source_in_git"] is False  # control dir is outside git: never claimed
    assert {o["origin"] for o in p["source_origins"].values()} == {"external-official-control"}
    assert snapshot_state(ds) == before
    r = hash_submission.verify(tmp_path / "out", dataset_root=ds)
    assert r["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE", r["mismatches"]  # fake ds has no tasks.jsonl
    assert r["git_evidence"] == "NOT_CLAIMED"
    assert all(i["status"] == "ok" for i in r["checks"] if i["check"].startswith("origin:"))


def test_builder_cli_fails_closed_without_any_dataset_root(tmp_path):
    assert build_submission.main([str(bundle(tmp_path)), "--out-dir", str(tmp_path / "o")]) == 1
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("target", ["", "x", "x/y/z"])
def test_builder_cli_explicit_root_with_env_unset(tmp_path, target):
    ds = write_tree(tmp_path / "ds", {"tasks.jsonl": "{}\n"})
    before = snapshot_state(ds)
    rc = build_submission.main([str(bundle(tmp_path)), "--dataset-root", str(ds), "--out-dir", str(ds / target)])
    assert rc == 1 and snapshot_state(ds) == before


def test_write_guard_is_immutable_and_resolved(tmp_path):
    g = WriteGuard(tmp_path)
    with pytest.raises(Exception):
        g.dataset_root = Path("/")
    assert g.with_extra(tmp_path / "x").protected[0] == tmp_path.resolve()


# =============================== P0-2: documented agent schema ======================================

@pytest.mark.parametrize("yaml_text,code", [
    (agent_yaml().replace("name: root\n", ""), "AGENT_NAME_MISSING"),
    (agent_yaml().replace("name: root\n", "name: ''\n"), "AGENT_NAME_MISSING"),
    (agent_yaml("agent_class: SwarmAgent\n"), "AGENT_CLASS_UNSUPPORTED"),
    (agent_yaml("agent_class: llmagent\n"), "AGENT_CLASS_UNSUPPORTED"),
    ("name: loop\nagent_class: LoopAgent\nmax_iterations: 0\n", "LOOP_MAX_ITERATIONS_INVALID"),
    ("name: loop\nagent_class: LoopAgent\nmax_iterations: 501\n", "LOOP_MAX_ITERATIONS_INVALID"),
    ("name: loop\nagent_class: LoopAgent\nmax_iterations: '5'\n", "LOOP_MAX_ITERATIONS_INVALID"),
    ("name: loop\nagent_class: LoopAgent\nmax_iterations: true\n", "LOOP_MAX_ITERATIONS_INVALID"),
    (agent_yaml("include_contents: all\n"), "INCLUDE_CONTENTS_INVALID"),
    (agent_yaml("description: [a, b]\n"), "AGENT_FIELD_TYPE"),
    (agent_yaml("disallow_transfer_to_parent: 'yes'\n"), "AGENT_FIELD_TYPE"),
    (agent_yaml().replace("tools:\n" + TOOLS_YAML, "tools: run_command\n"), "TOOLS_INVALID"),
    (agent_yaml("sub_agents: {config_path: x.yaml}\n"), "SUB_AGENTS_INVALID"),
    (agent_yaml("sub_agents:\n  - 42\n"), "SUB_AGENTS_INVALID"),
    (agent_yaml("  - 7\n"), "TOOL_ENTRY_INVALID"),
    ("- just\n- a list\n", "AGENT_CONFIG_INVALID"),
])
def test_documented_agent_schema(tmp_path, yaml_text, code):
    assert code in errs(bundle(tmp_path, {"agent.yaml": yaml_text}))


@pytest.mark.parametrize("yaml_text", [
    "name: loop\nagent_class: LoopAgent\nmax_iterations: 1\n",
    "name: loop\nagent_class: LoopAgent\nmax_iterations: 500\n",
    "name: loop\nagent_class: LoopAgent\n",  # documented default 500
    f"name: seq\nagent_class: SequentialAgent\nsub_agents:\n  - name: a\n    model: {MODEL}\n",
    f"name: par\nagent_class: ParallelAgent\nsub_agents:\n  - name: a\n    model: {MODEL}\n",
    agent_yaml("include_contents: none\noutput_key: out\ndisallow_transfer_to_peers: true\n"),
])
def test_documented_schema_accepts_valid_shapes(tmp_path, yaml_text):
    assert errs(bundle(tmp_path, {"agent.yaml": yaml_text})) == set()


@pytest.mark.parametrize("gen,ok", [
    ("temperature: -0.1", False), ("temperature: 0", True), ("temperature: 2.5", True),
    ("top_p: -0.01", False), ("top_p: 1.01", False), ("top_p: 0", True), ("top_p: 1", True),
    ("top_k: 0", False), ("top_k: 1", True),
    ("max_output_tokens: 0", False), ("max_output_tokens: 32769", False), ("max_output_tokens: 1", True),
    ("max_output_tokens: 32768", True), ("max_output_tokens: 1.5", False),
    ("thinking_config: {thinking_budget: -1}", False), ("thinking_config: {thinking_budget: 32769}", False),
    ("thinking_config: {thinking_budget: 0}", True), ("thinking_config: {thinking_budget: 32768}", True),
    ("thinking_config: {include_thoughts: 'yes'}", False), ("thinking_config: {include_thoughts: false}", True),
    ("thinking_config: {thinking_level: ULTRA}", False), ("thinking_config: {thinking_level: high}", True),
    ("thinking_config: {thinking_level: NONE}", True), ("thinking_config: {thinking_level: Minimal}", True),
    ("presence_penalty: high", False), ("frequency_penalty: -0.5", True), ("seed: abc", False), ("seed: 7", True),
    ("stop_sequences: stop", False), ("stop_sequences: [a, b]", True), ("response_mime_type: 3", False),
    ("tools: []", False), ("system_instruction: x", False), ("http_options: {}", False),
    ("safety_settings: []", False), ("response_schema: {}", False),
])
def test_documented_generation_config(tmp_path, gen, ok):
    e = errs(bundle(tmp_path, {"agent.yaml": agent_yaml("generate_content_config:\n  " + gen + "\n")}))
    assert (not e) is ok, e


def test_structural_limits_agents_depth_instruction(tmp_path):
    many = "".join(f"  - name: a{i}\n    model: {MODEL}\n" for i in range(500))  # 500 + root = 501
    assert "TOO_MANY_AGENTS" in errs(bundle(tmp_path, {"agent.yaml": f"name: r\nagent_class: SequentialAgent\nsub_agents:\n{many}"}, "many"))
    ok = "".join(f"  - name: a{i}\n    model: {MODEL}\n" for i in range(499))  # exactly 500
    assert "TOO_MANY_AGENTS" not in errs(bundle(tmp_path, {"agent.yaml": f"name: r\nagent_class: SequentialAgent\nsub_agents:\n{ok}"}, "ok500"))
    nested = cur = {"name": "d0", "agent_class": "SequentialAgent"}
    for i in range(1, 52):  # depth 51
        nxt = {"name": f"d{i}", "agent_class": "SequentialAgent"}
        cur["sub_agents"] = [nxt]
        cur = nxt
    assert "AGENT_DEPTH_EXCEEDED" in errs(bundle(tmp_path, {"agent.yaml": json.dumps(nested)}, "deep"))
    big = bundle(tmp_path, {"prompts/system.md": "x" * 1_000_001}, "long")
    assert "INSTRUCTION_TOO_LONG" in errs(big)
    assert "INSTRUCTION_TOO_LONG" not in errs(bundle(tmp_path, {"prompts/system.md": "x" * 1_000_000}, "edge"))


def test_agent_reference_cycle(tmp_path):
    sub = f"name: s\nmodel: {MODEL}\ntools:\n  - agent_tool:\n      config_path: s.yaml\n"
    src = bundle(tmp_path, {"agent.yaml": agent_yaml("  - agent_tool:\n      config_path: sub_agents/s.yaml\n"),
                            "sub_agents/s.yaml": sub})
    assert "AGENT_REFERENCE_CYCLE" in errs(src)


# =============================== P1-1: --require-clean proves git membership ========================

def git(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    git(r, "config", "commit.gpgsign", "false")
    (r / "README").write_text("r\n")
    git(r, "add", "README")
    git(r, "commit", "-qm", "init")
    return r


def cand(repo: Path, commit=True) -> Path:
    c = write_tree(repo / "agents" / "c1", {"agent.yaml": agent_yaml(), "prompts/system.md": "Fix it.\n"})
    if commit:
        git(repo, "add", "agents")
        git(repo, "commit", "-qm", "cand")
    return c


def rbuild(src, tmp_path, **kw):
    ds = tmp_path / "ds_guard"
    ds.mkdir(exist_ok=True)
    return build_submission.build(src, guard=WriteGuard(ds), candidate_id="c1", out_dir=tmp_path / "out", **kw)


def test_require_clean_passes_only_for_committed_identical_bytes(repo, tmp_path):
    src = cand(repo)
    res = rbuild(src, tmp_path, require_clean=True)
    p = res["provenance"]
    assert p["source_in_git"] is True and p["git_dirty"] is False
    assert {o["origin"] for o in p["source_origins"].values()} == {"git-tracked"}
    r = hash_submission.verify(tmp_path / "out", git_repo=repo)
    assert r["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE" and r["git_evidence"] == "VERIFIED"
    assert all(i["status"] == "ok" for i in r["checks"] if i["check"].startswith("origin:"))


@pytest.mark.parametrize("scenario,expected_status", [
    ("ignored_only", "ignored"),
    ("untracked", "untracked"),
    ("staged_new", "staged-not-in-HEAD"),
    ("staged_differs", "staged-differs"),
    ("modified", "modified"),
])
def test_require_clean_rejects_unproven_sources(repo, tmp_path, scenario, expected_status):
    if scenario == "ignored_only":
        (repo / ".gitignore").write_text("agents/\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-qm", "ignore")
        src = cand(repo, commit=False)
    elif scenario == "untracked":
        src = cand(repo, commit=False)
    elif scenario == "staged_new":
        src = cand(repo, commit=False)
        git(repo, "add", "agents")
    else:
        src = cand(repo)
        (src / "prompts/system.md").write_text("Changed.\n")
        if scenario == "staged_differs":
            git(repo, "add", "agents")
    with pytest.raises(build_submission.BuildError):
        rbuild(src, tmp_path, require_clean=True)
    assert not (tmp_path / "out").exists()
    res = rbuild(src, tmp_path)  # without the flag the build is allowed but must be truthful
    p = res["provenance"]
    assert p["source_in_git"] is False
    assert p["source_origins"]["prompts/system.md"]["git_status"] == expected_status
    assert p["source_origins"]["prompts/system.md"]["origin"] == "unproven"


def test_require_clean_rejects_unrelated_dirty_repo(repo, tmp_path):
    src = cand(repo)
    (repo / "scratch.txt").write_text("dirty\n")
    with pytest.raises(build_submission.BuildError):
        rbuild(src, tmp_path, require_clean=True)
    assert rbuild(src, tmp_path)["provenance"]["source_in_git"] is True  # files are proven; repo was dirty


# =============================== P1-2: verifier recomputes facts ====================================

@pytest.fixture
def built(tmp_path):
    src = bundle(tmp_path, {"agent.yaml": agent_yaml("adapter: m\n"), "adapters/m/adapter_config.json": "{}",
                            "adapters/m/adapter_model.safetensors": st_bytes()})
    ds = tmp_path / "ds_guard"
    ds.mkdir()
    build_submission.build(src, guard=WriteGuard(ds), candidate_id="v", out_dir=tmp_path / "out")
    assert hash_submission.verify(tmp_path / "out")["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE"
    return tmp_path / "out"


def tamper(out: Path, fn, file="provenance.json"):
    d = json.loads((out / file).read_text())
    fn(d)
    (out / file).write_text(json.dumps(d))


@pytest.mark.parametrize("name,fn", [
    ("negative unpacked", lambda p: p.update(unpacked_bytes=-1)),
    ("fake adapter hash", lambda p: p["adapter_hashes"].update(ghost={"adapters/ghost.safetensors": "0" * 64})),
    ("contradictory lora flag", lambda p: p.update(lora_present=False)),
    ("structural invalid", lambda p: p["validation"].update(structural="STRUCTURAL_INVALID")),
    ("harness claimed compatible", lambda p: p["validation"].update(harness_compatibility="COMPATIBLE")),
    ("source files edited", lambda p: p["source_files"].update({"agent.yaml": "f" * 64})),
    ("tree hash edited", lambda p: p.update(source_tree_sha256="0" * 64)),
    ("split hash edited", lambda p: p["environment"].update(split_sha256="0" * 64)),
    ("wheelhouse edited", lambda p: p["environment"].update(wheelhouse_fingerprint_sha256="1" * 64)),
    ("harness versions invented", lambda p: p["environment"].update(harness_versions={"swegemma": "9.9"})),
    ("source_in_git lie", lambda p: p.update(source_in_git=True)),
    ("git-tracked origin without commit", lambda p: p["source_origins"]["agent.yaml"].update(origin="git-tracked")),
    ("zip size edited", lambda p: p.update(submission_zip_bytes=1)),
    ("origins missing", lambda p: p.pop("source_origins")),
])
def test_verifier_rejects_contradictory_provenance(built, name, fn):
    tamper(built, fn)
    assert hash_submission.verify(built)["status"] == "FAILED", name


def test_verifier_rejects_structural_invalid_claim_everywhere(built):
    tamper(built, lambda m: m["validation"].update(structural="STRUCTURAL_INVALID"), "manifest.json")
    assert hash_submission.verify(built)["status"] == "FAILED"


def test_verifier_informational_fields_do_not_fail(built):
    tamper(built, lambda p: p.update(build_time_utc="1999-01-01T00:00:00+00:00", experiment_id="EXP-X"))
    r = hash_submission.verify(built)
    assert r["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE"
    info = {i["check"]: i["status"] for i in r["checks"] if i["class"] == "informational"}
    assert info["build_time_utc"] == info["experiment_id"] == "informational"  # reported, never "ok"


def test_verifier_reports_unverifiable_without_failing(built):
    r = hash_submission.verify(built)  # no dataset available: live checks are UNVERIFIABLE, not failures
    assert r["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE" and not r["mismatches"]
    assert {i["check"] for i in r["unverifiable"]} >= {"live_dataset.tasks_jsonl_sha256"}


def test_verifier_rejects_inconsistent_sums_and_garbage(built):
    (built / "SHA256SUMS").write_text("0" * 64 + "  submission.zip\n")
    assert hash_submission.verify(built)["status"] == "FAILED"
    (built / "provenance.json").write_text("[]")
    assert hash_submission.verify(built)["status"] == "FAILED"


# =============================== P1-3: split hashed directly ========================================

def meta_copy(tmp_path) -> Path:
    m = tmp_path / "meta"
    shutil.copytree(REPO / "vendor_meta", m / "vendor_meta")
    shutil.copytree(REPO / "eval" / "splits", m / "eval" / "splits")
    return m


def test_real_split_hash_is_direct_and_frozen():
    env = hash_submission.environment_fingerprints()
    assert env["split_sha256"] == SPLIT_V1_SHA == sha256_file(REPO / "eval/splits/v1.json")


@pytest.mark.parametrize("sidecar", ["0" * 64 + "  v1.json\n", "deadbeef  v1.json\n", ""])
def test_stale_split_sidecar_fails_generation_and_verification(tmp_path, built, sidecar):
    meta = meta_copy(tmp_path)
    (meta / "eval/splits/v1.sha256").write_text(sidecar)
    with pytest.raises(hash_submission.ProvenanceError):
        hash_submission.environment_fingerprints(meta)
    src = bundle(tmp_path, name="stale")
    ds = tmp_path / "ds_guard"
    with pytest.raises(hash_submission.ProvenanceError):
        build_submission.build(src, guard=WriteGuard(ds), out_dir=tmp_path / "never", meta_root=meta)
    assert not (tmp_path / "never").exists()
    r = hash_submission.verify(built, meta_root=meta)
    assert r["status"] == "FAILED" and any(i["check"] == "environment.split_sidecar" for i in r["mismatches"])


def test_sidecar_is_not_the_authority(tmp_path):
    meta = meta_copy(tmp_path)
    (meta / "eval/splits/v1.sha256").unlink()
    assert hash_submission.environment_fingerprints(meta)["split_sha256"] == SPLIT_V1_SHA
    (meta / "eval/splits/v1.json").write_text("{}\n")  # different split, no sidecar -> its own hash, not v1's
    assert hash_submission.environment_fingerprints(meta)["split_sha256"] != SPLIT_V1_SHA


# =============================== P2-1: malformed YAML is structured =================================

@pytest.mark.parametrize("yaml_text,code", [
    (agent_yaml("  - agent_tool: oops\n"), "AGENT_TOOL_INVALID"),
    (agent_yaml("  - agent_tool: [1, 2]\n"), "AGENT_TOOL_INVALID"),
    (agent_yaml("  - agent_tool: {skip_summarization: true}\n"), "AGENT_TOOL_INVALID"),
    ("name: &a [*a]\n", "YAML_RECURSIVE_ALIAS"),
    ("a: &a {b: *a}\nname: x\n", "YAML_RECURSIVE_ALIAS"),
    ("? [a, b]\n: c\n", "YAML_INVALID"),
    ("x: !!python/object:os.system {}\n", "YAML_INVALID"),
    (b"\xff\xfe\x00 not utf-8", "YAML_INVALID"),
])
def test_malformed_yaml_is_structured(tmp_path, yaml_text, code):
    src = bundle(tmp_path, {"agent.yaml": yaml_text})
    r = vs.validate(src).to_dict()
    assert r["structural"] == "STRUCTURAL_INVALID" and code in {i["code"] for i in r["errors"]}


def test_alias_bomb_is_bounded(tmp_path):
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 9):
        lines.append(f"a{i}: &a{i} [" + ", ".join([f"*a{i-1}"] * 10) + "]")
    src = bundle(tmp_path, {"configs/bomb.yaml": "\n".join(lines) + "\n"})
    assert "YAML_EXPANSION_LIMIT" in errs(src)


@pytest.mark.parametrize("yaml_text", [agent_yaml("  - agent_tool: oops\n"), "name: &a [*a]\n"])
def test_validator_cli_never_tracebacks(tmp_path, yaml_text):
    src = bundle(tmp_path, {"agent.yaml": yaml_text})
    r = subprocess.run([sys.executable, "-m", "tools.validate_submission", str(src), "--json"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 1 and "Traceback" not in r.stderr
    assert json.loads(r.stdout)["structural"] == "STRUCTURAL_INVALID"


# =============================== P2-2: archive metadata ============================================

def raw_zip(path: Path, entries, comment=b"") -> Path:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data, kw in entries:
                zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                zi.create_system, zi.external_attr = 3, 0o100644 << 16
                for k, v in kw.items():
                    setattr(zi, k, v)
                zf.writestr(zi, data)
            zf.comment = comment
    return path


GOOD = [("agent.yaml", agent_yaml().encode(), {}), ("prompts/system.md", b"Fix it.\n", {})]


@pytest.mark.parametrize("entries,comment,code,kind", [
    (GOOD + [("prompts/./system.md", b"x", {})], b"", "DUPLICATE_PATH_NORMALIZED", "errors"),
    (GOOD + [("prompts//extra.md", b"x", {})], b"", "NONCANONICAL_PATH", "errors"),
    (GOOD + [("Agent.yaml", b"x", {})], b"", "DUPLICATE_PATH_NORMALIZED", "errors"),
    ([GOOD[0], (unicodedata.normalize("NFC", "prompts/é.md"), b"x", {}), (unicodedata.normalize("NFD", "prompts/é.md"), b"y", {})],
     b"", "DUPLICATE_PATH_NORMALIZED", "errors"),
    (GOOD + [("prompts", b"x", {})], b"", "PATH_CONFLICT", "errors"),
    (GOOD, b"hello", "ARCHIVE_COMMENT", "policy_violations"),
    ([GOOD[0], ("prompts/system.md", b"Fix it.\n", {"comment": b"note"})], b"", "MEMBER_COMMENT", "policy_violations"),
    ([GOOD[0], ("prompts/system.md", b"Fix it.\n", {"extra": b"\xfe\xca\x00\x00"})], b"", "UNEXPECTED_EXTRA_FIELD", "policy_violations"),
])
def test_inspector_archive_metadata(tmp_path, monkeypatch, entries, comment, code, kind):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    r = inspect_submission.inspect(raw_zip(tmp_path / "a.zip", entries, comment))
    assert code in {i["code"] for i in r[kind]} and r["ok"] is False
    assert list(scratch.iterdir()) == []
    if kind == "errors":
        assert "validation" not in r  # rejected before extraction


def test_inspector_garbage_is_structured(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"not a zip at all")
    r = inspect_submission.inspect(p)
    assert r["ok"] is False and {i["code"] for i in r["errors"]} == {"BAD_ZIP"}


def test_builder_output_has_no_comments_or_extras(tmp_path):
    src = bundle(tmp_path)
    ds = tmp_path / "ds_guard"
    ds.mkdir()
    build_submission.build(src, guard=WriteGuard(ds), out_dir=tmp_path / "o")
    raw = (tmp_path / "o/submission.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "o/submission.zip") as zf:
        assert zf.comment == b"" and all(i.comment == b"" and i.extra == b"" for i in zf.infolist())
        for i in zf.infolist():  # local header extra length (offset 28) must be zero too
            assert struct.unpack("<H", raw[i.header_offset + 28:i.header_offset + 30])[0] == 0


# =============================== P2-3: safetensors container =======================================

@pytest.mark.parametrize("data,valid", [
    (b"not-safetensors", False),
    (b"", False),
    (struct.pack("<Q", 10_000) + b"{}", False),                        # header longer than file
    (struct.pack("<Q", 4) + b"nope", False),                           # not JSON
    (struct.pack("<Q", 2) + b"[]", False),                             # not an object
    (st_bytes()[:-1], False),                                          # payload truncated -> offsets out of bounds
    (raw_st({"a": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]},
             "b": {"dtype": "F32", "shape": [2], "data_offsets": [4, 12]}}, 12), False),   # overlapping tensors
    (raw_st({"w": {"dtype": "F32", "shape": [2, 2], "data_offsets": [0, 8]}}, 8), False),  # bytes != shape x dtype
    (raw_st({"w": {"dtype": "F32", "shape": [2], "data_offsets": [8, 0]}}, 8), False),     # begin > end
    (raw_st({"w": {"dtype": "F32", "shape": [-1], "data_offsets": [0, 0]}}, 0), False),    # negative dim
    (st_bytes(), True),
    (st_bytes(pad=b"   "), True),                                     # spec allows trailing header whitespace
    (st_bytes((("lora_A", "BF16", [4, 8]), ("lora_B", "BF16", [8, 4]))), True),
])
def test_safetensors_container(tmp_path, data, valid):
    p = tmp_path / "adapter_model.safetensors"
    p.write_bytes(data)
    assert (safetensors_check.check(p) == []) is valid


def test_fake_safetensors_rejected_by_validator(tmp_path):
    src = bundle(tmp_path, {"agent.yaml": agent_yaml("adapter: m\n"), "adapters/m/adapter_config.json": "{}",
                            "adapters/m/adapter_model.safetensors": b"not-safetensors"})
    assert "SAFETENSORS_CONTAINER_INVALID" in errs(src)


def test_official_e0_adapters_are_container_valid():
    adapters = sorted((REPO / "agents/baseline_v0_official/adapters").glob("*/adapter_model.safetensors"))
    if not adapters:
        pytest.skip("official adapters not restored (gitignored); run tools.preserve_sample")
    assert [safetensors_check.check(p) for p in adapters] == [[], []]
