"""E0 official control: restore/verify the official sample, validate it, and package it unchanged.

Usage:
    uv run python -m tools.package_official_control [--dataset-root DIR] [--out-dir DIR]

The resolved dataset root (explicit --dataset-root, else $GEMMA4_DATASET_ROOT) becomes ONE immutable WriteGuard
that every downstream writing step receives; safety does not depend on the environment variable when the flag is
given. All output paths are checked before anything is restored, created or written.

1. tools.preserve_sample restores gitignored files (adapters) from <dataset>/sample_submission and verifies the
   tree hash; it never overwrites.
2. Static validation. The sample's `../` includes are reported as H26_UNRESOLVED_PARENT_INCLUDE, NOT fixed.
3. Deterministic build to artifacts/submissions/e0_official_control/.
4. Every packaged file's sha256 must equal both the recorded official manifest and the live dataset sample.
   Provenance labels each file's origin truthfully: git-tracked, or external-official-control (the gitignored
   adapters). source_in_git is therefore false for E0, by design.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools import build_submission, hash_submission, preserve_sample
from tools.common import DATASET_ENV, REPO_ROOT, DatasetRootError, UnsafeOutputError, WriteGuard

CANDIDATE_ID = "e0_official_control"
CONTROL_DIR = REPO_ROOT / "agents" / "baseline_v0_official"
CONTROL_MANIFEST = REPO_ROOT / "vendor_meta" / "baseline_v0_official_manifest.json"


def package(guard: WriteGuard, out_dir: Path | None = None, *, control_dir: Path | None = None,
            control_manifest: Path | None = None) -> dict:
    control_dir = Path(control_dir) if control_dir else CONTROL_DIR  # resolved at call time (tests redirect it)
    control_manifest = Path(control_manifest) if control_manifest else CONTROL_MANIFEST
    out_dir = Path(out_dir) if out_dir else build_submission.DEFAULT_OUT / CANDIDATE_ID
    for n in ("submission.zip", "manifest.json", "provenance.json", "SHA256SUMS"):
        guard.with_extra(control_dir).check(out_dir / n)  # refuse before restoring or creating anything
    guard.check(control_dir)
    sample = guard.dataset_root / "sample_submission"
    src_m = preserve_sample.preserve(sample, control_dir, protected=guard.protected)
    recorded = json.loads(Path(control_manifest).read_text(encoding="utf-8"))
    if recorded["tree_sha256"] != src_m["tree_sha256"]:
        raise build_submission.BuildError("dataset sample differs from the recorded official manifest; re-inventory first")
    official = {k: v["sha256"] for k, v in src_m["files"].items()}  # live dataset hashes
    res = build_submission.build(control_dir, guard=guard, candidate_id=CANDIDATE_ID, out_dir=out_dir,
                                 official_reference=official, extra_provenance={
                                     "official_control": {
                                         "source": "<dataset>/sample_submission",
                                         "official_sample_tree_sha256": recorded["tree_sha256"],
                                         "source_manifest": "vendor_meta/baseline_v0_official_manifest.json",
                                         "modified": False}})
    packaged = {f["path"]: f["sha256"] for f in res["manifest"]["files"]}
    if packaged != official or packaged != {k: v["sha256"] for k, v in recorded["files"].items()}:
        raise build_submission.BuildError("packaged files differ from the official sample")
    origins = {o["origin"] for o in res["provenance"]["source_origins"].values()}
    if not origins <= {hash_submission.ORIGIN_GIT, hash_submission.ORIGIN_OFFICIAL}:
        raise build_submission.BuildError(f"E0 has files of unproven origin: {res['provenance']['source_origins']}")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset-root", help=f"protected + source dataset root (default ${DATASET_ENV})")
    ap.add_argument("--out-dir", type=Path)
    args = ap.parse_args(argv)
    try:
        res = package(WriteGuard.from_cli(args.dataset_root), args.out_dir)
    except (build_submission.BuildError, DatasetRootError, UnsafeOutputError, hash_submission.ProvenanceError) as e:
        print(f"E0 PACKAGING FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    v, p = res["manifest"]["validation"], res["provenance"]
    counts = {}
    for o in p["source_origins"].values():
        counts[o["origin"]] = counts.get(o["origin"], 0) + 1
    print(f"E0 official control: {res['out_dir']}/submission.zip")
    print(f"  zip_sha256={res['zip_sha256']}")
    print(f"  source_tree_sha256={p['source_tree_sha256']} (per-file == official sample: True)")
    print(f"  source origins: {counts} · source_in_git={p['source_in_git']}")
    print(f"  {v['structural']} · {v['harness_compatibility']}")
    for i in v["certification_issues"]:
        print(f"  [{i['code']}] {i['path'] or '.'}: {i['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
