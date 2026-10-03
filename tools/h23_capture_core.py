"""H23 harness capture core: stdlib only, embedded VERBATIM into notebooks/h23_harness_capture.ipynb.

Evidence acquisition, not certification. Prefers NO CLAIM over a weak claim:
  * bootstrap_state: BOOTSTRAP_NOT_RUN | BOOTSTRAP_FAILED | BOOTSTRAP_SUCCEEDED (CAPTURE_VALIDATED is set later, only by
    the local importer after independent recomputation)
  * the official bootstrap (mode official_all) reproduces the Getting Started notebook cell 2 and FAILS CLOSED
  * nothing here imports any target package (or torch): evidence is static, from importlib.metadata + RECORD files
  * every RECORD row is classified; a fingerprint is complete only if every row is in an allowed category
  * the archive carries the detailed inventories and wheel member manifests, so the importer can recompute every claim
  * all text and JSON pass through one recursive sanitizer; direct_url.json is never archived verbatim
The archive never contains a promotion claim; the hidden scorer relationship is always SCORER_ONLY_UNKNOWN.
"""
from __future__ import annotations

import base64
import binascii
import configparser
import csv
import dataclasses
import datetime as dt
import hashlib
import importlib
import importlib.metadata as md
import io
import json
import os
import platform
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

CAPTURE_SCHEMA = 3
TARGETS = ("swegemma", "adk-submission", "adk-eval-core", "google-adk", "google-genai", "litellm", "vllm", "transformers")
SOURCE_TARGETS = ("swegemma", "adk-submission", "adk-eval-core")
HARNESS_PREFIXES = ("google_adk-", "google_genai-", "adk_submission-", "adk_eval_core-", "swegemma-")
MODE_OFFICIAL, MODE_PARTIAL = "official_all", "harness_only"
BOOTSTRAP_NOT_RUN, BOOTSTRAP_FAILED, BOOTSTRAP_SUCCEEDED = "BOOTSTRAP_NOT_RUN", "BOOTSTRAP_FAILED", "BOOTSTRAP_SUCCEEDED"
ATTR_BASE, ATTR_OFFICIAL, ATTR_PARTIAL, ATTR_NOT_INSTALLED, ATTR_UNKNOWN = (
    "BASE_IMAGE", "OFFICIAL_FULL_BOOTSTRAP", "NON_OFFICIAL_PARTIAL_BOOTSTRAP", "NOT_INSTALLED", "UNKNOWN")
SRC_CAPTURED, SRC_NOT_CAPTURED, SRC_RECORD_UNAVAILABLE = "SOURCE_CAPTURED", "SOURCE_NOT_CAPTURED", "SOURCE_RECORD_UNAVAILABLE"
SCORER = "SCORER_ONLY_UNKNOWN"
METADATA_ALLOWLIST = ("METADATA", "WHEEL", "INSTALLER", "REQUESTED", "top_level.txt", "entry_points.txt")
VOLATILE_DISTINFO = ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json")  # rewritten on every (re)install
ALLOWED_ROW_CATEGORIES = ("ok", "excluded_volatile", "console_script")
SOURCE_SUFFIXES = (".py", ".pyi", ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".cfg", ".ini", ".typed", ".j2", ".jinja")
MAX_SOURCE_FILE = 20 * 1024 * 1024
CHUNK = 1 << 20
REDACTED = "<REDACTED>"


def namespace(dist: str) -> str:
    return re.sub(r"[-.]+", "_", dist.lower())


def norm(name) -> str:
    return re.sub(r"[-_.]+", "-", name or "").lower()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_path(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def record_hash_of(hexdigest: str) -> str:
    """RECORD encoding of a sha256 hex digest."""
    return "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(hexdigest)).rstrip(b"=").decode()


def decode_record_hash(value: str) -> str | None:
    """'sha256=<urlsafe b64>' -> hex digest; None if absent; raises ValueError if malformed."""
    if value == "":
        return None
    if not value.startswith("sha256="):
        raise ValueError("unsupported hash algorithm")
    b = value[7:]
    raw = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != b:
        raise ValueError("invalid sha256 encoding")
    return raw.hex()


def contained(child: Path, root: Path) -> bool:
    """Canonical containment (never a string-prefix test)."""
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def canonical_key(rel: str) -> str:
    return unicodedata.normalize("NFC", posixpath.normpath(rel)).casefold()


# ---- configuration ----------------------------------------------------------------------------------

@dataclasses.dataclass
class Config:
    input_root: Path = Path("/kaggle/input")
    output_dir: Path = Path("/kaggle/working")
    staging_parent: Path = Path("/tmp/h23_capture_staging")
    tmp_wheelhouse: Path = Path("/tmp/wheelhouse")
    mode: str = MODE_OFFICIAL
    skip_install: bool = False
    targets: tuple = TARGETS
    source_targets: tuple = SOURCE_TARGETS
    search_paths: list | None = None  # None = the live interpreter's sys.path
    scripts_dir: Path | None = None  # None = sysconfig scripts path of this interpreter
    pip_runner: object = None  # tests only; None = real `python -m pip`

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        c = cls()
        for field, var in (("input_root", "H23_INPUT_ROOT"), ("output_dir", "H23_OUTPUT_DIR"),
                           ("staging_parent", "H23_STAGING_DIR"), ("tmp_wheelhouse", "H23_TMP_WHEELHOUSE")):
            if e(var):
                setattr(c, field, Path(e(var)))
        c.mode = e("H23_BOOTSTRAP_MODE", MODE_OFFICIAL)
        c.skip_install = e("H23_SKIP_INSTALL") == "1"
        return c

    def overrides(self) -> list[str]:
        d = Config()
        out = [f.name for f in dataclasses.fields(self) if f.name != "pip_runner" and getattr(self, f.name) != getattr(d, f.name)]
        return out + (["pip_runner"] if self.pip_runner is not None else [])

    @property
    def official_wheelhouse(self) -> Path:
        return self.input_root / "datasets" / "metric" / "gemma-4-developer-agent-wheelhouse"

    def scripts(self) -> Path:
        return Path(self.scripts_dir or sysconfig.get_path("scripts")).resolve()


# ---- sanitization: one recursive implementation, every operation reports (value, did_redact) -----------

SENSITIVE_WORDS = ("api_key", "apikey", "token", "access_token", "refresh_token", "id_token", "password", "passwd", "pwd",
                   "secret", "client_secret", "authorization", "auth", "signature", "sig", "credential", "credentials",
                   "cookie", "session", "session_id", "private_key", "bearer")
_SENSITIVE_KEY = re.compile(r"^(?:[a-z0-9]+_)*(?:" + "|".join(sorted(SENSITIVE_WORDS, key=len, reverse=True)) + r")$")
_KEY_ALT = "|".join(sorted({w.replace("_", "[_-]?") for w in SENSITIVE_WORDS}, key=len, reverse=True))
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")
_B = r"(?<![A-Za-z0-9_\-])"  # token prefixes must start a token (avoids hits inside base64 hashes)
TOKEN_RES = [re.compile(p) for p in (
    _B + r"ghp_[A-Za-z0-9]{16,}", _B + r"github_pat_[A-Za-z0-9_]{16,}", _B + r"gh[ousr]_[A-Za-z0-9]{16,}",
    _B + r"hf_[A-Za-z0-9]{16,}", _B + r"sk-[A-Za-z0-9_\-]{16,}", _B + r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    _B + r"xox[abprs]-[A-Za-z0-9\-]{10,}", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}",
)]
KV_RES = [  # key=value, key: value and JSON-ish "key": "value" for sensitive keys inside free text
    re.compile(r"(?i)(\"(?:[a-z0-9]+[_-])*(?:" + _KEY_ALT + r")\"\s*:\s*)\"[^\"]*\""),
    re.compile(r"(?i)\b((?:[a-z0-9]+[_-])*(?:" + _KEY_ALT + r")\s*[=:]\s*)(?!<REDACTED>)[^\s&\"',;]+"),
]


