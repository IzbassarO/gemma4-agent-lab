"""Deterministic builder: agent source directory -> submission.zip + manifest.json + provenance.json + SHA256SUMS.

Usage:
    uv run python -m tools.build_submission agents/candidates/<id> [--candidate-id ID] [--experiment-id EXP-...]
                                            [--out-dir DIR] [--dataset-root DIR] [--require-clean]

Fail-closed write safety: the protected dataset root comes from --dataset-root or $GEMMA4_DATASET_ROOT (both are
protected when both are given); without either the CLI refuses to run. The source tree is protected too.
Default output: artifacts/submissions/<candidate-id>/ (gitignored). The source is only read.

Packaging happens only if static validation is STRUCTURAL_VALID; certification issues (uncertified harness behavior)
are recorded, never hidden, and do not block a build.

--require-clean proves, per packaged file, that the working-tree bytes equal the blob in HEAD (tracked, not ignored,
not staged-different, not modified) AND that the repository has no other uncommitted changes (the builder code
itself affects the output). There is no override.

ZIP policy (byte-identical output for byte-identical input, independent of zlib versions):
  entries = validator's packaged set, sorted by POSIX path; no directory entries; no wrapper directory;
  ZIP_STORED (no compression); date_time 1980-01-01 00:00:00; create_system Unix, mode 0644; no extra fields/comments.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from tools import hash_submission, inspect_submission
from tools import validate_submission as vs
from tools.common import (
    CHUNK,
    DATASET_ENV,
    REPO_ROOT,
    DatasetRootError,
    UnsafeOutputError,
    WriteGuard,
    dumps,
    open_nofollow,
    sha256_file,
    tree_sha256,
)

BUILDER_VERSION = "2"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "submissions"
FIXED_DATE_TIME = inspect_submission.FIXED_DATE_TIME
FILE_MODE = inspect_submission.FILE_MODE
CANDIDATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class BuildError(RuntimeError):
    pass


def _zipinfo(name: str, size: int) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
    zi.compress_type = zipfile.ZIP_STORED
    zi.create_system = 3
    zi.external_attr = FILE_MODE << 16
    zi.file_size = size
    return zi


def write_zip(source: Path, files: list[str], fh) -> list[dict]:
    """Write a deterministic STORED zip of `files` (relative to source) into binary handle `fh`."""
    entries = []
    with zipfile.ZipFile(fh, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for rel in sorted(files):
            src = source / rel
            size = src.lstat().st_size
            h = hashlib.sha256()
            with open_nofollow(src) as f, zf.open(_zipinfo(rel, size), "w") as out:
                for chunk in iter(lambda: f.read(CHUNK), b""):
                    h.update(chunk)
                    out.write(chunk)
            entries.append({"path": rel, "bytes": size, "sha256": h.hexdigest()})
    return entries


def build(source: Path, *, guard: WriteGuard, candidate_id: str | None = None, experiment_id: str | None = None,
          out_dir: Path | None = None, require_clean: bool = False, official_reference: dict[str, str] | None = None,
          meta_root: Path = REPO_ROOT, extra_provenance: dict | None = None) -> dict:
    """`guard` is mandatory: the caller states the authoritative protected dataset root explicitly."""
    if not isinstance(guard, WriteGuard):
        raise BuildError("build() requires an explicit WriteGuard")
    source = Path(source)
    candidate_id = candidate_id or source.name
    if not CANDIDATE_RE.match(candidate_id):
        raise BuildError(f"candidate id {candidate_id!r} must match {CANDIDATE_RE.pattern}")
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT / candidate_id
    g = guard.with_extra(source)
    outputs = [out_dir / n for n in ("submission.zip", "manifest.json", "provenance.json", "SHA256SUMS")]
    for o in outputs:  # before reading or creating anything
        g.check(o)

    report = vs.validate(source)
    rd = report.to_dict()
    if not report.structural_valid:
        raise BuildError("validation failed; nothing written:\n" + vs.render(rd))
    proof = hash_submission.git_source_proof(source, report.packaged_files)
    if require_clean and (not proof["all_tracked_clean"] or proof["global_dirty"] is not False):
        bad = {f: s for f, s in proof["files"].items() if s != "tracked-clean"}
        raise BuildError(f"--require-clean: unproven source files {bad or '{}'}; repository dirty={proof['global_dirty']}; nothing written")
    hash_submission.environment_fingerprints(meta_root)  # fail before writing if split sidecar is stale

    zip_path, man_path, prov_path, sums_path = outputs
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".staging-") as st:
        staged = Path(st) / "submission.zip"
        with open(staged, "xb") as fh:
            entries = write_zip(source, report.packaged_files, fh)
        ins = inspect_submission.inspect(staged)  # round trip: safe-extract + re-validate before publishing
        if not ins["ok"] or {f["path"]: f["sha256"] for f in ins["files"]} != {e["path"]: e["sha256"] for e in entries}:
            raise BuildError(f"round-trip inspection failed; nothing published: {ins['errors'] + ins['policy_violations']}")
        os.replace(staged, zip_path)
    zsha = sha256_file(zip_path)

    manifest = {  # deterministic: no wall-clock time, no absolute paths, no git state
        "manifest_schema": 1,
        "builder_version": BUILDER_VERSION,
        "candidate_id": candidate_id,
        "zip": {"sha256": zsha, "bytes": zip_path.stat().st_size, "entries": len(entries), "compression": "stored",
                "date_time": "1980-01-01T00:00:00", "unix_mode": "0644", "order": "sorted POSIX path",
                "directory_entries": False, "wrapper_directory": False},
        "files": entries,
        "unpacked_bytes": sum(e["bytes"] for e in entries),
        "source_tree_sha256": tree_sha256({e["path"]: e["sha256"] for e in entries}),
        "excluded_from_package": rd["excluded_files"],
        "validation": rd,
        "roundtrip_inspection": {"ok": True, "temp_extraction_removed": ins["temp_extraction_removed"]},
    }
    g.write_text(man_path, dumps(manifest))
    prov = hash_submission.build_provenance(candidate_id=candidate_id, experiment_id=experiment_id, manifest=manifest,
                                            zip_path=zip_path, builder_version=BUILDER_VERSION, proof=proof,
                                            official_reference=official_reference, meta_root=meta_root,
                                            extra=extra_provenance)
    prov["manifest_sha256"] = sha256_file(man_path)
    g.write_text(prov_path, dumps(prov))
    g.write_text(sums_path, f"{zsha}  submission.zip\n{prov['manifest_sha256']}  manifest.json\n")
    return {"out_dir": out_dir, "zip_sha256": zsha, "manifest": manifest, "provenance": prov}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("--candidate-id")
    ap.add_argument("--experiment-id")
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--dataset-root", help=f"protected dataset root (default ${DATASET_ENV}; one of them is required)")
    ap.add_argument("--require-clean", action="store_true", help="prove every packaged file equals HEAD; refuse otherwise")
    args = ap.parse_args(argv)
    try:
        guard = WriteGuard.from_cli(args.dataset_root)
        res = build(args.source, guard=guard, candidate_id=args.candidate_id, experiment_id=args.experiment_id,
                    out_dir=args.out_dir, require_clean=args.require_clean)
    except (BuildError, DatasetRootError, UnsafeOutputError, hash_submission.ProvenanceError) as e:
        print(f"BUILD FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    v, p = res["manifest"]["validation"], res["provenance"]
    print(f"built {res['out_dir']}/submission.zip")
    print(f"  zip_sha256={res['zip_sha256']} files={res['manifest']['zip']['entries']} unpacked={res['manifest']['unpacked_bytes']}")
    print(f"  {v['structural']} · {v['harness_compatibility']} · certification issues: "
          f"{sorted({i['code'] for i in v['certification_issues']})}")
    print(f"  git_commit={p['git_commit']} source_in_git={p['source_in_git']} repo_dirty={p['git_dirty']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
