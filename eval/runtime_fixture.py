"""Inert CPU fixtures for runtime certification, never competition task intake.

Only public scripted tools, constants and budgets live in this module. Private
fixture tests and verifier contract construction live in runtime_verifier_fixture.
"""
from __future__ import annotations

import hashlib


INITIAL = 'MARKER = "before"\n'
CHANGED = 'MARKER = "after"\n'
CASES = ("H05", "H04", "H18", "H13", "H14", "H29", "H29_BOUNDARY", "EMPTY_SUBMIT", "EMPTY_FINAL")


def expected_patch() -> str:
    def blob(content):
        data = content.encode()
        return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()[:7]

    return (f"diff --git a/app.py b/app.py\nindex {blob(INITIAL)}..{blob(CHANGED)} 100644\n"
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n" + f"-{INITIAL}+{CHANGED}")


def scripts(case: str) -> list[dict]:
    if case not in CASES:
        raise ValueError("unknown synthetic runtime case")

    def tool(name, args, identifier):
        import json
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": identifier, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)},
        }]}

    write = lambda number: tool("write_file", {"filepath": "app.py", "content": CHANGED}, f"write_{number}")
    submit = tool("submit_patch", {}, "submit")
    final = {"role": "assistant", "content": "Synthetic runtime fixture complete."}
    if case == "H18":
        return [write(1), tool("bash", {"command": "echo synthetic"}, "missing_tool"), submit, final]
    if case == "H13":
        return [write(1), final]
    if case == "H14":
        rejected = tool("write_file", {"filepath": "app.py", "content": INITIAL}, "rejected_write")
        return [write(1), write(2), write(3), rejected, final]
    if case == "H29":
        return [write(1), write(2), tool("get_status", {}, "status_at_limit"), submit, final]
    if case == "H29_BOUNDARY":
        return [write(1), write(2), final, tool("get_status", {}, "status_after_final"), submit, final]
    if case == "EMPTY_SUBMIT":
        return [submit, final]
    if case == "EMPTY_FINAL":
        return [final] * 8
    return [write(1), submit, final]


def declared_tools(case: str) -> tuple[str, ...]:
    if case not in CASES:
        raise ValueError("unknown synthetic runtime case")
    if case == "H04":
        return ("submit_patch",)
    if case.startswith("H29"):
        return ("write_file", "get_status", "submit_patch")
    return ("write_file", "submit_patch")


def budgets(case: str) -> dict:
    declared_tools(case)
    return {"max_time_minutes": 0.05 if case == "H13" else 1,
            "max_tool_calls": 2 if case.startswith("H29") else 3,
            "max_turns": 8}