def is_sensitive_key(key) -> bool:
    if not isinstance(key, str) or "/" in key or "." in key:
        return False
    return bool(_SENSITIVE_KEY.match(key.strip().lower().replace("-", "_")))


def sanitize_url(url: str) -> tuple[str, bool]:
    """Drop userinfo, query and fragment."""
    try:
        p = urlsplit(url)
    except ValueError:
        return "<unparseable-url>", True
    host = p.netloc.rsplit("@", 1)[-1]
    changed = "@" in p.netloc or bool(p.query) or bool(p.fragment)
    return urlunsplit((p.scheme, host, p.path, "", "")), changed


def sanitize_text(text: str) -> tuple[str, bool]:
    did = False

    def url_sub(m):
        nonlocal did
        s, changed = sanitize_url(m.group(0))
        did |= changed
        return s
    text = URL_RE.sub(url_sub, text)
    for r in TOKEN_RES:
        text, n = r.subn(REDACTED, text)
        did |= n > 0
    text, n = KV_RES[0].subn(lambda m: m.group(1) + '"' + REDACTED + '"', text)
    did |= n > 0
    text, n = KV_RES[1].subn(lambda m: m.group(1) + REDACTED, text)
    did |= n > 0
    return text, did


def sanitize_value(v) -> tuple[object, bool]:
    """Recursive: sensitive keys lose their value entirely; strings get text sanitization; did_redact ORs upward."""
    if isinstance(v, dict):
        out, did = {}, False
        for k, x in v.items():
            if is_sensitive_key(k) and x not in (None, "", [], {}) and not isinstance(x, bool):
                out[k], did = REDACTED, True
            else:
                out[k], d = sanitize_value(x)
                did |= d
        return out, did
    if isinstance(v, (list, tuple)):
        items = [sanitize_value(x) for x in v]
        return [i[0] for i in items], any(i[1] for i in items)
    if isinstance(v, str):
        return sanitize_text(v)
    return v, False


def summarize_direct_url(raw: str) -> dict:
    """direct_url.json is never archived: only a sanitized structural summary plus the original hash."""
    out = {"original_sha256": sha256_bytes(raw.encode("utf-8"))}
    try:
        d = json.loads(raw)
    except ValueError:
        return out | {"parse_error": True, "redacted": False}
    if not isinstance(d, dict):
        return out | {"parse_error": True, "redacted": False}
    did = False
    if isinstance(d.get("url"), str):
        out["url_sanitized"], did = sanitize_url(d["url"])
    for k in ("archive_info", "dir_info", "vcs_info"):
        if isinstance(d.get(k), dict):
            out["kind"] = k
            out[k], d2 = sanitize_value({kk: d[k][kk] for kk in ("editable", "vcs", "commit_id", "requested_revision") if kk in d[k]})
            did |= d2
    out["dropped_fields"] = sorted(str(k) for k in d if k not in ("url", "archive_info", "dir_info", "vcs_info"))
    out, d3 = sanitize_value(out)
    out["redacted"] = bool(did or d3)
    return out


def env_snapshot() -> dict:
    allow = {"PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "LD_LIBRARY_PATH", "HOME", "LANG", "SHELL",
             "VIRTUAL_ENV", "CONDA_PREFIX", "CUDA_VERSION", "NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES",
             "KAGGLE_KERNEL_RUN_TYPE", "KAGGLE_DOCKER_IMAGE", "KAGGLE_CONTAINER_NAME", "KAGGLE_URL_BASE",
             "OMP_NUM_THREADS", "MKL_NUM_THREADS", "LITELLM_LOCAL_MODEL_COST_MAP", "PIP_NO_INDEX"}
    values = {k: sanitize_text(v)[0] for k, v in os.environ.items() if k in allow and not is_sensitive_key(k)}
    return {"values": dict(sorted(values.items())), "all_names": sorted(os.environ),
            "policy": "values only for an explicit allowlist (sanitized); every other variable by NAME only"}


# ---- strict path resolution ---------------------------------------------------------------------------

class UnsafePath(Exception):
    pass


def _check_rel(rel) -> list[str]:
    if (not isinstance(rel, str) or not rel or rel.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", rel)
            or "\\" in rel or "\0" in rel or any(ord(c) < 32 for c in rel)):
        raise UnsafePath("absolute, drive, UNC, backslash or control-character path")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise UnsafePath("non-canonical component ('', '.', '..')")
    return parts


