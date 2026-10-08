"""Closed real-runtime values; no native imports or private data discovery."""
from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

from ._contracts import ContractError, closed_dict, hex_digest

E0_SIZE = 443572
E0_SHA256 = "25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3"
SERVED_MODEL = "gemma-4-31b-it-qat-w4a16-ct"
ADAPTER_NAMES = ("main_lora", "tool_lora")
FROZEN_BUDGET = {"time_minutes": 1.0, "tool_calls": 10, "turns": 50, "command_timeout_seconds": 60}
FROZEN_COMPACTION = {"compaction_interval": 5, "overlap_size": 2, "token_threshold": 14336,
                     "event_retention_size": 5, "cache_min_tokens": 2048}
E0_MEMBERS = {
    "agent.yaml": "c7fbbbbdc44778419be8e9f53a85800eeb17d0cf151e0846c46e107401b72034",
    "configs/sampling.yaml": "3dab0b2506dae34ce92fef7b380d6073729104b23fe2cf66d44ac1d2f213aa6a",
    "eval_config.yaml": "adb486b67535fa59b427b79d44dafc131176f1f1964d184e16046ed520ede02d",
    "prompts/system.md": "f1cbf7943ee9687382321b1dfd9cd88581d608c1c8b8e6ea61a763bd1a3234af",
    "prompts/analyzer.md": "c762b062032cc8463015e589ad8b4f0e17296062e1b0ff1e78712eef9f5e2d43",
    "sub_agents/code_analyzer.yaml": "7a764798e36cc68aa38900256245e4aed969b5439b992c7da4105b6ebcb7e62b",
    **{f"adapters/{name}/adapter_config.json": "75a46da2db7f3c70442e5c728f64059aff52cf64174cee7aeb3a4ec37f6e78fb"
       for name in ADAPTER_NAMES},
    **{f"adapters/{name}/adapter_model.safetensors": "dcbedd989af34f5201a39606e0bd4014d351a29162dd96da418782cbbe487ad9"
       for name in ADAPTER_NAMES},
}
PUBLIC_REPOSITORIES = {"fastapi/fastapi": "fastapi", "Textualize/rich": "rich",
                       "psf/requests": "requests", "encode/httpx": "httpx"}


def normalize_endpoint(value: str) -> str:
    """Admit only an explicit, credential-free IPv4 loopback /v1 endpoint."""
    if type(value) is not str:
        raise ContractError("real model endpoint must be an explicit loopback URL")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ContractError("invalid real model endpoint") from None
    if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or port is None
            or not 1 <= port <= 65535 or parts.username is not None or parts.password is not None
            or parts.path != "/v1" or parts.query or parts.fragment
            or value != f"http://127.0.0.1:{port}/v1"):
        raise ContractError("real model endpoint requires http://127.0.0.1:<port>/v1 without credentials")
    return value


def validate_budget(value: dict) -> dict:
    closed_dict(value, allowed=FROZEN_BUDGET, required=FROZEN_BUDGET, name="real budget")
    minutes = value["time_minutes"]
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes != 1:
        raise ContractError("real budget time_minutes must equal frozen 1 minute")
    for name in ("tool_calls", "turns", "command_timeout_seconds"):
        if type(value[name]) is not int or value[name] != FROZEN_BUDGET[name]:
            raise ContractError("real budget must match frozen preregistration")
    return dict(value)


def validate_compaction(value: dict | None) -> dict | None:
    if value is None:
        return None
    closed_dict(value, allowed=FROZEN_COMPACTION, required=FROZEN_COMPACTION, name="real compaction")
    if any(type(value[key]) is not int or value[key] != expected for key, expected in FROZEN_COMPACTION.items()):
        raise ContractError("real compaction must match documented README settings")
    return dict(value)


def validate_public_task_identity(task) -> None:
    prefix = PUBLIC_REPOSITORIES.get(task.repo)
    if prefix is None or re.fullmatch(rf"{prefix}_[0-9]+", task.instance_id) is None:
        raise ContractError("real task must have an official public repository and identifier")


def validate_real_runtime(runtime: dict) -> dict:
    """Validate real values after the caller enforces its closed runtime schema."""
    if runtime.get("sandbox") != "subprocess":
        raise ContractError("real runtime requires official subprocess sandbox")
    normalize_endpoint(runtime.get("model_endpoint"))
    validate_budget(runtime.get("budget"))
    validate_compaction(runtime.get("compaction"))
    user = runtime.get("worker_user")
    if type(user) is not str or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) is None or user == "root":
        raise ContractError("real runtime requires an explicit unprivileged worker_user")
    hex_digest(runtime.get("preregistration_sha256"), "preregistration_sha256")
    return runtime
