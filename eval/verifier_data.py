"""Explicit trusted read of private test evidence; never imported by solver IO.

This is a data admission helper, not a verifier runner or gold extractor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eval._contracts import ContractError, text
from eval.verifier_task import VerifierTask
from tools.common import sha256_bytes
from tools.h23_v4.filesystem import anchor_directory, read_regular
from tools.h23_v4.schema import PolicyError

MAX_TEST_PATCH_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class VerifierMaterial:
    task: VerifierTask
    test_patch: str

    def __post_init__(self):
        if type(self) is not VerifierMaterial:
            raise ContractError("VerifierMaterial must be the exact boundary type")
        if type(self.task) is not VerifierTask:
            raise ContractError("VerifierMaterial requires exactly VerifierTask")
        VerifierTask.__post_init__(self.task)
        text(self.test_patch, "test_patch")
        data = self.test_patch.encode("utf-8")
        if len(data) != self.task.test_patch.size_bytes or sha256_bytes(data) != self.task.test_patch.sha256:
            raise ContractError("private test evidence identity mismatch")


def load_verifier_material(task: VerifierTask, *, private_root: Path,
                           test_patch_relative_path: str) -> VerifierMaterial:
    """Read only the explicit private file matching the verifier content ref.

    No raw public-source extraction, environment fallback or private discovery.
    The caller supplies this path ONLY on the trusted verifier side; the contract
    and solver view contain neither this path nor an inherited descriptor.
    """
    if type(task) is not VerifierTask:
        raise ContractError("private evidence requires exactly VerifierTask")
    VerifierTask.__post_init__(task)
    if not isinstance(private_root, Path) or not private_root.is_absolute():
        raise ContractError("an explicit absolute private root Path is required")
    try:
        with anchor_directory(private_root, private=True) as descriptor:
            observed = read_regular(descriptor, test_patch_relative_path,
                                    max_bytes=MAX_TEST_PATCH_BYTES, include=True,
                                    reject_hardlinks=True, private=True)
    except PolicyError as exc:
        raise ContractError(f"private evidence admission failed: {exc}") from None
    if observed.size != task.test_patch.size_bytes or observed.sha256 != task.test_patch.sha256:
        raise ContractError("private test evidence identity mismatch")
    try:
        value = observed.data.decode("utf-8")
    except UnicodeDecodeError:
        raise ContractError("private test evidence must be UTF-8") from None
    return VerifierMaterial(task, value)