def resolve_record_path(root: Path, rel: str) -> Path:
    """Resolve one RECORD entry inside `root` (canonical install root). Rejects any symlink component; requires a
    regular file canonically contained in `root`. Raises UnsafePath, or FileNotFoundError for a missing file."""
    parts = _check_rel(rel)
    cur = root
    for p in parts:
        cur = cur / p
        try:
            st = cur.lstat()
        except FileNotFoundError:
            raise FileNotFoundError(rel) from None
        if stat.S_ISLNK(st.st_mode):
            raise UnsafePath("symlink component")
    real = cur.resolve(strict=True)
    if not contained(real, root) or real == root:
        raise UnsafePath("resolves outside the distribution root")
    if not stat.S_ISREG(real.lstat().st_mode):
        raise UnsafePath("not a regular file")
    return real


def safe_dest(base: Path, rel: str) -> Path:
    """Destination for `rel` strictly below `base` (a staging dir we created). Defense in depth: this helper itself
    rejects absolute, drive-letter, UNC, backslash and '..' paths even though callers also check. Creates parents."""
    parts = _check_rel(rel)
    base = base.resolve(strict=True)
    cur = base
    for p in parts[:-1]:
        cur = cur / p
        if cur.is_symlink():
            raise UnsafePath("symlink in staging path")
        if not cur.exists():
            cur.mkdir()
        if not cur.is_dir() or not contained(cur.resolve(), base):
            raise UnsafePath("staging escape")
    dest = cur / parts[-1]
    if dest.is_symlink() or dest.exists():
        raise UnsafePath("destination collision")
    if not contained(dest.parent.resolve() / dest.name, base):
        raise UnsafePath("staging escape")
    return dest


def write_new(dest: Path, data: bytes) -> None:
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


# ---- distributions and RECORD inventories (metadata only; no target code is imported) ------------------

def dists(search_paths=None):
    return md.distributions(path=search_paths) if search_paths is not None else md.distributions()


def find_distribution(name: str, search_paths=None):
    found = [d for d in dists(search_paths) if norm(d.metadata.get("Name")) == norm(name)]
    return (found[0] if found else None), [str(Path(d.locate_file("")).resolve()) for d in found]


def declared_scripts(d) -> set[str]:
    text = d.read_text("entry_points.txt")
    if not text:
        return set()
    cp = configparser.ConfigParser(delimiters=("=",), interpolation=None)
    cp.optionxform = str
    try:
        cp.read_string(text)
    except configparser.Error:
        return set()
    return {k for sec in ("console_scripts", "gui_scripts") if cp.has_section(sec) for k in cp[sec]}


SCRIPT_RE = re.compile(r"^(?:\.\./)+bin/([A-Za-z0-9_.\-]+)$")


def inventory(d, scripts_dir: Path) -> dict:
    """Classify EVERY RECORD row. Allowed: ok, excluded_volatile, console_script. Anything else makes the
    fingerprint incomplete: malformed, unsafe, outside_root, missing, unreadable, duplicate, hash_mismatch,
    size_mismatch (and ambiguous_ownership, assigned across targets later)."""
    root = Path(d.locate_file("")).resolve()
    text = d.read_text("RECORD")
    rows, seen = [], set()
    scripts = declared_scripts(d)
    for fields in (csv.reader(io.StringIO(text)) if text is not None else []):
        if not fields:
            continue
        row = {"path": fields[0], "record_hash": fields[1] if len(fields) > 1 else "",
               "record_size": fields[2] if len(fields) > 2 else "", "category": None}
        rows.append(row)
        rel = row["path"]
        try:
            if len(fields) != 3 or (row["record_size"] and not row["record_size"].isdigit()):
                raise ValueError("malformed row")
            want = decode_record_hash(row["record_hash"])
        except ValueError as e:
            row.update(category="malformed", reason=str(e))
            continue
        key = canonical_key(rel) if isinstance(rel, str) else rel
        if key in seen:
            row.update(category="duplicate")
            continue
        seen.add(key)
        m = SCRIPT_RE.match(rel or "")
        if m or (isinstance(rel, str) and posixpath.normpath(rel).startswith("../")):
            target = Path(os.path.normpath(str(root / rel)))
            ok = bool(m) and m.group(1) in scripts and target.parent == scripts_dir and target.name == m.group(1)
            if ok:
                try:
                    st = target.lstat()
                    ok = stat.S_ISREG(st.st_mode)
                except OSError:
                    ok = False
            if ok:
                digest = sha256_path(target)
                if want is not None and want != digest:
                    row.update(category="hash_mismatch")
                    continue
                row.update(category="console_script", bytes=target.stat().st_size, sha256=digest)
            else:
                row.update(category="outside_root")
            continue
        parts = rel.split("/")
        if "__pycache__" in parts or rel.endswith((".pyc", ".pyo")) or (
                len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] in VOLATILE_DISTINFO):
            row.update(category="excluded_volatile")
            continue
        try:
            real = resolve_record_path(root, rel)
            data_size, digest = real.stat().st_size, sha256_path(real)
        except FileNotFoundError:
            row.update(category="missing")
            continue
        except UnsafePath as e:
            row.update(category="unsafe", reason=str(e))
            continue
        except OSError:
            row.update(category="unreadable")
            continue
        if want is not None and want != digest:
            row.update(category="hash_mismatch", bytes=data_size, sha256=digest)
        elif row["record_size"] and int(row["record_size"]) != data_size:
            row.update(category="size_mismatch", bytes=data_size, sha256=digest)
        else:
            row.update(category="ok", bytes=data_size, sha256=digest)
    return {"record_present": text is not None, "rows": rows, "record_text": text}


