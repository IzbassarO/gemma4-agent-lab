"""Trusted public intake; export closed solver views, never raw dataset rows.

The published JSONL is mixed public/gold input and belongs to the trusted intake,
not a future solver process. No vendor evaluator or source discovery is used.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from eval._contracts import ContractError, closed_dict, load_json_object, text
from eval.manifests import ScreeningManifest, TaskMetadata, select_screen
from eval.solver_task import PublicAssetRef, SolverTask
from tools.h23_v4.filesystem import anchor_directory, read_regular
from tools.h23_v4.schema import PolicyError
from tools.inventory_dataset import CODE_INTEL_MIN_BYTES

MAX_TASK_SOURCE_BYTES = 32 * 1024 * 1024
MAX_PUBLIC_ASSET_BYTES = 8 * 1024 * 1024 * 1024
_PUBLIC_FIELDS = frozenset(("instance_id", "repo", "base_commit", "problem_statement", "hints_text"))
_SOURCE_FIELDS = _PUBLIC_FIELDS | {"created_at", "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"}


class PublicDataError(ContractError):
    """Public admission failed; errors never include raw task contents."""


@dataclass(frozen=True, slots=True)
class _PublicRow:
    metadata: TaskMetadata
    problem_statement: str
    hints_text: str


def _project_row(value: object) -> _PublicRow:
    row = closed_dict(value, allowed=_SOURCE_FIELDS, required=_PUBLIC_FIELDS, name="published task")
    metadata = TaskMetadata(row["instance_id"], row["repo"], row["base_commit"])
    problem = text(row["problem_statement"], "problem_statement")
    hints = text(row["hints_text"], "hints_text")
    for name in ("created_at", "patch", "test_patch"):
        if name in row:
            text(row[name], name)
    for name in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        if name in row:
            if type(row[name]) is not list:
                raise PublicDataError(f"{name} must be a JSON array of strings")
            for node in row[name]:
                text(node, name, nonempty=True)
    # Only allowlisted public fields survive; no raw dictionary is retained.
    return _PublicRow(metadata, problem, hints)


@contextmanager
def _public_root(root: Path):
    # Explicit lexical absolute root: no env fallback, expanduser or resolve that
    # could erase symlink/traversal evidence before descriptor admission.
    if not isinstance(root, Path) or not root.is_absolute():
        raise PublicDataError("an explicit absolute public root Path is required")
    try:
        with anchor_directory(root) as descriptor:
            yield descriptor
    except PolicyError as exc:
        raise PublicDataError(f"public admission failed: {exc}") from None


def _read_rows(descriptor: int) -> tuple[tuple[_PublicRow, ...], str]:
    source = read_regular(descriptor, "tasks.jsonl", max_bytes=MAX_TASK_SOURCE_BYTES,
                          include=True, reject_hardlinks=True)
    rows = []
    seen = set()
    for line in source.data.splitlines():
        if not line.strip():
            raise PublicDataError("blank task row")
        projected = _project_row(load_json_object(line, "published task"))
        instance_id = projected.metadata.instance_id
        if instance_id in seen:
            raise PublicDataError("duplicate instance_id")
        seen.add(instance_id)
        rows.append(projected)
    if not rows:
        raise PublicDataError("empty public tasks source")
    return tuple(rows), source.sha256


def load_public_metadata(root: Path) -> tuple[TaskMetadata, ...]:
    """Project the explicit published source onto ID/repo/base commit only."""
    with _public_root(root) as descriptor:
        rows, _ = _read_rows(descriptor)
        return tuple(row.metadata for row in rows)


def tasks_source_sha256(root: Path) -> str:
    """Full source-byte identity, not a metadata projection or inventory hash."""
    with _public_root(root) as descriptor:
        return read_regular(descriptor, "tasks.jsonl", max_bytes=MAX_TASK_SOURCE_BYTES,
                            include=False, reject_hardlinks=True).sha256


def _admit_asset(descriptor: int, metadata: TaskMetadata, kind: str) -> PublicAssetRef:
    if kind == "snapshot":
        asset_id, relative = metadata.instance_id, f"snapshots/{metadata.instance_id}.tgz"
    else:
        asset_id = f"{metadata.repo.split('/')[1]}_{metadata.base_commit}"
        directory, extension = ("graphs", "json") if kind == "graph" else ("embeddings", "npz")
        relative = f"{directory}/{asset_id}.{extension}"
    # Public exports may legitimately deduplicate with hardlinks. Full stream
    # SHA/size still govern identity, and runtime staging makes a fresh copy.
    # The mixed/gold tasks.jsonl source above retains single-link enforcement.
    observed = read_regular(descriptor, relative, max_bytes=MAX_PUBLIC_ASSET_BYTES,
                            include=False, reject_hardlinks=False)
    if observed.size == 0 or (kind != "snapshot" and observed.size <= CODE_INTEL_MIN_BYTES):
        raise PublicDataError(f"empty or unavailable public {kind} asset")
    # read_regular streams every byte: inventory partial_sha256 is never used.
    return PublicAssetRef(kind, asset_id, relative, observed.sha256, observed.size)


def solver_task_from_public_row(root: Path, row: dict, *, include_code_intelligence: bool = True) -> SolverTask:
    """Trusted projection/admission primitive; it does not select an experiment.

    Routine screening must use load_solver_tasks, which validates tune membership.
    This primitive accepts an explicitly supplied published row for synthetic
    admission or later trusted intake; never pass that row into a solver worker.
    """
    if type(include_code_intelligence) is not bool:
        raise PublicDataError("include_code_intelligence must be bool")
    projected = _project_row(row)
    with _public_root(root) as descriptor:
        return _solver_view(descriptor, projected, include_code_intelligence)


def _solver_view(descriptor: int, row: _PublicRow, code_intelligence: bool) -> SolverTask:
    snapshot = _admit_asset(descriptor, row.metadata, "snapshot")
    graph = _admit_asset(descriptor, row.metadata, "graph") if code_intelligence else None
    embedding = _admit_asset(descriptor, row.metadata, "embedding") if code_intelligence else None
    return SolverTask(1, row.metadata.instance_id, row.metadata.repo, row.metadata.base_commit,
                      row.problem_statement, row.hints_text, snapshot, graph, embedding)


def load_solver_tasks(root: Path, screening: ScreeningManifest, screen: str = "S1", *,
                      include_code_intelligence: bool = True) -> tuple[SolverTask, ...]:
    """Admit a validated tune screen; there is no holdout selection mode."""
    if type(screening) is not ScreeningManifest:
        raise PublicDataError("a validated ScreeningManifest is required")
    if type(include_code_intelligence) is not bool:
        raise PublicDataError("include_code_intelligence must be bool")
    selected = select_screen(screening, screen)
    with _public_root(root) as descriptor:
        rows, source_sha = _read_rows(descriptor)
        if source_sha != screening.source_tasks_sha256:
            raise PublicDataError("public source identity differs from screening admission")
        by_id = {row.metadata.instance_id: row for row in rows}
        admitted = []
        for metadata in selected:
            row = by_id.get(metadata.instance_id)
            if row is None or row.metadata != metadata:
                raise PublicDataError("screen task differs from public metadata")
            admitted.append(_solver_view(descriptor, row, include_code_intelligence))
        return tuple(admitted)
