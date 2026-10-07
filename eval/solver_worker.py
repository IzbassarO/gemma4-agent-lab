"""Fresh solver entry: only the closed SolverTask and staged public inputs."""
from __future__ import annotations

from datetime import datetime, timezone
import sys
import time
from pathlib import Path

from ._contracts import ContractError, load_json_object
from .solver_task import SolverTask
from .staging import validate_staged_solver_assets
from .runtime_events import EventRecorder
from .runtime_result import SolverRunResult, capture_runtime_fingerprint
from .worker_common import (E0_SHA256, evidence_artifacts, patch_sha256, process_observation, read_request,
                            seal_json, validate_request)
from tools.h23_v4.filesystem import anchor_directory, read_regular


def main() -> None:
    request = validate_request(read_request(sys.stdin))
    task = SolverTask.from_dict(request["task"])
    runtime = request["runtime"]
    evidence = Path(runtime["evidence_root"])
    validate_staged_solver_assets(task, Path(runtime["public_root"]))
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    recorder = EventRecorder(task.instance_id, "solver", enabled=request["observation_enabled"])
    recorder.append("solver_started")
    # This seal contains only the solver request, process metadata and imports.
    # Verifier module/code/evidence are not staged in this process root.
    seal_json(evidence / "process_observation.json", process_observation(request))
    if request["mode"] == "isolation_probe":
        native = {"returned_patch": "", "agent_error": None, "terminal_reason": "isolation_probe"}
    else:
        from .runtime_adapter import solver_execute
        native = solver_execute(request)
    if type(native) is not dict or type(native.get("returned_patch")) is not str:
        raise ContractError("native solver did not return patch text")
    if request["observation_enabled"]:
        trace_ref = native.get("native_trace_ref")
        if trace_ref is not None:
            with anchor_directory(evidence) as fd:
                observed = read_regular(fd, trace_ref["relative_path"], max_bytes=16 * 1024 * 1024,
                                        include=True, reject_hardlinks=True)
            if observed.sha256 != trace_ref["sha256"] or observed.size != trace_ref["size_bytes"]:
                raise ContractError("native trace identity mismatch")
            recorder.capture_native_trace(load_json_object(observed.data, "native trace"),
                                          trace_reference=trace_ref["relative_path"])
        for name in ("fallback", "timeout", "exception"):
            key = {"fallback": "fallback_used", "timeout": "timeout", "exception": "escaped_exception"}[name]
            if native.get(key):
                recorder.append(name, native_reference="native_observation.json")
    recorder.append("solver_finished")
    seal_json(evidence / "events.json", {"events": [event.to_dict() for event in recorder.events]})
    seal_json(evidence / "native_observation.json", native)
    provenance = request["provenance"]
    fingerprint = capture_runtime_fingerprint(**provenance, candidate_sha256=E0_SHA256,
                                              sandbox_image=runtime["image"], model_endpoint_identity=runtime["model_endpoint"])
    status = native.get("runtime_status", "completed")
    observations = {name: native.get(name) for name in (
        "submitted_patch", "workspace_patch", "agent_error", "escaped_exception", "llm_calls",
        "counted_tool_calls", "tool_attempts", "fallback_used", "timeout", "budget_exhausted", "terminal_reason")}
    result = SolverRunResult(1, task.instance_id, status, started_at, time.monotonic() - started,
                             native["returned_patch"], patch_sha256(native["returned_patch"]),
                             **observations, artifacts=evidence_artifacts(evidence), fingerprint=fingerprint)
    seal_json(evidence / "result.json", result.to_dict())


if __name__ == "__main__":
    main()