def summarize_inventory(inv: dict) -> dict:
    """Fingerprint = sha256 over sorted (path, size, sha256) of ok + console_script rows; complete only if every row
    is in an allowed category (and a RECORD exists)."""
    counted = sorted((r["path"], r["bytes"], r["sha256"]) for r in inv["rows"] if r["category"] in ("ok", "console_script"))
    cats = {}
    for r in inv["rows"]:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    complete = inv["record_present"] and all(r["category"] in ALLOWED_ROW_CATEGORIES for r in inv["rows"]) \
        and not inv.get("record_redacted")
    return {"sha256": sha256_bytes("".join(f"{p}\t{b}\t{s}\n" for p, b, s in counted).encode()) if inv["record_present"] else None,
            "complete": bool(complete), "record_present": inv["record_present"], "files": len(counted),
            "bytes": sum(b for _, b, _ in counted), "categories": dict(sorted(cats.items()))}


def target_snapshot(cfg: Config) -> dict:
    snap = {}
    for t in cfg.targets:
        d, locations = find_distribution(t, cfg.search_paths)
        if d is None:
            snap[t] = None
            continue
        snap[t] = {"dist": d, "name": d.metadata.get("Name"), "version": d.version,
                   "location": str(Path(d.locate_file("")).resolve()), "all_locations": locations,
                   "inv": inventory(d, cfg.scripts())}
    owners = {}
    for t, s in snap.items():  # ownership ambiguity: two target distributions claiming the same installed file
        if s:
            for r in s["inv"]["rows"]:
                if r["category"] == "ok":
                    owners.setdefault((s["location"], canonical_key(r["path"])), []).append((t, r))
    for claims in owners.values():
        if len({t for t, _ in claims}) > 1:
            for _, r in claims:
                r["category"] = "ambiguous_ownership"
    for s in snap.values():
        if s:
            s["fp"] = summarize_inventory(s["inv"])
    return snap


def all_distributions(cfg: Config) -> list[dict]:
    out = [{"name": d.metadata.get("Name"), "normalized": norm(d.metadata.get("Name")), "version": d.version,
            "location": str(d.locate_file(""))} for d in dists(cfg.search_paths)]
    return sorted(out, key=lambda x: (x["normalized"], str(x["version"]), x["location"]))


# ---- wheelhouse + official bootstrap -----------------------------------------------------------------

