"""Submission pipeline: validator, deterministic builder, ZIP inspector, provenance. Temp fixtures only."""
import json
import os
import stat
import tempfile
import warnings
import zipfile
from pathlib import Path

import pytest

from conftest import snapshot_state
from tools import build_submission, common, hash_submission, inspect_submission
from tools import validate_submission as vs
from tools.common import WriteGuard

REPO = Path(__file__).resolve().parent.parent
# Literal expectations from HARNESS_README, deliberately NOT imported from the implementation.
BUILTIN_TOOLS = ("run_command", "submit_patch", "get_status", "read_file", "edit_file", "write_file",
                 "get_code_neighbors", "search_similar_code", "get_code_subgraph")
TOOLS = "\n".join(f"  - {t}" for t in BUILTIN_TOOLS)
MODEL = "gemma-4-31b-it-qat-w4a16-ct"
AGENT = f"name: a\nmodel: {MODEL}\ninstruction: !include prompts/system.md\ntools:\n{TOOLS}\n"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("GEMMA4_DATASET_ROOT", raising=False)


def bundle(tmp_path, files: dict[str, str | bytes] | None = None, base: bool = True, name="cand") -> Path:
    root = tmp_path / name
    content = ({"agent.yaml": AGENT, "prompts/system.md": "Fix the bug.\n"} if base else {}) | (files or {})
    for rel, data in content.items():
        if data is None:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    root.mkdir(parents=True, exist_ok=True)
    return root


def codes(report, kind="errors"):
    return {i["code"] for i in report.to_dict()[kind]}


def guard_for(src: Path) -> WriteGuard:
    ds = src.parent / "_protected_ds"
    ds.mkdir(exist_ok=True)
    return WriteGuard(ds)


def build(src, out, **kw):
    return build_submission.build(src, guard=guard_for(src), candidate_id="cand", out_dir=out, **kw)


def safetensors_bytes(tensors=(("w", "F32", [2, 2]),)) -> bytes:
    """Independent minimal safetensors writer (spec: u64 LE header length + JSON header + buffer)."""
    import struct
    sizes = {"F32": 4, "BF16": 2, "F16": 2}
    header, off = {}, 0
    for name, dtype, shape in tensors:
        n = sizes[dtype]
        for d in shape:
            n *= d
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [off, off + n]}
        off += n
    h = json.dumps(header).encode()
    return struct.pack("<Q", len(h)) + h + b"\0" * off


# ---- deterministic builder ---------------------------------------------------------------------

def test_two_builds_are_byte_identical(tmp_path):
    src = bundle(tmp_path, {"configs/sampling.yaml": "temperature: 0.2\n", "eval_config.yaml": "evaluation:\n  max_turns: 5\n"})
    a, b = build(src, tmp_path / "a"), build(src, tmp_path / "b")
    assert (tmp_path / "a/submission.zip").read_bytes() == (tmp_path / "b/submission.zip").read_bytes()
    assert a["zip_sha256"] == b["zip_sha256"] == common.sha256_file(tmp_path / "b/submission.zip")
    assert (tmp_path / "a/manifest.json").read_bytes() == (tmp_path / "b/manifest.json").read_bytes()
    assert (tmp_path / "a/SHA256SUMS").read_bytes() == (tmp_path / "b/SHA256SUMS").read_bytes()


def test_zip_metadata_policy(tmp_path):
    src = bundle(tmp_path, {"sub_agents/z.yaml": f"name: z\nmodel: {MODEL}\ntools:\n{TOOLS}\n",
                            "configs/a.yaml": "x: 1\n", "eval_config.yaml": "evaluation: {}\n"})
    os.chmod(src / "agent.yaml", 0o755)  # source mode must not leak into the archive
    os.utime(src / "agent.yaml", (2_000_000_000, 2_000_000_000))
    build(src, tmp_path / "o")
    with zipfile.ZipFile(tmp_path / "o/submission.zip") as zf:
        infos = zf.infolist()
    names = [i.filename for i in infos]
    assert names == sorted(names)
    assert "agent.yaml" in names and not any(i.is_dir() for i in infos)  # root config at root, no wrapper/dirs
    assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in infos)
    assert all(i.external_attr >> 16 == 0o100644 and i.create_system == 3 for i in infos)
    assert all(i.compress_type == zipfile.ZIP_STORED and i.extra == b"" for i in infos)


