"""Verify frozen v1 and publish deterministic tune-only screening metadata.

This module never loads tasks.jsonl, task text, gold, assets or a vendor evaluator.
The trusted public loader supplies non-gold metadata and the source-file SHA256.
v1 is read and verified, never regenerated. S4 is deliberately absent from the API.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from tools.common import REPO_ROOT, WriteGuard, dumps, sha256_bytes
from tools.h23_v4.filesystem import anchor_directory, read_regular
from tools.h23_v4.schema import PolicyError

SPLIT_SHA256 = "420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12"
TASKS_SHA256 = "e4b3fd60f69dbc2b9213e54eeb9636db78aefe92c1d06269d73d9f5f8f3c8ad6"
PROJECTION_SHA256 = "5a6188365392b2febde6d3067f5fea9e8c5dddcd7389a6e02a87e4333245df30"
SPLIT_SEED = "gemma4-agent-lab/split/v1"
SCREEN_SEED = "gemma4-agent-lab/screen/v1"
SPLIT_DIR = REPO_ROOT / "eval" / "splits"
SPLIT_PATH = SPLIT_DIR / "v1.json"
SCREEN_PATH = SPLIT_DIR / "screens_v1.json"
_SPLIT_ALGORITHM = (
    "group-by-base_commit; per-repo largest-remainder holdout quota; groups ordered by "
    "sha256(seed:base_commit); greedy fill, overshooting groups go to dev"
)
_PROJECTION_DEFINITION = "sha256 of 'instance_id\\trepo\\tbase_commit\\n' lines sorted by instance_id"
_SCREEN_ALGORITHM = (
    "tune-only; per-repo base_commit groups ordered by sha256(seed:repo:base_commit); "
    "greedy exact-quota fill; task IDs sorted lexicographically"
)
_REPO_PREFIX = {
    "fastapi/fastapi": "fastapi", "Textualize/rich": "rich",
    "psf/requests": "requests", "encode/httpx": "httpx",
}
_DEV_COUNTS = {"fastapi/fastapi": 41, "Textualize/rich": 30, "psf/requests": 8, "encode/httpx": 1}
_HOLDOUT_COUNTS = {"fastapi/fastapi": 26, "Textualize/rich": 18, "psf/requests": 5}
_QUOTAS = {
    "S1": {"fastapi/fastapi": 6, "Textualize/rich": 4, "psf/requests": 1, "encode/httpx": 1},
    "S2": {"fastapi/fastapi": 20, "Textualize/rich": 15, "psf/requests": 4, "encode/httpx": 1},
    "S3": _DEV_COUNTS,
}
# Exact Appendix A memberships, independently checked against the seed algorithm.
_PLAN_IDS = {
    "S1": tuple(sorted("""
        fastapi_14186 fastapi_14372 fastapi_14794 fastapi_14978 fastapi_15763 fastapi_9555
        httpx_3672 requests_7502 rich_3468 rich_3676 rich_3777 rich_4079
    """.split())),
    "S2": tuple(sorted("""
        fastapi_11194 fastapi_11355 fastapi_14186 fastapi_14349 fastapi_14356 fastapi_14372
        fastapi_14448 fastapi_14458 fastapi_14583 fastapi_14794 fastapi_14873 fastapi_14953
        fastapi_14964 fastapi_14978 fastapi_15023 fastapi_15030 fastapi_15280 fastapi_15763
        fastapi_15800 fastapi_9555 httpx_3672 requests_6757 requests_7315 requests_7328
        requests_7502 rich_2725 rich_3043 rich_3063 rich_3064 rich_3468 rich_3472 rich_3486
        rich_3521 rich_3535 rich_3676 rich_3777 rich_3930 rich_3935 rich_3938 rich_4079
    """.split())),
}
_SPLIT_PROOF = object()
_SCREEN_PROOF = object()


class ManifestError(ValueError):
    """A source identity, frozen split or screening manifest failed admission."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _closed(value, keys: set[str], where: str) -> None:
    _require(type(value) is dict and set(value) == keys, f"invalid {where} schema")


@dataclass(frozen=True, slots=True)
class TaskMetadata:
    instance_id: str
    repo: str
    base_commit: str

    def __post_init__(self):
        _require(type(self.repo) is str and self.repo in _REPO_PREFIX, "invalid metadata repository")
        _require(type(self.instance_id) is str and bool(re.fullmatch(
            rf"{_REPO_PREFIX[self.repo]}_[0-9]+", self.instance_id)), "invalid metadata instance_id")
        _require(type(self.base_commit) is str and bool(re.fullmatch(
            "[0-9a-f]{40}", self.base_commit)), "invalid metadata base_commit")


