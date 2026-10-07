"""Synthetic immutable/closed task boundaries; no vendor or model required."""
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json

import pytest

from eval._contracts import ContractError, canonical_json
from eval.solver_task import PublicAssetRef, SolverTask, solver_task_dict, solver_task_json
from eval.verifier_task import PrivateTestPatchRef, VerifierTask


def asset(kind="snapshot"):
    path = {"snapshot": "snapshots/task_1.tgz", "graph": "graphs/toy_" + "a" * 40 + ".json",
            "embedding": "embeddings/toy_" + "a" * 40 + ".npz"}[kind]
    return PublicAssetRef(kind, "task_1", path, "b" * 64, 42)


def solver():
    return SolverTask(1, "toy_1", "org/toy", "a" * 40,
                      'Fix "quoted" text, backslash \\ and \nالعربية 🦙\t exactly.',
                      "  hints\n\t", asset(), asset("graph"), asset("embedding"))


def verifier():
    return VerifierTask(1, "toy_1", "org/toy", "a" * 40, asset(),
                        PrivateTestPatchRef("c" * 64, 18), "d" * 64,
                        ("tests/test_toy.py::test_changed[x]",), ("test_preserved",))


@pytest.mark.parametrize("factory", [solver, verifier, asset, lambda: PrivateTestPatchRef("a" * 64, 0)])
def test_contracts_frozen_slotted_and_json_round_trip(factory):
    value = factory()
    assert not hasattr(value, "__dict__")
    assert type(value).from_dict(value.to_dict()) == value
    assert type(value).from_json(value.to_json()) == value
    assert type(value).from_json(value.to_json().encode("utf-8")) == value
    with pytest.raises((FrozenInstanceError, AttributeError)):
        setattr(value, fields(value)[0].name, "changed")
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(value, "patch", "GOLD_PATCH_SENTINEL")


def test_text_and_canonical_identity_preserved_exactly():
    task = solver()
    assert SolverTask.from_json(task.to_json()).problem_statement == task.problem_statement
    assert SolverTask.from_json(task.to_json()).hints_text == task.hints_text
    assert task.to_json().endswith("\n")
    assert "العربية 🦙" in task.to_json()
    assert task.sha256() == hashlib.sha256(task.to_json().encode("utf-8")).hexdigest()
    reordered = dict(reversed(list(task.to_dict().items())))
    assert SolverTask.from_dict(reordered).sha256() == task.sha256()
    assert replace(task, hints_text=task.hints_text + " ").sha256() != task.sha256()
    assert verifier().sha256() == hashlib.sha256(verifier().to_json().encode("utf-8")).hexdigest()


@pytest.mark.parametrize("key", ["patch", "test_patch", "reference_solution", "expected_result",
                                 "partition", "holdout_diagnostics", "private_path", "fd", "created_at"])
def test_solver_schema_rejects_forbidden_or_unknown_fields(key):
    data = solver().to_dict() | {key: "PRIVATE_SENTINEL"}
    with pytest.raises(ContractError, match="unknown fields"):
        SolverTask.from_dict(data)
    with pytest.raises(ContractError, match="unknown fields"):
        SolverTask.from_json(json.dumps(data))
    with pytest.raises(TypeError):
        SolverTask(**{f.name: getattr(solver(), f.name) for f in fields(SolverTask)} | {key: "PRIVATE_SENTINEL"})


@pytest.mark.parametrize("factory", [solver, verifier, asset, lambda: PrivateTestPatchRef("a" * 64, 0)])
def test_missing_unknown_and_nested_unknown_keys_fail(factory):
    value = factory()
    data = value.to_dict()
    with pytest.raises(ContractError):
        type(value).from_dict(data | {"surprise": "PRIVATE_SENTINEL"})
    key = next(iter(data))
    data.pop(key)
    with pytest.raises(ContractError):
        type(value).from_dict(data)
    if type(value) in (SolverTask, VerifierTask):
        data = value.to_dict()
        data["snapshot"]["test_patch"] = "PRIVATE_SENTINEL"
        with pytest.raises(ContractError):
            type(value).from_dict(data)