def wheel_metadata(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            n = next(x for x in zf.namelist() if x.count("/") == 1 and x.endswith(".dist-info/METADATA"))
            hdr = zf.read(n).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    get = lambda key: next((l.split(":", 1)[1].strip() for l in hdr.splitlines() if l.startswith(key + ":")), None)
    return {"name": get("Name"), "normalized": norm(get("Name")), "version": get("Version")}


def locate_wheelhouse(cfg: Config) -> tuple[Path | None, str | None]:
    if cfg.official_wheelhouse.is_dir():
        return cfg.official_wheelhouse, "official_path"
    hits = sorted(cfg.input_root.glob("**/adk_submission-*.whl")) if cfg.input_root.is_dir() else []
    return (hits[0].parent, "glob_adk_submission_wheel") if hits else (None, None)


def wheel_inventory(wheelhouse: Path) -> list[dict]:
    inv = []
    for w in sorted(wheelhouse.iterdir()):
        if w.is_file() and not w.is_symlink():
            item = {"filename": w.name, "bytes": w.stat().st_size, "sha256": sha256_path(w)}
            if w.suffix == ".whl":
                item["metadata"] = wheel_metadata(w)
            inv.append(item)
    return inv


def official_link_name(name: str) -> str:
    return name.replace("cu128", "+cu128") if ("cu128" in name and "+" not in name) else name  # official cell 2


def plan_bootstrap(cfg: Config, wheelhouse: Path, inv: list[dict]) -> dict:
    """Official cell 2 selection: every non-cutlass *.whl (harness_only: only the five harness prefixes), symlinked
    into a FRESH tmp wheelhouse with '+cu128' restored, passed sorted to `pip install -q --no-deps --force-reinstall`."""
    tmp = cfg.tmp_wheelhouse
    if contained(tmp.resolve(), cfg.input_root.resolve()):
        raise RuntimeError("tmp wheelhouse must not be under the input root")
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_symlink() or not tmp.is_dir():
            raise RuntimeError(f"{tmp} exists and is not a plain directory")
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    by_name = {i["filename"]: i for i in inv}
    selected, excluded = [], []
    for w in sorted(wheelhouse.glob("*.whl")):
        if "cutlass" in w.name.lower():
            excluded.append({"wheel": w.name, "reason": "cutlass"})
            continue
        if cfg.mode == MODE_PARTIAL and not w.name.startswith(HARNESS_PREFIXES):
            excluded.append({"wheel": w.name, "reason": "harness_only"})
            continue
        link = tmp / official_link_name(w.name)
        if link.exists() or link.is_symlink():
            raise RuntimeError(f"wheel name collision after '+cu128' restoration: {link.name}")
        os.symlink(w, link)
        selected.append({"wheel": w.name, "link_name": link.name, "sha256": by_name.get(w.name, {}).get("sha256"),
                         "bytes": by_name.get(w.name, {}).get("bytes"), "metadata": by_name.get(w.name, {}).get("metadata")})
    args = sorted(str(p) for p in tmp.glob("*.whl"))
    return {"mode": cfg.mode, "wheelhouse": str(wheelhouse), "tmp_wheelhouse": str(tmp), "selected": selected,
            "excluded": excluded, "command": [sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall", *args]}


def run_pip(cmd: list[str]) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        return {"returncode": r.returncode, "stdout_tail": r.stdout[-8000:], "stderr_tail": r.stderr[-8000:]}
    except Exception as e:  # noqa: BLE001
        return {"returncode": None, "stdout_tail": "", "stderr_tail": f"{type(e).__name__}: {e}"}


def remove_cutlass_pth() -> list[str]:
    removed = []
    for pattern in ("/usr/local/lib/python*/dist-packages/*cutlass*.pth", "/usr/local/lib/python*/site-packages/*cutlass*.pth"):
        for pth in sorted(Path("/").glob(pattern.lstrip("/"))):
            try:
                pth.unlink()
                removed.append(str(pth))
            except OSError:
                pass
    return removed


# ---- attribution -------------------------------------------------------------------------------------

def wheel_manifest(wheel: Path, meta: dict) -> dict:
    """Member list (name, bytes, sha256) + the wheel's own RECORD, archived so the importer can recompute matches."""
    members = []
    with zipfile.ZipFile(wheel) as zf:
        record = None
        for info in zf.infolist():
            if info.is_dir():
                continue
            h = hashlib.sha256()
            with zf.open(info) as f:
                for chunk in iter(lambda: f.read(CHUNK), b""):
                    h.update(chunk)
            members.append({"name": info.filename, "bytes": info.file_size, "sha256": h.hexdigest()})
            p = info.filename.split("/")
            if len(p) == 2 and p[0].endswith(".dist-info") and p[1] == "RECORD":
                record = zf.read(info).decode("utf-8", "replace")
    rec_clean, did = sanitize_text(record or "")
    return {"filename": wheel.name, "sha256": sha256_path(wheel), "bytes": wheel.stat().st_size, "metadata": meta,
            "members": sorted(members, key=lambda m: m["name"]), "record_text": rec_clean if record is not None else None,
            "record_redacted": did}


def install_path_of(member: str) -> str | None:
    """Where pip installs a wheel member relative to the install root (None = outside it / not compared)."""
    parts = member.split("/")
    if parts[0].endswith(".data"):
        return "/".join(parts[2:]) if len(parts) > 2 and parts[1] in ("purelib", "platlib") else None
    if len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] in VOLATILE_DISTINFO:
        return None
    return member


def compare_wheel_install(manifest: dict, inv: dict) -> dict:
    """matched / mismatched / absent (wheel file not installed identically) / extra (installed package file that the
    wheel does not contain). Strong attribution needs matched > 0 and every other counter 0."""
    installed = {r["path"]: r for r in inv["rows"] if r["category"] == "ok"}
    wanted = {}
    for m in manifest["members"]:
        p = install_path_of(m["name"])
        if p is not None:
            wanted[p] = m
    matched = sum(1 for p, m in wanted.items() if p in installed and installed[p]["sha256"] == m["sha256"])
    mismatched = sum(1 for p, m in wanted.items() if p in installed and installed[p]["sha256"] != m["sha256"])
    absent = sum(1 for p in wanted if p not in installed)
    extra = sum(1 for p in installed if p not in wanted)
    return {"matched": matched, "mismatched": mismatched, "absent_in_install": absent, "extra_in_install": extra}


def attribute(pre, post, boot_state: str, mode: str, plan: dict | None, wheelhouse: Path | None) -> dict:
    out = {"pre_state": "PRESENT" if pre else "ABSENT", "post_state": "PRESENT" if post else "ABSENT",
           "change": None, "bootstrap_attribution": ATTR_UNKNOWN, "wheel_evidence": None, "wheel_manifest": None}
    if post is None:
        out.update(change="REMOVED" if pre else "ABSENT", bootstrap_attribution=ATTR_NOT_INSTALLED)
        return out
    if pre is None:
        out["change"] = "INSTALLED"
    elif pre["fp"]["complete"] and post["fp"]["complete"]:
        out["change"] = "UNCHANGED" if pre["fp"]["sha256"] == post["fp"]["sha256"] else "REPLACED"
    else:
        out["change"] = "UNDETERMINED"
    if boot_state != BOOTSTRAP_SUCCEEDED:
        out["bootstrap_attribution"] = ATTR_BASE if (boot_state == BOOTSTRAP_NOT_RUN and out["change"] == "UNCHANGED") else ATTR_UNKNOWN
        return out
    matches = [s for s in plan["selected"] if (s.get("metadata") or {}).get("normalized") == norm(post["name"])]
    exact = [s for s in matches if s["metadata"].get("version") == post["version"]]
    if len(exact) != 1 or len(matches) != 1:
        out["bootstrap_attribution"] = ATTR_BASE if out["change"] == "UNCHANGED" and not matches else ATTR_UNKNOWN
        return out
    s = exact[0]
    manifest = wheel_manifest(wheelhouse / s["wheel"], s["metadata"])
    counts = compare_wheel_install(manifest, post["inv"])
    out["wheel_manifest"] = manifest
    out["wheel_evidence"] = {"wheel": s["wheel"], "link_name": s["link_name"], "wheel_sha256": s["sha256"],
                             "wheel_bytes": s["bytes"], "wheel_name": s["metadata"]["name"], "wheel_version": s["metadata"]["version"],
                             "content_check": counts}
    strong = (post["fp"]["complete"] and counts["matched"] > 0 and counts["mismatched"] == 0
              and counts["absent_in_install"] == 0 and counts["extra_in_install"] == 0 and not manifest["record_redacted"])
    if strong:
        out["bootstrap_attribution"] = ATTR_OFFICIAL if mode == MODE_OFFICIAL else ATTR_PARTIAL
    return out


# ---- metadata + source capture -----------------------------------------------------------------------

def capture_metadata(d, inv: dict, stage: Path, ns: str) -> dict:
    out = {"metadata_files": {}, "metadata_status": "METADATA_UNAVAILABLE", "record_status": "RECORD_UNAVAILABLE",
           "record": None, "direct_url": None}
    mdir = stage / "metadata" / ns
    for fname in METADATA_ALLOWLIST:
        raw = d.read_text(fname)
        if raw is None:
            continue
        clean, did = sanitize_text(raw)
        mdir.mkdir(parents=True, exist_ok=True)
        write_new(safe_dest(mdir, fname), clean.encode("utf-8"))
        out["metadata_files"][fname] = {"archive_path": f"metadata/{ns}/{fname}", "original_sha256": sha256_bytes(raw.encode("utf-8")),
                                        "captured_sha256": sha256_bytes(clean.encode("utf-8")), "redacted": did}
    if "METADATA" in out["metadata_files"]:
        out["metadata_status"] = "METADATA_CAPTURED"
    raw = inv["record_text"]
    if raw is not None:
        clean, did = sanitize_text(raw)
        inv["record_redacted"] = did  # a redacted RECORD cannot back ownership checks: fingerprint becomes incomplete
        (stage / "records").mkdir(exist_ok=True)
        write_new(safe_dest(stage / "records", f"{ns}.RECORD"), clean.encode("utf-8"))
        out["record"] = {"archive_path": f"records/{ns}.RECORD", "original_sha256": sha256_bytes(raw.encode("utf-8")),
                         "captured_sha256": sha256_bytes(clean.encode("utf-8")), "redacted": did}
        out["record_status"] = "RECORD_CAPTURED"
    raw = d.read_text("direct_url.json")
    if raw is not None:
        out["direct_url"] = summarize_direct_url(raw)  # never archived verbatim
    return out


def capture_source(d, inv: dict, fp: dict, stage: Path, ns: str) -> dict:
    """Copy RECORD-owned package source/resources (rows classified ok; no dist-info, bytecode or binaries)."""
    if not inv["record_present"]:
        return {"status": SRC_RECORD_UNAVAILABLE, "reason": "RECORD missing"}
    if not fp["complete"]:
        return {"status": SRC_NOT_CAPTURED, "reason": f"incomplete RECORD inventory {fp['categories']}"}
    root = Path(d.locate_file("")).resolve()
    base = stage / "sources" / ns
    base.mkdir(parents=True)
    files, skipped = [], []
    for r in inv["rows"]:
        rel = r["path"]
        if r["category"] != "ok" or rel.split("/")[0].endswith((".dist-info", ".data")):
            continue
        if not rel.endswith(SOURCE_SUFFIXES) or r["bytes"] > MAX_SOURCE_FILE:
            skipped.append({"path": rel, "reason": "binary/unlisted type or too large"})
            continue
        try:
            data = resolve_record_path(root, rel).read_bytes()
        except (OSError, UnsafePath) as e:
            shutil.rmtree(base)
            return {"status": SRC_NOT_CAPTURED, "reason": f"unreadable during capture: {type(e).__name__}"}
        if sha256_bytes(data) != r["sha256"]:
            shutil.rmtree(base)
            return {"status": SRC_NOT_CAPTURED, "reason": "file changed between inventory and capture"}
        write_new(safe_dest(base, rel), data)
        files.append({"path": rel, "bytes": len(data), "sha256": r["sha256"]})
    if not files:
        shutil.rmtree(base)
        return {"status": SRC_NOT_CAPTURED, "reason": "zero source/resource files"}
    tree = sha256_bytes("".join(f"{f['path']}\t{f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"])).encode())
    return {"status": SRC_CAPTURED, "archive_dir": f"sources/{ns}", "files": files, "skipped": skipped,
            "file_count": len(files), "tree_sha256": tree}


