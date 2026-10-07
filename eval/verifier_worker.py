"""Fresh verifier entry. No solver workspace or solver process state is accepted."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from ._contracts import ContractError, canonical_json, canonical_sha256, text
from .verifier_task import VerifierTask
from .verifier_data import VerifierMaterial
from .staging import validate_staged_public_asset
from .runtime_events import EventRecorder
from .runtime_result import VerifierRunResult, capture_runtime_fingerprint
from .worker_common import (E0_SHA256, evidence_artifacts, patch_sha256, process_observation, read_request,
                            seal_json, validate_request)


def main() -> None:
    request = validate_request(read_request(sys.stdin), verifier=True)
    task = VerifierTask.from_dict(request["task"])
    if task.repo != "synthetic/probe" or not task.instance_id.startswith("synthetic_"):
        raise ContractError("public verification requires later operator admission")
    material = VerifierMaterial(task, request["test_patch"])
    text(request["returned_patch"], "returned_patch")
    if patch_sha256(request["returned_patch"]) != request["returned_patch_sha256"]:
        raise ContractError("returned patch seal mismatch")
    if request["agent_error"] is not None:
        text(request["agent_error"], "agent_error")
    if canonical_sha256(request["verification_config"]) != task.verification_config_sha256:
        raise ContractError("verification configuration identity mismatch")
    runtime = request["runtime"]
    evidence = Path(runtime["evidence_root"])
    validate_staged_public_asset(Path(runtime["public_root"]), task.snapshot)
    started = time.monotonic()
    recorder = EventRecorder(task.instance_id, "verifier", enabled=request["observation_enabled"])
    recorder.append("verifier_started")
    seal_json(evidence / "process_observation.json", process_observation(request))
    if request["mode"] == "isolation_probe":
        native = {"resolved": None, "error": None, "native_result": None,
                  "input_patch": request["returned_patch"], "input_patch_sha256": request["returned_patch_sha256"]}
    else:
        from .runtime_adapter import verifier_execute
        native = verifier_execute(request)
        recorder.append("verification", native_reference="native_observation.json")
    recorder.append("verifier_finished")
    seal_json(evidence / "events.json", {"events": [event.to_dict() for event in recorder.events]})
    seal_json(evidence / "native_observation.json", native)
    fingerprint = capture_runtime_fingerprint(**request["provenance"], candidate_sha256=E0_SHA256,
                                              sandbox_image=runtime["image"], model_endpoint_identity=runtime["model_endpoint"])
    native_result = native.get("native_result")
    result = VerifierRunResult(1, task.instance_id, "error" if native.get("error") else "completed",
                               request["returned_patch_sha256"], native.get("resolved"),
                               canonical_json(native_result) if native_result is not None else None,
                               native.get("error"), time.monotonic() - started,
                               artifacts=evidence_artifacts(evidence), fingerprint=fingerprint)
    seal_json(evidence / "result.json", result.to_dict())


if __name__ == "__main__":
    main()
