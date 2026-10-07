"""Worker protocol shared without trusted intake or verifier imports."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from ._contracts import ContractError, canonical_json, closed_dict, load_json_object
from .solver_task import SolverTask
from tools.h23_v4.filesystem import anchor_directory, read_regular

E0_SIZE = 443572
E0_SHA256 = "25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3"
CASES = ("H05", "H04", "H18", "H13", "H14", "H29", "H29_BOUNDARY", "EMPTY_SUBMIT", "EMPTY_FINAL")
RUNTIME_FIELDS = ("harness_root", "public_root", "worker_root", "evidence_root",
                  "submission_root", "wheels_root", "setup_root", "sandbox", "image",
                  "model_endpoint", "source_wheels_root", "pytest_support_root")


def read_request(stream) -> dict:
    raw = stream.buffer.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ContractError("worker request exceeds protocol limit")
    return load_json_object(raw, "worker request")


def validate_request(value: dict, *, verifier: bool = False) -> dict:
    required = ("schema_version", "task", "runtime", "candidate", "observation_enabled",
                "synthetic_case", "mode", "provenance")
    private = ("returned_patch", "returned_patch_sha256", "agent_error", "test_patch", "verification_config") if verifier else ()
    closed_dict(value, allowed=(*required, *private), required=(*required, *private), name="worker request")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("worker schema must be 1")
    if type(value["observation_enabled"]) is not bool:
        raise ContractError("observation_enabled must be bool")
    if value["synthetic_case"] not in CASES or value["mode"] not in ("isolation_probe", "native_scripted"):
        raise ContractError("only admitted synthetic execution is available")
    runtime = closed_dict(value["runtime"], allowed=RUNTIME_FIELDS, required=RUNTIME_FIELDS, name="runtime")
    if runtime["sandbox"] != "subprocess" or runtime["model_endpoint"] != "SCRIPTED_ONLY":
        raise ContractError("public/model execution requires later operator admission")
    root = Path(runtime["worker_root"])
    if not root.is_absolute() or Path.cwd() != root:
        raise ContractError("worker must start in its explicit root")
    with anchor_directory(root, private=True):
        pass
    for key in ("public_root", "evidence_root", "submission_root", "wheels_root", "setup_root", "source_wheels_root"):
        path = Path(runtime[key])
        if not path.is_absolute() or not path.is_relative_to(root):
            raise ContractError("worker runtime paths must be phase-local")
        with anchor_directory(path, private=True):
            pass
    if runtime["pytest_support_root"] is not None:
        if not verifier:
            raise ContractError("solver cannot receive verification support")
        path = Path(runtime["pytest_support_root"])
        if not path.is_absolute() or not path.is_relative_to(root):
            raise ContractError("verification support must be phase-local")
        with anchor_directory(path, private=True):
            pass
    candidate = closed_dict(value["candidate"], allowed=("relative_path", "sha256", "size_bytes"),
                            required=("relative_path", "sha256", "size_bytes"), name="candidate")
    if candidate != {"relative_path": "submission.zip", "sha256": E0_SHA256, "size_bytes": E0_SIZE}:
        raise ContractError("candidate must match frozen E0")
    with anchor_directory(Path(runtime["submission_root"]), private=True) as descriptor:
        observed = read_regular(descriptor, "submission.zip", max_bytes=E0_SIZE, include=False,
                                reject_hardlinks=True, private=True)
    if observed.sha256 != E0_SHA256 or observed.size != E0_SIZE:
        raise ContractError("frozen candidate identity mismatch")
    closed_dict(value["provenance"], allowed=("git_head", "solver_contract_sha256", "task_manifest_sha256"),
                required=("git_head", "solver_contract_sha256", "task_manifest_sha256"), name="provenance")
    if not verifier:
        task = SolverTask.from_dict(value["task"])
        if task.repo != "synthetic/probe" or not task.instance_id.startswith("synthetic_"):
            raise ContractError("public DEV execution is disabled in this tranche")
        if task.sha256() != value["provenance"]["solver_contract_sha256"]:
            raise ContractError("solver contract identity mismatch")
    return value


def seal_json(path: Path, value) -> None:
    """Publish once; private regular bytes, never following an existing output."""
    if path.exists() or path.is_symlink():
        raise ContractError("sealed worker evidence already exists")
    data = canonical_json(value).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".seal-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic, no-overwrite publication; the transient second link is our own
        # fresh staging inode, never a dataset or private source inode.
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ContractError("sealed evidence is not private and singly linked")
    finally:
        Path(temporary).unlink(missing_ok=True)


def patch_sha256(patch: str) -> str:
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def evidence_artifacts(root: Path):
    from .runtime_result import ArtifactRef
    results = []
    for path in sorted(root.rglob("*")):
        # The coordinator seals process.log after the process has exited.
        if path == root / "process.log" or (path.is_dir() and not path.is_symlink()):
            continue
        with anchor_directory(path.parent) as fd:
            observed = read_regular(fd, path.name, max_bytes=16 * 1024 * 1024, include=False,
                                    reject_hardlinks=True)
        results.append(ArtifactRef(path.suffix.lstrip(".") or "evidence", path.relative_to(root).as_posix(),
                                   observed.sha256, observed.size))
    return tuple(results)


def process_observation(request: dict) -> dict:
    """Read process metadata for sentinel tests; never read descriptor contents."""
    open_fds = []
    for number in range(256):
        try:
            os.fstat(number)
        except OSError:
            continue
        open_fds.append(number)
    return {"pid": os.getpid(), "cwd": str(Path.cwd()), "argv": list(__import__("sys").argv),
            "environment": dict(os.environ), "open_fds": open_fds,
            "request": request, "imports": sorted(__import__("sys").modules)}
