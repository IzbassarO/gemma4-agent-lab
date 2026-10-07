"""Pure, closed-contract validation; no dataset discovery or file access."""
from __future__ import annotations

import json
import math
import re

from tools.common import dumps, sha256_bytes


class ContractError(ValueError):
    """An explicit task contract is malformed or is the wrong boundary type."""


def text(value, field: str, *, nonempty: bool = False) -> str:
    if type(value) is not str or (nonempty and not value):
        raise ContractError(f"{field} must be {'a nonempty' if nonempty else 'a'} string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ContractError(f"{field} must be valid UTF-8 text") from None
    return value


def identifier(value, field: str) -> str:
    text(value, field, nonempty=True)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is None:
        raise ContractError(f"{field} must be a safe identifier")
    return value


def repo_name(value, field: str = "repo") -> str:
    text(value, field, nonempty=True)
    parts = value.split("/")
    if len(parts) != 2:
        raise ContractError(f"{field} must be an owner/repository name")
    for part in parts:
        identifier(part, field)
    return value


def hex_digest(value, field: str, *, length: int = 64) -> str:
    text(value, field)
    if re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise ContractError(f"{field} must be a lowercase {length}-character hex identity")
    return value


def nonnegative_int(value, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractError(f"{field} must be a nonnegative integer")
    return value


def schema_version(value) -> int:
    if type(value) is not int or value != 1:
        raise ContractError("schema_version must be integer 1")
    return value


def relative_asset_path(value, field: str, kind: str) -> str:
    """Accept only canonical flat public asset names, never host/private locators."""
    text(value, field, nonempty=True)
    namespaces = {"snapshot": ("snapshots", (".tgz", ".tar.gz")),
                  "graph": ("graphs", (".json",)), "embedding": ("embeddings", (".npz",))}
    if kind not in namespaces:
        raise ContractError("kind must be snapshot, graph or embedding")
    directory, extensions = namespaces[kind]
    parts = value.split("/")
    if (len(parts) != 2 or parts[0] != directory
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", parts[1]) is None
            or not parts[1].endswith(extensions)):
        raise ContractError(f"{field} must name a canonical public {kind} asset")
    return value


def closed_dict(value, *, allowed, required, name: str) -> dict:
    if type(value) is not dict or any(type(k) is not str for k in value):
        raise ContractError(f"{name} must be a JSON object")
    if set(value) - set(allowed):
        raise ContractError(f"{name} contains unknown fields")
    if set(required) - set(value):
        raise ContractError(f"{name} is missing required fields")
    return value


def load_json_object(raw, name: str) -> dict:
    if type(raw) is bytes:
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ContractError(f"{name} JSON must be UTF-8") from None
    text(raw, f"{name} JSON")

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ContractError(f"{name} JSON contains duplicate object keys")
            result[key] = value
        return result

    def constant(_value):
        raise ContractError(f"{name} JSON contains a nonfinite number")

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError):
        raise ContractError(f"{name} JSON is invalid") from None
    if type(result) is not dict:
        raise ContractError(f"{name} must be a JSON object")
    return result


def canonical_json(value) -> str:
    """Reuse the repo's sorted UTF-8 JSON after rejecting non-JSON identities."""
    active = set()

    def validate(item):
        if item is None or type(item) in (bool, int):
            return
        if type(item) is str:
            text(item, "JSON string")
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) not in (dict, list) or id(item) in active:
            raise ContractError("canonical identity must contain finite, acyclic JSON values")
        active.add(id(item))
        if type(item) is dict:
            for key, child in item.items():
                text(key, "JSON object key")
                validate(child)
        else:
            for child in item:
                validate(child)
        active.remove(id(item))

    try:
        validate(value)
    except RecursionError:
        raise ContractError("canonical identity is too deeply nested") from None
    return dumps(value)


def canonical_sha256(value) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))