@pytest.mark.parametrize("field,bad", [
    ("schema_version", True), ("schema_version", 1.0), ("schema_version", "1"), ("schema_version", 2),
    ("instance_id", "../toy_1"), ("instance_id", "toy/1"), ("instance_id", "."), ("instance_id", ""),
    ("instance_id", "toy\n1"), ("repo", "org/toy/more"), ("repo", "../toy"), ("repo", "org\\toy"),
    ("base_commit", "a" * 39), ("base_commit", "A" * 40), ("base_commit", 123),
    ("problem_statement", 1), ("hints_text", None), ("problem_statement", "\ud800"),
])
def test_solver_wrong_types_rejected_in_constructor_and_decoder(field, bad):
    with pytest.raises(ContractError):
        replace(solver(), **{field: bad})
    with pytest.raises(ContractError):
        SolverTask.from_dict(solver().to_dict() | {field: bad})


@pytest.mark.parametrize("field,bad", [
    ("kind", "private"), ("kind", 1), ("asset_id", "fd:3"), ("asset_id", "../toy"),
    ("sha256", "A" * 64), ("sha256", "a" * 63), ("sha256", "g" * 64),
    ("size_bytes", True), ("size_bytes", 1.0), ("size_bytes", -1), ("size_bytes", "1"),
])
def test_public_asset_validation_in_constructor_and_decoder(field, bad):
    with pytest.raises(ContractError):
        replace(asset(), **{field: bad})
    with pytest.raises(ContractError):
        PublicAssetRef.from_dict(asset().to_dict() | {field: bad})


@pytest.mark.parametrize("bad", [
    "/snapshots/task_1.tgz", "../snapshots/task_1.tgz", "snapshots/../task_1.tgz",
    "snapshots/./task_1.tgz", "snapshots//task_1.tgz", "snapshots/task_1.tgz/",
    "snapshots\\task_1.tgz", "C:/snapshots/task_1.tgz", "file:///snapshots/task_1.tgz",
    "fd:3", "secret/solution.patch", "verifier/test_patch.patch", "graphs/task_1.json",
    "snapshots/task_1.patch", "snapshots/task_1\x00.tgz", "snapshots/task_1\n.tgz",
    "snapshots/%2e%2e.tgz", "snapshots/subdir/task_1.tgz",
])
def test_asset_paths_fail_closed(bad):
    with pytest.raises(ContractError):
        replace(asset(), source_relative_path=bad)
    with pytest.raises(ContractError):
        PublicAssetRef.from_dict(asset().to_dict() | {"source_relative_path": bad})


@pytest.mark.parametrize("field,bad", [("snapshot", asset("graph")), ("graph", asset("snapshot")),
                                     ("embedding", asset("graph")), ("snapshot", {}), ("graph", "path")])
def test_solver_nested_assets_exact_type_and_kind(field, bad):
    with pytest.raises(ContractError):
        replace(solver(), **{field: bad})


@pytest.mark.parametrize("factory", [solver, verifier, asset, lambda: PrivateTestPatchRef("a" * 64, 0)])
def test_duplicate_json_keys_rejected_at_all_depths(factory):
    value = factory()
    key, val = next(iter(value.to_dict().items()))
    duplicate = value.to_json().rstrip()[:-1] + "," + json.dumps(key) + ":" + json.dumps(val) + "}"
    with pytest.raises(ContractError, match="duplicate"):
        type(value).from_json(duplicate)
    if type(value) in (SolverTask, VerifierTask):
        nested = value.to_json().replace('"size_bytes": 42', '"size_bytes": 42, "size_bytes": 9')
        with pytest.raises(ContractError, match="duplicate"):
            type(value).from_json(nested)


@pytest.mark.parametrize("raw", ["[]", "null", "1", "{", b"\xff", bytearray(b"{}"), None,
                                 '{"schema_version": NaN}', '{"schema_version": Infinity}'])
def test_invalid_json_fails(raw):
    with pytest.raises(ContractError):
        SolverTask.from_json(raw)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), object(), {1: "value"}, (1, 2)])
def test_canonical_json_rejects_non_json_or_nonfinite_values(bad):
    with pytest.raises(ContractError):
        canonical_json(bad)


def test_canonical_json_rejects_cycles():
    value = []
    value.append(value)
    with pytest.raises(ContractError):
        canonical_json(value)


def test_optional_assets_and_verifier_metadata_absence_stay_explicit():
    task = replace(solver(), graph=None, embedding=None)
    data = task.to_dict()
    data.pop("graph")
    data.pop("embedding")
    assert SolverTask.from_dict(data) == task
    private = replace(verifier(), fail_to_pass=None, pass_to_pass=None)
    assert private.to_dict()["fail_to_pass"] is None
    assert private != replace(private, fail_to_pass=())
    assert private.sha256() != replace(private, fail_to_pass=()).sha256()
    assert VerifierTask.from_json(private.to_json()) == private


