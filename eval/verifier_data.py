"""Explicit trusted read of private test evidence; never imported by solver IO.

This is a data admission helper, not a verifier runner or gold extractor.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from eval._contracts import (
    ContractError, canonical_sha256, closed_dict, hex_digest, identifier,
    load_json_object, text,
)
from eval.verifier_task import PrivateTestPatchRef, VerifierTask
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


def _verification_identity(config: dict) -> str:
    fields = ("schema_version", "sandbox", "timeout_seconds", "setup_py_sha256", "wheels_tree_sha256")
    closed_dict(config, allowed=fields, required=fields, name="real verification config")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ContractError("real verification schema must be 1")
    if config["sandbox"] != "subprocess":
        raise ContractError("real verification requires subprocess")
    if type(config["timeout_seconds"]) is not int or config["timeout_seconds"] != 60:
        raise ContractError("real verification requires the frozen command timeout")
    for key in ("setup_py_sha256", "wheels_tree_sha256"):
        hex_digest(config[key], key)
    return canonical_sha256(config)


def _trusted_rows(public_root: Path, task_ids: tuple[str, ...]):
    """Keep the public projection and private source tied to one admitted identity."""
    from eval import public_data

    with public_data._public_root(public_root) as descriptor:
        projected, source_sha = public_data._read_rows(descriptor)
        by_id = {row.metadata.instance_id: row for row in projected}
        if any(task_id not in by_id for task_id in task_ids):
            raise ContractError("requested task is absent from the admitted public source")
        # _read_rows deliberately discards private fields. The trusted path alone
        # rereads the exact same source identity, retaining only requested rows.
        source = read_regular(descriptor, "tasks.jsonl", max_bytes=public_data.MAX_TASK_SOURCE_BYTES,
                              include=True, reject_hardlinks=True)
        if source.sha256 != source_sha:
            raise ContractError("published source changed during private admission")
        selected = {}
        requested = set(task_ids)
        for line in source.data.splitlines():
            row = load_json_object(line, "published task")
            if row["instance_id"] in requested:
                selected[row["instance_id"]] = row
        snapshots = {task_id: public_data._admit_asset(descriptor, by_id[task_id].metadata, "snapshot")
                     for task_id in task_ids}
        return by_id, selected, snapshots


def publish_private_test_patches(public_root: Path, task_ids, private_root: Path, *,
                                 guard: WriteGuard, verification_config: dict) -> dict[str, VerifierTask]:
    """Publish explicit verifier identities; never export private locators to a solver.

    The caller owns experiment/screen admission. This helper does no discovery,
    never overwrites a prior publication, and admits snapshots through precisely
    the public admission primitive used for SolverTask.
    """
    from tools.common import WriteGuard

    if type(guard) is not WriteGuard:
        raise ContractError("private publication requires an explicit WriteGuard")
    if type(task_ids) not in (list, tuple) or not task_ids:
        raise ContractError("private publication requires explicit task IDs")
    task_ids = tuple(identifier(item, "task_id") for item in task_ids)
    if len(set(task_ids)) != len(task_ids):
        raise ContractError("private publication contains duplicate task IDs")
    if not isinstance(private_root, Path) or not private_root.is_absolute():
        raise ContractError("private publication requires an absolute private root")
    # Guard before creating anything. Descriptor admission below still rejects
    # lexical symlinks, even when WriteGuard resolves their target safely.
    guarded = guard.with_extra(public_root)
    guarded.check(private_root)
    for task_id in task_ids:
        guarded.check(private_root / f"{task_id}.test.patch")
    config_sha = _verification_identity(verification_config)
    rows, private_rows, snapshots = _trusted_rows(public_root, task_ids)
    pending = {}
    tasks = {}
    for task_id in task_ids:
        row = private_rows[task_id]
        if "test_patch" not in row:
            raise ContractError("requested task lacks explicit private test evidence")
        data = text(row["test_patch"], "test_patch").encode("utf-8")
        if len(data) > MAX_TEST_PATCH_BYTES:
            raise ContractError("private test evidence exceeds the admission limit")
        metadata = rows[task_id].metadata
        tasks[task_id] = VerifierTask(
            1, task_id, metadata.repo, metadata.base_commit, snapshots[task_id],
            PrivateTestPatchRef(sha256_bytes(data), len(data)), config_sha, None, None,
        )
        pending[f"{task_id}.test.patch"] = data
    try:
        with anchor_directory(private_root.parent) as parent:
            try:
                os.mkdir(private_root.name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        with anchor_directory(private_root, private=True) as descriptor:
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
                raise ContractError("private publication root must have mode 0700")
            # Refuse the whole requested batch before creating its first file.
            for name in pending:
                try:
                    os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ContractError("private test publication already exists")
            for name, data in pending.items():
                file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=descriptor)
                try:
                    os.fchmod(file_fd, 0o600)
                    view = memoryview(data)
                    while view:
                        count = os.write(file_fd, view)
                        if count <= 0:
                            raise ContractError("private publication write failed")
                        view = view[count:]
                    os.fsync(file_fd)
                finally:
                    os.close(file_fd)
                observed = read_regular(descriptor, name, max_bytes=MAX_TEST_PATCH_BYTES,
                                        include=False, reject_hardlinks=True, private=True)
                if observed.sha256 != sha256_bytes(data) or observed.size != len(data):
                    raise ContractError("private publication identity mismatch")
            os.fsync(descriptor)
    except (OSError, PolicyError):
        raise ContractError("private test publication failed safe filesystem admission") from None
    return tasks


def read_reference_patch(public_root: Path, instance_id: str) -> str:
    """Trusted verifier-control read only; reference bytes must not be logged.

    This module is excluded from solver staging. The driver labels its control
    root gold-assisted/verifier-only and excludes it from normal reports.
    """
    instance_id = identifier(instance_id, "instance_id")
    _, rows, _ = _trusted_rows(public_root, (instance_id,))
    if "patch" not in rows[instance_id]:
        raise ContractError("requested control lacks an explicit reference patch")
    return text(rows[instance_id]["patch"], "reference patch")