@dataclass(frozen=True, slots=True, init=False)
class VerifiedSplit:
    split_sha256: str
    source_tasks_sha256: str
    input_projection_sha256: str
    tune_tasks: tuple[TaskMetadata, ...]
    _proof: object

    def __init__(self, *args, **kwargs):
        raise TypeError("VerifiedSplit must be obtained from verify_frozen_split")


@dataclass(frozen=True, slots=True, init=False)
class ScreeningManifest:
    sha256: str
    parent_split_sha256: str
    source_tasks_sha256: str
    _stages: tuple[tuple[str, tuple[TaskMetadata, ...]], ...]
    _proof: object

    def __init__(self, *args, **kwargs):
        raise TypeError("ScreeningManifest must be obtained from manifest validation")


def _mint(cls, **values):
    result = object.__new__(cls)
    for key, value in values.items():
        object.__setattr__(result, key, value)
    return result


def _unique_metadata(metadata) -> tuple[TaskMetadata, ...]:
    _require(type(metadata) is tuple and all(type(t) is TaskMetadata for t in metadata),
             "trusted metadata must be a tuple of TaskMetadata")
    _require(len(metadata) == 129, "source must contain exactly 129 metadata tasks")
    _require(len({t.instance_id for t in metadata}) == 129, "duplicate source instance_id")
    return tuple(sorted(metadata, key=lambda t: t.instance_id))


def _validate_frozen_data(data, metadata: tuple[TaskMetadata, ...], tasks_sha256: str):
    """Pure structural checks, also tested independently of the byte-level frozen pin."""
    source = _unique_metadata(metadata)
    _require(type(tasks_sha256) is str and tasks_sha256 == TASKS_SHA256, "public source SHA256 mismatch")
    _closed(data, {
        "algorithm", "base_commits_crossing_split", "counts", "holdout_fraction",
        "holdout_quota_by_repo", "input_projection_definition", "input_projection_sha256",
        "inputs_used", "seed", "split_version", "tasks",
    }, "frozen split")
    _require(data["split_version"] == "v1" and data["seed"] == SPLIT_SEED, "frozen split version/seed mismatch")
    _require(data["algorithm"] == _SPLIT_ALGORITHM, "frozen algorithm mismatch")
    _require(data["inputs_used"] == ["instance_id", "repo", "base_commit"], "frozen input fields mismatch")
    _require(data["holdout_fraction"] == "49/129", "frozen holdout fraction mismatch")
    _require(data["input_projection_definition"] == _PROJECTION_DEFINITION, "projection definition mismatch")
    projection = sha256_bytes("".join(
        f"{t.instance_id}\t{t.repo}\t{t.base_commit}\n" for t in source
    ).encode("utf-8"))
    _require(projection == PROJECTION_SHA256 == data["input_projection_sha256"], "metadata projection mismatch")
    _require(type(data["tasks"]) is list and len(data["tasks"]) == 129, "frozen task count mismatch")
    rows = []
    sides = defaultdict(set)
    by_side = {"dev": [], "holdout": []}
    for row in data["tasks"]:
        _closed(row, {"instance_id", "repo", "base_commit", "split"}, "frozen task")
        task = TaskMetadata(row["instance_id"], row["repo"], row["base_commit"])
        _require(type(row["split"]) is str and row["split"] in by_side, "invalid frozen partition")
        rows.append(task)
        sides[task.base_commit].add((task.repo, row["split"]))
        by_side[row["split"]].append(task)
    _require(len({t.instance_id for t in rows}) == 129, "duplicate frozen instance_id")
    _require(tuple(rows) == source, "frozen/source metadata membership or ordering mismatch")
    _require(all(len(s) == 1 for s in sides.values()), "base_commit crosses repository or partition")
    _require(len(sides) == 127, "unique base_commit count mismatch")
    _require(data["base_commits_crossing_split"] == [], "frozen crossing assertion mismatch")
    expected_counts = {
        "dev": {"total": 80, "by_repo": _DEV_COUNTS, "unique_base_commits": 79},
        "holdout": {"total": 49, "by_repo": _HOLDOUT_COUNTS, "unique_base_commits": 48},
    }
    for side, expected in expected_counts.items():
        tasks = by_side[side]
        actual = {"total": len(tasks), "by_repo": dict(Counter(t.repo for t in tasks)),
                  "unique_base_commits": len({t.base_commit for t in tasks})}
        _require(dumps(actual) == dumps(expected), f"{side} composition mismatch")
    _require(dumps(data["counts"]) == dumps(expected_counts), "frozen counts metadata mismatch")
    _require(dumps(data["holdout_quota_by_repo"]) == dumps(_HOLDOUT_COUNTS | {"encode/httpx": 0}),
             "frozen quotas mismatch")
    return tuple(by_side["dev"])


