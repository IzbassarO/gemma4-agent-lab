"""Initial failure taxonomy for local runs and Kaggle submissions.

Every failed task gets exactly one primary category. `layer` separates platform/scorer and harness
failures from agent-quality failures, so a scorer exception is never read as an agent regression.
Categories are listed roughly in pipeline order: classify by the EARLIEST stage that failed.
"""
from __future__ import annotations

from enum import Enum


class Layer(str, Enum):
    PLATFORM = "platform"          # Kaggle scorer / GPU / model server; not attributable to the agent
    HARNESS = "harness"            # compile/validation/tool plumbing; usually config or harness bug
    AGENT_PROCESS = "agent_process"  # agent ran but session ended without a usable patch
    AGENT_QUALITY = "agent_quality"  # patch exists but is wrong
    VERIFICATION = "verification"  # verifier environment artifact, not the patch
    UNKNOWN = "unknown"


class Failure(str, Enum):
    def __new__(cls, value: str, layer: Layer, description: str):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.layer = layer
        obj.description = description
        return obj

    PLATFORM_SCORER_EXCEPTION = ("PLATFORM_SCORER_EXCEPTION", Layer.PLATFORM, "Kaggle 'Notebook Threw Exception' / scorer crash / outage; no task-level signal.")
    MODEL_SERVER_START_FAILURE = ("MODEL_SERVER_START_FAILURE", Layer.PLATFORM, "vLLM/Transformers server failed to start or died (OOM, LoRA load, CUDA).")
    HARNESS_COMPILE_VALIDATION = ("HARNESS_COMPILE_VALIDATION", Layer.HARNESS, "validate_directory / compile_submission / single-model rule rejected the bundle.")
    INVALID_TOOL_FATAL = ("INVALID_TOOL_FATAL", Layer.HARNESS, "Undeclared or hallucinated tool call raised and ended the session (H04/H18).")
    TOOL_SERIALIZATION = ("TOOL_SERIALIZATION", Layer.HARNESS, "Tool args/results mangled in transit (double-JSON, int/str typing, escaping) (H06/H07/H17).")
    CONTEXT_OVERFLOW = ("CONTEXT_OVERFLOW", Layer.AGENT_PROCESS, "Prompt + output exceeded max_model_len (32768) or the server rejected the request for length.")
    AGENT_TIMEOUT = ("AGENT_TIMEOUT", Layer.AGENT_PROCESS, "Session wall-clock budget exhausted.")
    TOOL_BUDGET_EXHAUSTED = ("TOOL_BUDGET_EXHAUSTED", Layer.AGENT_PROCESS, "max_tool_calls or max_turns exhausted, or nudges exhausted.")
    NO_PATCH = ("NO_PATCH", Layer.AGENT_PROCESS, "Session ended with an empty diff (no edits, or edits reverted).")
    PATCH_MALFORMED_OR_APPLY_FAIL = ("PATCH_MALFORMED_OR_APPLY_FAIL", Layer.AGENT_QUALITY, "All 4 apply passes failed in Container B.")
    SCRATCH_OR_TEST_POLLUTION = ("SCRATCH_OR_TEST_POLLUTION", Layer.AGENT_QUALITY, "Patch includes scratch files or test/config edits that break apply or tests.")
    LOCALIZATION_WRONG = ("LOCALIZATION_WRONG", Layer.AGENT_QUALITY, "Edited the wrong file/function.")
    ROOT_CAUSE_WRONG = ("ROOT_CAUSE_WRONG", Layer.AGENT_QUALITY, "Right location, wrong diagnosis/fix.")
    EDIT_FAILED = ("EDIT_FAILED", Layer.AGENT_QUALITY, "Intended edit never landed (old_string not found, repeated edit errors).")
    SYNTAX_ERROR = ("SYNTAX_ERROR", Layer.AGENT_QUALITY, "Patch introduces a SyntaxError/ImportError at collection time.")
    TARGETED_TEST_FAIL = ("TARGETED_TEST_FAIL", Layer.AGENT_QUALITY, "FAIL_TO_PASS tests still fail.")
    HIDDEN_TEST_FAIL_BEHAVIORAL = ("HIDDEN_TEST_FAIL_BEHAVIORAL", Layer.AGENT_QUALITY, "Fix plausible but hidden-test behavior differs (API shape, edge cases).")
    OVERFIX_REGRESSION = ("OVERFIX_REGRESSION", Layer.AGENT_QUALITY, "Target tests pass but PASS_TO_PASS tests regress.")
    ENVIRONMENT_VERIFICATION_ARTIFACT = ("ENVIRONMENT_VERIFICATION_ARTIFACT", Layer.VERIFICATION, "Verifier env problem (missing test dep, DNS/network stall); patch may be correct.")
    UNKNOWN = ("UNKNOWN", Layer.UNKNOWN, "Not yet classified. Must be resolved before keep/reject decisions.")


def is_agent_failure(f: Failure | str) -> bool:
    """True only for failures attributable to the agent (process or quality)."""
    return Failure(f).layer in (Layer.AGENT_PROCESS, Layer.AGENT_QUALITY)
