"""Deterministic dev / locked-holdout split from NON-GOLD metadata only (see eval/splits/SPLIT_POLICY.md).

Usage:
    GEMMA4_DATASET_ROOT=... python -m tools.build_split            # write eval/splits/v1.json + v1.sha256 (refuses to change a frozen split)
    GEMMA4_DATASET_ROOT=... python -m tools.build_split --check    # regenerate in memory and compare with the frozen files

Inputs: instance_id, repo, base_commit. Every other task field (patch, test_patch, problem_statement, ...) is
discarded the moment a line is parsed and never reaches the algorithm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from tools.common import DATASET_ENV, REPO_ROOT, dataset_root, dumps, guard_output, open_nofollow, write_text_safe

VERSION = "v1"
SEED = "gemma4-agent-lab/split/v1"
ALLOWED_INPUTS = ("instance_id", "repo", "base_commit")
HOLDOUT_FRACTION = Fraction(49, 129)
SPLIT_DIR = REPO_ROOT / "eval" / "splits"


class FrozenSplitError(RuntimeError):
    pass


def load_inputs(tasks_path: Path) -> list[dict]:
    """Parse tasks.jsonl and immediately project each row onto ALLOWED_INPUTS."""
    rows = []
    with open_nofollow(tasks_path) as f:
        for line in f:
            if line.strip():
                full = json.loads(line)
                rows.append({k: full[k] for k in ALLOWED_INPUTS})
                del full  # gold fields go out of scope here
    return rows


def _order_key(seed: str, base_commit: str) -> str:
    return hashlib.sha256(f"{seed}:{base_commit}".encode()).hexdigest()


def holdout_quotas(repo_sizes: dict[str, int], fraction: Fraction) -> dict[str, int]:
    """Largest-remainder apportionment of round(total*fraction) holdout slots across repos (ties: repo name)."""
    total = round(sum(repo_sizes.values()) * fraction)
    exact = {r: n * fraction for r, n in repo_sizes.items()}
    quota = {r: int(x) for r, x in exact.items()}
    by_remainder = sorted(repo_sizes, key=lambda r: (-(exact[r] - quota[r]), r))
    for r in by_remainder[: total - sum(quota.values())]:
        quota[r] += 1
    return quota


def build_split(rows: list[dict], seed: str = SEED, fraction: Fraction = HOLDOUT_FRACTION) -> dict:
    ids = [r["instance_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate instance_id")
    groups: dict[str, list[dict]] = defaultdict(list)  # base_commit -> tasks
    for r in rows:
        groups[r["base_commit"]].append(r)
    for commit, g in groups.items():
        if len({t["repo"] for t in g}) != 1:
            raise ValueError(f"base_commit {commit} spans repos")

    repo_sizes: dict[str, int] = defaultdict(int)
    for r in rows:
        repo_sizes[r["repo"]] += 1
    quotas = holdout_quotas(dict(repo_sizes), fraction)

    assign: dict[str, str] = {}
    for repo in sorted(repo_sizes):
        commits = sorted((c for c, g in groups.items() if g[0]["repo"] == repo), key=lambda c: (_order_key(seed, c), c))
        taken = 0
        for c in commits:  # greedy in seeded-hash order; a group that would overshoot the quota stays in dev
            size = len(groups[c])
            side = "holdout" if taken + size <= quotas[repo] else "dev"
            taken += size if side == "holdout" else 0
            for t in groups[c]:
                assign[t["instance_id"]] = side

    tasks = sorted(({**r, "split": assign[r["instance_id"]]} for r in rows), key=lambda t: t["instance_id"])
    counts = {s: {"total": 0, "by_repo": defaultdict(int), "base_commits": set()} for s in ("dev", "holdout")}
    for t in tasks:
        c = counts[t["split"]]
        c["total"] += 1
        c["by_repo"][t["repo"]] += 1
        c["base_commits"].add(t["base_commit"])
    projection = "".join(f"{r['instance_id']}\t{r['repo']}\t{r['base_commit']}\n" for r in sorted(rows, key=lambda r: r["instance_id"]))
    return {
        "split_version": VERSION,
        "algorithm": "group-by-base_commit; per-repo largest-remainder holdout quota; groups ordered by "
                     "sha256(seed:base_commit); greedy fill, overshooting groups go to dev",
        "seed": seed,
        "holdout_fraction": f"{fraction.numerator}/{fraction.denominator}",
        "inputs_used": list(ALLOWED_INPUTS),
        "input_projection_sha256": hashlib.sha256(projection.encode()).hexdigest(),
        "input_projection_definition": "sha256 of 'instance_id\\trepo\\tbase_commit\\n' lines sorted by instance_id",
        "holdout_quota_by_repo": dict(sorted(quotas.items())),
        "counts": {s: {"total": c["total"], "by_repo": dict(sorted(c["by_repo"].items())),
                       "unique_base_commits": len(c["base_commits"])} for s, c in counts.items()},
        "base_commits_crossing_split": sorted(counts["dev"]["base_commits"] & counts["holdout"]["base_commits"]),
        "tasks": tasks,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset-root", help=f"overrides ${DATASET_ENV}")
    ap.add_argument("--out-dir", type=Path, default=SPLIT_DIR)
    ap.add_argument("--check", action="store_true", help="verify frozen files; write nothing")
    args = ap.parse_args(argv)
    root = dataset_root(args.dataset_root)
    json_path, sha_path = args.out_dir / f"{VERSION}.json", args.out_dir / f"{VERSION}.sha256"
    for p in (json_path, sha_path):
        guard_output(p, root)

    text = dumps(build_split(load_inputs(root / "tasks.jsonl")))
    digest = hashlib.sha256(text.encode()).hexdigest()
    sha_line = f"{digest}  {json_path.name}\n"

    if json_path.exists() or sha_path.exists():
        same = json_path.exists() and json_path.read_text(encoding="utf-8") == text and \
            sha_path.exists() and sha_path.read_text(encoding="utf-8") == sha_line
        if not same:
            raise FrozenSplitError(f"{VERSION} is frozen and the regenerated split differs; create a new version "
                                   "with a documented methodological reason instead (SPLIT_POLICY.md)")
        print(f"{VERSION} unchanged sha256={digest}")
        return 0
    if args.check:
        raise FrozenSplitError(f"{json_path} does not exist")
    write_text_safe(json_path, text, root)
    write_text_safe(sha_path, sha_line, root)
    print(f"wrote {VERSION} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