def _read(path: Path, *, max_bytes: int = 512 * 1024) -> bytes:
    try:
        with anchor_directory(path.parent) as descriptor:
            return read_regular(descriptor, path.name, max_bytes=max_bytes,
                                include=True, reject_hardlinks=True).data
    except PolicyError:
        raise ManifestError(f"cannot safely read manifest file: {path.name}") from None


def _parse(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ManifestError("non-finite JSON constant")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("invalid manifest JSON") from exc


def verify_frozen_split(metadata: tuple[TaskMetadata, ...], *, tasks_sha256: str,
                        split_path: Path = SPLIT_PATH, sidecar_path: Path | None = None) -> VerifiedSplit:
    """Read-only verification against exact frozen bytes and trusted source metadata."""
    split_path = Path(split_path)
    sidecar_path = Path(sidecar_path) if sidecar_path is not None else split_path.with_suffix(".sha256")
    raw = _read(split_path)
    _require(sha256_bytes(raw) == SPLIT_SHA256, "frozen split SHA256 mismatch")
    _require(_read(sidecar_path, max_bytes=512) == f"{SPLIT_SHA256}  {split_path.name}\n".encode(),
             "frozen split sidecar mismatch")
    tune = _validate_frozen_data(_parse(raw), metadata, tasks_sha256)
    return _mint(VerifiedSplit, split_sha256=SPLIT_SHA256, source_tasks_sha256=tasks_sha256,
                 input_projection_sha256=PROJECTION_SHA256, tune_tasks=tune, _proof=_SPLIT_PROOF)


def _admitted_split(split: VerifiedSplit):
    _require(type(split) is VerifiedSplit and getattr(split, "_proof", None) is _SPLIT_PROOF,
             "an admitted VerifiedSplit is required")


def _screen_ids(tune: tuple[TaskMetadata, ...], stage: str) -> tuple[str, ...]:
    selected = []
    for repo, quota in _QUOTAS[stage].items():
        groups = defaultdict(list)
        for task in tune:
            if task.repo == repo:
                groups[task.base_commit].append(task.instance_id)
        ordered = sorted(groups, key=lambda commit: (
            sha256_bytes(f"{SCREEN_SEED}:{repo}:{commit}".encode()), commit))
        used = 0
        for commit in ordered:
            group = groups[commit]
            if used + len(group) <= quota:
                selected.extend(group)
                used += len(group)
        _require(used == quota, f"cannot fill exact {stage} repository quota")
    result = tuple(sorted(selected))
    if stage in _PLAN_IDS:
        _require(result == _PLAN_IDS[stage], f"{stage} algorithm differs from committed plan")
    return result


def build_screen_manifest(split: VerifiedSplit) -> dict:
    """Build new screening metadata only; never produces or rewrites frozen v1."""
    _admitted_split(split)
    stages = {stage: {"task_ids": list(_screen_ids(split.tune_tasks, stage)),
                      "count": sum(quota.values()), "by_repo": dict(quota)}
              for stage, quota in _QUOTAS.items()}
    return {
        "schema_version": 1, "screen_version": "v1", "algorithm_version": 1,
        "algorithm": _SCREEN_ALGORITHM, "seed": SCREEN_SEED,
        "parent_split": {"version": "v1", "sha256": split.split_sha256},
        "source_dataset": {"tasks_sha256": split.source_tasks_sha256,
                           "input_projection_sha256": split.input_projection_sha256},
        "tasks": [{"instance_id": t.instance_id, "repo": t.repo, "base_commit": t.base_commit,
                   "partition": "tune", "group_key": f"{t.repo}@{t.base_commit}"}
                  for t in split.tune_tasks],
        "stages": stages,
    }


def validate_screen_manifest(data, split: VerifiedSplit) -> ScreeningManifest:
    _admitted_split(split)
    _closed(data, {"schema_version", "screen_version", "algorithm_version", "algorithm", "seed",
                   "parent_split", "source_dataset", "tasks", "stages"}, "screening manifest")
    _closed(data["parent_split"], {"version", "sha256"}, "parent split")
    _closed(data["source_dataset"], {"tasks_sha256", "input_projection_sha256"}, "source dataset")
    _closed(data["stages"], {"S1", "S2", "S3"}, "screening stages")
    _require(type(data["tasks"]) is list, "screen tasks must be a list")
    tune = {t.instance_id: t for t in split.tune_tasks}
    seen = []
    for row in data["tasks"]:
        _closed(row, {"instance_id", "repo", "base_commit", "partition", "group_key"}, "screen task")
        task = TaskMetadata(row["instance_id"], row["repo"], row["base_commit"])
        _require(task.instance_id in tune and task == tune[task.instance_id], "screen contains non-tune or mismatched task")
        _require(row["partition"] == "tune", "screen task partition must be tune")
        _require(row["group_key"] == f"{task.repo}@{task.base_commit}", "screen group key mismatch")
        seen.append(task.instance_id)
    _require(tuple(seen) == tuple(tune), "screen task membership or ordering mismatch")
    selections = []
    group_ids = defaultdict(set)
    for task in split.tune_tasks:
        group_ids[task.base_commit].add(task.instance_id)
    previous = set()
    for stage in ("S1", "S2", "S3"):
        row = data["stages"][stage]
        _closed(row, {"task_ids", "count", "by_repo"}, f"{stage} stage")
        ids = row["task_ids"]
        _require(type(ids) is list and all(type(i) is str for i in ids), "stage IDs must be strings")
        _require(len(ids) == len(set(ids)), "duplicate screening instance_id")
        _require(set(ids) <= set(tune), "stage contains non-tune task")
        _require(previous < set(ids), "screening nesting mismatch")
        _require(all(not (set(ids) & group) or group <= set(ids) for group in group_ids.values()),
                 "screening splits a base_commit group")
        _require(type(row["count"]) is int and row["count"] == len(ids), "stage count mismatch")
        actual = dict(Counter(tune[i].repo for i in ids))
        _require(dumps(actual) == dumps(_QUOTAS[stage]) == dumps(row["by_repo"]), "stage composition mismatch")
        selections.append((stage, tuple(tune[i] for i in ids)))
        previous = set(ids)
    expected = build_screen_manifest(split)
    _require(dumps(data) == dumps(expected), "screening schema, identity or exact seeded membership mismatch")
    return _mint(ScreeningManifest, sha256=sha256_bytes(dumps(data).encode()),
                 parent_split_sha256=split.split_sha256, source_tasks_sha256=split.source_tasks_sha256,
                 _stages=tuple(selections), _proof=_SCREEN_PROOF)


def select_screen(manifest: ScreeningManifest, stage: str = "S1") -> tuple[TaskMetadata, ...]:
    _require(type(manifest) is ScreeningManifest and getattr(manifest, "_proof", None) is _SCREEN_PROOF,
             "an admitted ScreeningManifest is required")
    _require(type(stage) is str and stage in _QUOTAS, "only tune screens S1/S2/S3 are supported")
    return dict(manifest._stages)[stage]


def incremental_screen(manifest: ScreeningManifest, stage: str) -> tuple[TaskMetadata, ...]:
    """Membership only: later gates must not count reused tasks as fresh confirmation."""
    tasks = select_screen(manifest, stage)
    prior_stage = {"S1": None, "S2": "S1", "S3": "S2"}[stage]
    prior = set() if prior_stage is None else {t.instance_id for t in select_screen(manifest, prior_stage)}
    return tuple(t for t in tasks if t.instance_id not in prior)


def load_screen_manifest(split: VerifiedSplit, *, path: Path = SCREEN_PATH,
                         sidecar_path: Path | None = None) -> ScreeningManifest:
    path = Path(path)
    sidecar_path = Path(sidecar_path) if sidecar_path is not None else path.with_suffix(".sha256")
    raw = _read(path)
    _require(_read(sidecar_path, max_bytes=512) == f"{sha256_bytes(raw)}  {path.name}\n".encode(),
             "screen sidecar mismatch")
    data = _parse(raw)
    _require(raw == dumps(data).encode(), "screen manifest is not canonically serialized")
    return validate_screen_manifest(data, split)


def write_screen_manifest(split: VerifiedSplit, *, guard: WriteGuard, path: Path = SCREEN_PATH,
                          sidecar_path: Path | None = None) -> ScreeningManifest:
    """Explicit guarded creation; existing mismatched or incomplete files are never replaced."""
    _require(type(guard) is WriteGuard, "an explicit WriteGuard is required")
    path = Path(path)
    sidecar_path = Path(sidecar_path) if sidecar_path is not None else path.with_suffix(".sha256")
    protected_guard = guard.with_extra(SPLIT_PATH, SPLIT_PATH.with_suffix(".sha256"))
    for dest in (path, sidecar_path):
        protected_guard.check(dest)
        _require(not any(p.is_symlink() for p in (dest, *dest.parents)), "symlinked screen output is forbidden")
    _require(path.resolve() != sidecar_path.resolve(), "screen output paths must differ")
    data = build_screen_manifest(split)
    verified = validate_screen_manifest(data, split)
    if path.exists() or sidecar_path.exists():
        _require(path.exists() and sidecar_path.exists(), "incomplete existing screen publication")
        return load_screen_manifest(split, path=path, sidecar_path=sidecar_path)
    protected_guard.write_text(path, dumps(data))
    protected_guard.write_text(sidecar_path, f"{verified.sha256}  {path.name}\n")
    return verified
