"""Preserve the official sample_submission as an immutable control (agents/baseline_v0_official/).

Usage:
    GEMMA4_DATASET_ROOT=... python -m tools.preserve_sample           # copy missing files, then verify
    GEMMA4_DATASET_ROOT=... python -m tools.preserve_sample --verify  # verify only

Copies file bytes only. Never overwrites an existing file; any drift fails verification.
Rejects symlinks/special files in source or destination; never reads bytes through a symlink.
Writes vendor_meta/baseline_v0_official_manifest.json (kept outside the bundle so it is not zipped with it).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tools.common import (
    DATASET_ENV,
    REPO_ROOT,
    dataset_root,
    guard_output,
    iter_files,
    open_nofollow,
    sha256_bytes,
    sha256_file,
    write_bytes_safe,
    write_json,
)

MAX_BYTES = 5 * 1024 * 1024  # stop instead of copying anything that looks like real model weights


class SampleMismatchError(RuntimeError):
    pass


def tree_manifest(root: Path) -> dict:
    files = {p.relative_to(root).as_posix(): {"bytes": p.lstat().st_size, "sha256": sha256_file(p)} for p in iter_files(root)}
    tree = sha256_bytes("".join(f"{k}\t{v['sha256']}\n" for k, v in sorted(files.items())).encode())
    return {"file_count": len(files), "total_bytes": sum(v["bytes"] for v in files.values()),
            "tree_sha256": tree, "tree_sha256_definition": "sha256 of lines 'relpath\\tsha256\\n' sorted by relpath",
            "files": files}


def preserve(src: Path, dest: Path, verify_only: bool = False, protected: tuple[Path, ...] = ()) -> dict:
    """Copy missing files src -> dest, then verify the tree hash. Symlinks anywhere in src or dest are rejected
    (SymlinkError) before any byte is copied; dest may never be inside src or any protected root."""
    guard_output(dest, src, *protected)
    src_m = tree_manifest(src)  # raises SymlinkError on any symlink/special file in the source
    if src_m["total_bytes"] > MAX_BYTES:
        raise SampleMismatchError(f"sample is {src_m['total_bytes']} bytes (> {MAX_BYTES}); refusing to copy, report instead")
    if not verify_only:
        if dest.exists():
            iter_files(dest, ignore_names=())  # refuse to write into a dest tree that contains symlinks
        dest_r = dest.resolve()
        for rel in src_m["files"]:
            out = dest / rel
            if out.exists() or out.is_symlink():
                continue  # fill missing files (e.g. gitignored adapters on a fresh clone); never overwrite
            if dest_r not in guard_output(out, src, *protected).parents:
                raise SampleMismatchError(f"{out} resolves outside {dest_r}")
            with open_nofollow(src / rel) as f:
                write_bytes_safe(out, f.read(), src, *protected)
    dest_m = tree_manifest(dest)
    if dest_m["tree_sha256"] != src_m["tree_sha256"]:
        raise SampleMismatchError(f"{dest} differs from official sample (tree {dest_m['tree_sha256']} != {src_m['tree_sha256']})")
    return src_m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset-root", help=f"overrides ${DATASET_ENV}")
    ap.add_argument("--dest", type=Path, default=REPO_ROOT / "agents" / "baseline_v0_official")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    root = dataset_root(args.dataset_root)
    out = REPO_ROOT / "vendor_meta" / "baseline_v0_official_manifest.json"
    guard_output(out, root)
    m = preserve(root / "sample_submission", args.dest, args.verify, protected=(root,))
    m["source"] = "$GEMMA4_DATASET_ROOT/sample_submission"
    m["copy"] = "agents/baseline_v0_official"
    write_json(out, m, root)
    print(f"OK files={m['file_count']} bytes={m['total_bytes']} tree_sha256={m['tree_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
