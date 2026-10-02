"""Read-only wheelhouse fingerprint (H23). Parses wheel METADATA with zipfile; installs nothing.

Usage:
    GEMMA4_DATASET_ROOT=... python -m tools.wheelhouse

Writes vendor_meta/wheelhouse_manifest.json.
"""
from __future__ import annotations

import argparse
import re
import zipfile
from email.parser import HeaderParser
from pathlib import Path

from tools.common import DATASET_ENV, REPO_ROOT, dataset_root, guard_output, open_nofollow, scan_tree, sha256_bytes, sha256_file, write_json

# Packages the certification plan cares about. Presence/absence in the *local* wheelhouse is recorded.
KEY_PACKAGES = (
    "swegemma", "adk-submission", "adk-eval-core", "google-adk", "google-genai", "litellm", "vllm",
    "transformers", "pydantic", "pydantic-core", "networkx", "numpy", "pandas", "pyarrow", "docker",
    "pytest", "starlette", "fastapi", "rich", "requests", "httpx",
)
MANIFEST_VERSION = 2  # v2: symlink/nested-file anomalies


def normalize(name: str) -> str:
    """PEP 503 normalized project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_filename(filename: str) -> dict | None:
    """PEP 427: {name}-{ver}(-{build})?-{py}-{abi}-{plat}.whl. Returns None if not a wheel name."""
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) not in (5, 6):
        return None
    name, version = parts[0], parts[1]
    py, abi, plat = parts[-3:]
    return {"name": name, "normalized": normalize(name), "version": version,
            "build": parts[2] if len(parts) == 6 else None, "python_tag": py, "abi_tag": abi, "platform_tag": plat}


def read_wheel(path: Path) -> dict:
    entry: dict = {"filename": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    fn = parse_filename(path.name)
    entry["filename_parse"] = fn
    try:
        with open_nofollow(path) as fh, zipfile.ZipFile(fh) as zf:
            names = zf.namelist()
            meta_names = [n for n in names if n.count("/") == 1 and n.endswith(".dist-info/METADATA")]
            entry["zip_ok"] = zf.testzip() is None
            entry["py_files"] = sum(1 for n in names if n.endswith(".py"))
            entry["compiled_ext_files"] = sum(1 for n in names if n.endswith((".so", ".pyd", ".dylib")))
            entry["top_level_entries"] = sorted({n.split("/")[0] for n in names if not n.split("/")[0].endswith((".dist-info", ".data"))})
            if meta_names:
                raw = zf.read(meta_names[0])
                hdr = HeaderParser().parsestr(raw.decode("utf-8", errors="replace"))
                entry["metadata"] = {
                    "name": hdr.get("Name"),
                    "version": hdr.get("Version"),
                    "requires_python": hdr.get("Requires-Python"),
                    "requires_dist_count": len(hdr.get_all("Requires-Dist") or []),
                    "metadata_sha256": sha256_bytes(raw),
                }
            else:
                entry["metadata"] = None
    except zipfile.BadZipFile as e:
        entry["zip_ok"] = False
        entry["error"] = f"BadZipFile: {e}"
    md = entry.get("metadata") or {}
    entry["normalized_name"] = normalize(md["name"]) if md.get("name") else (fn or {}).get("normalized")
    entry["version"] = md.get("version") or (fn or {}).get("version")
    entry["filename_metadata_agree"] = bool(fn and md) and fn["normalized"] == entry["normalized_name"] and fn["version"] == md.get("version")
    return entry


def build_manifest(wheels_dir: Path) -> dict:
    files, links = scan_tree(wheels_dir)  # symlinked wheels are reported, never opened
    wheels = [read_wheel(p) for p in files if p.parent == wheels_dir]
    versions: dict[str, list[str]] = {}
    for w in wheels:
        if w["normalized_name"]:
            versions.setdefault(w["normalized_name"], [])
            if w["version"] not in versions[w["normalized_name"]]:
                versions[w["normalized_name"]].append(w["version"])
    key = {k: sorted(versions.get(k, [])) or None for k in KEY_PACKAGES}
    # Fingerprint is over (filename, sha256) pairs only, so it is stable across machines and paths.
    fingerprint = sha256_bytes("".join(f"{w['filename']}\t{w['sha256']}\n" for w in wheels).encode())
    return {
        "manifest_version": MANIFEST_VERSION,
        "dataset_root_env": DATASET_ENV,
        "source_dir": "wheels/",
        "wheel_count": len(wheels),
        "distinct_projects": len(versions),
        "wheelhouse_fingerprint_sha256": fingerprint,
        "fingerprint_definition": "sha256 of lines 'filename\\tsha256\\n' for every wheel, sorted by filename",
        "key_packages": key,
        "key_packages_absent": [k for k, v in key.items() if v is None],
        "project_versions": {k: sorted(v) for k, v in sorted(versions.items())},
        "anomalies": {
            "bad_zip": [w["filename"] for w in wheels if not w.get("zip_ok")],
            "no_metadata": [w["filename"] for w in wheels if not w.get("metadata")],
            "filename_metadata_mismatch": [w["filename"] for w in wheels if w.get("metadata") and not w["filename_metadata_agree"]],
            "symlinks_or_special_not_read": [p.relative_to(wheels_dir).as_posix() for p in links],
            "nested_files_not_read": [p.relative_to(wheels_dir).as_posix() for p in files if p.parent != wheels_dir],
        },
        "wheels": wheels,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset-root", help=f"overrides ${DATASET_ENV}")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "vendor_meta" / "wheelhouse_manifest.json")
    args = ap.parse_args(argv)
    root = dataset_root(args.dataset_root)
    guard_output(args.out, root)  # fail before any work or any write
    m = build_manifest(root / "wheels")
    write_json(args.out, m, root)
    print(f"wheels={m['wheel_count']} projects={m['distinct_projects']} fingerprint={m['wheelhouse_fingerprint_sha256']}")
    print("absent key packages:", ", ".join(m["key_packages_absent"]) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