def module_candidates(inv: dict) -> list[str]:
    """Top-level import names inferred from RECORD-owned files only (no import, no find_spec)."""
    ok = {r["path"] for r in inv["rows"] if r["category"] == "ok"}
    names = set()
    for rel in ok:
        parts = rel.split("/")
        if parts[0].endswith((".dist-info", ".data")) or not rel.endswith(".py"):
            continue
        if len(parts) == 1:
            names.add(rel[:-3])
        else:
            names.add(parts[0] if f"{parts[0]}/__init__.py" in ok else f"{parts[0]} (namespace)")
    return sorted(names)


# ---- source index (locations only) -------------------------------------------------------------------

PATTERNS = {
    "H01_reasoning_content": [r"reasoning_content", r"\breasoning\b"],
    "H02_include_thoughts": [r"include_thoughts", r"enable_thinking"],
    "H03_thinking_budget": [r"thinking_budget", r"reasoning_effort", r"thinking_level"],
    "H04_undeclared_tool": [r"_get_tool\b", r"not found in the tools", r"[Tt]ool .{0,40}not found", r"tools_dict"],
    "H05_declared_tool_control": [r"class ToolRegistry", r"def create_tools", r"FunctionTool\("],
    "H06_read_file_range": [r"def read_file", r"start_line", r"end_line"],
    "H08_H10_edit_file": [r"def edit_file", r"apply_replacement", r"old_string", r"allow_multiple"],
    "H11_protected_reset": [r"_is_protected_test_or_config_path", r"git checkout HEAD --", r"git clean -f"],
    "H13_timeout_recovery": [r"timed out", r"TimeoutExceeded", r"without explicit submit_patch", r"time_minutes"],
    "H14_tool_budget_recovery": [r"budget_gated", r"max_tool_calls", r"tool_calls_used", r"budget_warning"],
    "H15_compaction": [r"EventsCompactionConfig", r"compaction_interval", r"event_retention_size", r"token_threshold"],
    "H17_tool_serialization": [r"json\.dumps\(", r"function_response", r"tool_call_id", r"role.{0,5}tool"],
    "H18_nonexistent_tool": [r"ValueError\(.{0,80}[Tt]ool", r"unknown tool", r"hallucinat"],
    "H19_compaction_tool_outputs": [r"compact\w*.{0,60}(function_response|tool)", r"summariz\w*.{0,60}(event|tool)"],
    "H20_agent_tool": [r"skip_summarization", r"class AgentTool", r"AgentTool\("],
    "H23_package_identity": [r"__version__\s*=", r"importlib\.metadata"],
    "H26_include_traversal": [r"!include", r"PathTraversalError", r"is_relative_to"],
    "H27_no_lora": [r"discover_adapters", r"lora[-_]modules", r"enable_lora", r"max_loras"],
    "H28_submission_path": [r"submission\.parquet", r"ALLOWED_MODEL_NAMES", r"MAX_SUBMISSION_SIZE_BYTES",
                            r"load_submission_eval_config", r"inference\.py", r"metric\.py"],
    "H29_free_calls": [r"count_tool_call", r"def get_status", r"def submit_patch"],
    "H30_graph_advertisement": [r"Code Intelligence Tools", r"build_agent_prompt", r"search_similar_code"],
}
MAX_HITS = 40


