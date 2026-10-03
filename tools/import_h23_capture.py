"""Import a downloaded H23 capture archive as EXTERNAL evidence (never into Git, never into Python).

Usage:
    uv run python -m tools.import_h23_capture <capture.zip> --harness-root DIR [--dataset-root DIR]
        [--attest-notebook-version N --attest-wheelhouse-version V]

The archive is UNTRUSTED. No claim in it is accepted as authority:
  * fingerprints, completeness, RECORD agreement, wheel/install matches, source ownership and change classification
    are RECOMPUTED from the archived inventories, RECORD files, wheel manifests and source bytes;
  * impossible combinations (e.g. skip_install + BOOTSTRAP_SUCCEEDED) are CAPTURE_CONTRADICTION and fail;
  * promotion keys (evidence_strength, capture_state, record_hash_match) must not appear in the archive at all;
  * evidence_strength is DERIVED here from independently verified primitives only.
Extraction is race-safe (tools/safe_fs.py: dir-fd relative, O_NOFOLLOW, staging + same-directory rename) and happens
only after every check passed. Summaries and evidence directories are immutable (EVIDENCE_CONFLICT).
Nothing is installed, imported or executed; no subprocess is started.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import secrets
import site
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from tools import safe_fs
from tools.common import REPO_ROOT, DatasetRootError, UnsafeOutputError, WriteGuard, dumps

HARNESS_ENV = "GEMMA4_HARNESS_ROOT"
PREFIX = "h23_capture/"
SUMMARY_DIR = REPO_ROOT / "vendor_meta" / "h23_captures"
SUMMARY_SCHEMA = 3
CAPTURE_SCHEMA = 3
# ---- literal schema (independent of the capture core) -------------------------------------------
TARGET_SCHEMA = {
    "swegemma": "swegemma", "adk-submission": "adk_submission", "adk-eval-core": "adk_eval_core",
    "google-adk": "google_adk", "google-genai": "google_genai", "litellm": "litellm", "vllm": "vllm",
    "transformers": "transformers",
}
HARNESS = ("swegemma", "adk-submission", "adk-eval-core")
HARNESS_PREFIXES = ("google_adk-", "google_genai-", "adk_submission-", "adk_eval_core-", "swegemma-")
REQUIRED_FILES = ("environment.json", "installed_distributions.json", "packages.json", "source_index.json",
                  "source_files.json", "wheelhouse.json", "hashes.json", "CAPTURE_README.md")
TOP_DIRS = ("metadata", "records", "sources", "inventories", "wheels")
BOOT_STATES = ("BOOTSTRAP_NOT_RUN", "BOOTSTRAP_FAILED", "BOOTSTRAP_SUCCEEDED")
MODES = ("official_all", "harness_only")
ATTRIBUTIONS = ("BASE_IMAGE", "OFFICIAL_FULL_BOOTSTRAP", "NON_OFFICIAL_PARTIAL_BOOTSTRAP", "NOT_INSTALLED", "UNKNOWN")
STRONG = ("OFFICIAL_FULL_BOOTSTRAP", "NON_OFFICIAL_PARTIAL_BOOTSTRAP")
SOURCE_STATUSES = ("SOURCE_CAPTURED", "SOURCE_NOT_CAPTURED", "SOURCE_RECORD_UNAVAILABLE")
METADATA_ALLOWLIST = ("METADATA", "WHEEL", "INSTALLER", "REQUESTED", "top_level.txt", "entry_points.txt")
ROW_CATEGORIES = ("ok", "excluded_volatile", "console_script", "malformed", "duplicate", "outside_root", "unsafe",
                  "missing", "unreadable", "hash_mismatch", "size_mismatch", "ambiguous_ownership")
ALLOWED_ROW = ("ok", "excluded_volatile", "console_script")
VOLATILE = ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json")
FORBIDDEN_CLAIM_KEYS = ("evidence_strength", "capture_state", "record_hash_match")
DIRECT_URL_KEYS = {"original_sha256", "redacted", "url_sanitized", "kind", "archive_info", "dir_info", "vcs_info",
                   "parse_error", "dropped_fields"}
CHANGES = ("INSTALLED", "UNCHANGED", "REPLACED", "UNDETERMINED", "REMOVED", "ABSENT")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SCRIPT_RE = re.compile(r"^(?:\.\./)+bin/([A-Za-z0-9_.\-]+)$")
# credential detection (literal, independent of the core sanitizer)
SENSITIVE_KEY = re.compile(r"^(?:[a-z0-9]+_)*(?:api_?key|token|access_token|refresh_token|id_token|password|passwd|pwd|"
                           r"secret|client_secret|authorization|auth|signature|sig|credentials?|cookie|session(?:_id)?|"
                           r"private_key|bearer)$")
_K = r"(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|password|passwd|pwd|" \
     r"client[_-]?secret|secret|authorization|auth|signature|sig|credentials?|cookie|session(?:[_-]?id)?|private[_-]?key|bearer)"
CREDENTIAL_RES = [re.compile(p) for p in (
    r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s/\"'@]+@",                                 # URL userinfo
    r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]*[?#]",                              # URL query/fragment
    r"(?i)(?<![A-Za-z0-9_\-])" + _K + r"\s*[=:]\s*(?!<REDACTED>)[^\s&\"',;<]+",  # key=value / key: value
    r"(?i)\"" + _K + r"\"\s*:\s*\"(?!<REDACTED>\")[^\"]+\"",                    # "key": "value" inside text
    r"(?<![A-Za-z0-9_\-])(?:ghp_|gh[ousr]_)[A-Za-z0-9]{16,}", r"(?<![A-Za-z0-9_\-])github_pat_[A-Za-z0-9_]{16,}",
    r"(?<![A-Za-z0-9_\-])hf_[A-Za-z0-9]{16,}", r"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{16,}",
    r"(?<![A-Za-z0-9_\-])(?:AKIA|ASIA)[0-9A-Z]{16}", r"(?<![A-Za-z0-9_\-])xox[abprs]-[A-Za-z0-9\-]{10,}",
    r"(?i)\bbearer\s+(?!<REDACTED>)[A-Za-z0-9._~+/=\-]{8,}",
)]
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 ** 3
MAX_MEMBERS = 200_000


class CaptureError(RuntimeError):
    code = "CAPTURE_INVALID"


class CaptureContradiction(CaptureError):
    code = "CAPTURE_CONTRADICTION"


class EvidenceConflict(CaptureError):
    code = "EVIDENCE_CONFLICT"


def _need(cond, msg):
    if not cond:
        raise CaptureError(msg)


def _consistent(cond, msg):
    if not cond:
        raise CaptureContradiction(f"CAPTURE_CONTRADICTION: {msg}")


def _norm(name) -> str:
    return re.sub(r"[-_.]+", "-", name or "").lower()


def _int0(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _safe_rel(p) -> bool:
    return (isinstance(p, str) and bool(p) and not p.startswith("/") and "\\" not in p and "\0" not in p
            and not re.match(r"^[A-Za-z]:", p) and all(x not in ("", ".", "..") for x in p.split("/")))


def _ckey(p: str) -> str:
    return unicodedata.normalize("NFC", posixpath.normpath(p)).casefold()


def _decode_hash(v: str) -> str | None:
    """'' -> None; 'sha256=<urlsafe b64, no padding>' -> hex; anything else -> ValueError."""
    if v == "":
        return None
    if not isinstance(v, str) or not v.startswith("sha256="):
        raise ValueError("unsupported hash")
    b = v[7:]
    raw = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != b:
        raise ValueError("bad encoding")
    return raw.hex()


def _parse_record(text: str) -> list[tuple[str, str, str]]:
    rows = []
    for f in csv.reader(io.StringIO(text)):
        if f:
            rows.append((f[0], f[1] if len(f) > 1 else "", f[2] if len(f) > 2 else ""))
    return rows


def _link_name(name: str) -> str:
    return name.replace("cu128", "+cu128") if ("cu128" in name and "+" not in name) else name


# ---- archive + integrity -------------------------------------------------------------------------

def read_archive(zip_path: Path) -> dict:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise CaptureError("not a readable ZIP") from None
    with zf:
        infos = zf.infolist()
        _need(len(infos) <= MAX_MEMBERS, "too many members")
        seen, data, total = set(), {}, 0
        for i in infos:
            n = i.filename
            _need(not i.is_dir() and not stat.S_ISLNK(i.external_attr >> 16), "directory or symlink entry")
            _need(_safe_rel(n) and posixpath.normpath(n) == n and n.startswith(PREFIX) and n != PREFIX, "invalid member path")
            _need(_ckey(n) not in seen, "duplicate member after normalization")
            seen.add(_ckey(n))
            _need(i.file_size <= MAX_MEMBER_BYTES, "member too large")
            total += i.file_size
            _need(total <= MAX_TOTAL_BYTES, "archive too large")
            rel = n[len(PREFIX):]
            top = rel.split("/")[0]
            _need(rel in REQUIRED_FILES or rel == "bootstrap_plan.json" or (top in TOP_DIRS and "/" in rel), "unexpected member")
            data[rel] = zf.read(i)
            _need(len(data[rel]) == i.file_size, "member size mismatch")
    _need(all(f in data for f in REQUIRED_FILES), "missing required metadata files")
    hashes = json.loads(data["hashes.json"].decode("utf-8"))
    _need(isinstance(hashes, dict) and hashes.get("algorithm") == "sha256" and isinstance(hashes.get("files"), dict), "hashes.json malformed")
    _need(set(hashes["files"]) == set(data) - {"hashes.json"}, "hashes.json coverage mismatch")
    sha = {rel: hashlib.sha256(b).hexdigest() for rel, b in data.items()}
    for rel, meta in hashes["files"].items():
        _need(isinstance(meta, dict) and meta.get("sha256") == sha[rel] and meta.get("bytes") == len(data[rel]), "member hash/size mismatch")
    docs = {}
    for rel, b in data.items():
        if rel.endswith(".json") and not rel.startswith("sources/"):
            docs[rel] = json.loads(b.decode("utf-8"))
    return {"data": data, "sha": sha, "docs": docs}


# ---- credentials (defense in depth) ---------------------------------------------------------------

def _scan_value(v, where: str) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(k, str) and "/" not in k and "." not in k and SENSITIVE_KEY.match(k.strip().lower().replace("-", "_")):
                _need(x in (None, "", "<REDACTED>") or isinstance(x, bool), f"credential-bearing field in {where}")
            else:
                _scan_value(x, where)
    elif isinstance(v, list):
        for x in v:
            _scan_value(x, where)
    elif isinstance(v, str):
        _need(not any(r.search(v) for r in CREDENTIAL_RES), f"credential-like content in {where}")


def scan_credentials(arch: dict) -> None:
    for rel, b in arch["data"].items():
        if rel.startswith("sources/"):
            continue  # third-party source code is evidence and is exempt (it is never parsed as a claim)
        top = rel.split("/")[0]
        if rel in arch["docs"]:
            _scan_value(arch["docs"][rel], top)
            for k in FORBIDDEN_CLAIM_KEYS:
                _consistent(f'"{k}"' not in b.decode("utf-8", "replace"), f"archive carries a promotion claim key {k!r}")
        else:
            _need(not any(r.search(b.decode("utf-8", "replace")) for r in CREDENTIAL_RES), f"credential-like content in {top}")


# ---- recomputation primitives --------------------------------------------------------------------

def recompute_inventory(inv: dict, where: str, declared_scripts: set[str] | None) -> dict:
    """Re-derive the fingerprint from the detailed rows; reject rows whose label contradicts their own evidence."""
    _need(isinstance(inv, dict) and isinstance(inv.get("rows"), list) and isinstance(inv.get("record_present"), bool)
          and isinstance(inv.get("record_redacted"), bool), f"{where}: inventory malformed")
    keys, counted, cats = set(), [], {}
    for r in inv["rows"]:
        _need(isinstance(r, dict) and isinstance(r.get("path"), str) and r.get("category") in ROW_CATEGORIES
              and isinstance(r.get("record_hash"), str) and isinstance(r.get("record_size"), str), f"{where}: bad inventory row")
        c, p = r["category"], r["path"]
        cats[c] = cats.get(c, 0) + 1
        k = _ckey(p)
        if c == "duplicate":
            _consistent(k in keys, f"{where}: 'duplicate' row without an earlier occurrence")
            continue
        if c != "malformed":
            _consistent(k not in keys, f"{where}: duplicate RECORD path not labelled duplicate")
            keys.add(k)
        if c in ("ok", "console_script", "hash_mismatch", "size_mismatch"):
            _need(_int0(r.get("bytes")) and HEX64.match(r.get("sha256") or ""), f"{where}: row without content evidence")
        if c in ("ok", "console_script"):
            try:
                want = _decode_hash(r["record_hash"])
            except ValueError:
                raise CaptureContradiction(f"CAPTURE_CONTRADICTION: {where}: malformed RECORD hash on a row labelled {c}") from None
            _consistent(want is None or want == r["sha256"], f"{where}: RECORD hash disagrees with content on a row labelled {c}")
            _consistent(not r["record_size"] or (r["record_size"].isdigit() and int(r["record_size"]) == r["bytes"]),
                        f"{where}: RECORD size disagrees on a row labelled {c}")
            counted.append((p, r["bytes"], r["sha256"]))
        if c == "ok":
            _consistent(_safe_rel(p) and not posixpath.normpath(p).startswith(".."), f"{where}: unsafe path labelled ok")
        if c == "console_script":
            m = SCRIPT_RE.match(p)
            _consistent(bool(m) and (declared_scripts is None or m.group(1) in declared_scripts),
                        f"{where}: console_script row is not a declared script under bin/")
        if posixpath.normpath(p).startswith("../"):
            _consistent(c in ("outside_root", "console_script", "malformed", "duplicate"), f"{where}: outside-root row labelled {c}")
    counted.sort()
    complete = inv["record_present"] and not inv["record_redacted"] and all(r["category"] in ALLOWED_ROW for r in inv["rows"])
    return {"sha256": hashlib.sha256("".join(f"{p}\t{b}\t{s}\n" for p, b, s in counted).encode()).hexdigest() if inv["record_present"] else None,
            "complete": bool(complete), "record_present": inv["record_present"], "files": len(counted),
            "bytes": sum(b for _, b, _ in counted), "categories": dict(sorted(cats.items()))}


def recompute_wheel_match(manifest: dict, post_inv: dict) -> dict:
    installed = {r["path"]: r for r in post_inv["rows"] if r["category"] == "ok"}
    wanted = {}
    for m in manifest["members"]:
        parts = m["name"].split("/")
        if parts[0].endswith(".data"):
            if len(parts) > 2 and parts[1] in ("purelib", "platlib"):
                wanted["/".join(parts[2:])] = m
        elif not (len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] in VOLATILE):
            wanted[m["name"]] = m
    return {"matched": sum(1 for p, m in wanted.items() if p in installed and installed[p]["sha256"] == m["sha256"]),
            "mismatched": sum(1 for p, m in wanted.items() if p in installed and installed[p]["sha256"] != m["sha256"]),
            "absent_in_install": sum(1 for p in wanted if p not in installed),
            "extra_in_install": sum(1 for p in installed if p not in wanted)}


def verify_manifest(manifest: dict, where: str) -> None:
    _need(isinstance(manifest, dict) and isinstance(manifest.get("members"), list) and manifest["members"], f"{where}: manifest malformed")
    names = [m.get("name") for m in manifest["members"] if isinstance(m, dict)]
    _need(len(names) == len(manifest["members"]) and len(set(map(_ckey, names))) == len(names), f"{where}: duplicate wheel members")
    for m in manifest["members"]:
        _need(_safe_rel(m["name"]) and _int0(m.get("bytes")) and HEX64.match(m.get("sha256") or ""), f"{where}: bad wheel member")
    _consistent(manifest.get("record_redacted") is False and isinstance(manifest.get("record_text"), str), f"{where}: wheel RECORD unavailable")
    rec = {p: (h, s) for p, h, s in _parse_record(manifest["record_text"])}
    for m in manifest["members"]:
        if m["name"].endswith(".dist-info/RECORD") and m["name"].count("/") == 1:
            continue
        _consistent(m["name"] in rec, f"{where}: wheel member not in the wheel's RECORD")
        h, s = rec[m["name"]]
        try:
            _consistent(_decode_hash(h) == m["sha256"] and (not s or s == str(m["bytes"])), f"{where}: wheel member disagrees with its RECORD")
        except ValueError:
            raise CaptureContradiction(f"CAPTURE_CONTRADICTION: {where}: malformed hash in the wheel RECORD") from None


def verify_source_claims(t: str, ns: str, claims: list, data: dict, sha: dict, rec_rows: list, post_ok: dict) -> bool:
    """Every claimed source file: canonical unique path, exactly one owning RECORD row, actual bytes == claim ==
    RECORD hash/size == installed file. Returns whether every claim had a RECORD hash (record_hash_match, computed)."""
    by_path, keys = {}, set()
    for p, h, z in rec_rows:
        _consistent(_ckey(p) not in keys, f"{t}: duplicate RECORD rows")
        keys.add(_ckey(p))
        by_path[p] = (h, z)
    seen, all_match = set(), True
    for c in claims:
        _need(isinstance(c, dict) and _safe_rel(c.get("path")) and not c["path"].split("/")[0].endswith((".dist-info", ".data")),
              f"{t}: bad source claim path")
        _need(_ckey(c["path"]) not in seen, f"{t}: duplicate source claim")
        seen.add(_ckey(c["path"]))
        member = f"sources/{ns}/{c['path']}"
        _need(member in data, f"{t}: claimed source file not in the archive")
        actual = data[member]
        _need(_int0(c.get("bytes")) and c["bytes"] == len(actual) and c.get("sha256") == sha[member], f"{t}: source claim size/sha differs from bytes")
        _need(c["path"] in by_path, f"{t}: source file not owned by the distribution's RECORD")
        h, z = by_path[c["path"]]
        try:
            want = _decode_hash(h)
        except ValueError:
            raise CaptureContradiction(f"CAPTURE_CONTRADICTION: {t}: malformed RECORD hash for a source file") from None
        _consistent(want is None or want == sha[member], f"{t}: captured source bytes contradict RECORD")
        _consistent(not z or z == str(len(actual)), f"{t}: captured source size contradicts RECORD")
        _consistent(c["path"] in post_ok and post_ok[c["path"]]["sha256"] == sha[member], f"{t}: source bytes differ from the installed file")
        all_match &= want is not None
    return all_match


# ---- semantics -----------------------------------------------------------------------------------

def validate(arch: dict) -> dict:
    data, sha, docs = arch["data"], arch["sha"], arch["docs"]
    scan_credentials(arch)
    env, pk, sf, wh = (docs["environment.json"], docs["packages.json"], docs["source_files.json"], docs["wheelhouse.json"])
    for d in (env, pk, sf, wh, docs["source_index.json"], docs["installed_distributions.json"]):
        _need(isinstance(d, dict), "JSON document must be an object")
    _need(env.get("capture_schema") == CAPTURE_SCHEMA and pk.get("capture_schema") == CAPTURE_SCHEMA, "unsupported capture schema")
    _need(isinstance(env.get("capture_utc"), str) and re.match(r"^\d{8}T\d{6}Z$", env["capture_utc"]), "bad capture_utc")
    _need(isinstance(env.get("run_context"), str) and isinstance(env.get("config_overrides"), list), "bad run context")
    boot = env.get("bootstrap")
    _need(isinstance(boot, dict) and boot.get("state") in BOOT_STATES and boot.get("mode") in MODES, "bad bootstrap record")
    for k in ("skip_install", "dry_run", "pip_executed", "pip_simulated"):
        _need(isinstance(boot.get(k), bool), f"bootstrap.{k} must be a boolean")
    state, mode, plan = boot["state"], boot["mode"], docs.get("bootstrap_plan.json")
    # ---- P1-4: bootstrap consistency (contradictions fail, they are not downgraded)
    _consistent(pk.get("bootstrap_state") == state and pk.get("bootstrap_mode") == mode, "packages/environment bootstrap mismatch")
    _consistent(pk.get("scorer_relationship") == "SCORER_ONLY_UNKNOWN", "scorer relationship must be SCORER_ONLY_UNKNOWN")
    _consistent(boot["dry_run"] == boot["skip_install"], "dry_run and skip_install disagree")
    _consistent(not boot["skip_install"] or state == "BOOTSTRAP_NOT_RUN", "skip_install with a bootstrap result")
    _consistent(boot["pip_executed"] == (state != "BOOTSTRAP_NOT_RUN"), "pip_executed contradicts the bootstrap state")
    _consistent(boot["pip_executed"] or (boot.get("pip") is None and plan is None and boot.get("plan") is None),
                "pip result or plan recorded although pip never ran")
    _consistent(not boot["pip_simulated"] or "pip_runner" in env["config_overrides"], "simulated pip not listed as an override")
    _consistent(env["run_context"] != "kaggle_kernel" or (not env["config_overrides"] and not boot["pip_simulated"]),
                "kaggle_kernel run context with overrides")
    pip_rc = None
    if state != "BOOTSTRAP_NOT_RUN":
        _consistent(isinstance(plan, dict) and plan == boot.get("plan"), "bootstrap plan missing or differs from the recorded plan")
        _consistent(isinstance(boot.get("pip"), dict) and "returncode" in boot["pip"], "pip result missing")
        pip_rc = boot["pip"]["returncode"]
        _consistent((pip_rc == 0) == (state == "BOOTSTRAP_SUCCEEDED"), "pip returncode contradicts the bootstrap state")
        _consistent(plan.get("mode") == mode, "plan mode differs")
        inv_whl = sorted(i["filename"] for i in wh.get("inventory", []) if isinstance(i, dict) and str(i.get("filename", "")).endswith(".whl"))
        expect = [w for w in inv_whl if "cutlass" not in w.lower() and (mode == "official_all" or w.startswith(HARNESS_PREFIXES))]
        sel = plan.get("selected")
        _consistent(isinstance(sel, list) and [s.get("wheel") for s in sel] == expect, "planned wheels differ from the wheelhouse inventory selection")
        inv_by = {i["filename"]: i for i in wh["inventory"]}
        for s in sel:
            i = inv_by[s["wheel"]]
            _consistent(s.get("sha256") == i.get("sha256") and s.get("bytes") == i.get("bytes") and s.get("metadata") == i.get("metadata")
                        and s.get("link_name") == _link_name(s["wheel"]), "planned wheel does not match the inventory")
        tmp = plan.get("tmp_wheelhouse")
        cmd = plan.get("command")
        _consistent(isinstance(cmd, list) and isinstance(tmp, str) and cmd[0] == (env.get("post_bootstrap") or {}).get("sys_executable")
                    and cmd[1:7] == ["-m", "pip", "install", "-q", "--no-deps", "--force-reinstall"]
                    and cmd[7:] == sorted(posixpath.join(tmp, s["link_name"]) for s in sel) and sel,
                    "recorded command is not the official invocation")
    else:
        _consistent("bootstrap_plan.json" not in data, "NOT_RUN capture carries a bootstrap plan")

    targets = pk.get("targets")
    _need(isinstance(targets, dict) and set(targets) == set(TARGET_SCHEMA), "targets must be exactly the 8 schema targets")
    facts, claimed = {}, {"sources": set(), "metadata": set(), "records": set(), "inventories": set(), "wheels": set()}
    post_owned = {}
    for t, ns in TARGET_SCHEMA.items():
        e = targets[t]
        _need(isinstance(e, dict) and e.get("distribution_query") == t and e.get("namespace") == ns, f"{t}: identity mismatch")
        _consistent(e.get("scorer_relationship") == "SCORER_ONLY_UNKNOWN" and e.get("bootstrap_state") == state, f"{t}: state mismatch")
        _need(e.get("pre_state") in ("PRESENT", "ABSENT") and e.get("post_state") in ("PRESENT", "ABSENT"), f"{t}: bad states")
        _need(e.get("bootstrap_attribution") in ATTRIBUTIONS and e.get("change") in CHANGES and e.get("source_status") in SOURCE_STATUSES,
              f"{t}: bad vocabulary")
        f = facts[t] = {"post_complete": False, "wheel_strong": False, "source_verified": False, "record_verified": False,
                        "metadata_verified": False, "all_source_record_hashes_match": False}
        attr = e["bootstrap_attribution"]
        # inventories and fingerprints
        fps = {}
        for when in ("pre", "post"):
            path = f"inventories/{ns}.{when}.json"
            present = e[f"{when}_state"] == "PRESENT"
            _need((path in data) == present, f"{t}: {when} inventory presence mismatch")
            if present:
                claimed["inventories"].add(path)
                scripts = None
                if when == "post":
                    ep = data.get(f"metadata/{ns}/entry_points.txt")
                    scripts = _declared(ep.decode("utf-8", "replace")) if ep is not None else set()
                fps[when] = recompute_inventory(docs[path], f"{t} {when}", scripts)
                _consistent(e.get(f"{when}_fingerprint") == fps[when], f"{t}: {when} fingerprint claim differs from the recomputed one")
            else:
                _need(e.get(f"{when}_fingerprint") is None, f"{t}: {when} fingerprint without {when} state")
        if e["post_state"] == "ABSENT":
            _consistent(e.get("distribution_name") is None and e.get("version") is None and attr == "NOT_INSTALLED"
                        and e["source_status"] != "SOURCE_CAPTURED" and e.get("metadata_status") == "METADATA_UNAVAILABLE"
                        and e.get("record_status") == "RECORD_UNAVAILABLE" and e.get("install_status") == "PACKAGE_NOT_INSTALLED"
                        and e.get("wheel_evidence") is None, f"{t}: absent package carries evidence")
            _consistent(e["change"] == ("REMOVED" if e["pre_state"] == "PRESENT" else "ABSENT"), f"{t}: change mismatch")
            continue
        _need(isinstance(e.get("distribution_name"), str) and _norm(e["distribution_name"]) == _norm(t), f"{t}: distribution name mismatch")
        _need(isinstance(e.get("version"), str) and e["version"], f"{t}: version missing")
        post_inv = docs[f"inventories/{ns}.post.json"]
        f["post_complete"] = fps["post"]["complete"]
        expected_change = ("INSTALLED" if e["pre_state"] == "ABSENT" else
                           ("UNCHANGED" if fps["pre"]["sha256"] == fps["post"]["sha256"] else "REPLACED")
                           if fps["pre"]["complete"] and fps["post"]["complete"] else "UNDETERMINED")
        _consistent(e["change"] == expected_change, f"{t}: change claim differs from recomputed {expected_change}")
        for r in post_inv["rows"]:
            if r["category"] == "ok":
                post_owned.setdefault((e.get("location"), _ckey(r["path"])), set()).add(t)
        # metadata + RECORD references
        mfs = e.get("metadata_files") or {}
        _need(isinstance(mfs, dict), f"{t}: metadata_files malformed")
        for fname, m in mfs.items():
            path = f"metadata/{ns}/{fname}"
            _need(fname in METADATA_ALLOWLIST and isinstance(m, dict) and m.get("archive_path") == path and path in data
                  and m.get("captured_sha256") == sha[path] and HEX64.match(m.get("original_sha256") or "")
                  and isinstance(m.get("redacted"), bool), f"{t}: metadata reference invalid")
            claimed["metadata"].add(path)
        _need(e.get("metadata_status") in ("METADATA_CAPTURED", "METADATA_UNAVAILABLE")
              and (e["metadata_status"] == "METADATA_CAPTURED") == ("METADATA" in mfs), f"{t}: metadata status mismatch")
        f["metadata_verified"] = "METADATA" in mfs and not mfs["METADATA"]["redacted"]
        rec = e.get("record")
        rpath = f"records/{ns}.RECORD"
        if e.get("record_status") == "RECORD_CAPTURED":
            _need(isinstance(rec, dict) and rec.get("archive_path") == rpath and rpath in data and rec.get("captured_sha256") == sha[rpath]
                  and isinstance(rec.get("redacted"), bool), f"{t}: RECORD reference invalid")
            claimed["records"].add(rpath)
            _consistent(rec["redacted"] == post_inv["record_redacted"], f"{t}: RECORD redaction flags disagree")
            if not rec["redacted"]:
                rows = _parse_record(data[rpath].decode("utf-8"))
                _consistent(rows == [(r["path"], r["record_hash"], r["record_size"]) for r in post_inv["rows"]],
                            f"{t}: post inventory does not match the captured RECORD")
                f["record_verified"] = True
        else:
            _need(e.get("record_status") == "RECORD_UNAVAILABLE" and rec is None and rpath not in data, f"{t}: RECORD status mismatch")
            _consistent(not post_inv["record_present"], f"{t}: RECORD present in inventory but not captured")
        du = e.get("direct_url")
        if du is not None:
            _need(isinstance(du, dict) and set(du) <= DIRECT_URL_KEYS and isinstance(du.get("redacted"), bool), f"{t}: direct_url summary malformed")
            if du.get("url_sanitized") is not None:
                u = urlsplit(du["url_sanitized"])
                _need("@" not in u.netloc and not u.query and not u.fragment, f"{t}: direct_url not sanitized")
        # ---- P1-3: wheel identity and install match, recomputed
        _consistent((e.get("install_status") == "PACKAGE_BOOTSTRAPPED_FROM_OFFICIAL_NOTEBOOK") == (attr == "OFFICIAL_FULL_BOOTSTRAP")
                    and e.get("install_status") in ("PACKAGE_INSTALLED", "PACKAGE_BOOTSTRAPPED_FROM_OFFICIAL_NOTEBOOK"), f"{t}: install status mismatch")
        if state == "BOOTSTRAP_FAILED":
            _consistent(attr == "UNKNOWN", f"{t}: failed bootstrap cannot attribute packages")
        elif state == "BOOTSTRAP_NOT_RUN":
            _consistent(attr in ("BASE_IMAGE", "UNKNOWN"), f"{t}: no bootstrap ran but {attr} claimed")
        if attr in STRONG:
            _consistent(state == "BOOTSTRAP_SUCCEEDED" and mode == ("official_all" if attr == "OFFICIAL_FULL_BOOTSTRAP" else "harness_only"),
                        f"{t}: {attr} contradicts bootstrap state/mode")
        we = e.get("wheel_evidence")
        if we is not None:
            _need(isinstance(we, dict) and state == "BOOTSTRAP_SUCCEEDED", f"{t}: wheel evidence outside a successful bootstrap")
            w = we.get("wheel")
            planned = [s for s in plan["selected"] if s.get("wheel") == w]
            same_dist = [s for s in plan["selected"] if _norm((s.get("metadata") or {}).get("name")) == _norm(t)]
            _consistent(len(planned) == 1 and len(same_dist) == 1 and same_dist[0] is planned[0], f"{t}: wheel is not the one planned wheel of this distribution")
            s = planned[0]
            mpath = f"wheels/{w}.json"
            _need(mpath in docs, f"{t}: wheel manifest missing")
            claimed["wheels"].add(mpath)
            man = docs[mpath]
            verify_manifest(man, f"{t} wheel")
            link = _link_name(w)
            fname_name, fname_ver = (link[:-4].split("-") + ["", ""])[:2]
            _consistent(man.get("filename") == w and man.get("sha256") == s["sha256"] == we.get("wheel_sha256")
                        and man.get("bytes") == s["bytes"] == we.get("wheel_bytes") and HEX64.match(man.get("sha256") or ""),
                        f"{t}: wheel identity (filename/sha/size) inconsistent")
            mmeta = man.get("metadata") or {}
            _consistent(_norm(mmeta.get("name")) == _norm(t) == _norm(fname_name) == _norm(we.get("wheel_name"))
                        and mmeta.get("version") == e["version"] == fname_ver == we.get("wheel_version") and we.get("link_name") == link,
                        f"{t}: wheel name/version does not match the distribution")
            counts = recompute_wheel_match(man, post_inv)
            _consistent(we.get("content_check") == counts, f"{t}: claimed wheel/install counters differ from recomputed {counts}")
            f["wheel_strong"] = (counts["matched"] > 0 and counts["mismatched"] == 0 and counts["absent_in_install"] == 0
                                 and counts["extra_in_install"] == 0 and fps["post"]["complete"])
        if attr in STRONG:
            _consistent(we is not None and f["wheel_strong"], f"{t}: {attr} claimed but recomputed wheel/install evidence is not complete")
        # ---- P1-2: source ownership, recomputed from RECORD and bytes
        if e["source_status"] == "SOURCE_CAPTURED":
            _consistent(t in HARNESS and state != "BOOTSTRAP_FAILED", f"{t}: source capture not allowed here")
            _consistent(f["record_verified"] and f["metadata_verified"], f"{t}: source without verified metadata and RECORD")
            s = sf.get(t)
            _need(isinstance(s, dict) and s.get("archive_dir") == f"sources/{ns}" and isinstance(s.get("files"), list) and s["files"],
                  f"{t}: source files must live in sources/{ns}/")
            post_ok = {r["path"]: r for r in post_inv["rows"] if r["category"] == "ok"}
            all_match = verify_source_claims(t, ns, s["files"], data, sha, _parse_record(data[rpath].decode("utf-8")), post_ok)
            claimed["sources"].update(f"sources/{ns}/{c['path']}" for c in s["files"])
            _need(e.get("source_file_count") == len(s["files"]), f"{t}: source count mismatch")
            tree = hashlib.sha256("".join(f"{c['path']}\t{c['sha256']}\n" for c in sorted(s["files"], key=lambda c: c["path"])).encode()).hexdigest()
            _need(e.get("source_tree_sha256") == tree, f"{t}: source tree hash mismatch")
            f["source_verified"], f["all_source_record_hashes_match"] = True, all_match
        else:
            _need(t not in sf, f"{t}: source files listed without SOURCE_CAPTURED")
    for key, owners in post_owned.items():
        _consistent(len(owners) == 1, f"ownership ambiguity: {sorted(owners)} own the same file")
    _need(set(sf) <= set(TARGET_SCHEMA), "source_files lists unknown distributions")
    for top in ("sources", "metadata", "records", "inventories", "wheels"):
        actual = {r for r in data if r.startswith(top + "/")}
        _need(actual == claimed[top], f"orphaned or cross-distribution {top} members")
    return {"env": env, "packages": pk, "boot": boot, "pip_rc": pip_rc, "facts": facts, "index": docs["source_index.json"]}


def _declared(text: str) -> set[str]:
    cp = configparser.ConfigParser(delimiters=("=",), interpolation=None)
    cp.optionxform = str
    try:
        cp.read_string(text)
    except configparser.Error:
        return set()
    return {k for sec in ("console_scripts", "gui_scripts") if cp.has_section(sec) for k in cp[sec]}


def derive_strength(v: dict, attest: dict) -> tuple[str, list[str]]:
    """VERSION-SPECIFIC only from independently verified primitives; never from an archive claim."""
    env, boot, targets, facts = v["env"], v["boot"], v["packages"]["targets"], v["facts"]
    checks = [
        (boot["state"] == "BOOTSTRAP_SUCCEEDED", f"bootstrap state {boot['state']}"),
        (boot["mode"] == "official_all", "NON_OFFICIAL_PARTIAL_BOOTSTRAP (harness_only)"),
        (not boot["dry_run"] and not boot["skip_install"], "dry run / skip_install"),
        (boot["pip_executed"] and v["pip_rc"] == 0 and not boot["pip_simulated"], "official pip not executed successfully"),
        (env["run_context"] == "kaggle_kernel" and not env["config_overrides"], f"run context {env['run_context']} / overrides {env['config_overrides']}"),
        (bool(attest.get("notebook_version")) and bool(attest.get("wheelhouse_dataset_version")), "missing human attestation"),
        (v["packages"]["scorer_relationship"] == "SCORER_ONLY_UNKNOWN", "scorer relationship"),
    ]
    for t in HARNESS:
        f, e = facts[t], targets[t]
        checks += [
            (e["bootstrap_attribution"] == "OFFICIAL_FULL_BOOTSTRAP", f"{t}: attribution {e['bootstrap_attribution']}"),
            (f["wheel_strong"], f"{t}: wheel identity/install match not complete"),
            (f["post_complete"], f"{t}: post fingerprint incomplete"),
            (e["source_status"] == "SOURCE_CAPTURED" and f["source_verified"], f"{t}: source not captured/verified"),
            (f["all_source_record_hashes_match"], f"{t}: not every source file is hash-verified against RECORD"),
            (f["metadata_verified"], f"{t}: METADATA not verified"),
            (f["record_verified"], f"{t}: RECORD not verified"),
        ]
    reasons = [msg for ok, msg in checks if not ok]
    return ("VERSION-SPECIFIC" if not reasons else "NONE"), reasons


def summarize(v: dict, zip_sha: str, zip_bytes: int, hashes_sha: str, attest: dict) -> dict:
    strength, reasons = derive_strength(v, attest)
    env, boot, pk = v["env"], v["boot"], v["packages"]
    targets = {}
    for t, e in sorted(pk["targets"].items()):
        targets[t] = {k: e.get(k) for k in ("distribution_name", "version", "pre_state", "post_state", "change",
                                            "bootstrap_attribution", "source_status", "metadata_status", "record_status",
                                            "install_status", "source_file_count", "source_tree_sha256", "scorer_relationship")}
        targets[t]["post_fingerprint_sha256"] = (e.get("post_fingerprint") or {}).get("sha256")
        targets[t]["wheel_sha256"] = (e.get("wheel_evidence") or {}).get("wheel_sha256")
        targets[t]["verified"] = v["facts"][t]
    return {
        "summary_schema": SUMMARY_SCHEMA, "capture_id": f"{env['capture_utc']}_{zip_sha[:12]}", "capture_utc": env["capture_utc"],
        "archive_sha256": zip_sha, "archive_bytes": zip_bytes, "hashes_json_sha256": hashes_sha,
        "capture_state": "CAPTURE_VALIDATED" if boot["state"] == "BOOTSTRAP_SUCCEEDED" else boot["state"],
        "bootstrap_state": boot["state"], "bootstrap_mode": boot["mode"],
        "bootstrap_label": "OFFICIAL_FULL_BOOTSTRAP" if boot["mode"] == "official_all" else "NON_OFFICIAL_PARTIAL_BOOTSTRAP",
        "run_context": env["run_context"], "config_overrides": env["config_overrides"],
        "attestation": {"notebook_version": attest.get("notebook_version"), "wheelhouse_dataset_version": attest.get("wheelhouse_dataset_version")},
        "evidence_strength": strength, "ineligibility_reasons": reasons,
        "scorer_relationship": "SCORER_ONLY_UNKNOWN",
        "h23_matrix_status": "HOST-UNKNOWN (updated by hand only; VERSION-SPECIFIC evidence may justify an update)",
        "wheelhouse_fingerprint_sha256": boot.get("wheelhouse_fingerprint_sha256"), "pip_returncode": v["pip_rc"],
        "targets": targets,
        "source_index": {k: v["index"].get(k) for k in ("files_scanned", "hit_counts", "scorer_entrypoints")},
    }


# ---- race-safe destinations ------------------------------------------------------------------------

def forbidden_roots(guard: WriteGuard) -> list[Path]:
    roots = {REPO_ROOT, *guard.protected, Path(sys.prefix), Path(sys.base_prefix), Path(sys.exec_prefix)}
    for getter in (site.getsitepackages, lambda: [site.getusersitepackages()]):
        try:
            roots.update(Path(p) for p in getter())
        except Exception:  # noqa: BLE001
            pass
    return sorted({p.resolve() for p in roots if str(p)})


def _check_root(root: Path, forbidden: list[Path]) -> None:
    for f in forbidden:
        if root == f or f in root.parents:
            raise UnsafeOutputError(f"evidence root {root} is inside a protected location ({f})")


def extract(root: Path, cid: str, files: dict[str, bytes], forbidden: list[Path]) -> tuple[Path, str]:
    """All writes are relative to directory descriptors opened below the canonical, pre-checked root."""
    root = root.resolve()
    _check_root(root, forbidden)
    root.mkdir(parents=True, exist_ok=True)
    root_fd = safe_fs.open_root(root)
    try:
        _check_root(Path(root).resolve(), forbidden)
        h23_fd = safe_fs.open_dir(root_fd, "h23", create=True)
        try:
            k = safe_fs.kind(h23_fd, cid)
            if k is not None:
                if k != "dir":
                    raise UnsafeOutputError(f"capture path exists and is a {k}")
                fd = safe_fs.open_dir(h23_fd, cid)
                try:
                    if safe_fs.read_tree(fd) != files:
                        raise EvidenceConflict("EVIDENCE_CONFLICT: evidence directory exists with different content")
                finally:
                    os.close(fd)
                return root / "h23" / cid, "already_imported"
            staging = f".staging-{cid}-{secrets.token_hex(8)}"
            st_fd = safe_fs.open_dir(h23_fd, staging, exclusive=True)
            try:
                safe_fs.write_tree(st_fd, files)
            except BaseException:
                os.close(st_fd)
                safe_fs.remove_tree(h23_fd, staging)
                raise
            os.close(st_fd)
            if safe_fs.kind(h23_fd, cid) is not None:
                safe_fs.remove_tree(h23_fd, staging)
                raise EvidenceConflict("EVIDENCE_CONFLICT: capture directory appeared during import")
            safe_fs.rename(h23_fd, staging, cid)  # same directory fd: no path is re-resolved
            return root / "h23" / cid, "imported"
        finally:
            os.close(h23_fd)
    finally:
        os.close(root_fd)


def write_summary(summary_dir: Path, name: str, content: bytes, guard: WriteGuard) -> str:
    guard.check(summary_dir / name)
    summary_dir.mkdir(parents=True, exist_ok=True)
    fd = safe_fs.open_root(guard.check(summary_dir))
    try:
        k = safe_fs.kind(fd, name)
        if k is None:
            safe_fs.write_file(fd, name, content)
            return "created"
        if k != "file":
            raise UnsafeOutputError(f"summary path is a {k}")
        if safe_fs.read_file(fd, name) == content:
            return "identical"
        raise EvidenceConflict(f"EVIDENCE_CONFLICT: {name} exists with different content; refusing to overwrite")
    finally:
        os.close(fd)


# ---- orchestration --------------------------------------------------------------------------------

def import_capture(zip_path: Path, *, harness_root: Path, guard: WriteGuard, summary_dir: Path = SUMMARY_DIR,
                   attestation: dict | None = None) -> dict:
    try:
        return _import(Path(zip_path), Path(harness_root), guard, Path(summary_dir), attestation or {})
    except (CaptureError, UnsafeOutputError):
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, UnicodeDecodeError, zipfile.BadZipFile) as e:
        raise CaptureError(f"MALFORMED_CAPTURE ({type(e).__name__})") from None


def _import(zip_path, harness_root, guard, summary_dir, attest):
    root = guard.check(harness_root).resolve()
    forbidden = forbidden_roots(guard)
    _check_root(root, forbidden)
    arch = read_archive(zip_path)
    v = validate(arch)  # every semantic check happens BEFORE any filesystem write
    raw = zip_path.read_bytes()
    zip_sha = hashlib.sha256(raw).hexdigest()
    summary = summarize(v, zip_sha, len(raw), arch["sha"]["hashes.json"], attest)
    cid = summary["capture_id"]
    files = {f"h23_capture/{k}": b for k, b in arch["data"].items()}
    files["IMPORT_SOURCE.json"] = dumps({"archive_sha256": zip_sha, "archive_bytes": len(raw)}).encode()
    dest, status = extract(root, cid, files, forbidden)
    summary_status = write_summary(summary_dir, f"{cid}.json", dumps(summary).encode(), guard)
    return {"status": status, "summary_status": summary_status, "capture_id": cid, "evidence_dir": dest,
            "summary_path": summary_dir / f"{cid}.json", "summary": summary}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("zip", type=Path)
    ap.add_argument("--harness-root", help=f"external evidence root (default ${HARNESS_ENV})")
    ap.add_argument("--dataset-root", help="protected dataset root (default $GEMMA4_DATASET_ROOT)")
    ap.add_argument("--summary-dir", type=Path, default=SUMMARY_DIR)
    ap.add_argument("--attest-notebook-version", help="Kaggle notebook version that produced the archive")
    ap.add_argument("--attest-wheelhouse-version", help="version of metric/gemma-4-developer-agent-wheelhouse attached")
    args = ap.parse_args(argv)
    harness = args.harness_root or os.environ.get(HARNESS_ENV)
    try:
        if not harness:
            raise CaptureError(f"--harness-root or ${HARNESS_ENV} is required")
        res = import_capture(args.zip, harness_root=Path(harness), guard=WriteGuard.from_cli(args.dataset_root),
                             summary_dir=args.summary_dir,
                             attestation={"notebook_version": args.attest_notebook_version,
                                          "wheelhouse_dataset_version": args.attest_wheelhouse_version})
    except (CaptureError, DatasetRootError, UnsafeOutputError) as e:
        print(f"IMPORT REFUSED [{getattr(e, 'code', type(e).__name__)}]: {e}", file=sys.stderr)
        return 1
    s = res["summary"]
    print(f"{res['status']} / summary {res['summary_status']}: {res['capture_id']} -> {res['evidence_dir']}")
    print(f"  capture_state={s['capture_state']} evidence_strength={s['evidence_strength']} scorer={s['scorer_relationship']}")
    for r in s["ineligibility_reasons"]:
        print(f"  - not promoted: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