def test_build_never_modifies_source_and_excludes_junk(tmp_path):
    src = bundle(tmp_path, {".DS_Store": b"x", "prompts/.DS_Store": b"x", "__pycache__/m.cpython-313.pyc": b"x",
                            ".pytest_cache/v": "x", "README.md": "notes\n", "notes.txt": "n\n", "prompts/old.md~": "x"})
    before = snapshot_state(src)
    res = build(src, tmp_path / "o")
    assert snapshot_state(src) == before
    with zipfile.ZipFile(tmp_path / "o/submission.zip") as zf:
        assert zf.namelist() == ["agent.yaml", "prompts/system.md"]
    w = {(i["code"], i["path"]) for i in res["manifest"]["validation"]["warnings"]}
    assert ("NOT_PACKAGED", "README.md") in w and ("JUNK_EXCLUDED", "__pycache__") in w


def test_referenced_file_outside_contract_dirs_is_packaged(tmp_path):
    src = bundle(tmp_path, {"agent.yaml": AGENT.replace("prompts/system.md", "instructions.txt"), "instructions.txt": "hi\n",
                            "prompts/system.md": None})
    r = vs.validate(src)
    assert r.structural_valid and "instructions.txt" in r.packaged_files


@pytest.mark.parametrize("files,code", [
    ({".env": "K=1"}, "SECRET_FILE"),
    ({"configs/kaggle.json": "{}"}, "SECRET_FILE"),
    ({"snapshots/x.tgz": b"x"}, "DATASET_ARTIFACT"),
    ({"prompts/tasks.jsonl": "{}"}, "DATASET_ARTIFACT"),
])
def test_secrets_and_dataset_artifacts_block_build(tmp_path, files, code):
    src = bundle(tmp_path, files)
    assert code in codes(vs.validate(src))
    with pytest.raises(build_submission.BuildError):
        build(src, tmp_path / "o")
    assert not (tmp_path / "o").exists()  # nothing written on validation failure


# ---- validator: structure ----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["agent.yaml", "agent.yml", "root_agent.yaml", "root_agent.yml"])
def test_root_config_detection(tmp_path, name):
    src = bundle(tmp_path, {"agent.yaml": None, name: AGENT})
    r = vs.validate(src)
    assert r.structural_valid and r.root_config == name


def test_root_config_missing_and_duplicate(tmp_path):
    assert "ROOT_CONFIG_MISSING" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": None}, name="none")))
    assert "ROOT_CONFIG_MULTIPLE" in codes(vs.validate(bundle(tmp_path, {"root_agent.yml": AGENT}, name="two")))


def test_source_not_dir(tmp_path):
    assert codes(vs.validate(tmp_path / "missing")) == {"SOURCE_NOT_DIR"}
    (tmp_path / "f").write_text("x")
    assert codes(vs.validate(tmp_path / "f")) == {"SOURCE_NOT_DIR"}


@pytest.mark.parametrize("text", ["name: [unclosed\n", "name: a\nname: b\n"])
def test_invalid_yaml_and_duplicate_keys(tmp_path, text):
    assert "YAML_INVALID" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": text})))


@pytest.mark.parametrize("files,code", [
    ({"prompts/run.sh": "x"}, "UNSUPPORTED_EXTENSION"),
    ({"configs/noext": "x"}, "UNSUPPORTED_EXTENSION"),
    ({"configs/helper.py": "x = 1\n"}, "PYTHON_OUTSIDE_SKILLS"),
    ({"prompts/w.safetensors": b"x"}, "SAFETENSORS_OUTSIDE_ADAPTERS"),
])
def test_unsupported_files(tmp_path, files, code):
    assert code in codes(vs.validate(bundle(tmp_path, files)))


def test_skill_python_allowed(tmp_path):
    agent = AGENT + "skills:\n  - skills/nav\n"
    r = vs.validate(bundle(tmp_path, {"agent.yaml": agent, "skills/nav/SKILL.md": "# nav\n", "skills/nav/run.py": "print(1)\n"}))
    assert r.structural_valid, r.to_dict()["errors"]
    assert "SKILL_MISSING" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": AGENT + "skills:\n  - skills/none\n"}, name="b")))