def build_index(snap: dict) -> dict:
    compiled = {h: [re.compile(p) for p in ps] for h, ps in PATTERNS.items()}
    index = {"note": "INDEX ONLY: candidate locations; no runtime claim", "patterns": PATTERNS,
             "hits": {h: [] for h in PATTERNS}, "files_scanned": {}, "scorer_entrypoints": []}
    counts = {}
    for t, s in snap.items():
        if s is None:
            continue
        root, n = Path(s["location"]), 0
        for r in sorted(s["inv"]["rows"], key=lambda r: r["path"]):
            rel = r["path"]
            if r["category"] != "ok" or not rel.endswith(".py"):
                continue
            n += 1
            if rel.split("/")[-1] in ("inference.py", "metric.py", "evaluate.py") or "/scripts/" in f"/{rel}":
                index["scorer_entrypoints"].append({"distribution": t, "path": rel})
            try:
                lines = resolve_record_path(root, rel).read_text(encoding="utf-8", errors="replace").splitlines()
            except (OSError, UnsafePath):
                continue
            for i, line in enumerate(lines, 1):
                for h, regs in compiled.items():
                    if any(rg.search(line) for rg in regs):
                        counts[(h, t)] = counts.get((h, t), 0) + 1
                        if counts[(h, t)] <= MAX_HITS:
                            index["hits"][h].append({"distribution": t, "path": rel, "line": i, "text": line.strip()[:200]})
        index["files_scanned"][t] = n
    index["hit_counts"] = {f"{h}|{t}": c for (h, t), c in sorted(counts.items())}
    return index


# ---- orchestration -----------------------------------------------------------------------------------

def environment_snapshot(stage: str) -> dict:
    pip = run_pip([sys.executable, "-m", "pip", "--version"])
    info = {"stage": stage, "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "python_version": sys.version, "platform": platform.platform(), "machine": platform.machine(),
            "sys_executable": sys.executable, "sys_prefix": sys.prefix, "sys_path": list(sys.path),
            "cpu_count": os.cpu_count(), "env": env_snapshot(), "pip_version": pip["stdout_tail"].strip(),
            "nvidia_smi_present": shutil.which("nvidia-smi") is not None, "kernel_packages": {}}
    for k in ("ipykernel", "jupyter_client", "jupyter_core", "papermill"):
        try:
            info["kernel_packages"][k] = md.version(k)
        except md.PackageNotFoundError:
            info["kernel_packages"][k] = None
    return info


