"""Immutable runtime observations; no vendor imports, discovery or private data IO.

``None`` means unobserved for optional observations. Fingerprint strings use
``UNKNOWN`` explicitly. Patch identity always covers the actual returned text.
"""
from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
import hashlib
from importlib import metadata
import math
import platform

from ._contracts import (
    ContractError, canonical_json, closed_dict, hex_digest, identifier,
    load_json_object, nonnegative_int, schema_version, text,
)


UNKNOWN = "UNKNOWN"
_STATUSES = frozenset(("completed", "error", "timeout", "budget_exhausted", "worker_error"))


def patch_sha256(patch: str) -> str:
    return hashlib.sha256(text(patch, "returned_patch").encode("utf-8")).hexdigest()


def elapsed(value, field="elapsed_seconds", *, optional=False):
    if optional and value is None:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ContractError(f"{field} must be finite and nonnegative")


def _optional_text(value, field):
    if value is not None:
        text(value, field)


def _optional_bool(value, field):
    if value is not None and type(value) is not bool:
        raise ContractError(f"{field} must be boolean or None (unobserved)")


def _optional_count(value, field):
    if value is not None:
        nonnegative_int(value, field)


def _identity(value, field, *, length=64):
    if value != UNKNOWN:
        hex_digest(value, field, length=length)
    else:
        text(value, field)


def _exact(value, cls, field):
    if type(value) is not cls:
        raise ContractError(f"{field} must be an exact {cls.__name__}")
    cls.__post_init__(value)


def _artifacts(value):
    if type(value) is not tuple:
        raise ContractError("artifacts must be an immutable tuple")
    paths = set()
    for ref in value:
        _exact(ref, ArtifactRef, "artifacts")
        if ref.relative_path in paths:
            raise ContractError("artifacts contains duplicate relative_path entries")
        paths.add(ref.relative_path)


def _fingerprint(value):
    if value is not None:
        _exact(value, RuntimeFingerprint, "fingerprint")


class _ClosedRecord:
    """Uniform closed serialization, with fresh JSON containers on every call."""

    __slots__ = ()

    @classmethod
    def from_dict(cls, value):
        declared = fields(cls)
        required = tuple(f.name for f in declared
                         if f.default is MISSING and f.default_factory is MISSING)
        closed_dict(value, allowed=tuple(f.name for f in declared), required=required,
                    name=cls.__name__)
        values = dict(value)
        if "artifacts" in values:
            if type(values["artifacts"]) is not list:
                raise ContractError("artifacts must be a JSON array")
            values["artifacts"] = tuple(ArtifactRef.from_dict(v) for v in values["artifacts"])
        if values.get("fingerprint") is not None:
            values["fingerprint"] = RuntimeFingerprint.from_dict(values["fingerprint"])
        if "solver_result" in values:
            values["solver_result"] = SolverRunResult.from_dict(values["solver_result"])
        if values.get("verifier_result") is not None:
            values["verifier_result"] = VerifierRunResult.from_dict(values["verifier_result"])
        return cls(**values)

    @classmethod
    def from_json(cls, raw):
        return cls.from_dict(load_json_object(raw, cls.__name__))

    def to_dict(self):
        type(self).__post_init__(self)
        result = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, _ClosedRecord):
                value = value.to_dict()
            elif type(value) is tuple:
                value = [v.to_dict() for v in value]
            result[field.name] = value
        return result

    def to_json(self):
        return canonical_json(self.to_dict())

    def identity_sha256(self):
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactRef(_ClosedRecord):
    """A sealed evidence file, relative to its phase's separate evidence root."""

    kind: str
    relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self):
        if type(self) is not ArtifactRef:
            raise ContractError("ArtifactRef must be the exact contract type")
        identifier(self.kind, "kind")
        text(self.relative_path, "relative_path", nonempty=True)
        parts = self.relative_path.split("/")
        if (any(p in ("", ".", "..") for p in parts) or "\\" in self.relative_path
                or ":" in self.relative_path or any(ord(c) < 32 for c in self.relative_path)):
            raise ContractError("relative_path must be a canonical relative evidence path")
        hex_digest(self.sha256, "sha256")
        nonnegative_int(self.size_bytes, "size_bytes")


@dataclass(frozen=True, slots=True)
class RuntimeFingerprint(_ClosedRecord):
    """Explicit provenance, without environment values, credentials or endpoint URLs."""

    git_head: str = UNKNOWN
    candidate_sha256: str = UNKNOWN
    solver_contract_sha256: str = UNKNOWN
    task_manifest_sha256: str = UNKNOWN
    python_version: str = UNKNOWN
    platform: str = UNKNOWN
    architecture: str = UNKNOWN
    swegemma_version: str = UNKNOWN
    adk_submission_version: str = UNKNOWN
    adk_eval_core_version: str = UNKNOWN
    google_adk_version: str = UNKNOWN
    google_genai_version: str = UNKNOWN
    litellm_version: str = UNKNOWN
    sandbox_image: str = UNKNOWN
    model_endpoint_identity: str = UNKNOWN

    def __post_init__(self):
        if type(self) is not RuntimeFingerprint:
            raise ContractError("RuntimeFingerprint must be the exact contract type")
        _identity(self.git_head, "git_head", length=40)
        for name in ("candidate_sha256", "solver_contract_sha256", "task_manifest_sha256"):
            _identity(getattr(self, name), name)
        for field in fields(self):
            text(getattr(self, field.name), field.name, nonempty=True)
        # These are public labels, never arbitrary network configuration.
        if any(token in self.model_endpoint_identity for token in ("@", "?", "#", "://")):
            raise ContractError("model endpoint identity must be a credential-free label")
        if any(token in self.sandbox_image for token in ("?", "#", "://")):
            raise ContractError("sandbox image identity must be a credential-free image label")


