"""Passive, append-only observations derived from lifecycle and native evidence.

No callback wraps a model or tool. Native traces are normalized after execution;
their prompt, arguments and result bodies are represented only by entry hashes.
Missing native events (including requests and tool body starts) stay missing.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import time

from ._contracts import (
    ContractError, canonical_json, closed_dict, hex_digest, identifier,
    load_json_object, nonnegative_int, text,
)
from .runtime_result import elapsed


EVENT_TYPES = frozenset((
    "solver_started", "solver_finished", "verifier_started", "verifier_finished",
    "model_request", "model_response", "tool_attempt", "tool_rejected", "tool_started",
    "tool_result", "command", "file_edit", "status_call", "submit_patch", "fallback",
    "timeout", "exception", "patch_apply", "verification", "native_trace_entry",
))


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    sequence: int
    elapsed_seconds: float | None
    task_id: str
    worker_phase: str
    event_type: str
    agent: str | None = None
    tool_name: str | None = None
    native_reference: str | None = None
    native_sha256: str | None = None
    budget_state_json: str | None = None
    elapsed_origin: str = "worker"

    def __post_init__(self):
        if type(self) is not RuntimeEvent:
            raise ContractError("RuntimeEvent must be the exact contract type")
        nonnegative_int(self.sequence, "sequence")
        elapsed(self.elapsed_seconds, optional=True)
        text(self.elapsed_origin, "elapsed_origin")
        if self.elapsed_origin not in ("worker", "native_trace", "UNKNOWN"):
            raise ContractError("elapsed_origin must identify the observed clock")
        identifier(self.task_id, "task_id")
        text(self.worker_phase, "worker_phase")
        if self.worker_phase not in ("solver", "verifier", "coordinator"):
            raise ContractError("worker_phase must be solver, verifier or coordinator")
        text(self.event_type, "event_type")
        if self.event_type not in EVENT_TYPES:
            raise ContractError("unknown runtime event type")
        for field in ("agent", "tool_name", "native_reference"):
            value = getattr(self, field)
            if value is not None:
                text(value, field, nonempty=True)
        if self.native_sha256 is not None:
            hex_digest(self.native_sha256, "native_sha256")
        if self.budget_state_json is not None:
            budget = load_json_object(self.budget_state_json, "budget_state_json")
            if canonical_json(budget) != self.budget_state_json:
                raise ContractError("budget_state_json must be canonical JSON")

    @classmethod
    def from_dict(cls, value):
        if cls is not RuntimeEvent:
            raise ContractError("RuntimeEvent must be the exact contract type")
        names = tuple(f.name for f in fields(cls))
        closed_dict(value, allowed=names, required=names[:5], name="RuntimeEvent")
        return cls(**value)

    @classmethod
    def from_json(cls, raw):
        return cls.from_dict(load_json_object(raw, "RuntimeEvent"))

    def to_dict(self):
        RuntimeEvent.__post_init__(self)
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def to_json(self):
        return canonical_json(self.to_dict())


_UNSET = object()
_BUDGET_FIELDS = frozenset((
    "tool_calls_used", "tool_calls_remaining", "max_tool_calls", "llm_calls_used",
    "time_seconds_remaining", "max_time_minutes", "agent_elapsed_seconds", "max_turns",
    "command_timeout_seconds",
))


class EventRecorder:
    """An append-only journal; snapshots expose only frozen event values.

    The caller owns evidence sealing. This recorder does not open files, inspect
    workspaces, change context, invoke tools or install model instrumentation.
    """

    __slots__ = ("task_id", "worker_phase", "enabled", "_started", "_events")

    def __init__(self, task_id: str, worker_phase: str, *, enabled: bool = True):
        identifier(task_id, "task_id")
        text(worker_phase, "worker_phase")
        if worker_phase not in ("solver", "verifier", "coordinator"):
            raise ContractError("invalid worker phase")
        if type(enabled) is not bool:
            raise ContractError("enabled must be boolean")
        self.task_id = task_id
        self.worker_phase = worker_phase
        self.enabled = enabled
        self._started = time.monotonic()
        self._events = []

    def append(self, event_type: str, *, elapsed_seconds=_UNSET, agent=None, tool_name=None,
               native_reference=None, native_sha256=None, budget_state_json=None,
               elapsed_origin="worker"):
        if not self.enabled:
            return None
        if elapsed_seconds is _UNSET:
            elapsed_seconds = time.monotonic() - self._started
        event = RuntimeEvent(len(self._events), elapsed_seconds, self.task_id,
                             self.worker_phase, event_type, agent, tool_name,
                             native_reference, native_sha256, budget_state_json, elapsed_origin)
        self._events.append(event)
        return event

    @property
    def events(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)

    def to_json(self):
        return canonical_json([event.to_dict() for event in self._events])

    def capture_native_trace(self, trace_data: dict, *, trace_reference="native_trace.json"):
        """Project exact SessionTrace legacy entry fields after the native run.

        ``tool_call`` proves an attempt, not body execution. ``tool_response``
        proves a response, not body execution. A structured native
        ``error_type=BudgetExceeded`` additionally proves rejection. Status and
        submission activity labels refer to observed attempts. No timeout or
        fallback is guessed from text; those require separate native evidence.
        """
        if not self.enabled:
            return
        if type(trace_data) is not dict or type(trace_data.get("entries")) is not list:
            raise ContractError("native trace must contain a JSON entries array")
        text(trace_reference, "trace_reference", nonempty=True)
        for index, entry in enumerate(trace_data["entries"]):
            if type(entry) is not dict:
                raise ContractError("native trace entry must be a JSON object")
            serialized = canonical_json(entry)
            native_type = entry.get("type")
            if native_type is not None:
                text(native_type, "native entry type")
            event_type = {"tool_call": "tool_attempt", "tool_response": "tool_result",
                          "final": "model_response"}.get(native_type, "native_trace_entry")
            budget_json = None
            response = {}
            if native_type == "tool_response":
                # Legacy native results are JSON strings. Other forms remain
                # hashed native evidence rather than interpreted outcomes.
                result = entry.get("result")
                if type(result) is str:
                    try:
                        response = load_json_object(result, "native tool result")
                    except ContractError:
                        response = {}
                if entry.get("tool") == "get_status":
                    observed = {k: v for k, v in response.items() if k in _BUDGET_FIELDS}
                    if observed:
                        budget_json = canonical_json(observed)
            observed_fields = {
                "elapsed_seconds": entry.get("elapsed_s"),
                "agent": entry.get("author"), "tool_name": entry.get("tool"),
                "native_reference": f"{trace_reference}#/entries/{index}",
                "native_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "budget_state_json": budget_json,
                "elapsed_origin": "native_trace" if entry.get("elapsed_s") is not None else "UNKNOWN",
            }
            self.append(event_type, **observed_fields)
            if native_type == "tool_call" and entry.get("tool") in ("get_status", "submit_patch"):
                activity = "status_call" if entry["tool"] == "get_status" else "submit_patch"
                self.append(activity, **observed_fields)
            if native_type == "tool_response" and response.get("error_type") == "BudgetExceeded":
                self.append("tool_rejected", **observed_fields)
