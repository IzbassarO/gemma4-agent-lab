"""Deterministic, read-only inventory of the Kaggle competition dataset.

Usage:
    GEMMA4_DATASET_ROOT=/path/to/gemma-4-developer-agent python -m tools.inventory_dataset

Writes vendor_meta/dataset_manifest.json and vendor_meta/source_hashes.json.
Opens every dataset file read-only; never writes under the dataset root.
Gold `patch` / `test_patch` fields are only checked for presence and type, never read for content.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from tools.common import (
    DATASET_ENV,
    REPO_ROOT,
    dataset_root,
    guard_output,
    partial_sha256,
    scan_tree,
    sha256_file,
    write_json,
)

REQUIRED = {
    "HARNESS_README.md": "file",
    "tasks.jsonl": "file",
    "sample_submission": "dir",
    "snapshots": "dir",
    "graphs": "dir",
    "embeddings": "dir",
    "wheels": "dir",
    "docker": "dir",
    "sandbox": "dir",
}
# Small official files we fully hash (everything outside the bulk data dirs).
BULK_DIRS = ("snapshots", "graphs", "embeddings", "wheels")
GOLD_FIELDS = ("patch", "test_patch")
# Harness README: graph tools are advertised only when graph .json and embedding .npz are >100 bytes.
CODE_INTEL_MIN_BYTES = 100
INVENTORY_VERSION = 2  # v2: canonical (no allocation/.DS_Store counts), symlinks as metadata


class MissingPathsError(RuntimeError):
    pass


def check_required(root: Path) -> list[str]:
    """Return a list of human-readable problems; empty means all required paths are present."""
    problems = []
    for rel, kind in REQUIRED.items():
        p = root / rel
        if p.is_symlink():
            problems.append(f"symlink not allowed: {rel}")
        elif kind == "file" and not p.is_file():
            problems.append(f"missing file: {rel}")
        elif kind == "dir" and not p.is_dir():
            problems.append(f"missing dir: {rel}")
    return problems


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def inspect_tasks(path: Path) -> dict:
    rows, bad_lines = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad_lines += 1
    key_sets = Counter(",".join(sorted(r)) for r in rows)
    field_types: dict[str, list[str]] = {}
    for r in rows:
        for k, v in r.items():
            t = type(v).__name__
            if t not in field_types.setdefault(k, []):
                field_types[k].append(t)
    ids = [r.get("instance_id") for r in rows]
    commits = [r.get("base_commit") for r in rows]
    created = sorted(r["created_at"] for r in rows if r.get("created_at"))
    return {
        "rows": len(rows),
        "bad_json_lines": bad_lines,
        "unique_instance_ids": len(set(ids)),
        "duplicate_instance_ids": sorted(i for i, c in Counter(ids).items() if c > 1),
        "unique_base_commits": len(set(commits)),
        "repos": dict(sorted(Counter(r.get("repo") for r in rows).items())),
        "nonempty_hints": sum(1 for r in rows if str(r.get("hints_text") or "").strip()),
        "key_sets": dict(key_sets),
        "field_types": {k: sorted(v) for k, v in sorted(field_types.items())},
        "gold_fields_present": {g: sum(1 for r in rows if isinstance(r.get(g), str) and r[g] != "") for g in GOLD_FIELDS},
        "created_at_min": created[0] if created else None,
        "created_at_max": created[-1] if created else None,
        "_tasks": [(r.get("instance_id"), r.get("repo"), r.get("base_commit")) for r in rows],
    }


def inspect_blob_dir(root: Path, files: list[Path], rel: str, suffix: str, full_hash: bool) -> dict:
    """Summarize the regular files directly under root/rel (symlinks were already excluded by scan_tree)."""
    files = [p for p in files if p.parent == root / rel]
    entries = {}
    for p in files:
        e = {"bytes": p.lstat().st_size}
        e["sha256" if full_hash else "partial_sha256"] = sha256_file(p) if full_hash else partial_sha256(p)
        entries[p.name] = e
    sizes = [e["bytes"] for e in entries.values()]
    return {
        "count": len(files),
        "count_with_suffix": sum(1 for p in files if p.name.endswith(suffix)),
        "unexpected_suffix": [p.name for p in files if not p.name.endswith(suffix)],
        "total_bytes": sum(sizes),
        "min_bytes": min(sizes) if sizes else None,
        "max_bytes": max(sizes) if sizes else None,
        "zero_byte": [n for n, e in entries.items() if e["bytes"] == 0],
        "at_or_below_code_intel_threshold": [n for n, e in entries.items() if e["bytes"] <= CODE_INTEL_MIN_BYTES],
        "hash_mode": "sha256" if full_hash else "partial_sha256(size+first/last 1MiB)",
        "files": entries,
    }


def task_coverage(tasks: list[tuple], root: Path, files: list[Path]) -> dict:
    """Map each task to its snapshot and code-intelligence files (regular files only, by naming convention)."""
    present = {_rel(root, p) for p in files}
    missing = {"snapshot": [], "graph": [], "embedding": []}
    for iid, repo, commit in tasks:
        short = str(repo).split("/")[-1]
        if f"snapshots/{iid}.tgz" not in present:
            missing["snapshot"].append(iid)
        if f"graphs/{short}_{commit}.json" not in present:
            missing["graph"].append(iid)
        if f"embeddings/{short}_{commit}.npz" not in present:
            missing["embedding"].append(iid)
    return {"naming": {"snapshot": "{instance_id}.tgz", "graph": "{repo_short}_{base_commit}.json",
                       "embedding": "{repo_short}_{base_commit}.npz"},
            "tasks_missing": missing}


def build_manifest(root: Path) -> tuple[dict, dict]:
    """Canonical manifest: logical sizes and content hashes only (no allocation, mtimes, owners, or paths),
    so the same dataset bytes give the same JSON on any filesystem."""
    problems = check_required(root)
    if problems:
        raise MissingPathsError("; ".join(problems))

    all_files, links = scan_tree(root)  # .DS_Store ignored; symlinks never followed
    symlinks = {_rel(root, p): (os.readlink(p) if p.is_symlink() else "<non-regular>") for p in links}
    by_top: dict[str, dict] = {}
    for p in all_files:
        top = _rel(root, p).split("/")[0]
        b = by_top.setdefault(top, {"files": 0, "bytes": 0})
        b["files"] += 1
        b["bytes"] += p.lstat().st_size

    small = [p for p in all_files if _rel(root, p).split("/")[0] not in BULK_DIRS]
    source_hashes = {_rel(root, p): {"bytes": p.lstat().st_size, "sha256": sha256_file(p)} for p in small}

    tasks = inspect_tasks(root / "tasks.jsonl")
    coverage = task_coverage(tasks.pop("_tasks"), root, all_files)

    wheels = [p.name for p in all_files if p.parent == root / "wheels"]
    manifest = {
        "inventory_version": INVENTORY_VERSION,
        "dataset_root_env": DATASET_ENV,
        "required_paths_ok": True,
        "file_count_excluding_ds_store": len(all_files),
        "symlinks_and_special_files": symlinks,
        "total_logical_bytes": sum(b["bytes"] for b in by_top.values()),
        "by_top_level": dict(sorted(by_top.items())),
        "key_file_sha256": {k: source_hashes[k]["sha256"] for k in ("HARNESS_README.md", "tasks.jsonl")},
        "tasks": tasks,
        "task_coverage": coverage,
        "snapshots": inspect_blob_dir(root, all_files, "snapshots", ".tgz", full_hash=False),
        "graphs": inspect_blob_dir(root, all_files, "graphs", ".json", full_hash=True),
        "embeddings": inspect_blob_dir(root, all_files, "embeddings", ".npz", full_hash=True),
        "wheels": {"count": len(wheels), "non_whl": [w for w in wheels if not w.endswith(".whl")],
                   "detail": "see vendor_meta/wheelhouse_manifest.json"},
        "notes": [
            "Canonical: logical byte sizes (st_size) and content hashes only. No allocation sizes, mtimes, owners, "
            "inode data or absolute paths; .DS_Store files are ignored. Identical bytes => identical JSON on any filesystem.",
            "Symlinks/special files are never followed or hashed; they are listed by relative path with their link text.",
            "Snapshots are not fully hashed (20 GB, immutable); partial_sha256 = sha256(size + first 1MiB + last 1MiB).",
            "Graphs and embeddings are fully SHA256-hashed.",
            "Gold patch/test_patch fields are checked for presence/type only; content is never read into this manifest.",
        ],
    }
    return manifest, {"dataset_root_env": DATASET_ENV, "files": source_hashes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset-root", help=f"overrides ${DATASET_ENV}")
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "vendor_meta")
    args = ap.parse_args(argv)
    root = dataset_root(args.dataset_root)
    outs = (args.out_dir / "dataset_manifest.json", args.out_dir / "source_hashes.json")
    for o in outs:
        guard_output(o, root)  # fail before any work or any write
    manifest, hashes = build_manifest(root)
    write_json(outs[0], manifest, root)
    write_json(outs[1], hashes, root)
    t = manifest["tasks"]
    print(f"files={manifest['file_count_excluding_ds_store']} bytes={manifest['total_logical_bytes']} "
          f"tasks={t['rows']} repos={t['repos']} commits={t['unique_base_commits']} "
          f"graphs={manifest['graphs']['count']} embeddings={manifest['embeddings']['count']} "
          f"wheels={manifest['wheels']['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
