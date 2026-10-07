"""Evaluator-only contracts; no gold text, paths, dataset IO or solver conversion."""
from __future__ import annotations

from dataclasses import dataclass

from ._contracts import (
    ContractError, canonical_json, canonical_sha256, closed_dict, hex_digest,
    identifier, load_json_object, nonnegative_int, repo_name, schema_version, text,
)
from .solver_task import PublicAssetRef


@dataclass(frozen=True, slots=True)
class PrivateTestPatchRef:
    """Private test evidence identity. Its storage locator stays in trusted intake."""

    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if type(self) is not PrivateTestPatchRef:
            raise ContractError("PrivateTestPatchRef must be the exact contract type")
        hex_digest(self.sha256, "sha256")
        nonnegative_int(self.size_bytes, "size_bytes")

    @classmethod
    def from_dict(cls, value) -> PrivateTestPatchRef:
        if cls is not PrivateTestPatchRef:
            raise ContractError("PrivateTestPatchRef must be the exact contract type")
        fields = ("sha256", "size_bytes")
        closed_dict(value, allowed=fields, required=fields, name="PrivateTestPatchRef")
        return cls(**value)

    @classmethod
    def from_json(cls, raw) -> PrivateTestPatchRef:
        if cls is not PrivateTestPatchRef:
            raise ContractError("PrivateTestPatchRef must be the exact contract type")
        return PrivateTestPatchRef.from_dict(load_json_object(raw, "PrivateTestPatchRef"))

    def to_dict(self) -> dict:
        PrivateTestPatchRef.__post_init__(self)
        return {"sha256": self.sha256, "size_bytes": self.size_bytes}

    def to_json(self) -> str:
        return canonical_json(PrivateTestPatchRef.to_dict(self))


def _test_nodes(value, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if type(value) is not tuple:
        raise ContractError(f"{field} must be an immutable tuple or None")
    for node in value:
        text(node, field, nonempty=True)
    if len(set(value)) != len(value):
        raise ContractError(f"{field} contains duplicate test nodes")
    return value


def _nodes_from_json(value, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if type(value) is not list:
        raise ContractError(f"{field} must be a JSON array or null")
    return _test_nodes(tuple(value), field)


@dataclass(frozen=True, slots=True)
class VerifierTask:
    """Physically separate verification-only identity, never a solver input."""

    schema_version: int
    instance_id: str
    repo: str
    base_commit: str
    snapshot: PublicAssetRef
    test_patch: PrivateTestPatchRef
    verification_config_sha256: str
    fail_to_pass: tuple[str, ...] | None = None
    pass_to_pass: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if type(self) is not VerifierTask:
            raise ContractError("VerifierTask must be the exact contract type")
        schema_version(self.schema_version)
        identifier(self.instance_id, "instance_id")
        repo_name(self.repo)
        hex_digest(self.base_commit, "base_commit", length=40)
        if type(self.snapshot) is not PublicAssetRef:
            raise ContractError("snapshot must be an exact PublicAssetRef")
        PublicAssetRef.__post_init__(self.snapshot)
        if self.snapshot.kind != "snapshot":
            raise ContractError("snapshot has the wrong public asset kind")
        if type(self.test_patch) is not PrivateTestPatchRef:
            raise ContractError("test_patch must be an exact PrivateTestPatchRef")
        PrivateTestPatchRef.__post_init__(self.test_patch)
        hex_digest(self.verification_config_sha256, "verification_config_sha256")
        _test_nodes(self.fail_to_pass, "fail_to_pass")
        _test_nodes(self.pass_to_pass, "pass_to_pass")

    @classmethod
    def from_dict(cls, value) -> VerifierTask:
        if cls is not VerifierTask:
            raise ContractError("VerifierTask must be the exact contract type")
        required = ("schema_version", "instance_id", "repo", "base_commit", "snapshot",
                    "test_patch", "verification_config_sha256")
        closed_dict(value, allowed=(*required, "fail_to_pass", "pass_to_pass"), required=required, name="VerifierTask")
        private = {key: value[key] for key in required if key not in ("snapshot", "test_patch")}
        private["snapshot"] = PublicAssetRef.from_dict(value["snapshot"])
        private["test_patch"] = PrivateTestPatchRef.from_dict(value["test_patch"])
        for key in ("fail_to_pass", "pass_to_pass"):
            private[key] = _nodes_from_json(value.get(key), key)
        return cls(**private)

    @classmethod
    def from_json(cls, raw) -> VerifierTask:
        if cls is not VerifierTask:
            raise ContractError("VerifierTask must be the exact contract type")
        return VerifierTask.from_dict(load_json_object(raw, "VerifierTask"))

    def to_dict(self) -> dict:
        VerifierTask.__post_init__(self)
        return {"schema_version": self.schema_version, "instance_id": self.instance_id,
                "repo": self.repo, "base_commit": self.base_commit,
                "snapshot": self.snapshot.to_dict(), "test_patch": self.test_patch.to_dict(),
                "verification_config_sha256": self.verification_config_sha256,
                "fail_to_pass": list(self.fail_to_pass) if self.fail_to_pass is not None else None,
                "pass_to_pass": list(self.pass_to_pass) if self.pass_to_pass is not None else None}

    def to_json(self) -> str:
        return canonical_json(VerifierTask.to_dict(self))

    def sha256(self) -> str:
        return canonical_sha256(VerifierTask.to_dict(self))