def test_symlinks_rejected_and_not_built(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("EXTERNAL")
    src = bundle(tmp_path)
    (src / "prompts" / "extra.md").symlink_to(outside)
    (src / "configs").symlink_to(tmp_path, target_is_directory=True)
    assert codes(vs.validate(src)) >= {"SYMLINK"}
    with pytest.raises(build_submission.BuildError):
        build(src, tmp_path / "o")
    assert not (tmp_path / "o").exists()


# ---- validator: includes / references ----------------------------------------------------------

def test_absolute_include(tmp_path):
    src = bundle(tmp_path, {"agent.yaml": AGENT.replace("prompts/system.md", "/etc/passwd")})
    assert "INCLUDE_ABSOLUTE" in codes(vs.validate(src))


def test_missing_include_and_config_path(tmp_path):
    assert "INCLUDE_MISSING" in codes(vs.validate(bundle(tmp_path, {"prompts/system.md": None})))
    agent = AGENT + "  - agent_tool:\n      config_path: sub_agents/nope.yaml\n"
    assert "CONFIG_PATH_MISSING" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": agent}, name="b")))


def test_parent_relative_include_is_certification_issue_not_error(tmp_path):
    sub = f"name: s\nmodel: {MODEL}\ninstruction: !include ../prompts/system.md\ntools:\n{TOOLS}\n"
    agent = AGENT + "  - agent_tool:\n      config_path: sub_agents/s.yaml\n      skip_summarization: true\n"
    r = vs.validate(bundle(tmp_path, {"agent.yaml": agent, "sub_agents/s.yaml": sub}))
    assert r.structural_valid
    cert = codes(r, "certification_issues")
    assert {"H26_UNRESOLVED_PARENT_INCLUDE", "H20_AGENT_TOOL_UNCERTIFIED"} <= cert
    assert all(i["h_id"] for i in r.to_dict()["certification_issues"])
    assert r.to_dict()["harness_compatibility"] == "CURRENT_HARNESS_COMPATIBILITY_UNKNOWN"


def test_parent_include_escaping_bundle_is_error(tmp_path):
    src = bundle(tmp_path, {"agent.yaml": AGENT.replace("prompts/system.md", "../outside.md")})
    (tmp_path / "outside.md").write_text("x")
    r = vs.validate(src)
    assert "INCLUDE_ESCAPES_ROOT" in codes(r) and "H26_UNRESOLVED_PARENT_INCLUDE" in codes(r, "certification_issues")


def test_nested_include_base_is_flagged(tmp_path):
    sub = f"name: s\nmodel: {MODEL}\ninstruction: !include prompts/system.md\ntools:\n{TOOLS}\n"
    agent = AGENT + "  - agent_tool:\n      config_path: sub_agents/s.yaml\n"
    r = vs.validate(bundle(tmp_path, {"agent.yaml": agent, "sub_agents/s.yaml": sub}))
    assert r.structural_valid and "H26_REFERENCE_BASE_UNCERTIFIED" in codes(r, "certification_issues")


def test_include_cycle(tmp_path):
    agent = AGENT + "generate_content_config: !include configs/a.yaml\n"
    src = bundle(tmp_path, {"agent.yaml": agent, "configs/a.yaml": "x: !include b.yaml\n", "configs/b.yaml": "y: !include a.yaml\n"})
    assert "INCLUDE_CYCLE" in codes(vs.validate(src))


# ---- validator: models / adapters / generation ----------------------------------------------

def test_model_rules(tmp_path):
    assert "MODEL_NOT_ALLOWED" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": AGENT.replace(MODEL, "gemma-3-27b-it")}, name="w")))
    assert vs.validate(bundle(tmp_path, {"agent.yaml": AGENT.replace(MODEL, f"openai/{MODEL}")}, name="p")).structural_valid
    assert "MODEL_NOT_DECLARED" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": AGENT.replace(f"model: {MODEL}\n", "")}, name="n")))


def test_mixed_models_rejected(tmp_path):
    sub = f"name: s\nmodel: gemma-4-12b-it\ntools:\n{TOOLS}\n"
    agent = AGENT + "sub_agents:\n  - config_path: sub_agents/s.yaml\n"
    c = codes(vs.validate(bundle(tmp_path, {"agent.yaml": agent, "sub_agents/s.yaml": sub})))
    assert {"MULTIPLE_BASE_MODELS", "MODEL_NOT_ALLOWED"} <= c
    standalone = codes(vs.validate(bundle(tmp_path, {"configs/other.yaml": "model: hosted_vllm/gemma-4-9b-it\n"}, name="s2")))
    assert "MULTIPLE_BASE_MODELS" in standalone


def test_adapters(tmp_path):
    with_adapter = AGENT + "adapter: main\n"
    assert "ADAPTER_MISSING" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": with_adapter}, name="m")))
    ok = vs.validate(bundle(tmp_path, {"agent.yaml": with_adapter, "adapters/main/adapter_config.json": '{"r": 4}',
                                       "adapters/main/adapter_model.safetensors": safetensors_bytes()}, name="ok"))
    assert ok.structural_valid and "LORA_DEFERRED" in codes(ok, "certification_issues")
    bad = vs.validate(bundle(tmp_path, {"adapters/x/adapter_config.json": "not json", "adapters/x/notes.md": "x"}, name="bad"))
    assert {"ADAPTER_CONFIG_INVALID", "ADAPTER_INCOMPLETE", "ADAPTER_UNEXPECTED_FILE"} <= codes(bad)
    assert "ADAPTER_UNREFERENCED" in codes(bad, "warnings")


def test_generation_and_tools(tmp_path):
    agent = AGENT + "generate_content_config:\n  tools: []\n  max_output_tokens: 99999\n  thinking_config:\n    thinking_budget: -1\n"
    r = vs.validate(bundle(tmp_path, {"agent.yaml": agent}))
    assert {"GENERATION_FIELD_FORBIDDEN", "GENERATION_VALUE_INVALID"} <= codes(r)
    assert "H02_H03_THINKING_CONFIG_UNCERTIFIED" in codes(r, "certification_issues")
    assert "UNKNOWN_TOOL" in codes(vs.validate(bundle(tmp_path, {"agent.yaml": AGENT + "  - bash\n"}, name="t")))
    subset = vs.validate(bundle(tmp_path, {"agent.yaml": AGENT.replace("  - search_similar_code\n", "")}, name="s"))
    assert subset.structural_valid and "H04_H30_TOOL_SUBSET" in codes(subset, "certification_issues")


def test_oversize_with_mocked_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "MAX_TOTAL_BYTES", 50)
    src = bundle(tmp_path)
    assert "SIZE_LIMIT" in codes(vs.validate(src))
    with pytest.raises(build_submission.BuildError):
        build(src, tmp_path / "o")