def run_capture(cfg: Config) -> dict:
    if cfg.mode not in (MODE_OFFICIAL, MODE_PARTIAL):
        raise ValueError(f"unknown bootstrap mode {cfg.mode!r}")
    utc = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for p in (cfg.staging_parent, cfg.output_dir, cfg.tmp_wheelhouse):
        if contained(p.resolve(), cfg.input_root.resolve()):
            raise RuntimeError(f"refusing to write under the input root: {p}")
    if cfg.staging_parent.exists():
        shutil.rmtree(cfg.staging_parent)
    stage = cfg.staging_parent / "h23_capture"
    stage.mkdir(parents=True)
    overrides = cfg.overrides()
    run_context = "kaggle_kernel" if os.environ.get("KAGGLE_KERNEL_RUN_TYPE") and not overrides else "non_kaggle_or_overridden"

    def dump(name, obj):  # every JSON document passes the recursive sanitizer as a final defense
        clean, _ = sanitize_value(obj)
        write_new(safe_dest(stage, name), (json.dumps(clean, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8"))

    pre_env, pre_all, pre = environment_snapshot("pre_bootstrap"), all_distributions(cfg), target_snapshot(cfg)
    wheelhouse, found_by = locate_wheelhouse(cfg)
    winv = wheel_inventory(wheelhouse) if wheelhouse else []
    boot = {"state": BOOTSTRAP_NOT_RUN, "mode": cfg.mode, "skip_install": cfg.skip_install, "dry_run": cfg.skip_install,
            "pip_executed": False, "pip_simulated": cfg.pip_runner is not None,
            "wheelhouse_dir": str(wheelhouse) if wheelhouse else None, "wheelhouse_found_by": found_by,
            "plan": None, "pip": None, "removed_pth": [], "reason": None}
    if winv:
        boot["wheelhouse_fingerprint_sha256"] = sha256_bytes("".join(f"{i['filename']}\t{i['sha256']}\n" for i in winv).encode())
    plan = None
    if cfg.skip_install:
        boot["reason"] = "skip_install set (local dry run)"
    elif not wheelhouse:
        boot["reason"] = "wheelhouse dataset not attached"
    else:
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # official cell 2; no network fetch if anything loads litellm
        boot["removed_pth"] = remove_cutlass_pth() if cfg.pip_runner is None else []
        plan = plan_bootstrap(cfg, wheelhouse, winv)
        boot["plan"] = plan
        dump("bootstrap_plan.json", plan)  # written BEFORE pip runs
        boot["pip_executed"] = True
        result = (cfg.pip_runner or run_pip)(plan["command"])
        boot["pip"] = {"returncode": result.get("returncode"), "stdout_tail": result.get("stdout_tail") or "",
                       "stderr_tail": result.get("stderr_tail") or ""}
        boot["state"] = BOOTSTRAP_SUCCEEDED if result.get("returncode") == 0 else BOOTSTRAP_FAILED  # fail closed
        importlib.invalidate_caches()
    post_env, post_all, post = environment_snapshot("post_bootstrap"), all_distributions(cfg), target_snapshot(cfg)

    packages = {"capture_schema": CAPTURE_SCHEMA, "capture_utc": utc, "bootstrap_state": boot["state"],
                "bootstrap_mode": cfg.mode, "scorer_relationship": SCORER, "targets": {}}
    source_files = {}
    for t in cfg.targets:
        ns = namespace(t)
        if post[t]:
            meta = capture_metadata(post[t]["dist"], post[t]["inv"], stage, ns)
            post[t]["fp"] = summarize_inventory(post[t]["inv"])  # record_redacted may have changed completeness
        else:
            meta = {"metadata_files": {}, "metadata_status": "METADATA_UNAVAILABLE", "record_status": "RECORD_UNAVAILABLE",
                    "record": None, "direct_url": None}
        a = attribute(pre[t], post[t], boot["state"], cfg.mode, plan, wheelhouse)
        manifest = a.pop("wheel_manifest")
        if manifest is not None:
            (stage / "wheels").mkdir(exist_ok=True)
            dump(f"wheels/{manifest['filename']}.json", manifest)
        e = {"distribution_query": t, "namespace": ns, "distribution_name": post[t]["name"] if post[t] else None,
             "version": post[t]["version"] if post[t] else None, "location": post[t]["location"] if post[t] else None,
             "all_locations": post[t]["all_locations"] if post[t] else [], "pre_version": pre[t]["version"] if pre[t] else None,
             "pre_fingerprint": pre[t]["fp"] if pre[t] else None, "post_fingerprint": post[t]["fp"] if post[t] else None,
             "bootstrap_state": boot["state"], **a, **meta, "source_status": SRC_NOT_CAPTURED, "scorer_relationship": SCORER}
        e["install_status"] = ("PACKAGE_NOT_INSTALLED" if post[t] is None else "PACKAGE_BOOTSTRAPPED_FROM_OFFICIAL_NOTEBOOK"
                               if a["bootstrap_attribution"] == ATTR_OFFICIAL else "PACKAGE_INSTALLED")
        (stage / "inventories").mkdir(exist_ok=True)
        for when, snap in (("pre", pre[t]), ("post", post[t])):
            if snap:
                dump(f"inventories/{ns}.{when}.json", {"record_present": snap["inv"]["record_present"],
                                                       "record_redacted": bool(snap["inv"].get("record_redacted")),
                                                       "rows": snap["inv"]["rows"]})
        if post[t]:
            e["module_candidates_from_record"] = module_candidates(post[t]["inv"])
            if t in cfg.source_targets:
                if boot["state"] == BOOTSTRAP_FAILED:
                    e["source_reason"] = "bootstrap failed: no source claim"
                else:
                    sc = capture_source(post[t]["dist"], post[t]["inv"], post[t]["fp"], stage, ns)
                    e["source_status"], e["source_reason"] = sc["status"], sc.get("reason")
                    if sc["status"] == SRC_CAPTURED:
                        source_files[t] = {k: sc[k] for k in ("archive_dir", "files", "skipped")}
                        e.update(source_file_count=sc["file_count"], source_tree_sha256=sc["tree_sha256"])
        packages["targets"][t] = e

    env_doc = {"capture_schema": CAPTURE_SCHEMA, "capture_utc": utc, "run_context": run_context, "config_overrides": overrides,
               "pre_bootstrap": pre_env, "post_bootstrap": post_env, "bootstrap": boot, "input_root": str(cfg.input_root),
               "scripts_dir": str(cfg.scripts())}
    dump("environment.json", env_doc)
    dump("installed_distributions.json", {"pre_bootstrap": pre_all, "post_bootstrap": post_all})
    dump("wheelhouse.json", {"inventory": winv, "fingerprint_sha256": boot.get("wheelhouse_fingerprint_sha256"),
                             "fingerprint_definition": "sha256 of 'filename\\tsha256\\n' lines sorted by filename"})
    dump("packages.json", packages)
    dump("source_files.json", source_files)
    dump("source_index.json", build_index(post))
    readme = (f"# H23 harness capture {utc}\n\nbootstrap_state: {boot['state']} | mode: {cfg.mode} | run_context: {run_context}\n"
              f"overrides: {overrides}\n\n")
    if boot["state"] != BOOTSTRAP_SUCCEEDED:
        readme += "THIS CAPTURE IS NOT USABLE FOR H23 PROMOTION (bootstrap did not succeed).\n\n"
    if cfg.mode == MODE_PARTIAL:
        readme += "NON_OFFICIAL_PARTIAL_BOOTSTRAP: harness_only mode never establishes official VERSION-SPECIFIC evidence.\n\n"
    readme += ("Interactive Kaggle evidence only. Scorer relationship: SCORER_ONLY_UNKNOWN. Promotion is derived only by the\n"
               "local importer, which needs the Kaggle notebook version and the wheelhouse dataset version.\n")
    write_new(safe_dest(stage, "CAPTURE_README.md"), sanitize_text(readme)[0].encode("utf-8"))
    files = sorted(p for p in stage.rglob("*") if p.is_file())
    dump("hashes.json", {"algorithm": "sha256", "files": {p.relative_to(stage).as_posix(): {"sha256": sha256_path(p), "bytes": p.stat().st_size} for p in files}})

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    archive = cfg.output_dir / f"h23_harness_capture_{utc}_{boot['state']}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(p for p in stage.rglob("*") if p.is_file()):
            zi = zipfile.ZipInfo(f"h23_capture/{p.relative_to(stage).as_posix()}", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type, zi.create_system, zi.external_attr = zipfile.ZIP_DEFLATED, 3, 0o100644 << 16
            zf.writestr(zi, p.read_bytes())
    shutil.rmtree(cfg.staging_parent)
    if plan and cfg.tmp_wheelhouse.is_dir() and not cfg.tmp_wheelhouse.is_symlink():
        shutil.rmtree(cfg.tmp_wheelhouse)  # symlinks only; the dataset is untouched
    return {"archive": archive, "sha256": sha256_path(archive), "bytes": archive.stat().st_size, "bootstrap_state": boot["state"],
            "mode": cfg.mode, "run_context": run_context, "overrides": overrides, "packages": packages,
            "pip_returncode": (boot["pip"] or {}).get("returncode")}


def report(res: dict) -> None:
    bar = "=" * 76
    print(bar)
    if res["bootstrap_state"] == BOOTSTRAP_FAILED:
        print(f"!!! BOOTSTRAP FAILED (pip returncode {res['pip_returncode']}): capture is NOT usable for H23 promotion.")
        print("!!! Do not fall back automatically. A harness_only run is a separate, non-official experiment.")
    print("CAPTURE STATE   :", res["bootstrap_state"], "| mode", res["mode"], "| run_context", res["run_context"])
    print("CAPTURE ARCHIVE :", res["archive"])
    print("CAPTURE SHA256  :", res["sha256"])
    print("CAPTURE SIZE    :", res["bytes"], "bytes")
    print("TARGET PACKAGE VERSIONS (interactive notebook; scorer relationship SCORER_ONLY_UNKNOWN):")
    for t, e in res["packages"]["targets"].items():
        print(f"  {t:15s} {str(e['version']):14s} {e['bootstrap_attribution']:32s} {e['source_status']}")
    print(bar)
