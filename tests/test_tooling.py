import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from conftest import C1, C2, make_wheel, snapshot_state
from eval.failure_taxonomy import Failure, Layer, is_agent_failure
from tools import common, inventory_dataset, preserve_sample, wheelhouse

REPO = Path(__file__).resolve().parent.parent


# ---- common / SHA ----------------------------------------------------------------------------

def test_sha256_matches_hashlib_and_known_vector(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    assert common.sha256_file(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    big = tmp_path / "big"
    big.write_bytes(b"x" * (3 * common.CHUNK + 7))  # spans chunk boundaries
    assert common.sha256_file(big) == hashlib.sha256(big.read_bytes()).hexdigest()


def test_partial_sha256_detects_head_tail_and_size_changes(tmp_path):
    p = tmp_path / "blob"
    data = bytearray(b"\0" * (4 * common.CHUNK))
    p.write_bytes(data)
    base = common.partial_sha256(p)
    data[0] = 1
    p.write_bytes(data)
    assert common.partial_sha256(p) != base
    data[0] = 0
    data[-1] = 1
    p.write_bytes(data)
    assert common.partial_sha256(p) != base
    p.write_bytes(bytes(data[:-1]))
    assert common.partial_sha256(p) != base


def test_dataset_root_requires_env_or_value(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMMA4_DATASET_ROOT", raising=False)
    with pytest.raises(common.DatasetRootError):
        common.dataset_root()
    with pytest.raises(common.DatasetRootError):
        common.dataset_root(str(tmp_path / "nope"))
    monkeypatch.setenv("GEMMA4_DATASET_ROOT", str(tmp_path))
    assert common.dataset_root() == tmp_path


# ---- dataset inventory -----------------------------------------------------------------------

def test_required_path_check(fake_dataset):
    assert inventory_dataset.check_required(fake_dataset) == []
    (fake_dataset / "tasks.jsonl").unlink()
    for f in (fake_dataset / "wheels").iterdir():
        f.unlink()
    (fake_dataset / "wheels").rmdir()
    problems = inventory_dataset.check_required(fake_dataset)
    assert problems == ["missing file: tasks.jsonl", "missing dir: wheels"]
    with pytest.raises(inventory_dataset.MissingPathsError):
        inventory_dataset.build_manifest(fake_dataset)


def test_inventory_is_deterministic_and_read_only(fake_dataset, tmp_path):
    before = snapshot_state(fake_dataset)
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    inventory_dataset.main(["--dataset-root", str(fake_dataset), "--out-dir", str(out1)])
    inventory_dataset.main(["--dataset-root", str(fake_dataset), "--out-dir", str(out2)])
    for name in ("dataset_manifest.json", "source_hashes.json"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
    assert snapshot_state(fake_dataset) == before
    assert str(fake_dataset) not in (out1 / "dataset_manifest.json").read_text()  # no machine-specific paths


def test_inventory_contents(fake_dataset):
    m, hashes = inventory_dataset.build_manifest(fake_dataset)
    assert "ds_store_files_ignored" not in m and "total_allocated_bytes" not in m  # machine-specific fields removed
    assert m["file_count_excluding_ds_store"] == 3 + 2 + 2 + 2 + 2 + 1 + 1 + 1 + 1  # snaps graphs embs wheels sample readme tasks docker sandbox
    t = m["tasks"]
    assert (t["rows"], t["unique_instance_ids"], t["unique_base_commits"]) == (3, 3, 2)
    assert t["repos"] == {"Textualize/rich": 1, "fastapi/fastapi": 2}
    assert t["nonempty_hints"] == 1  # whitespace-only hint does not count
    assert m["graphs"]["zero_byte"] == [f"rich_{C2}.json"]
    assert m["embeddings"]["at_or_below_code_intel_threshold"] == [f"rich_{C2}.npz"]
    assert m["task_coverage"]["tasks_missing"] == {"snapshot": [], "graph": [], "embedding": []}
    assert m["key_file_sha256"]["tasks.jsonl"] == common.sha256_file(fake_dataset / "tasks.jsonl")
    assert "GOLD" not in common.dumps(m)  # gold patch content never copied into the manifest
    assert set(hashes["files"]) == {"HARNESS_README.md", "tasks.jsonl", "docker/Dockerfile.sandbox",
                                    "sandbox/setup.py", "sample_submission/agent.yaml", "sample_submission/prompts/system.md"}


def test_inventory_reports_missing_code_intel(fake_dataset):
    (fake_dataset / "graphs" / f"fastapi_{C1}.json").unlink()
    m, _ = inventory_dataset.build_manifest(fake_dataset)
    assert m["task_coverage"]["tasks_missing"]["graph"] == ["fastapi_1", "fastapi_2"]


# ---- wheelhouse ------------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("pydantic_core-2.46.4-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
     ("pydantic_core", "pydantic-core", "2.46.4", "cp313", "manylinux_2_17_x86_64.manylinux2014_x86_64")),
    ("Flask-2.2.5-py3-none-any.whl", ("Flask", "flask", "2.2.5", "py3", "any")),
    ("pkg-1.0-1build-py3-none-any.whl", ("pkg", "pkg", "1.0", "py3", "any")),
])
def test_parse_wheel_filename(filename, expected):
    p = wheelhouse.parse_filename(filename)
    assert (p["name"], p["normalized"], p["version"], p["python_tag"], p["platform_tag"]) == expected


def test_parse_wheel_filename_rejects_non_wheels():
    assert wheelhouse.parse_filename("pkg-1.0.tar.gz") is None
    assert wheelhouse.parse_filename("bad.whl") is None


def test_read_wheel_metadata_and_mismatch(tmp_path):
    good = wheelhouse.read_wheel(make_wheel(tmp_path / "Foo_Bar-1.2.3-py3-none-any.whl", "Foo_Bar", "1.2.3", "Foo.Bar"))
    assert good["metadata"]["name"] == "Foo.Bar"
    assert (good["normalized_name"], good["version"], good["filename_metadata_agree"]) == ("foo-bar", "1.2.3", True)
    assert good["py_files"] == 1 and good["zip_ok"]
    liar = wheelhouse.read_wheel(make_wheel(tmp_path / "foo-9.9-py3-none-any.whl", "foo", "1.0"))
    assert liar["version"] == "1.0" and not liar["filename_metadata_agree"]  # METADATA wins, mismatch flagged


def test_read_wheel_bad_zip(tmp_path):
    p = tmp_path / "broken-1.0-py3-none-any.whl"
    p.write_bytes(b"not a zip")
    w = wheelhouse.read_wheel(p)
    assert w["zip_ok"] is False and w["version"] == "1.0"


def test_wheelhouse_manifest_deterministic_and_key_packages(fake_dataset):
    before = snapshot_state(fake_dataset)
    m1 = wheelhouse.build_manifest(fake_dataset / "wheels")
    m2 = wheelhouse.build_manifest(fake_dataset / "wheels")
    assert common.dumps(m1) == common.dumps(m2)
    assert snapshot_state(fake_dataset) == before
    assert m1["key_packages"]["pydantic"] == ["2.13.4"]
    assert "swegemma" in m1["key_packages_absent"]
    assert m1["wheelhouse_fingerprint_sha256"] == hashlib.sha256("".join(
        f"{w['filename']}\t{w['sha256']}\n" for w in m1["wheels"]).encode()).hexdigest()


# ---- sample preservation ---------------------------------------------------------------------

def test_preserve_sample_copies_verifies_and_refuses_drift(fake_dataset, tmp_path):
    src, dest = fake_dataset / "sample_submission", tmp_path / "baseline"
    m = preserve_sample.preserve(src, dest)
    assert m == preserve_sample.tree_manifest(dest)
    assert preserve_sample.preserve(src, dest, verify_only=True)["tree_sha256"] == m["tree_sha256"]
    (dest / "prompts" / "system.md").unlink()  # e.g. gitignored file missing on a fresh clone
    with pytest.raises(preserve_sample.SampleMismatchError):
        preserve_sample.preserve(src, dest, verify_only=True)
    assert preserve_sample.preserve(src, dest)["tree_sha256"] == m["tree_sha256"]  # refilled
    (dest / "agent.yaml").write_text("tampered\n")
    with pytest.raises(preserve_sample.SampleMismatchError):
        preserve_sample.preserve(src, dest)  # non-empty dest is never overwritten
    assert (dest / "agent.yaml").read_text() == "tampered\n"


# ---- committed metadata ----------------------------------------------------------------------

MATRIX_FIELDS = {"id", "title", "hypothesis", "authority", "local_possible", "requires_model", "requires_gpu",
                 "requires_docker", "risk", "status", "result_path", "minimal_repro", "notes"}


def test_matrix_is_complete_and_honest():
    m = yaml.safe_load((REPO / "harness_cert" / "matrix.yaml").read_text())
    items = m["items"]
    assert [i["id"] for i in items] == [f"H{n:02d}" for n in range(1, 31)]
    allowed = set(m["status_vocabulary"])
    assert allowed == {"PASS", "FAIL", "VERSION-SPECIFIC", "HOST-UNKNOWN", "NOT-REPRODUCED", "DEFERRED"}
    for i in items:
        assert MATRIX_FIELDS <= set(i), i["id"]
        assert i["status"] in allowed
        for flag in ("local_possible", "requires_model", "requires_gpu", "requires_docker"):
            assert isinstance(i[flag], bool), (i["id"], flag)
    assert {i["id"] for i in items if i["status"] == "PASS"} == {
        "H04", "H05", "H13", "H14", "H18", "H26", "H28", "H29",
    }
    for case in ("H04", "H05", "H18"):
        item = next(i for i in items if i["id"] == case)
        assert item["blocked_by"] == []
        assert any(authority.startswith("REPRODUCED-LOCAL") for authority in item["authority"])
        assert item["scope"] == "synthetic local harness only; no hidden scorer inference"
        assert item["result_path"] == f"harness_cert/results/{case}/20261004T172725Z-wynp5tjr/"
        assert "H04_H05_H18_LOCAL_CERTIFICATION_2026-10-04.md" in item["notes"]
    assert (REPO / "harness_cert/reports/H04_H05_H18_LOCAL_CERTIFICATION_2026-10-04.md").is_file()
    budget_report = "H13_H14_H29_LOCAL_CERTIFICATION_2026-10-05.md"
    for case in ("H13", "H14", "H29"):
        item = next(i for i in items if i["id"] == case)
        assert item["blocked_by"] == []
        assert (
            "REPRODUCED-LOCAL (20261005T003205Z-_fgsypg5; swegemma 0.2.7, "
            "google-adk 1.36.1, adk-submission 0.2.12)"
        ) in item["authority"]
        assert item["scope"] == "synthetic local harness only; no hidden scorer inference"
        assert item["result_path"] == f"harness_cert/results/{case}/20261005T003205Z-_fgsypg5/"
        assert f"harness_cert/reports/{budget_report}" in item["notes"]
    assert (REPO / "harness_cert/reports" / budget_report).is_file()
    h26 = next(i for i in items if i["id"] == "H26")
    assert h26["blocked_by"] == []
    assert any(authority.startswith("REPRODUCED-LOCAL") for authority in h26["authority"])
    assert (REPO / "harness_cert/reports/H26_INCLUDE_PATHS_2026-10-03.md").is_file()
    h28 = next(i for i in items if i["id"] == "H28")
    assert h28["hypothesis"] == "The official sample packaged deterministically passes the current Kaggle submission path."
    assert h28["blocked_by"] == []
    assert any(authority.startswith("OWN-KAGGLE-RUN") and "operator-reported" in authority
               for authority in h28["authority"])
    assert not any(authority.startswith("REPRODUCED-LOCAL") for authority in h28["authority"])
    assert h28["scope"] == "one frozen E0 artifact and operator-reported hosted completion; hidden runtime unknown"
    assert h28["result_path"] == "harness_cert/results/H28/2026-10-06-e0-certification/"
    assert (h28["local_possible"], h28["requires_model"], h28["requires_gpu"], h28["requires_docker"]) == (
        False, True, True, False,
    )
    h28_report = "harness_cert/reports/H28_KAGGLE_E0_CERTIFICATION_2026-10-06.md"
    assert h28_report in h28["notes"]
    assert (REPO / h28_report).is_file()
    assert next(i for i in items if i["id"] == "H30")["status"] == "NOT-REPRODUCED"
    assert {i["id"] for i in items if i["status"] == "DEFERRED"} == {"H16", "H24", "H25"}


def test_failure_taxonomy_is_exact():
    assert [f.value for f in Failure] == [
        "PLATFORM_SCORER_EXCEPTION", "MODEL_SERVER_START_FAILURE", "HARNESS_COMPILE_VALIDATION",
        "INVALID_TOOL_FATAL", "TOOL_SERIALIZATION", "CONTEXT_OVERFLOW", "AGENT_TIMEOUT", "TOOL_BUDGET_EXHAUSTED",
        "NO_PATCH", "PATCH_MALFORMED_OR_APPLY_FAIL", "SCRATCH_OR_TEST_POLLUTION", "LOCALIZATION_WRONG",
        "ROOT_CAUSE_WRONG", "EDIT_FAILED", "SYNTAX_ERROR", "TARGETED_TEST_FAIL", "HIDDEN_TEST_FAIL_BEHAVIORAL",
        "OVERFIX_REGRESSION", "ENVIRONMENT_VERIFICATION_ARTIFACT", "UNKNOWN"]
    assert not is_agent_failure("PLATFORM_SCORER_EXCEPTION")
    assert not is_agent_failure(Failure.ENVIRONMENT_VERIFICATION_ARTIFACT)
    assert is_agent_failure(Failure.EDIT_FAILED) and is_agent_failure("NO_PATCH")
    assert all(isinstance(f.layer, Layer) and f.description for f in Failure)


def test_registry_has_no_real_experiments():
    lines = [json.loads(l) for l in (REPO / "experiments" / "registry.jsonl").read_text().splitlines() if l.strip()]
    assert all(r.get("_is_example") is True for r in lines)