def capture_runtime_fingerprint(*, git_head=UNKNOWN, candidate_sha256=UNKNOWN,
                                solver_contract_sha256=UNKNOWN, task_manifest_sha256=UNKNOWN,
                                sandbox_image=UNKNOWN, model_endpoint_identity=UNKNOWN):
    """Read this process's installed metadata; never import the harness or search data.

    The coordinator supplies admitted identities. This helper does not discover
    Git, candidate files, manifests, model configuration or environment roots.
    """
    versions = {}
    for field, distribution in (
        ("swegemma_version", "swegemma"), ("adk_submission_version", "adk-submission"),
        ("adk_eval_core_version", "adk-eval-core"), ("google_adk_version", "google-adk"),
        ("google_genai_version", "google-genai"), ("litellm_version", "litellm"),
    ):
        try:
            versions[field] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[field] = UNKNOWN
    return RuntimeFingerprint(
        git_head=git_head, candidate_sha256=candidate_sha256,
        solver_contract_sha256=solver_contract_sha256, task_manifest_sha256=task_manifest_sha256,
        python_version=platform.python_version(), platform=platform.system(),
        architecture=platform.machine() or UNKNOWN, sandbox_image=sandbox_image,
        model_endpoint_identity=model_endpoint_identity, **versions,
    )


@dataclass(frozen=True, slots=True)
class SolverRunResult(_ClosedRecord):
    schema_version: int
    task_id: str
    runtime_status: str
    started_at: str
    elapsed_seconds: float
    returned_patch: str
    returned_patch_sha256: str
    submitted_patch: str | None = None
    workspace_patch: str | None = None
    agent_error: str | None = None
    escaped_exception: str | None = None
    llm_calls: int | None = None
    counted_tool_calls: int | None = None
    tool_attempts: int | None = None
    fallback_used: bool | None = None
    timeout: bool | None = None
    budget_exhausted: bool | None = None
    terminal_reason: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    fingerprint: RuntimeFingerprint | None = None

    def __post_init__(self):
        if type(self) is not SolverRunResult:
            raise ContractError("SolverRunResult must be the exact contract type")
        _run_header(self)
        text(self.started_at, "started_at", nonempty=True)
        elapsed(self.elapsed_seconds)
        text(self.returned_patch, "returned_patch")
        hex_digest(self.returned_patch_sha256, "returned_patch_sha256")
        if patch_sha256(self.returned_patch) != self.returned_patch_sha256:
            raise ContractError("returned patch hash does not match actual returned text")
        for name in ("submitted_patch", "workspace_patch", "agent_error", "escaped_exception", "terminal_reason"):
            _optional_text(getattr(self, name), name)
        for name in ("llm_calls", "counted_tool_calls", "tool_attempts"):
            _optional_count(getattr(self, name), name)
        for name in ("fallback_used", "timeout", "budget_exhausted"):
            _optional_bool(getattr(self, name), name)


def _run_header(value):
    schema_version(value.schema_version)
    identifier(value.task_id, "task_id")
    text(value.runtime_status, "runtime_status")
    if value.runtime_status not in _STATUSES:
        raise ContractError("unknown runtime_status")
    _artifacts(value.artifacts)
    _fingerprint(value.fingerprint)


@dataclass(frozen=True, slots=True)
class VerifierRunResult(_ClosedRecord):
    schema_version: int
    task_id: str
    runtime_status: str
    returned_patch_sha256: str
    resolved: bool | None = None
    native_result_json: str | None = None
    error: str | None = None
    elapsed_seconds: float | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    fingerprint: RuntimeFingerprint | None = None

    def __post_init__(self):
        if type(self) is not VerifierRunResult:
            raise ContractError("VerifierRunResult must be the exact contract type")
        _run_header(self)
        hex_digest(self.returned_patch_sha256, "returned_patch_sha256")
        _optional_bool(self.resolved, "resolved")
        _optional_text(self.error, "error")
        elapsed(self.elapsed_seconds, optional=True)
        if self.native_result_json is not None:
            native = load_json_object(self.native_result_json, "native_result_json")
            if canonical_json(native) != self.native_result_json:
                raise ContractError("native_result_json must be canonical JSON")


@dataclass(frozen=True, slots=True)
class TaskRuntimeResult(_ClosedRecord):
    schema_version: int
    task_id: str
    solver_result: SolverRunResult
    verifier_result: VerifierRunResult | None = None
    resolved: bool | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    fingerprint: RuntimeFingerprint | None = None

    def __post_init__(self):
        if type(self) is not TaskRuntimeResult:
            raise ContractError("TaskRuntimeResult must be the exact contract type")
        schema_version(self.schema_version)
        identifier(self.task_id, "task_id")
        _exact(self.solver_result, SolverRunResult, "solver_result")
        if self.task_id != self.solver_result.task_id:
            raise ContractError("solver task identity does not agree")
        _optional_bool(self.resolved, "resolved")
        if self.verifier_result is not None:
            _exact(self.verifier_result, VerifierRunResult, "verifier_result")
            if self.task_id != self.verifier_result.task_id:
                raise ContractError("verifier task identity does not agree")
            if self.solver_result.returned_patch_sha256 != self.verifier_result.returned_patch_sha256:
                raise ContractError("verifier must consume the authoritative returned patch")
            if self.resolved != self.verifier_result.resolved:
                raise ContractError("resolved must equal the native verifier observation")
        elif self.resolved is not None:
            raise ContractError("resolved cannot be known without a verifier result")
        _artifacts(self.artifacts)
        _fingerprint(self.fingerprint)
