"""H23 capture core, notebook and importer. Fake environments only: no network, Kaggle, GPU or real pip.

Expected statuses are LITERALS from the audit; nothing is imported from production vocabularies for assertions.
"""
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import snapshot_state
from h23_fakes import b64, dist_info_name, edit_json, install_files, load_zip, scenario, write_capture, write_record
from tools import build_h23_notebook, safe_fs
from tools import h23_capture_core as core
from tools import import_h23_capture as imp
from tools.common import UnsafeOutputError, WriteGuard

REPO = Path(__file__).resolve().parent.parent
HARNESS = ["swegemma", "adk-submission", "adk-eval-core"]
SECRET_URL = "https://user:pass@example.com/pkg.whl?token=SECRET123#frag"
NESTED = {"outer": {"api_key": "SECRET123", "nested": [{"password": "PASS4567"}, {"url": "https://u:p@example.com/x?token=T0KEN99#frag"}]}}
SECRETS = ["SECRET123", "PASS4567", "T0KEN99", "user:pass", "u:p@", "pass@", "#frag", "SECRETKEY2", "ghp_ABCDEFGHIJKLMNOPQRSTUV"]
ATTEST = {"notebook_version": "7", "wheelhouse_dataset_version": "20058023"}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in ("GEMMA4_DATASET_ROOT", "GEMMA4_HARNESS_ROOT", "KAGGLE_KERNEL_RUN_TYPE"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def ds(tmp_path):
    d = tmp_path / "dataset"
    d.mkdir()
    (d / "tasks.jsonl").write_text("{}\n")
    return d


def run(cfg):
    return core.run_capture(cfg)


def do_import(zip_path, tmp_path, ds, attest=None, root="ext"):
    return imp.import_capture(zip_path, harness_root=tmp_path / root, guard=WriteGuard(ds),
                              summary_dir=tmp_path / f"summaries_{root}", attestation=attest)


def kernel(files):
    """Relabel a fake-environment capture as an un-overridden Kaggle kernel run (a forgery used to test the rules)."""
    return edit_json(files, "environment.json", lambda e: (e.update(run_context="kaggle_kernel", config_overrides=[]),
                                                           e["bootstrap"].update(pip_simulated=False)))


@pytest.fixture
def good(tmp_path):
    cfg, _, _ = scenario(tmp_path / "env")
    return kernel(load_zip(run(cfg)["archive"]))


# =============================== bootstrap (fail closed) =======================================

def test_official_success_reproduces_cell2_and_attributes_from_verified_content(tmp_path):
    cfg, calls, _ = scenario(tmp_path)
    res = run(cfg)
    assert res["bootstrap_state"] == "BOOTSTRAP_SUCCEEDED" and res["archive"].name.endswith("_BOOTSTRAP_SUCCEEDED.zip")
    cmd = calls[0]["cmd"]
    assert calls[0]["plan_present"] is True
    assert cmd[:7] == [sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall"]
    assert cmd[7:] == sorted(cmd[7:]) and not any("cutlass" in a for a in cmd)
    assert any(a.endswith("vllm-0.19.1+cu128-py3-none-any.whl") for a in cmd)
    t = res["packages"]["targets"]
    for h in HARNESS:
        assert t[h]["bootstrap_attribution"] == "OFFICIAL_FULL_BOOTSTRAP" and t[h]["source_status"] == "SOURCE_CAPTURED"
        cc = t[h]["wheel_evidence"]["content_check"]
        assert cc["matched"] > 0 and cc["mismatched"] == cc["absent_in_install"] == cc["extra_in_install"] == 0
    assert t["swegemma"]["change"] == "INSTALLED" and t["adk-eval-core"]["change"] == "REPLACED"
    assert t["google-adk"]["bootstrap_attribution"] == "BASE_IMAGE" and t["google-adk"]["change"] == "UNCHANGED"
    assert t["google-adk"]["post_fingerprint"]["complete"] is True  # declared console script is an allowed category
    assert t["litellm"]["bootstrap_attribution"] == "NOT_INSTALLED"


def test_pip_failure_is_fail_closed(tmp_path, ds, capsys):
    cfg, _, _ = scenario(tmp_path, returncode=1)
    res = run(cfg)
    core.report(res)
    assert "BOOTSTRAP FAILED" in capsys.readouterr().out
    assert res["bootstrap_state"] == "BOOTSTRAP_FAILED" and res["archive"].name.endswith("_BOOTSTRAP_FAILED.zip")
    for e in res["packages"]["targets"].values():
        assert e["bootstrap_attribution"] in ("UNKNOWN", "NOT_INSTALLED") and e["source_status"] != "SOURCE_CAPTURED"
    s = do_import(res["archive"], tmp_path, ds, ATTEST)["summary"]
    assert s["capture_state"] == "BOOTSTRAP_FAILED" and s["evidence_strength"] == "NONE"


def test_harness_only_is_non_official(tmp_path, ds):
    cfg, calls, _ = scenario(tmp_path, mode="harness_only")
    res = run(cfg)
    assert not any("vllm" in a for a in calls[0]["cmd"])
    assert all(res["packages"]["targets"][h]["bootstrap_attribution"] == "NON_OFFICIAL_PARTIAL_BOOTSTRAP" for h in HARNESS)
    s = do_import(write_capture(tmp_path / "k.zip", kernel(load_zip(res["archive"]))), tmp_path, ds, ATTEST)["summary"]
    assert s["bootstrap_label"] == "NON_OFFICIAL_PARTIAL_BOOTSTRAP" and s["evidence_strength"] == "NONE"


def test_dry_run_cannot_claim_version_specific(tmp_path, ds, monkeypatch):
    monkeypatch.setenv("KAGGLE_KERNEL_RUN_TYPE", "Interactive")
    cfg, calls, _ = scenario(tmp_path, skip=True)
    res = run(cfg)
    assert res["bootstrap_state"] == "BOOTSTRAP_NOT_RUN" and calls == []
    s = do_import(res["archive"], tmp_path, ds, ATTEST)["summary"]
    assert s["capture_state"] == "BOOTSTRAP_NOT_RUN" and s["evidence_strength"] == "NONE"


def test_version_specific_requires_every_condition(tmp_path, ds, good):
    z = write_capture(tmp_path / "ok.zip", good)
    assert do_import(z, tmp_path, ds, attest=None, root="a")["summary"]["evidence_strength"] == "NONE"
    s = do_import(z, tmp_path, ds, attest=ATTEST, root="b")["summary"]
    assert s["evidence_strength"] == "VERSION-SPECIFIC" and s["capture_state"] == "CAPTURE_VALIDATED" and s["ineligibility_reasons"] == []
    assert s["scorer_relationship"] == "SCORER_ONLY_UNKNOWN" and s["h23_matrix_status"].startswith("HOST-UNKNOWN")


# =============================== P1-4: contradictions fail =====================================

@pytest.mark.parametrize("name,fn", [
    ("skip_install + success", lambda e: e["bootstrap"].update(skip_install=True, dry_run=True)),
    ("dry_run + success", lambda e: e["bootstrap"].update(dry_run=True)),
    ("pip not run + success", lambda e: e["bootstrap"].update(pip_executed=False)),
    ("returncode 1 + success", lambda e: e["bootstrap"]["pip"].update(returncode=1)),
    ("pip result missing", lambda e: e["bootstrap"].update(pip=None)),
    ("kernel with overrides", lambda e: e.update(config_overrides=["search_paths"])),
    ("simulated pip as kernel", lambda e: e["bootstrap"].update(pip_simulated=True)),
    ("harness_only + official attribution", lambda e: e["bootstrap"].update(mode="harness_only")),
    ("command not official", lambda e: e["bootstrap"]["plan"]["command"].remove("--force-reinstall")),
    ("plan omits a wheel", lambda e: (e["bootstrap"]["plan"]["command"].remove(e["bootstrap"]["plan"]["tmp_wheelhouse"] + "/anyio-4.0.0-py3-none-any.whl"),
        e["bootstrap"]["plan"].update(selected=[x for x in e["bootstrap"]["plan"]["selected"] if not x["wheel"].startswith("anyio")]))),
    # command stays consistent and anyio carries no wheel evidence: only the inventory-selection rule can catch it
])
def test_bootstrap_contradictions_are_rejected(tmp_path, ds, good, name, fn):
    files = edit_json(good, "environment.json", fn)
    if "plan" in name or "command" in name:  # keep bootstrap_plan.json equal to the edited plan: the contradiction must be caught elsewhere
        files = edit_json(files, "bootstrap_plan.json", lambda p: p.update(json.loads(files["environment.json"])["bootstrap"]["plan"]))
    with pytest.raises(imp.CaptureContradiction):
        do_import(write_capture(tmp_path / "c.zip", files), tmp_path, ds, ATTEST)
    assert not (tmp_path / "summaries_ext").exists()


def test_failed_bootstrap_with_promotion_is_contradiction(tmp_path, ds):
    cfg, _, _ = scenario(tmp_path / "env", returncode=1)
    files = load_zip(run(cfg)["archive"])
    files = edit_json(files, "packages.json", lambda p: p["targets"]["swegemma"].update(bootstrap_attribution="OFFICIAL_FULL_BOOTSTRAP"))
    with pytest.raises(imp.CaptureError):
        do_import(write_capture(tmp_path / "c.zip", files), tmp_path, ds)


def test_promotion_claim_keys_are_rejected(tmp_path, ds, good):
    for key, val in (("evidence_strength", "VERSION-SPECIFIC"), ("record_hash_match", True), ("capture_state", "CAPTURE_VALIDATED")):
        files = edit_json(good, "packages.json", lambda p: p["targets"]["swegemma"].update({key: val}))
        with pytest.raises(imp.CaptureContradiction):
            do_import(write_capture(tmp_path / f"{key}.zip", files), tmp_path, ds, ATTEST)


# =============================== P1-2: RECORD ownership recomputed =============================

def test_modified_source_with_forged_claims_is_rejected(tmp_path, ds, good):
    new = b"ALLOWED = 2  # modified\n"
    files = dict(good)
    files["sources/swegemma/swegemma/config.py"] = new
    files = edit_json(files, "source_files.json", lambda s: [f.update(bytes=len(new), sha256=hashlib.sha256(new).hexdigest(), record_hash_match=True)
                                                              for f in s["swegemma"]["files"] if f["path"] == "swegemma/config.py"])
    with pytest.raises(imp.CaptureError):
        do_import(write_capture(tmp_path / "m.zip", files), tmp_path, ds, ATTEST)
    files = edit_json(files, "source_files.json", lambda s: [f.pop("record_hash_match", None) for f in s["swegemma"]["files"]])
    with pytest.raises(imp.CaptureContradiction, match="RECORD"):  # bytes match their claim, but contradict RECORD
        do_import(write_capture(tmp_path / "m2.zip", files), tmp_path, ds, ATTEST)


def test_source_not_owned_by_record_is_rejected(tmp_path, ds, good):
    data = b"EXTRA = 1\n"
    files = dict(good)
    files["sources/swegemma/swegemma/extra.py"] = data
    files = edit_json(files, "source_files.json", lambda s: s["swegemma"]["files"].append(
        {"path": "swegemma/extra.py", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}))
    with pytest.raises(imp.CaptureError, match="not owned"):
        do_import(write_capture(tmp_path / "o.zip", files), tmp_path, ds, ATTEST)


@pytest.mark.parametrize("dup_path", ["swegemma/config.py", "swegemma/Config.py"])
def test_duplicate_or_aliased_source_claims_are_rejected(tmp_path, ds, good, dup_path):
    def add(s):
        first = next(f for f in s["swegemma"]["files"] if f["path"] == "swegemma/config.py")
        s["swegemma"]["files"].append(dict(first, path=dup_path))  # second claim -> same archive member / normalized path
    files = edit_json(good, "source_files.json", add)
    with pytest.raises(imp.CaptureError, match="duplicate"):
        do_import(write_capture(tmp_path / "d.zip", files), tmp_path, ds, ATTEST)


# =============================== P1-3: wheel identity and counters recomputed ==================

def test_other_distributions_wheel_assigned_is_rejected(tmp_path, ds, good):
    env = json.loads(good["environment.json"])
    vllm = next(s for s in env["bootstrap"]["plan"]["selected"] if s["wheel"].startswith("vllm"))
    files = edit_json(good, "packages.json", lambda p: p["targets"]["swegemma"]["wheel_evidence"].update(
        wheel=vllm["wheel"], wheel_sha256=vllm["sha256"], wheel_bytes=vllm["bytes"], link_name=vllm["link_name"]))
    with pytest.raises(imp.CaptureError):
        do_import(write_capture(tmp_path / "w.zip", files), tmp_path, ds, ATTEST)


@pytest.mark.parametrize("field,val", [("absent_in_install", 1), ("mismatched", 1), ("extra_in_install", 2), ("matched", 99)])
def test_forged_wheel_counters_are_rejected(tmp_path, ds, good, field, val):
    files = edit_json(good, "packages.json", lambda p: p["targets"]["swegemma"]["wheel_evidence"]["content_check"].update({field: val}))
    with pytest.raises(imp.CaptureContradiction):
        do_import(write_capture(tmp_path / "f.zip", files), tmp_path, ds, ATTEST)


def _rewrite_swegemma(site_dir, mutate):
    """Post-install drift with a CONSISTENT RECORD (RECORD regenerated after the change)."""
    di = dist_info_name("swegemma", "0.2.7")
    mutate(site_dir)
    files = {p.relative_to(site_dir).as_posix(): p.read_bytes() for p in site_dir.rglob("*")
             if p.is_file() and p.relative_to(site_dir).as_posix().startswith(("swegemma/", di + "/")) and p.name != "RECORD"}
    write_record(site_dir, di, files)


@pytest.mark.parametrize("mutate,counter", [
    (lambda s: (s / "swegemma" / "config.py").unlink(), "absent_in_install"),
    (lambda s: (s / "swegemma" / "config.py").write_bytes(b"PATCHED = 1\n"), "mismatched"),
    (lambda s: (s / "swegemma" / "stray.py").write_bytes(b"LEFTOVER = 1\n"), "extra_in_install"),
])
def test_real_install_drift_blocks_attribution_and_forgery_is_rejected(tmp_path, ds, mutate, counter):
    cfg, _, _ = scenario(tmp_path / "env", post=lambda s: _rewrite_swegemma(s, mutate))
    res = run(cfg)
    e = res["packages"]["targets"]["swegemma"]
    assert e["post_fingerprint"]["complete"] is True and e["wheel_evidence"]["content_check"][counter] == 1
    assert e["bootstrap_attribution"] == "UNKNOWN"
    files = kernel(load_zip(res["archive"]))
    assert do_import(write_capture(tmp_path / "h.zip", files), tmp_path, ds, ATTEST, root="h")["summary"]["evidence_strength"] == "NONE"
    forged = edit_json(files, "packages.json", lambda p: p["targets"]["swegemma"].update(
        bootstrap_attribution="OFFICIAL_FULL_BOOTSTRAP", install_status="PACKAGE_BOOTSTRAPPED_FROM_OFFICIAL_NOTEBOOK"))
    with pytest.raises(imp.CaptureContradiction):
        do_import(write_capture(tmp_path / "f.zip", forged), tmp_path, ds, ATTEST, root="f")


def test_tampered_install_without_record_update_is_incomplete(tmp_path):
    cfg, _, _ = scenario(tmp_path, post=lambda s: (s / "swegemma" / "config.py").write_bytes(b"tampered\n"))
    e = run(cfg)["packages"]["targets"]["swegemma"]
    assert e["post_fingerprint"]["complete"] is False and e["post_fingerprint"]["categories"].get("hash_mismatch") == 1
    assert e["bootstrap_attribution"] == "UNKNOWN" and e["source_status"] == "SOURCE_NOT_CAPTURED"


# =============================== P1-6: RECORD inventory completeness ===========================

def _inv(tmp_path, files, extra_rows=None, **kw):
    site_dir = tmp_path / "site"
    install_files(site_dir, "swegemma", "1.0", files, extra_record_rows=extra_rows, **kw)
    d, _ = core.find_distribution("swegemma", [str(site_dir)])
    inv = core.inventory(d, (tmp_path / "bin").resolve() if (tmp_path / "bin").exists() else tmp_path / "bin")
    return d, inv, core.summarize_inventory(inv)


@pytest.mark.parametrize("row,category", [
    ("../outside.py,,", "outside_root"), ("swegemma/../../outside.py,,", "outside_root"), ("/etc/passwd,,", "unsafe"),
    ("C:/escape.py,,", "unsafe"), ("swegemma\\win.py,,", "unsafe"), ("swegemma/missing.py,,", "missing"),
    ("swegemma/__init__.py,,", "duplicate"), ("swegemma/x.py,md5=abc,3", "malformed"), ("swegemma/x.py,sha256=!!,1", "malformed"),
    ("only_one_field", "malformed"), ("../bin/undeclared,,", "outside_root"),
])
def test_any_unclassified_row_makes_fingerprint_incomplete(tmp_path, row, category):
    (tmp_path / "outside.py").write_text("x\n")
    _, inv, fp = _inv(tmp_path, {"swegemma/__init__.py": b"x\n"}, extra_rows=[row])
    assert category in fp["categories"] and fp["complete"] is False


def test_record_hash_and_size_disagreement_is_incomplete(tmp_path):
    site_dir = tmp_path / "site"
    install_files(site_dir, "swegemma", "1.0", {"swegemma/__init__.py": b"x\n"})
    (site_dir / "swegemma" / "__init__.py").write_bytes(b"changed!\n")
    d, _ = core.find_distribution("swegemma", [str(site_dir)])
    fp = core.summarize_inventory(core.inventory(d, tmp_path / "bin"))
    assert fp["categories"].get("hash_mismatch") == 1 and fp["complete"] is False
    _, _, fp2 = _inv(tmp_path / "b", {"swegemma/__init__.py": b"x\n"}, extra_rows=["swegemma/__init__.py,,999"])
    assert fp2["complete"] is False  # duplicate row (and size disagreement) never complete


def test_symlinked_record_entries_make_fingerprint_incomplete(tmp_path):
    (tmp_path / "outside.py").write_text("secret\n")
    site_dir = tmp_path / "site"
    install_files(site_dir, "swegemma", "1.0", {"swegemma/__init__.py": b"x\n"})
    (site_dir / "swegemma" / "link.py").symlink_to(tmp_path / "outside.py")
    (site_dir / "dangling.py").symlink_to(tmp_path / "nope.py")
    (site_dir / "mid").symlink_to(tmp_path, target_is_directory=True)
    write_record(site_dir, dist_info_name("swegemma", "1.0"), {"swegemma/__init__.py": b"x\n"},
                 ["swegemma/link.py,,", "dangling.py,,", "mid/outside.py,,"])
    d, _ = core.find_distribution("swegemma", [str(site_dir)])
    fp = core.summarize_inventory(core.inventory(d, tmp_path / "bin"))
    assert fp["categories"].get("unsafe") == 3 and fp["complete"] is False


def test_declared_console_script_is_the_only_allowed_outside_row(tmp_path):
    (tmp_path / "bin").mkdir()
    _, _, fp = _inv(tmp_path, {"swegemma/__init__.py": b"x\n"}, entry_points="[console_scripts]\nswegemma = swegemma:main\n",
                    scripts={"swegemma": b"#!/bin/sh\n"})
    assert fp["complete"] is True and fp["categories"]["console_script"] == 1


def test_ambiguous_ownership_across_targets(tmp_path):
    site_dir = tmp_path / "site"
    install_files(site_dir, "google-adk", "1.0", {"google/shared.py": b"s\n"})
    install_files(site_dir, "google-genai", "1.0", {"google/shared.py": b"s\n"})
    cfg = core.Config(search_paths=[str(site_dir)], scripts_dir=tmp_path / "bin", targets=("google-adk", "google-genai"))
    snap = core.target_snapshot(cfg)
    assert snap["google-adk"]["fp"]["complete"] is False and snap["google-adk"]["fp"]["categories"].get("ambiguous_ownership") == 1


@pytest.mark.parametrize("fn", [
    lambda inv: inv["rows"].append({"path": "../outside.py", "record_hash": "", "record_size": "", "category": "ok", "bytes": 1, "sha256": "0" * 64}),
    lambda inv: [r.update(category="ok") for r in inv["rows"] if r["category"] == "console_script"],
    lambda inv: [r.update(sha256="0" * 64) for r in inv["rows"] if r["category"] == "ok"][:1],
])
def test_forged_inventory_labels_are_rejected(tmp_path, ds, good, fn):
    files = edit_json(good, "inventories/google_adk.post.json", fn)
    with pytest.raises(imp.CaptureError):
        do_import(write_capture(tmp_path / "i.zip", files), tmp_path, ds, ATTEST)


def test_forged_complete_flag_is_rejected(tmp_path, ds):
    cfg, _, site_dir = scenario(tmp_path / "env")
    (tmp_path / "env" / "outside.py").write_text("x\n")
    install_files(site_dir, "litellm", "1.0", {"litellm/__init__.py": b"x\n"}, extra_record_rows=["../outside.py,,"])
    files = kernel(load_zip(run(cfg)["archive"]))
    assert json.loads(files["packages.json"])["targets"]["litellm"]["post_fingerprint"]["complete"] is False
    forged = edit_json(files, "packages.json", lambda p: p["targets"]["litellm"]["post_fingerprint"].update(complete=True))
    with pytest.raises(imp.CaptureContradiction):
        do_import(write_capture(tmp_path / "f.zip", forged), tmp_path, ds, ATTEST)


# =============================== P1-1 / P2-1: credentials =========================================

def test_nested_sanitizer_and_redaction_flags():
    clean, did = core.sanitize_value(NESTED)
    text = json.dumps(clean)
    assert did is True and not any(s in text for s in SECRETS)
    assert clean["outer"]["api_key"] == "<REDACTED>" and clean["outer"]["nested"][0]["password"] == "<REDACTED>"
    assert clean["outer"]["nested"][1]["url"] == "https://example.com/x"
    assert core.sanitize_value({"a": {"b": ["fine", {"c": "ok"}]}}) == ({"a": {"b": ["fine", {"c": "ok"}]}}, False)
    du = core.summarize_direct_url(json.dumps({"url": "https://example.com/p.whl",
                                               "vcs_info": {"vcs": "git", "requested_revision": "main?token=ABCDEFGH123"}}))
    assert du["redacted"] is True and "ABCDEFGH123" not in json.dumps(du)
    for key in ("api_key", "APIKEY", "Api-Key", "x_api_key", "access_token", "refresh_token", "client_secret", "authorization",
                "Cookie", "session", "private_key", "signature", "sig", "credential", "credentials", "token", "passwd", "auth"):
        assert core.sanitize_value({key: "VALUE123"}) == ({key: "<REDACTED>"}, True), key


@pytest.mark.parametrize("text,leak", [
    (SECRET_URL, "SECRET123"), ("git+https://tok:x-oauth@github.com/o/r.git", "tok:x-oauth"),
    ("https://h/p?sig=abc&x=1", "sig=abc"), ("api_key=SECRET999", "SECRET999"), ("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ("ASIAABCDEFGHIJKLMNOP", "ASIAABCDEFGHIJKLMNOP"), ("hf_abcdefghijklmnopqrstu", "hf_abcdefghijklmnopqrstu"),
    ("password: hunter22", "hunter22"), ('{"api_key": "SECRET777"}', "SECRET777"), ("Authorization: Bearer abcdefgh12345", "abcdefgh12345"),
    ("sk-abcdefghijklmnop1234", "sk-abcdefghijklmnop1234"), ("github_pat_ABCDEFGHIJKLMNOPQRST", "github_pat_ABCDEFGHIJKLMNOPQRST"),
])
def test_text_sanitizer(text, leak):
    clean, did = core.sanitize_text(text)
    assert leak not in clean and did is True


def test_record_hashes_are_not_mistaken_for_tokens():
    for h in ("sha256=Ask-ABCDEFGHIJKLMNOPQRS_xyz0123456789abcdefgh", "sha256=xhf_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345678"):
        assert core.sanitize_text(h) == (h, False)


def test_credentials_never_reach_archive_output_or_summary(tmp_path, ds, capsys, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", SECRET_URL)
    monkeypatch.setenv("HOME", "/home/u?token=SECRET123")
    cfg, _, site_dir = scenario(tmp_path, skip=True)
    install_files(site_dir, "google-genai", "2.0", {"google/genai/__init__.py": b"x\n"},
                  metadata_extra="Project-URL: Home, https://user:pass@example.com/x?api_key=SECRETKEY2\n"
                                 f"Description: token=ghp_ABCDEFGHIJKLMNOPQRSTUV {json.dumps(NESTED)}\n",
                  direct_url=json.dumps({"url": SECRET_URL, "archive_info": {"hash": "sha256=abc"}, "extra": NESTED}))
    res = run(cfg)
    core.report(res)
    printed = capsys.readouterr().out
    raw = res["archive"].read_bytes()
    members = load_zip(res["archive"])
    blob = b"".join(members.values()).decode("utf-8", "replace")
    for s in SECRETS:
        assert s not in printed and s.encode() not in raw and s not in blob, s
    du = json.loads(members["packages.json"])["targets"]["google-genai"]["direct_url"]
    assert du["url_sanitized"] == "https://example.com/pkg.whl" and du["redacted"] is True and du["dropped_fields"] == ["extra"]
    assert not any(k.endswith("direct_url.json") for k in members)
    summary = do_import(res["archive"], tmp_path, ds)["summary_path"].read_text()
    assert not any(s in summary for s in SECRETS)


@pytest.mark.parametrize("where", ["json", "metadata"])
def test_importer_rejects_unsanitized_nested_credentials(tmp_path, ds, good, where):
    if where == "json":
        files = edit_json(good, "packages.json", lambda p: p["targets"]["google-adk"].update(notes=NESTED))
    else:
        files = dict(good)
        files["metadata/google_adk/METADATA"] += json.dumps(NESTED).encode()
        files = edit_json(files, "packages.json", lambda p: p["targets"]["google-adk"]["metadata_files"]["METADATA"].update(
            captured_sha256=hashlib.sha256(files["metadata/google_adk/METADATA"]).hexdigest()))
    with pytest.raises(imp.CaptureError, match="credential"):
        do_import(write_capture(tmp_path / "c.zip", files), tmp_path, ds, ATTEST)


# =============================== P1-5: race-safe extraction =====================================

def _listing(p: Path):
    return sorted(str(x.relative_to(p)) for x in p.rglob("*")) if p.is_dir() else None


def test_ancestor_swap_creates_nothing_in_protected_targets(tmp_path, ds, good, monkeypatch):
    z = write_capture(tmp_path / "g.zip", good)
    real = safe_fs.MKDIR
    for label, victim in (("dataset", ds), ("git", REPO / "harness_cert"), ("python", Path(sys.prefix))):
        def racing(name, mode=0o777, *, dir_fd=None, _v=victim):
            if name == "h23":  # attacker wins the race: h23 becomes a symlink into a protected tree
                os.symlink(_v, name, dir_fd=dir_fd)
            else:
                real(name, mode, dir_fd=dir_fd)
        monkeypatch.setattr(safe_fs, "MKDIR", racing)
        before = snapshot_state(victim) if label == "dataset" else sorted(x.name for x in victim.iterdir())
        with pytest.raises(UnsafeOutputError):
            do_import(z, tmp_path, ds, ATTEST, root=f"ext_{label}")
        after = snapshot_state(victim) if label == "dataset" else sorted(x.name for x in victim.iterdir())
        assert after == before, label
        assert not (victim / "h23").exists()


def test_swap_after_open_writes_only_into_original_directory(tmp_path, ds, good, monkeypatch):
    z = write_capture(tmp_path / "g.zip", good)
    root = tmp_path / "ext"
    real = safe_fs.MKDIR

    def racing(name, mode=0o777, *, dir_fd=None):
        real(name, mode, dir_fd=dir_fd)
        if name.startswith(".staging-"):  # after staging exists: move h23 away and point the old path at the dataset
            os.rename(root / "h23", root / "moved")
            os.symlink(ds, root / "h23")
    monkeypatch.setattr(safe_fs, "MKDIR", racing)
    before = snapshot_state(ds)
    res = do_import(z, tmp_path, ds, ATTEST)
    assert snapshot_state(ds) == before
    assert (root / "moved" / res["capture_id"] / "h23_capture" / "packages.json").is_file()  # writes followed the inode


def test_symlinked_components_rejected(tmp_path, ds, good):
    z = write_capture(tmp_path / "g.zip", good)
    root = tmp_path / "ext"
    (root / "elsewhere").mkdir(parents=True)
    (root / "h23").symlink_to(root / "elsewhere", target_is_directory=True)  # even inside the root
    with pytest.raises(UnsafeOutputError):
        do_import(z, tmp_path, ds)
    assert list((root / "elsewhere").iterdir()) == []
    (root / "h23").unlink()
    (root / "h23").mkdir()
    cid = f"{json.loads(good['environment.json'])['capture_utc']}_{hashlib.sha256(z.read_bytes()).hexdigest()[:12]}"
    (root / "h23" / cid).symlink_to(ds, target_is_directory=True)
    before = snapshot_state(ds)
    with pytest.raises(UnsafeOutputError):
        do_import(z, tmp_path, ds)
    assert snapshot_state(ds) == before


def test_harness_root_in_repo_dataset_or_python_rejected(tmp_path, ds, good):
    z = write_capture(tmp_path / "g.zip", good)
    for root in (REPO / "nope_h23_evidence", ds / "h", Path(sys.prefix) / "nope_h23"):
        with pytest.raises(UnsafeOutputError):
            imp.import_capture(z, harness_root=root, guard=WriteGuard(ds), summary_dir=tmp_path / "s")
        assert not root.exists()


# =============================== P2-2: safe_dest boundary ========================================

@pytest.mark.parametrize("rel", ["C:/escape.py", "c:escape.py", "//server/share/x.py", "\\\\server\\x.py", "a\\b.py",
                                 "/abs.py", "../x.py", "a/../../x.py", "", "a//b.py", "./a.py"])
def test_safe_dest_rejects_dangerous_paths_itself(tmp_path, rel):
    base = tmp_path / "stage"
    base.mkdir()
    with pytest.raises(core.UnsafePath):
        core.safe_dest(base, rel)


# =============================== resolver / containment ==========================================

@pytest.fixture
def droot(tmp_path):
    r = tmp_path / "sp" / "site"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "a.py").write_text("x\n")
    (tmp_path / "sp" / "site_external" / "pkg").mkdir(parents=True)
    (tmp_path / "sp" / "site_external" / "pkg" / "a.py").write_text("outside\n")
    (tmp_path / "outside.py").write_text("outside\n")
    return r.resolve()


@pytest.mark.parametrize("make,rel", [
    (lambda r, t: None, "/etc/passwd"), (lambda r, t: None, "../site_external/pkg/a.py"), (lambda r, t: None, "C:/x.py"),
    (lambda r, t: (r / "linkdir").symlink_to(t / "sp" / "site_external" / "pkg", target_is_directory=True), "linkdir/a.py"),
    (lambda r, t: (r / "pkg" / "f.py").symlink_to(t / "outside.py"), "pkg/f.py"),
    (lambda r, t: (r / "pkg" / "dangling.py").symlink_to(t / "nope.py"), "pkg/dangling.py"),
])
def test_record_resolver_rejects_escapes(droot, tmp_path, make, rel):
    make(droot, tmp_path)
    with pytest.raises(core.UnsafePath):
        core.resolve_record_path(droot, rel)


def test_containment_is_canonical_not_string_prefix(tmp_path):
    assert not core.contained(tmp_path / "foo_external" / "x", tmp_path / "foo")
    assert str(tmp_path / "foo_external" / "x").startswith(str(tmp_path / "foo"))


# =============================== no target imports / notebook identity ===========================

FORBIDDEN_MODULES = {"swegemma", "adk_submission", "adk_eval_core", "google", "litellm", "vllm", "transformers", "torch"}


def test_core_never_imports_targets_or_executes_code():
    tree = ast.parse((REPO / "tools" / "h23_capture_core.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & FORBIDDEN_MODULES
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in FORBIDDEN_MODULES
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            assert name not in ("import_module", "__import__", "find_spec", "exec", "eval", "run_module", "run_path",
                                "spec_from_file_location", "exec_module", "load_module"), name
    src = (REPO / "tools" / "h23_capture_core.py").read_text()
    assert "importable" not in src and src.count("subprocess.run(") == 1


def test_notebook_embeds_core_verbatim():
    assert build_h23_notebook.main(["--check"]) == 0
    nb = json.loads((REPO / "notebooks" / "h23_harness_capture.ipynb").read_text())
    code = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    assert code[0] == (REPO / "tools" / "h23_capture_core.py").read_text()
    assert nb["metadata"]["kaggle"]["accelerator"] == "none" and nb["metadata"]["kaggle"]["isInternetEnabled"] is False


# =============================== immutability / CLI ============================================

def test_summary_and_evidence_are_immutable(tmp_path, ds, good):
    z = write_capture(tmp_path / "g.zip", good)
    a = do_import(z, tmp_path, ds, ATTEST)
    assert (a["status"], a["summary_status"]) == ("imported", "created")
    b = do_import(z, tmp_path, ds, ATTEST)
    assert (b["status"], b["summary_status"]) == ("already_imported", "identical")
    with pytest.raises(imp.EvidenceConflict):
        do_import(z, tmp_path, ds, {"notebook_version": "8", "wheelhouse_dataset_version": "20058023"})
    (a["evidence_dir"] / "h23_capture" / "packages.json").write_text("{}")
    with pytest.raises(imp.EvidenceConflict):
        do_import(z, tmp_path, ds, ATTEST)


def test_malformed_capture_cli_never_tracebacks(tmp_path, ds, good):
    z = write_capture(tmp_path / "m.zip", edit_json(good, "source_files.json", lambda s: s["swegemma"].pop("archive_dir")))
    r = subprocess.run([sys.executable, "-m", "tools.import_h23_capture", str(z), "--harness-root", str(tmp_path / "ext"),
                        "--dataset-root", str(ds), "--summary-dir", str(tmp_path / "s")], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 1 and "Traceback" not in r.stderr and "IMPORT REFUSED" in r.stderr
    with pytest.raises(imp.CaptureError):
        do_import(write_capture(tmp_path / "m2.zip", {**good, "packages.json": b"[1, 2"}), tmp_path, ds)


def test_cli_fails_closed_and_never_spawns(tmp_path, ds, good, monkeypatch):
    z = write_capture(tmp_path / "g.zip", good)
    assert imp.main([str(z), "--dataset-root", str(ds)]) == 1
    assert imp.main([str(z), "--harness-root", str(tmp_path / "e")]) == 1
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocesses"))
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    mods = set(sys.modules)
    assert imp.main([str(z), "--harness-root", str(tmp_path / "e"), "--dataset-root", str(ds), "--summary-dir", str(tmp_path / "s")]) == 0
    assert not ({"swegemma", "adk_submission", "adk_eval_core"} & (set(sys.modules) - mods))


# =============================== promotion derivation: every primitive is load-bearing ==============

@pytest.mark.parametrize("mutate,reason", [
    (lambda v: v["boot"].update(state="BOOTSTRAP_FAILED"), "bootstrap state"),
    (lambda v: v["boot"].update(mode="harness_only"), "NON_OFFICIAL_PARTIAL_BOOTSTRAP"),
    (lambda v: v["boot"].update(dry_run=True), "dry run"),
    (lambda v: v["boot"].update(skip_install=True), "dry run"),
    (lambda v: v["boot"].update(pip_executed=False), "official pip"),
    (lambda v: v.update(pip_rc=1), "official pip"),
    (lambda v: v["boot"].update(pip_simulated=True), "official pip"),
    (lambda v: v["env"].update(run_context="non_kaggle_or_overridden"), "run context"),
    (lambda v: v["env"].update(config_overrides=["x"]), "run context"),
    (lambda v: v["packages"].update(scorer_relationship="X"), "scorer relationship"),
    (lambda v: v["packages"]["targets"]["swegemma"].update(bootstrap_attribution="UNKNOWN"), "swegemma: attribution"),
    (lambda v: v["facts"]["adk-submission"].update(wheel_strong=False), "adk-submission: wheel"),
    (lambda v: v["facts"]["adk-eval-core"].update(post_complete=False), "adk-eval-core: post fingerprint"),
    (lambda v: v["facts"]["swegemma"].update(source_verified=False), "swegemma: source not"),
    (lambda v: v["packages"]["targets"]["swegemma"].update(source_status="SOURCE_NOT_CAPTURED"), "swegemma: source not"),
    (lambda v: v["facts"]["swegemma"].update(all_source_record_hashes_match=False), "hash-verified"),
    (lambda v: v["facts"]["adk-eval-core"].update(metadata_verified=False), "METADATA"),
    (lambda v: v["facts"]["adk-submission"].update(record_verified=False), "RECORD not verified"),
])
def test_each_promotion_primitive_is_required(tmp_path, good, mutate, reason):
    v = imp.validate(imp.read_archive(write_capture(tmp_path / "g.zip", good)))
    assert imp.derive_strength(v, ATTEST) == ("VERSION-SPECIFIC", [])
    mutate(v)
    strength, reasons = imp.derive_strength(v, ATTEST)
    assert strength == "NONE" and any(reason in r for r in reasons), reasons


@pytest.mark.parametrize("attest", [{}, {"notebook_version": "7"}, {"wheelhouse_dataset_version": "20058023"},
                                    {"notebook_version": "", "wheelhouse_dataset_version": "20058023"}])
def test_attestation_is_required(tmp_path, good, attest):
    v = imp.validate(imp.read_archive(write_capture(tmp_path / "g.zip", good)))
    assert imp.derive_strength(v, attest)[0] == "NONE"


# =============================== guard-level tests (each guard rejects inputs only it can catch) ====

def test_wheel_not_in_plan_is_a_contradiction(tmp_path, ds, good):
    files = edit_json(good, "packages.json", lambda p: p["targets"]["swegemma"]["wheel_evidence"].update(wheel="swegemma-9.9-py3-none-any.whl"))
    with pytest.raises(imp.CaptureContradiction, match="planned wheel"):
        do_import(write_capture(tmp_path / "w.zip", files), tmp_path, ds, ATTEST)


def test_pip_executed_without_any_result_is_a_contradiction(tmp_path, ds):
    cfg, _, _ = scenario(tmp_path / "env", skip=True)
    files = edit_json(load_zip(run(cfg)["archive"]), "environment.json", lambda e: e["bootstrap"].update(pip_executed=True))
    with pytest.raises(imp.CaptureContradiction, match="pip_executed"):
        do_import(write_capture(tmp_path / "p.zip", files), tmp_path, ds)


def _row(path, category="ok", data=b"x\n", record_hash=None, size=None):
    h = hashlib.sha256(data).hexdigest()
    return {"path": path, "category": category, "bytes": len(data), "sha256": h,
            "record_hash": b64(data) if record_hash is None else record_hash, "record_size": str(len(data)) if size is None else size}


@pytest.mark.parametrize("row,scripts", [
    (_row("/etc/passwd"), None), (_row("C:/escape.py"), None), (_row("a\\b.py"), None),            # unsafe paths labelled ok
    (_row("pkg/a.py", record_hash=b64(b"other")), None),                                       # RECORD hash disagrees
    (_row("pkg/a.py", size="999"), None),                                                       # RECORD size disagrees
    (_row("pkg/a.py", record_hash="sha256=!!"), None),                                          # malformed hash labelled ok
    (_row("../bin/evil", "console_script"), {"adk"}),                                           # undeclared console script
    (_row("../lib/evil", "console_script"), None),                                              # not under bin/
])
def test_recompute_inventory_rejects_self_contradicting_rows(row, scripts):
    with pytest.raises(imp.CaptureContradiction):
        imp.recompute_inventory({"record_present": True, "record_redacted": False, "rows": [row]}, "t", scripts)


def test_recompute_inventory_duplicates_must_be_labelled():
    rows = [_row("pkg/a.py"), _row("pkg/A.py")]
    with pytest.raises(imp.CaptureContradiction, match="duplicate"):
        imp.recompute_inventory({"record_present": True, "record_redacted": False, "rows": rows}, "t", None)
    rows[1]["category"] = "duplicate"
    assert imp.recompute_inventory({"record_present": True, "record_redacted": False, "rows": rows}, "t", None)["complete"] is False


def _claims_fixture(data=b"A = 1\n"):
    h = hashlib.sha256(data).hexdigest()
    member = "sources/swegemma/swegemma/a.py"
    return ([{"path": "swegemma/a.py", "bytes": len(data), "sha256": h}], {member: data}, {member: h},
            [("swegemma/a.py", b64(data), str(len(data)))], {"swegemma/a.py": {"sha256": h}})


def test_verify_source_claims_accepts_consistent_and_computes_hash_match():
    claims, data, sha, rec, post_ok = _claims_fixture()
    assert imp.verify_source_claims("swegemma", "swegemma", claims, data, sha, rec, post_ok) is True
    rec_nohash = [(p, "", z) for p, _, z in rec]
    assert imp.verify_source_claims("swegemma", "swegemma", claims, data, sha, rec_nohash, post_ok) is False  # computed, not claimed


@pytest.mark.parametrize("break_it", [
    lambda c, d, s, r, p: r.__setitem__(0, (r[0][0], b64(b"different"), r[0][2])),       # RECORD hash contradicts bytes
    lambda c, d, s, r, p: r.__setitem__(0, (r[0][0], r[0][1], "999")),                    # RECORD size contradicts bytes
    lambda c, d, s, r, p: p.clear(),                                                      # not an installed file
    lambda c, d, s, r, p: p["swegemma/a.py"].update(sha256="0" * 64),                     # differs from the installed file
    lambda c, d, s, r, p: r.clear(),                                                      # not owned by RECORD
    lambda c, d, s, r, p: r.append(("swegemma/A.py", "", "")),                            # ambiguous RECORD
    lambda c, d, s, r, p: c.append(dict(c[0])),                                           # duplicate claim
    lambda c, d, s, r, p: c[0].update(bytes=-1),                                          # negative size
])
def test_verify_source_claims_rejects_each_violation(break_it):
    claims, data, sha, rec, post_ok = _claims_fixture()
    break_it(claims, data, sha, rec, post_ok)
    with pytest.raises(imp.CaptureError):
        imp.verify_source_claims("swegemma", "swegemma", claims, data, sha, rec, post_ok)


@pytest.mark.parametrize("notes", [{"api_key": "PLAINVALUE1"}, {"deep": [{"Client-Secret": "PLAINVALUE2"}]},   # key rule only
                                   {"note": "contact https://bob:pw@host/x"}, {"note": "ghp_ABCDEFGHIJKLMNOPQRSTUV"}])  # value rule only
def test_each_credential_rule_is_load_bearing(tmp_path, ds, good, notes):
    files = edit_json(good, "packages.json", lambda p: p["targets"]["google-adk"].update(notes=notes))
    with pytest.raises(imp.CaptureError, match="credential"):
        do_import(write_capture(tmp_path / "c.zip", files), tmp_path, ds, ATTEST)