def test_template_cannot_be_packaged(tmp_path):
    r = vs.validate(REPO / "agents" / "candidates" / "_template")
    assert "PLACEHOLDER_PRESENT" in codes(r) and "README.md" in r.excluded_files
    with pytest.raises(build_submission.BuildError):
        build(REPO / "agents" / "candidates" / "_template", tmp_path / "o")


def test_validator_cli_exit_codes_and_json(tmp_path, capsys):
    assert vs.main([str(bundle(tmp_path)), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["structural"] == "STRUCTURAL_VALID" and d["harness_compatibility"] == "CURRENT_HARNESS_COMPATIBILITY_UNKNOWN"
    assert vs.main([str(tmp_path / "missing")]) == 1
    assert str(tmp_path) not in json.dumps(d)  # no absolute paths in reports


# ---- output guard ------------------------------------------------------------------------------

def test_builder_refuses_dataset_and_source_outputs(tmp_path, fake_dataset):
    src = bundle(tmp_path)  # GEMMA4_DATASET_ROOT is unset (autouse fixture): protection comes from the explicit guard
    before = snapshot_state(fake_dataset)
    for out in (fake_dataset / "subs", fake_dataset, src / "out", src):
        with pytest.raises(common.UnsafeOutputError):
            build_submission.build(src, guard=WriteGuard(fake_dataset), out_dir=out)
    assert snapshot_state(fake_dataset) == before
    assert not (src / "out").exists()


def test_build_requires_explicit_guard(tmp_path):
    with pytest.raises(TypeError):
        build_submission.build(bundle(tmp_path), out_dir=tmp_path / "o")  # no guard keyword


def test_bad_candidate_id(tmp_path):
    with pytest.raises(build_submission.BuildError):
        src = bundle(tmp_path)
        build_submission.build(src, guard=guard_for(src), candidate_id="../x", out_dir=tmp_path / "o")


# ---- inspector ---------------------------------------------------------------------------------

def _raw_zip(path: Path, entries: list[tuple[str, bytes, int]]):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on duplicate names
        with zipfile.ZipFile(path, "w") as zf:
            for name, data, mode in entries:
                zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                zi.create_system, zi.external_attr = 3, mode << 16
                zf.writestr(zi, data)
    return path


@pytest.mark.parametrize("entries,code", [
    ([("agent.yaml", AGENT.encode(), 0o100644), ("../evil.md", b"x", 0o100644)], "PATH_TRAVERSAL"),
    ([("agent.yaml", AGENT.encode(), 0o100644), ("prompts/../../evil.md", b"x", 0o100644)], "PATH_TRAVERSAL"),
    ([("agent.yaml", AGENT.encode(), 0o100644), ("/tmp/evil.md", b"x", 0o100644)], "ABSOLUTE_PATH"),
    ([("agent.yaml", AGENT.encode(), 0o100644), ("prompts/system.md", b"/etc/passwd", 0o120777)], "SYMLINK_ENTRY"),
    ([("agent.yaml", AGENT.encode(), 0o100644), ("agent.yaml", b"x", 0o100644)], "DUPLICATE_PATH"),
    ([("cand/agent.yaml", AGENT.encode(), 0o100644), ("cand/prompts/system.md", b"x", 0o100644)], "WRAPPER_DIRECTORY"),
])
def test_inspector_rejects_unsafe_archives_without_extracting(tmp_path, monkeypatch, entries, code):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    z = _raw_zip(tmp_path / "bad.zip", entries)
    r = inspect_submission.inspect(z)
    assert code in {e["code"] for e in r["errors"]} and not r["ok"]
    assert "validation" not in r and list(scratch.iterdir()) == []  # never extracted
    assert not (tmp_path / "evil.md").exists() and not Path("/tmp/evil.md").exists()


def test_inspector_policy_flags_nondeterministic_zip(tmp_path):
    z = tmp_path / "n.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("prompts/system.md", "x")
        zf.writestr("agent.yaml", AGENT)
    r = inspect_submission.inspect(z)
    got = {p["code"] for p in r["policy_violations"]}
    assert {"UNSORTED_ENTRIES", "NONFIXED_TIMESTAMP", "NONSTORED_COMPRESSION"} <= got and not r["ok"]


def test_inspector_clean_temp_extraction(tmp_path, monkeypatch):
    build(bundle(tmp_path), tmp_path / "o")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    r = inspect_submission.inspect(tmp_path / "o/submission.zip")
    assert r["ok"] and r["temp_extraction_removed"] and list(scratch.iterdir()) == []
    assert r["validation"]["structural"] == "STRUCTURAL_VALID"
    assert {f["path"] for f in r["files"]} == {"agent.yaml", "prompts/system.md"}


# ---- provenance --------------------------------------------------------------------------------

def test_provenance_consistency_and_tamper_detection(tmp_path):
    src = bundle(tmp_path, {"adapters/m/adapter_config.json": "{}", "adapters/m/adapter_model.safetensors": safetensors_bytes(),
                            "agent.yaml": AGENT + "adapter: m\n"})
    res = build(src, tmp_path / "o", experiment_id="EXP-20261002-001")
    out = tmp_path / "o"
    assert hash_submission.verify(out)["status"] == "VERIFIED_WITH_UNVERIFIABLE_EVIDENCE"  # no live dataset in tests
    p = json.loads((out / "provenance.json").read_text())
    m = json.loads((out / "manifest.json").read_text())
    assert p["submission_zip_sha256"] == m["zip"]["sha256"] == res["zip_sha256"]
    assert p["source_tree_sha256"] == m["source_tree_sha256"] == common.tree_sha256(p["source_files"])
    assert p["experiment_id"] == "EXP-20261002-001" and p["lora_present"] is True
    assert set(p["adapter_hashes"]["m"]) == {"adapters/m/adapter_config.json", "adapters/m/adapter_model.safetensors"}
    assert p["git_commit"] is None and p["source_in_git"] is False  # temp source is outside git
    assert {o["origin"] for o in p["source_origins"].values()} == {"unproven"}
    env = p["environment"]
    assert env["split_version"] == "v1" and env["harness_versions"] is None
    assert env["split_sha256"] == "420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12"
    assert "build_time_utc" in p and "build_time" not in json.dumps(m)  # wall-clock only outside the ZIP/manifest

    m["files"][0]["sha256"] = "0" * 64
    (out / "manifest.json").write_text(json.dumps(m))
    assert hash_submission.verify(out)["status"] == "FAILED"
    build(src, out)  # rebuild restores consistency
    with open(out / "submission.zip", "r+b") as f:
        f.seek(40)
        f.write(b"\xff")
    assert hash_submission.verify(out)["status"] == "FAILED"


def test_require_clean_refuses_outside_git(tmp_path):
    with pytest.raises(build_submission.BuildError):
        build(bundle(tmp_path), tmp_path / "o", require_clean=True)