@pytest.mark.parametrize("field,bad", [("test_patch", "RAW_PRIVATE_SENTINEL"), ("test_patch", {}),
                                     ("snapshot", asset("graph")), ("schema_version", True),
                                     ("verification_config_sha256", "bad"), ("fail_to_pass", ["test_a"]),
                                     ("pass_to_pass", "test_a"), ("fail_to_pass", (1,)),
                                     ("fail_to_pass", ("",)), ("pass_to_pass", ("test_a", "test_a"))])
def test_verifier_direct_constructor_validation(field, bad):
    with pytest.raises(ContractError):
        replace(verifier(), **{field: bad})


@pytest.mark.parametrize("field,bad", [("fail_to_pass", "[\"test_a\"]"), ("pass_to_pass", ("test_a",)),
                                     ("fail_to_pass", [1]), ("pass_to_pass", [""]),
                                     ("fail_to_pass", ["test_a", "test_a"]),
                                     ("test_patch", {"sha256": "a" * 64, "size_bytes": True}),
                                     ("test_patch", {"sha256": "a" * 64, "size_bytes": 1, "path": "/private/gold"})])
def test_verifier_json_metadata_and_private_ref_fail_closed(field, bad):
    with pytest.raises(ContractError):
        VerifierTask.from_dict(verifier().to_dict() | {field: bad})


def test_solver_and_verifier_boundaries_cannot_interchange():
    for value in (verifier(), verifier().to_dict(), verifier().to_json(), asset(), None):
        with pytest.raises(ContractError):
            solver_task_dict(value)
        with pytest.raises(ContractError):
            solver_task_json(value)
    with pytest.raises(ContractError):
        SolverTask.from_dict(verifier().to_dict())
    with pytest.raises(ContractError):
        VerifierTask.from_dict(solver().to_dict())
    assert solver_task_dict(solver()) == solver().to_dict()
    assert solver_task_json(solver()) == solver().to_json()


def test_subclasses_do_not_expand_the_solver_boundary():
    class ExpandedSolver(SolverTask):
        pass

    with pytest.raises(ContractError):
        ExpandedSolver(**{f.name: getattr(solver(), f.name) for f in fields(SolverTask)})
    bypassed = object.__new__(ExpandedSolver)
    with pytest.raises(ContractError):
        solver_task_dict(bypassed)
    with pytest.raises(ContractError):
        ExpandedSolver.from_dict(solver().to_dict())
    with pytest.raises(ContractError):
        ExpandedSolver.from_json(solver().to_json())


@pytest.mark.parametrize("method", [SolverTask.to_dict, SolverTask.to_json, SolverTask.sha256])
def test_unbound_solver_methods_reject_a_verifier(method):
    with pytest.raises(ContractError):
        method(verifier())


def test_subclass_overrides_cannot_bypass_inherited_serialization():
    class ExpandedSolver(SolverTask):
        def __post_init__(self):
            pass

        @classmethod
        def from_dict(cls, _value):
            return verifier()

    expanded = ExpandedSolver(**{f.name: getattr(solver(), f.name) for f in fields(SolverTask)})
    for method in (SolverTask.to_dict, SolverTask.to_json, SolverTask.sha256, solver_task_json):
        with pytest.raises(ContractError):
            method(expanded)
    with pytest.raises(ContractError):
        ExpandedSolver.from_json(solver().to_json())


def test_serialization_revalidates_tampered_python_state():
    task = solver()
    object.__setattr__(task, "snapshot", PrivateTestPatchRef("a" * 64, 1))
    with pytest.raises(ContractError):
        solver_task_json(task)


def test_solver_identity_has_only_public_fields_and_no_private_reference():
    task = solver()
    assert {f.name for f in fields(SolverTask)} == {
        "schema_version", "instance_id", "repo", "base_commit", "problem_statement",
        "hints_text", "snapshot", "graph", "embedding"}
    assert set(task.to_dict()) == {f.name for f in fields(SolverTask)}
    assert set(verifier().test_patch.to_dict()) == {"sha256", "size_bytes"}
    for forbidden in ("test_patch", "reference_solution", "PRIVATE_SENTINEL", "/private/gold", "partition"):
        assert forbidden not in task.to_json()
