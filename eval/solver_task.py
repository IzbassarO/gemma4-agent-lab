"""Immutable solver-only values. No verifier imports, private IO or discovery."""
from __future__ import annotations

from dataclasses import dataclass

from ._contracts import (
    ContractError, canonical_json, canonical_sha256, closed_dict, hex_digest,
    identifier, load_json_object, nonnegative_int, relative_asset_path, repo_name,
    schema_version, text,
)


@dataclass(frozen=True, slots=True)
class PublicAssetRef:
    """Full content identity of an explicitly admitted public asset, not a partial hash."""

    kind: str
    asset_id: str
    source_relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if type(self) is not PublicAssetRef:
            raise ContractError("PublicAssetRef must be the exact contract type")
        text(self.kind, "kind")
        if self.kind not in ("snapshot", "graph", "embedding"):
            raise ContractError("kind must be snapshot, graph or embedding")
        identifier(self.asset_id, "asset_id")
        relative_asset_path(self.source_relative_path, "source_relative_path", self.kind)
        hex_digest(self.sha256, "sha256")
        nonnegative_int(self.size_bytes, "size_bytes")

    @classmethod
    def from_dict(cls, value) -> PublicAssetRef:
        if cls is not PublicAssetRef:
            raise ContractError("PublicAssetRef must be the exact contract type")
        fields = ("kind", "asset_id", "source_relative_path", "sha256", "size_bytes")
        closed_dict(value, allowed=fields, required=fields, name="PublicAssetRef")
        return cls(**value)

    @classmethod
    def from_json(cls, raw) -> PublicAssetRef:
        if cls is not PublicAssetRef:
            raise ContractError("PublicAssetRef must be the exact contract type")
        return PublicAssetRef.from_dict(load_json_object(raw, "PublicAssetRef"))

    def to_dict(self) -> dict:
        PublicAssetRef.__post_init__(self)
        return {"kind": self.kind, "asset_id": self.asset_id,
                "source_relative_path": self.source_relative_path,
                "sha256": self.sha256, "size_bytes": self.size_bytes}

    def to_json(self) -> str:
        return canonical_json(PublicAssetRef.to_dict(self))


def _asset(value, kind: str) -> None:
    if type(value) is not PublicAssetRef:
        raise ContractError(f"{kind} must be an exact PublicAssetRef")
    PublicAssetRef.__post_init__(value)
    if value.kind != kind:
        raise ContractError(f"{kind} has the wrong public asset kind")


@dataclass(frozen=True, slots=True)
class SolverTask:
    """Only legitimate task-time information, with closed serialization."""

    schema_version: int
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str
    snapshot: PublicAssetRef
    graph: PublicAssetRef | None = None
    embedding: PublicAssetRef | None = None

    def __post_init__(self) -> None:
        if type(self) is not SolverTask:
            raise ContractError("SolverTask must be the exact contract type")
        schema_version(self.schema_version)
        identifier(self.instance_id, "instance_id")
        repo_name(self.repo)
        hex_digest(self.base_commit, "base_commit", length=40)
        text(self.problem_statement, "problem_statement")
        text(self.hints_text, "hints_text")
        _asset(self.snapshot, "snapshot")
        if self.graph is not None:
            _asset(self.graph, "graph")
        if self.embedding is not None:
            _asset(self.embedding, "embedding")

    @classmethod
    def from_dict(cls, value) -> SolverTask:
        if cls is not SolverTask:
            raise ContractError("SolverTask must be the exact contract type")
        required = ("schema_version", "instance_id", "repo", "base_commit",
                    "problem_statement", "hints_text", "snapshot")
        closed_dict(value, allowed=(*required, "graph", "embedding"), required=required, name="SolverTask")
        public = {key: value[key] for key in required if key != "snapshot"}
        public["snapshot"] = PublicAssetRef.from_dict(value["snapshot"])
        for key in ("graph", "embedding"):
            public[key] = (PublicAssetRef.from_dict(value[key])
                           if value.get(key) is not None else None)
        return cls(**public)

    @classmethod
    def from_json(cls, raw) -> SolverTask:
        if cls is not SolverTask:
            raise ContractError("SolverTask must be the exact contract type")
        return SolverTask.from_dict(load_json_object(raw, "SolverTask"))

    def to_dict(self) -> dict:
        SolverTask.__post_init__(self)
        return {"schema_version": self.schema_version, "instance_id": self.instance_id,
                "repo": self.repo, "base_commit": self.base_commit,
                "problem_statement": self.problem_statement, "hints_text": self.hints_text,
                "snapshot": self.snapshot.to_dict(),
                "graph": self.graph.to_dict() if self.graph is not None else None,
                "embedding": self.embedding.to_dict() if self.embedding is not None else None}

    def to_json(self) -> str:
        return canonical_json(SolverTask.to_dict(self))

    def sha256(self) -> str:
        return canonical_sha256(SolverTask.to_dict(self))


def solver_task_dict(task: SolverTask) -> dict:
    """Boundary serializer; a verifier value or subclass can never pass by duck typing."""
    if type(task) is not SolverTask:
        raise ContractError("solver boundary requires an exact SolverTask")
    return task.to_dict()


def solver_task_json(task: SolverTask) -> str:
    return canonical_json(solver_task_dict(task))
