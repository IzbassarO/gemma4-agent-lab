"""Inspect a submission.zip: entry safety, determinism policy, hashes, then static validation of a safe extraction.

Usage:
    uv run python -m tools.inspect_submission artifacts/submissions/<id>/submission.zip [--json]

Exit status: 0 = archive safe + policy ok + extracted bundle STRUCTURAL_VALID, 1 otherwise.
Extraction happens only after every entry passed the path checks; it writes regular files only (never symlinks),
re-checks each resolved destination stays inside the temp dir (Zip Slip), and the temp dir is always deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import stat
import struct
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

from tools import validate_submission as vs
from tools.common import CHUNK, sha256_file

FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644


def _entry_problems(info: zipfile.ZipInfo) -> list[str]:
    n = info.filename
    probs = []
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        probs.append("ABSOLUTE_PATH")
    if "\\" in n or "\0" in n or any(ord(c) < 32 for c in n):
        probs.append("INVALID_PATH_NAME")
    if ".." in n.replace("\\", "/").split("/"):
        probs.append("PATH_TRAVERSAL")
    if stat.S_ISLNK(info.external_attr >> 16):
        probs.append("SYMLINK_ENTRY")
    return probs


def _canonical_key(name: str) -> str:
    """Collision key: what two members would map to on a normalizing / case-insensitive filesystem."""
    return unicodedata.normalize("NFC", posixpath.normpath(name.rstrip("/"))).casefold()


def _local_extra_lengths(zip_path: Path, infos: list[zipfile.ZipInfo]) -> dict[str, int]:
    """Extra-field length of each member's LOCAL header (zipfile only exposes the central-directory copy)."""
    out = {}
    with open(zip_path, "rb") as f:
        for i in infos:
            f.seek(i.header_offset)
            h = f.read(30)
            if len(h) != 30 or h[:4] != b"PK\x03\x04":
                raise zipfile.BadZipFile(f"bad local header for {i.filename!r}")
            out[i.filename] = struct.unpack("<H", h[28:30])[0]
    return out


def inspect(zip_path: Path, *, run_validation: bool = True) -> dict:
    """Never raises on archive content: unexpected failures become a structured INSPECTION_ERROR."""
    zip_path = Path(zip_path)
    out: dict = {"zip_sha256": sha256_file(zip_path), "zip_bytes": zip_path.stat().st_size,
                 "errors": [], "policy_violations": [], "files": []}
    try:
        return _inspect(zip_path, out, run_validation)
    except Exception as e:  # noqa: BLE001 - untrusted archive input must never produce a traceback
        out["errors"].append({"code": "INSPECTION_ERROR", "path": "", "message": f"{type(e).__name__}: archive could not be processed"})
        out["files"] = []
        out.pop("validation", None)
        out["ok"] = False
        return out


