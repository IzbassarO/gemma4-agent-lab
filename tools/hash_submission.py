"""Provenance generation and independent verification for built submissions.

Usage:
    uv run python -m tools.hash_submission artifacts/submissions/<id> [--dataset-root DIR] [--json]

Verification trusts FILES, not provenance.json. Every claim is classified:
  recomputed     re-derived from submission.zip / manifest.json / repository metadata / git objects and compared
  informational  point-in-time metadata that cannot be re-derived (build time, git_dirty at build, experiment id)
  environment    needs something outside the artifact (the recorded commit, the live dataset); reported
                 UNVERIFIABLE when unavailable, never silently accepted and never a failure by itself
Every check is ok / mismatch / unverifiable; unverifiable is never coerced into ok or mismatch.
Overall status:
  FAILED                                any recomputable fact or available evidence contradicts the artifact   exit 1
  VERIFIED_WITH_UNVERIFIABLE_EVIDENCE   no mismatch, but >= 1 fact could not be checked here (listed)          exit 0 (2 with --strict)
  VERIFIED                              every non-informational fact checked and consistent                     exit 0
`git_evidence` (for source_in_git=true): VERIFIED | FAILED | UNVERIFIABLE; NOT_CLAIMED when source_in_git=false.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from tools import inspect_submission
from tools import validate_submission as vs
from tools.common import CHUNK, DATASET_ENV, REPO_ROOT, sha256_file, tree_sha256

PROVENANCE_SCHEMA = 2
SPLIT_JSON, SPLIT_SIDECAR = "eval/splits/v1.json", "eval/splits/v1.sha256"
ORIGIN_GIT, ORIGIN_OFFICIAL, ORIGIN_UNPROVEN = "git-tracked", "external-official-control", "unproven"
GIT_VERIFIED, GIT_FAILED, GIT_UNVERIFIABLE = "VERIFIED", "FAILED", "UNVERIFIABLE"
GIT_NOT_CLAIMED, GIT_NOT_CHECKED = "NOT_CLAIMED", "NOT_CHECKED"


class ProvenanceError(RuntimeError):
    pass


# ---- git ---------------------------------------------------------------------------------------

def _git(args: list[str], cwd: Path, *, raw: bool = False):
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout if raw else r.stdout.decode("utf-8", "replace").strip()


def git_source_proof(source: Path, files: list[str]) -> dict:
    """Prove, per packaged file, that its working-tree bytes equal the blob at HEAD and the index agrees.

    Statuses: tracked-clean | modified | staged-differs | staged-not-in-HEAD | untracked | ignored | outside-repo.
    `all_tracked_clean` is true only if EVERY file is tracked-clean (and there is at least one file).
    """
    source = Path(source).resolve()
    top = _git(["rev-parse", "--show-toplevel"], source)
    head = _git(["rev-parse", "--verify", "HEAD"], source) if top else None
    if not top or not head:
        return {"git_commit": head, "source_path_in_repo": None, "global_dirty": None,
                "files": {f: "outside-repo" for f in files}, "all_tracked_clean": False}
    top_p = Path(top).resolve()
    src_rel = source.relative_to(top_p).as_posix()
    rel = {f: (f if src_rel == "." else f"{src_rel}/{f}") for f in files}
    paths = sorted(rel.values())

    def table(args: list[str], sha_field: int) -> dict[str, str]:
        out = _git(["--literal-pathspecs", *args, "--", *paths], top_p, raw=True) or b""
        res = {}
        for rec in out.split(b"\0"):
            if b"\t" in rec:
                meta, p = rec.split(b"\t", 1)
                res[p.decode("utf-8", "surrogateescape")] = meta.split()[sha_field].decode()
        return res

    head_blobs = table(["ls-tree", "-r", "-z", "--full-tree", "HEAD"], 2) if paths else {}
    index_blobs = table(["ls-files", "-s", "-z"], 1) if paths else {}
    statuses = {}
    for f, p in rel.items():
        if p not in head_blobs:
            if p in index_blobs:
                statuses[f] = "staged-not-in-HEAD"
            else:
                statuses[f] = "ignored" if _git(["check-ignore", "-q", "--", p], top_p) is not None else "untracked"
        elif index_blobs.get(p) != head_blobs[p]:
            statuses[f] = "staged-differs"
        elif _git(["hash-object", "--no-filters", "--", p], top_p) != head_blobs[p]:
            statuses[f] = "modified"
        else:
            statuses[f] = "tracked-clean"
    return {"git_commit": head, "source_path_in_repo": src_rel,
            "global_dirty": bool(_git(["status", "--porcelain"], top_p)),
            "files": statuses, "all_tracked_clean": bool(files) and all(s == "tracked-clean" for s in statuses.values())}


# ---- environment fingerprints ------------------------------------------------------------------

def environment_fingerprints(repo: Path = REPO_ROOT) -> dict:
    """Recompute from repository metadata. The split is hashed directly; a stale/corrupt sidecar is an error."""
    repo = Path(repo)

    def load(p):
        try:
            return json.loads((repo / p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    dm, wm = load("vendor_meta/dataset_manifest.json"), load("vendor_meta/wheelhouse_manifest.json")
    split_sha = sha256_file(repo / SPLIT_JSON) if (repo / SPLIT_JSON).is_file() else None
    sidecar = repo / SPLIT_SIDECAR
    if sidecar.exists():
        declared = (sidecar.read_text(encoding="utf-8").split() or [""])[0]
        if declared != split_sha:
            raise ProvenanceError(f"{SPLIT_SIDECAR} declares {declared!r} but sha256({SPLIT_JSON}) = {split_sha!r}")
    return {
        "recorded_from": "repository metadata (vendor_meta/; eval/splits/v1.json hashed directly)",
        "dataset_manifest_sha256": sha256_file(repo / "vendor_meta/dataset_manifest.json") if dm else None,
        "tasks_jsonl_sha256": (dm or {}).get("key_file_sha256", {}).get("tasks.jsonl"),
        "harness_readme_sha256": (dm or {}).get("key_file_sha256", {}).get("HARNESS_README.md"),
        "wheelhouse_fingerprint_sha256": (wm or {}).get("wheelhouse_fingerprint_sha256"),
        "split_version": "v1" if split_sha else None,
        "split_sha256": split_sha,
        "harness_versions": None,  # H23 HOST-UNKNOWN: never filled from forum/notebook claims
    }


# ---- provenance --------------------------------------------------------------------------------

def adapter_hashes(files: dict[str, str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for p, h in sorted(files.items()):
        if p.startswith("adapters/"):
            out.setdefault(p.split("/")[1].removesuffix(".safetensors"), {})[p] = h
    return out


def source_origins(proof: dict, files: dict[str, str], official_reference: dict[str, str] | None) -> dict:
    """Origin per packaged file. `official_reference` (E0 only) labels a non-git file as the official control's
    external origin when its sha256 matches; it never upgrades git proof or source_in_git."""
    out = {}
    for f, sha in sorted(files.items()):
        st = proof["files"].get(f, "outside-repo")
        if st == "tracked-clean":
            origin = ORIGIN_GIT
        elif official_reference is not None and official_reference.get(f) == sha:
            origin = ORIGIN_OFFICIAL
        else:
            origin = ORIGIN_UNPROVEN
        out[f] = {"origin": origin, "git_status": st}
    return out


def build_provenance(*, candidate_id: str, experiment_id: str | None, manifest: dict, zip_path: Path,
                     builder_version: str, proof: dict, official_reference: dict[str, str] | None = None,
                     meta_root: Path = REPO_ROOT, extra: dict | None = None) -> dict:
    files = {f["path"]: f["sha256"] for f in manifest["files"]}
    adapters = adapter_hashes(files)
    v = manifest["validation"]
    return {
        "provenance_schema": PROVENANCE_SCHEMA,
        "builder_version": builder_version,
        "candidate_id": candidate_id,
        "experiment_id": experiment_id,
        "build_time_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),  # informational only
        "git_commit": proof["git_commit"],
        "git_dirty": proof["global_dirty"],  # informational (point in time)
        "source_path_in_repo": proof["source_path_in_repo"],
        "source_in_git": proof["all_tracked_clean"],
        "source_origins": source_origins(proof, files, official_reference),
        "source_tree_sha256": tree_sha256(files),
        "source_files": files,
        "submission_zip_sha256": sha256_file(zip_path),
        "submission_zip_bytes": zip_path.stat().st_size,
        "unpacked_bytes": manifest["unpacked_bytes"],
        "manifest_sha256": None,  # filled by the builder after manifest.json is written
        "environment": environment_fingerprints(meta_root),
        "lora_present": bool(adapters),
        "adapter_hashes": adapters,
        "validation": {"structural": v["structural"], "harness_compatibility": v["harness_compatibility"],
                       "certification_issue_codes": sorted({i["code"] for i in v["certification_issues"]})},
        **(extra or {}),
    }


# ---- verification ------------------------------------------------------------------------------

class _Checks:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, name, kind, ok, detail=""):
        status = "unverifiable" if ok is None else ("ok" if ok else "mismatch")
        self.items.append({"check": name, "class": kind, "status": status, "detail": detail})

    def eq(self, name, actual, *claims):
        bad = [c for c in claims if c != actual]
        self.add(name, "recomputed", not bad, "" if not bad else f"recomputed {actual!r}, claimed {bad!r}"[:600])


def _dig(obj, keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def verify(artifact_dir: Path, *, meta_root: Path = REPO_ROOT, git_repo: Path | None = None,
           dataset_root: Path | None = None) -> dict:
    """meta_root: repository whose vendor_meta/ and eval/splits/ are recomputed; git_repo: where the recorded commit
    is looked up (defaults to meta_root)."""
    d = Path(artifact_dir)
    c = _Checks()
    paths = {n: d / n for n in ("submission.zip", "manifest.json", "provenance.json", "SHA256SUMS")}
    missing = [n for n, p in paths.items() if not p.is_file()]
    if missing:
        c.add("artifact_files", "recomputed", False, f"missing {missing}")
        return _result(c)
    try:
        man = json.loads(paths["manifest.json"].read_text(encoding="utf-8"))
        prov = json.loads(paths["provenance.json"].read_text(encoding="utf-8"))
        if not isinstance(man, dict) or not isinstance(prov, dict):
            raise ValueError
    except ValueError:
        c.add("metadata_json", "recomputed", False, "manifest/provenance is not a JSON object")
        return _result(c)
    g = lambda obj, *keys: _dig(obj, keys)

    zsha, zbytes = sha256_file(paths["submission.zip"]), paths["submission.zip"].stat().st_size
    msha = sha256_file(paths["manifest.json"])
    c.eq("zip_sha256", zsha, g(man, "zip", "sha256"), g(prov, "submission_zip_sha256"))
    c.eq("zip_bytes", zbytes, g(man, "zip", "bytes"), g(prov, "submission_zip_bytes"))
    c.eq("manifest_sha256", msha, g(prov, "manifest_sha256"))
    sums = {}
    for line in paths["SHA256SUMS"].read_text(encoding="utf-8").splitlines():
        parts = line.split()
        sums[parts[-1] if parts else ""] = parts[0] if len(parts) == 2 else None
    c.eq("SHA256SUMS", {"submission.zip": zsha, "manifest.json": msha}, sums)

    ins = inspect_submission.inspect(paths["submission.zip"])
    c.add("zip_inspection", "recomputed", ins["ok"], "" if ins["ok"] else str(ins["errors"] + ins["policy_violations"])[:500])
    members = {f["path"]: f["sha256"] for f in ins["files"]}
    sizes = {f["path"]: f["bytes"] for f in ins["files"]}
    man_files = g(man, "files") if isinstance(g(man, "files"), list) else []
    c.eq("archive_members_and_hashes", members, {f.get("path"): f.get("sha256") for f in man_files if isinstance(f, dict)},
         g(prov, "source_files"))
    c.eq("member_sizes", sizes, {f.get("path"): f.get("bytes") for f in man_files if isinstance(f, dict)})
    c.eq("unpacked_bytes", sum(sizes.values()), g(man, "unpacked_bytes"), g(prov, "unpacked_bytes"))
    c.add("unpacked_bytes_nonnegative", "recomputed",
          all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (g(man, "unpacked_bytes"), g(prov, "unpacked_bytes"))))
    c.eq("source_tree_sha256", tree_sha256(members), g(man, "source_tree_sha256"), g(prov, "source_tree_sha256"))
    val = ins.get("validation") or {}
    c.eq("validation_structural", vs.STRUCTURAL_VALID, val.get("structural"), g(man, "validation", "structural"),
         g(prov, "validation", "structural"))
    c.eq("certification_issue_codes", sorted({i["code"] for i in val.get("certification_issues", [])}),
         g(prov, "validation", "certification_issue_codes"))
    c.eq("harness_compatibility", vs.COMPAT_UNKNOWN, g(prov, "validation", "harness_compatibility"))
    adapters = adapter_hashes(members)
    c.eq("adapter_hashes", adapters, g(prov, "adapter_hashes"))
    c.eq("lora_present", bool(adapters), g(prov, "lora_present"))
    c.eq("candidate_id", g(man, "candidate_id"), g(prov, "candidate_id"))

    try:
        env = environment_fingerprints(meta_root)
    except ProvenanceError as e:
        c.add("environment.split_sidecar", "recomputed", False, str(e))
        env = None
    if env is not None:
        for k in ("dataset_manifest_sha256", "tasks_jsonl_sha256", "harness_readme_sha256",
                  "wheelhouse_fingerprint_sha256", "split_version", "split_sha256"):
            if env[k] is None:
                c.add(f"environment.{k}", "environment", None, "repository metadata unavailable")
            else:
                c.eq(f"environment.{k}", env[k], g(prov, "environment", k))
    c.eq("environment.harness_versions", None, g(prov, "environment", "harness_versions"))
    ds = Path(dataset_root) if dataset_root else (Path(os.environ[DATASET_ENV]) if os.environ.get(DATASET_ENV) else None)
    for k, rel in (("tasks_jsonl_sha256", "tasks.jsonl"), ("harness_readme_sha256", "HARNESS_README.md")):
        live = ds / rel if ds else None
        if live and live.is_file():
            c.eq(f"live_dataset.{k}", sha256_file(live), g(prov, "environment", k))
        else:
            c.add(f"live_dataset.{k}", "environment", None, "dataset not available")

    git_evidence = GIT_NOT_CHECKED
    if members and ins["ok"]:
        git_evidence = _verify_origins(c, prov, members, paths["submission.zip"], Path(git_repo or meta_root), ds)
    for k in ("build_time_utc", "git_dirty", "experiment_id", "builder_version"):
        c.items.append({"check": k, "class": "informational", "status": "informational",
                        "detail": "point-in-time metadata; reported, not verified"})
    return _result(c, git_evidence)


def _verify_origins(c: _Checks, prov: dict, members: dict[str, str], zip_path: Path, repo: Path, ds: Path | None) -> str:
    """Per-file origin evidence is tri-state (ok / mismatch / unverifiable) and NEVER collapsed: unavailable git
    objects make a fact UNVERIFIABLE, while any contradiction that can be observed (from provenance itself or from
    available objects) is a mismatch. Returns the aggregate git evidence state for source_in_git."""
    origins = prov.get("source_origins")
    if not isinstance(origins, dict) or set(origins) != set(members) or not all(isinstance(v, dict) for v in origins.values()):
        c.add("source_origins", "recomputed", False, "origins must be objects covering exactly the archive members")
        return GIT_FAILED
    labels = {f: origins[f].get("origin") for f in members}
    commit, src, claimed = prov.get("git_commit"), prov.get("source_path_in_repo"), prov.get("source_in_git")

    # Internal consistency: observable from provenance alone, so never UNVERIFIABLE.
    for f in sorted(members):
        if labels[f] not in (ORIGIN_GIT, ORIGIN_OFFICIAL, ORIGIN_UNPROVEN):
            c.add(f"origin:{f}", "recomputed", False, f"unknown origin label {labels[f]!r}")
        elif (labels[f] == ORIGIN_GIT) != (origins[f].get("git_status") == "tracked-clean"):
            c.add(f"origin_status:{f}", "recomputed", False,
                  f"origin {labels[f]!r} contradicts git_status {origins[f].get('git_status')!r}")
    if not isinstance(claimed, bool):
        c.add("source_in_git", "recomputed", False, "must be a boolean")
        return GIT_FAILED
    all_git_labels = all(lbl == ORIGIN_GIT for lbl in labels.values())
    c.add("source_in_git_consistent_with_origins", "recomputed", claimed == all_git_labels,
          f"source_in_git={claimed} but origins all git-tracked={all_git_labels}")

    # Git-backed facts, one per file.
    git_files = [f for f in sorted(members) if labels[f] == ORIGIN_GIT]
    states = {}
    if git_files and not (isinstance(commit, str) and isinstance(src, str)):
        for f in git_files:
            c.add(f"origin:{f}", "recomputed", False, "git-tracked origin claimed without a recorded commit/source path")
            states[f] = GIT_FAILED
    elif git_files:
        tree_ok = _git(["cat-file", "-e", f"{commit}^{{tree}}"], repo) is not None
        blobs = {}
        if tree_ok:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():  # git blob id of each member, streamed (adapters can be large)
                    if info.filename in git_files:
                        h = hashlib.sha1(b"blob %d\0" % info.file_size)
                        with zf.open(info) as fh:
                            for chunk in iter(lambda: fh.read(CHUNK), b""):
                                h.update(chunk)
                        blobs[info.filename] = h.hexdigest()
        for f in git_files:
            if not tree_ok:
                c.add(f"origin:{f}", "environment", None, f"recorded commit {commit[:12]} is not available in {repo}")
                states[f] = GIT_UNVERIFIABLE
                continue
            p = f if src == "." else f"{src}/{f}"
            listing = _git(["--literal-pathspecs", "ls-tree", "-z", commit, "--", p], repo, raw=True)
            if listing is None:  # tree walk failed (e.g. missing subtree object): evidence unavailable, not a verdict
                c.add(f"origin:{f}", "environment", None, f"tree objects for {p} at {commit[:12]} unavailable")
                states[f] = GIT_UNVERIFIABLE
                continue
            committed = listing.split(b"\t", 1)[0].split()[2].decode() if listing.strip(b"\0") else None
            ok = committed == blobs[f]
            c.add(f"origin:{f}", "recomputed", ok, f"{commit[:12]}:{p} -> {committed or 'ABSENT'}; archive member blob {blobs[f]}")
            states[f] = GIT_VERIFIED if ok else GIT_FAILED

    for f in sorted(members):
        if labels[f] == ORIGIN_OFFICIAL:
            ref = ds / "sample_submission" / f if ds else None
            if ref and ref.is_file():
                c.add(f"origin:{f}", "recomputed", sha256_file(ref) == members[f], "compared with <dataset>/sample_submission")
            else:
                c.add(f"origin:{f}", "environment", None, "official sample not available")

    # Aggregate for source_in_git=true: FAILED beats UNVERIFIABLE beats VERIFIED; never coerced to a boolean.
    if not claimed:
        return GIT_NOT_CLAIMED
    if not all_git_labels or GIT_FAILED in states.values():
        agg = GIT_FAILED
    elif GIT_UNVERIFIABLE in states.values():
        agg = GIT_UNVERIFIABLE
    else:
        agg = GIT_VERIFIED
    c.add("source_in_git", "recomputed" if agg != GIT_UNVERIFIABLE else "environment",
          None if agg == GIT_UNVERIFIABLE else agg == GIT_VERIFIED,
          f"build-time proof claimed; evidence now: {agg}")
    return agg


def _result(c: _Checks, git_evidence: str = None) -> dict:
    mism = [i for i in c.items if i["status"] == "mismatch"]
    unver = [i for i in c.items if i["status"] == "unverifiable"]
    status = "FAILED" if mism else ("VERIFIED_WITH_UNVERIFIABLE_EVIDENCE" if unver else "VERIFIED")
    return {"status": status, "git_evidence": git_evidence or GIT_NOT_CHECKED, "mismatches": mism,
            "unverifiable": unver, "checks": c.items}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artifact_dir", type=Path)
    ap.add_argument("--dataset-root", type=Path, help=f"live dataset for environment checks (default ${DATASET_ENV})")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="treat VERIFIED_WITH_UNVERIFIABLE_EVIDENCE as failure (exit 2)")
    args = ap.parse_args(argv)
    r = verify(args.artifact_dir, dataset_root=args.dataset_root)
    if args.json:
        print(json.dumps(r, indent=2, sort_keys=True))
    else:
        print(f"{r['status']} (git evidence: {r['git_evidence']}; {len(r['checks'])} checks, "
              f"{len(r['mismatches'])} mismatches, {len(r['unverifiable'])} unverifiable)")
        for i in r["mismatches"]:
            print(f"  MISMATCH {i['check']}: {i['detail']}")
        for i in r["unverifiable"]:
            print(f"  UNVERIFIABLE {i['check']}: {i['detail']}")
    if r["status"] == "FAILED":
        return 1
    return 2 if args.strict and r["status"] != "VERIFIED" else 0


if __name__ == "__main__":
    sys.exit(main())