def _inspect(zip_path: Path, out: dict, run_validation: bool) -> dict:
    err = lambda code, path, msg: out["errors"].append({"code": code, "path": path, "message": msg})
    pol = lambda code, path, msg: out["policy_violations"].append({"code": code, "path": path, "message": msg})
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        err("BAD_ZIP", "", str(e))
        out["ok"] = False
        return out
    with zf:
        infos = zf.infolist()
        names = [i.filename for i in infos]
        for dup in sorted({n for n in names if names.count(n) > 1}):
            err("DUPLICATE_PATH", dup, "archive path appears more than once")
        by_key: dict[str, list[str]] = {}
        for n in names:
            by_key.setdefault(_canonical_key(n), []).append(n)
        for key, group in sorted(by_key.items()):
            if len(set(group)) > 1:
                err("DUPLICATE_PATH_NORMALIZED", group[0], f"{sorted(set(group))} collide after path/Unicode/case normalization")
        file_keys = {_canonical_key(i.filename) for i in infos if not i.is_dir()}
        for i in infos:
            parts = _canonical_key(i.filename).split("/")
            for k in range(1, len(parts)):
                if "/".join(parts[:k]) in file_keys:
                    err("PATH_CONFLICT", i.filename, "a parent path component is also a file member")
                    break
        for info in infos:
            n = info.filename
            if posixpath.normpath(n.rstrip("/")) != n.rstrip("/") or n.startswith("./") or "//" in n:
                err("NONCANONICAL_PATH", n, "member path is not in canonical normalized form")
            for p in _entry_problems(info):
                err(p, n, "unsafe archive entry")
        if zf.comment:
            pol("ARCHIVE_COMMENT", "", f"archive comment of {len(zf.comment)} bytes")
        local_extra = _local_extra_lengths(zip_path, infos)
        for i in infos:
            if i.comment:
                pol("MEMBER_COMMENT", i.filename, f"member comment of {len(i.comment)} bytes")
            if i.extra or local_extra.get(i.filename):
                pol("UNEXPECTED_EXTRA_FIELD", i.filename, f"extra field bytes: central {len(i.extra)}, local {local_extra.get(i.filename)}")
        files = [i for i in infos if not i.is_dir()]
        out["directory_entries"] = sorted(i.filename for i in infos if i.is_dir())
        out["compressed_bytes"] = sum(i.compress_size for i in files)
        out["uncompressed_bytes"] = sum(i.file_size for i in files)
        if out["uncompressed_bytes"] >= vs.MAX_TOTAL_BYTES:
            err("SIZE_LIMIT", "", f"declared uncompressed {out['uncompressed_bytes']} >= {vs.MAX_TOTAL_BYTES}")
        roots = [n for n in names if n in vs.ROOT_CONFIG_NAMES]
        if not roots:
            tops = {n.split("/")[0] for n in names}
            nested = [n for n in names if posixpath.basename(n) in vs.ROOT_CONFIG_NAMES]
            err("WRAPPER_DIRECTORY" if nested and len(tops) == 1 else "ROOT_CONFIG_MISSING", "",
                f"no root config at archive root (top-level entries: {sorted(tops)})")
        for i in files:
            ext = posixpath.splitext(i.filename)[1].lower()
            if ext not in vs.ALLOWED_EXTENSIONS:
                err("UNSUPPORTED_EXTENSION", i.filename, f"extension {ext or '(none)'} not allowed")
        policy = out["policy_violations"]
        if names != sorted(names):
            policy.append({"code": "UNSORTED_ENTRIES", "path": "", "message": "entries not in lexicographic order"})
        for i in infos:
            if i.date_time != FIXED_DATE_TIME:
                policy.append({"code": "NONFIXED_TIMESTAMP", "path": i.filename, "message": f"{i.date_time}"})
            if i.external_attr >> 16 != FILE_MODE or i.create_system != 3:
                policy.append({"code": "NONNORMAL_PERMISSIONS", "path": i.filename, "message": f"mode {oct(i.external_attr >> 16)} system {i.create_system}"})
            if i.compress_type != zipfile.ZIP_STORED:
                policy.append({"code": "NONSTORED_COMPRESSION", "path": i.filename, "message": f"compress_type {i.compress_type}"})
        if out["directory_entries"]:
            policy.append({"code": "DIRECTORY_ENTRIES", "path": "", "message": "builder never writes directory entries"})

        if not out["errors"]:  # hash + extract only archives whose entries are all safe
            for i in files:
                h = hashlib.sha256()
                with zf.open(i) as f:
                    for chunk in iter(lambda: f.read(CHUNK), b""):
                        h.update(chunk)
                out["files"].append({"path": i.filename, "bytes": i.file_size, "compressed_bytes": i.compress_size, "sha256": h.hexdigest()})
            if run_validation:
                tmp_dir = None
                try:
                    with tempfile.TemporaryDirectory(prefix="inspect_submission_") as tmp:
                        tmp_dir = base = Path(tmp).resolve()
                        for i in files:
                            dest = (base / i.filename).resolve()
                            if base not in dest.parents:  # Zip Slip: re-checked after path validation
                                raise _Unsafe(i.filename)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(i) as src, open(dest, "xb") as dst:
                                for chunk in iter(lambda: src.read(CHUNK), b""):
                                    dst.write(chunk)
                        out["validation"] = vs.validate(base).to_dict()
                except _Unsafe as e:
                    err("PATH_TRAVERSAL", str(e), "resolved extraction path left the temp directory")
                except (OSError, zipfile.BadZipFile) as e:
                    err("EXTRACTION_FAILED", "", f"{type(e).__name__} during extraction")
                out["temp_extraction_removed"] = tmp_dir is not None and not tmp_dir.exists()
    v = out.get("validation", {})
    out["ok"] = not out["errors"] and not out["policy_violations"] and v.get("structural") == vs.STRUCTURAL_VALID
    return out


class _Unsafe(Exception):
    pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("zip", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = inspect(args.zip)
    if args.json:
        print(json.dumps(r, indent=2, sort_keys=True))
    else:
        v = r.get("validation", {})
        print(f"{'OK' if r['ok'] else 'FAIL'} zip_sha256={r['zip_sha256']} files={len(r['files'])} "
              f"uncompressed={r.get('uncompressed_bytes')} compressed={r.get('compressed_bytes')}")
        for e in r["errors"] + r["policy_violations"]:
            print(f"  [{e['code']}] {e['path'] or '.'}: {e['message']}")
        if v:
            print(f"  extracted bundle: {v['structural']} · {v['harness_compatibility']} "
                  f"(errors {len(v['errors'])}, certification issues {len(v['certification_issues'])})")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
