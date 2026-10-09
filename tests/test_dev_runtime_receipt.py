"""CPU regressions for repository failures versus failed verifier environments."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from eval import runtime_real as real
from eval.solver_task import PublicAssetRef, SolverTask
from tools.dev_eval import control_passes
from test_dev_runtime_dependencies import (dependency_sandbox, local_pytest_wheels,
                                           native_manager_class, native_verification_functions,
                                           _identity, _wheel)


def _error_xml(text):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="1", failures="0", errors="1", skipped="0")
    case = ET.SubElement(suite, "testcase", name="test_fixture")
    ET.SubElement(case, "error", message="collection failure").text = text
    return ET.tostring(root, encoding="unicode")


def test_failed_dependency_setup_cannot_become_a_test_control_pass():
    receipt = real.test_execution_observation(
        {"setup_error": {"phase": "dependency provisioning", "exception_type": "RealAdmissionError"}},
        {"resolved": False, "test_exit_code": -1}, "synthetic fixture patch")
    assert receipt == {"status": "infrastructure_error", "reason": "sandbox_dependency_setup_failed",
                       "command_exit_code": None, "junit_test_count": None,
                       "required_test_count": None, "required_tests_executed": False}


@pytest.mark.parametrize("kind", ["missing_symbol", "missing_repo_submodule", "missing_repo_top", "repo_runtime_error",
                                  "repo_syntax_error", "missing_external", "external_symbol",
                                  "private_test_frame", "unknown", "outside", "symlink", "hardlink", "removed"])
def test_error_provenance_requires_existing_anchored_repository_source(tmp_path, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "fixture_package.py"
    source.write_text("VALUE = 1\n")
    package = workspace / "fixture_package"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n")
    (workspace / "test_fixture.py").write_text("def test_expected(): pass\n")
    external = tmp_path / "site-packages/external.py"
    external.parent.mkdir()
    external.write_text("VALUE = 1\n")
    messages = {
        "missing_symbol": "ImportError: cannot import name 'answer' from 'fixture_package' (" + str(source) + ")",
        "missing_repo_submodule": "ModuleNotFoundError: No module named 'fixture_package.added_by_patch'",
        "missing_repo_top": "ModuleNotFoundError: No module named 'fixture_package'",
        "repo_runtime_error": "fixture_package.py:1: in <module>\nRuntimeError: repository bug",
        "repo_syntax_error": 'File "' + str(source) + '", line 1\nSyntaxError: repository bug',
        "missing_external": "fixture_package.py:1: in <module>\nModuleNotFoundError: No module named 'unavailable_external'",
        "external_symbol": "ImportError: cannot import name 'answer' from 'external' (" + str(external) + ")",
        "private_test_frame": "test_fixture.py:1: in <module>\nRuntimeError: unconfirmed test code origin",
        "unknown": "RuntimeError: no source provenance",
        "outside": 'File "' + str(tmp_path / "outside.py") + '", line 1\nRuntimeError: outside',
        "symlink": 'File "' + str(workspace / "linked.py") + '", line 1\nRuntimeError: linked',
        "hardlink": 'File "' + str(workspace / "hardlinked.py") + '", line 1\nRuntimeError: linked',
        "removed": 'File "' + str(workspace / "removed.py") + '", line 1\nRuntimeError: removed',
    }
    (workspace / "linked.py").symlink_to(external)
    os.link(external, workspace / "hardlinked.py")
    diagnostic = real.test_failure_diagnostics(_error_xml(messages[kind]), workspace)
    expected = ("repository_errors" if kind in {"missing_symbol", "missing_repo_submodule", "repo_runtime_error", "repo_syntax_error"}
                else "external_dependency_errors" if kind in {"missing_external", "external_symbol"}
                else "unclassified_errors")
    assert diagnostic == {**{name: int(name == expected) for name in
                             ("repository_errors", "external_dependency_errors", "unclassified_errors")},
                          "external_dependency_failures": 0}
    assert all(type(value) is int for value in diagnostic.values())


@pytest.mark.parametrize("source,known_patch,status,control_ok,exit_code", [
    ("VALUE = 41\n", False, "repository_failure", True, 2),
    ("VALUE = 41\n", True, "executed", True, 0),
    ("def answer():\n    return 41\n", False, "executed", True, 1),
    ("raise RuntimeError('synthetic repository import bug')\n", False, "repository_failure", True, 2),
    ("def answer(:\n    pass\n", False, "repository_failure", True, 2),
    ("import fixture_unavailable_external_dependency\n", False, "infrastructure_error", False, 2),
    ("def answer():\n    import fixture_unavailable_external_dependency\n    return 42\n", False, "infrastructure_error", False, 1),
], ids=["unpatched_missing_symbol", "known_patch_supplies_symbol", "unpatched_assertion",
        "repository_collection_exception", "repository_syntax_error", "missing_external_dependency",
        "missing_external_dependency_in_test_body"])
def test_native_controls_preserve_repository_failure_and_reject_missing_dependencies(
        dependency_sandbox, native_verification_functions, tmp_path, source, known_patch, status, control_ok, exit_code):
    """Original native patch application, pytest invocation and pass validator."""
    manager, _, paths, _ = dependency_sandbox
    models, verification = native_verification_functions
    repo = tmp_path / "snapshot_repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "fixture_package.py").write_text(source)
    snapshot = paths["public_root"] / "snapshots/fastapi_1.tgz"
    snapshot.parent.mkdir(mode=0o700)
    with tarfile.open(snapshot, "w:gz") as archive:
        for path in sorted(repo.iterdir()):
            archive.add(path, arcname=path.name)
    snapshot.chmod(0o600)
    private_patch = ("diff --git a/test_fixture.py b/test_fixture.py\nnew file mode 100644\n"
                     "--- /dev/null\n+++ b/test_fixture.py\n@@ -0,0 +1,3 @@\n"
                     "+from fixture_package import answer\n+def test_expected():\n+    assert answer() == 42\n")
    patch = ("diff --git a/fixture_package.py b/fixture_package.py\n--- a/fixture_package.py\n"
             "+++ b/fixture_package.py\n@@ -1 +1,3 @@\n VALUE = 41\n+def answer():\n+    return 42\n") if known_patch else ""
    ref = PublicAssetRef("snapshot", "fastapi_1", "snapshots/fastapi_1.tgz",
                         hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot.stat().st_size)
    task = SolverTask(1, "fastapi_1", "fastapi/fastapi", "1" * 40, "synthetic public fixture", "", ref)
    observations = real.observe_manager(manager, paths, task)
    native_task = models["Task"](instance_id=task.instance_id, repo=task.repo, base_commit=task.base_commit,
                                problem_statement="", test_patch=private_patch)
    config = SimpleNamespace(wheels_dir=paths["wheels_root"], tasks_path=paths["public_root"] / "unopened.jsonl",
                             snapshots_dir=snapshot.parent, submission_dir=None, results_dir=None, sandbox="subprocess",
                             harness=SimpleNamespace(command_timeout_seconds=60))
    result = asyncio.run(verification["verify_task"](manager, config, native_task, snapshot,
                                                     agent_patch=patch, agent_error=None))
    native = {"resolved": result.resolved, "test_exit_code": result.test_exit_code}
    receipt = real.test_execution_observation(observations, native, private_patch)
    assert receipt["status"] == status
    assert receipt["required_tests_executed"] is (status == "executed")
    assert result.resolved is known_patch
    assert result.test_exit_code == exit_code
    control = {"resolved": result.resolved, "verification_observations": {
        "apply_status": "applied" if known_patch else "not_needed", "apply_error": None,
        "required_tests_passed": result.resolved, "test_execution": receipt},
        "verifier_result": {"native_result_json": json.dumps(native)}}
    assert control_passes("known_patch" if known_patch else "no_patch", control) is control_ok
    assert observations["sandbox_ids"] == observations["stopped_ids"]


@pytest.mark.parametrize("changes", [{}, {"repository_errors": 0}, {"repository_errors": True},
                                     {"unclassified_errors": 1}, {"unexpected": 1}])
def test_repository_failure_requires_complete_well_formed_error_provenance(native_verification_functions, changes):
    test_patch = ("diff --git a/test_fixture.py b/test_fixture.py\nnew file mode 100644\n"
                  "--- /dev/null\n+++ b/test_fixture.py\n@@ -0,0 +1,2 @@\n"
                  "+def test_expected():\n+    assert True\n")
    diagnostic = {"repository_errors": 1, "external_dependency_errors": 0, "unclassified_errors": 0,
                  "external_dependency_failures": 0, **changes}
    observations = {"pytest_invocations": [{"targets": "test_fixture.py", "exit_code": 2,
        "junit_xml": _error_xml("fixture"), "failure_diagnostics": diagnostic}]}
    receipt = real.test_execution_observation(observations, {"resolved": False, "test_exit_code": 2}, test_patch)
    assert receipt["status"] == ("repository_failure" if not changes else "infrastructure_error")
    assert receipt["required_tests_executed"] is False


def _failure_xml(text):
    root = ET.fromstring(_error_xml(text))
    suite = root.find("testsuite")
    suite.set("errors", "0")
    suite.set("failures", "1")
    suite.find("testcase/error").tag = "failure"
    return ET.tostring(root, encoding="unicode")


@pytest.mark.parametrize("exception,collection", [
    ("AssertionError: repository assertion", False),
    ("ImportError: repository import contract failed", False),
    ("ImportError: repository import contract failed", True),
])
def test_installed_final_frame_without_explicit_missing_import_is_not_external(
        tmp_path, exception, collection):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "fixture_package.py").write_text("VALUE = 1\n")
    installed = tmp_path / "site-packages/fixture_callback.py"
    text = ("fixture_package.py:1: in answer\n"
            + str(installed) + ":9: in assert_contract\n" + exception)
    diagnostic = real.test_failure_diagnostics((_error_xml if collection else _failure_xml)(text), workspace)
    assert diagnostic == {"repository_errors": int(collection), "external_dependency_errors": 0,
                          "unclassified_errors": 0, "external_dependency_failures": 0}


@pytest.mark.parametrize("source_root,namespace,missing", [
    ("", True, "module"), ("", True, "symbol"),
    ("src", False, "module"), ("src", False, "symbol"),
    ("lib", False, "module"), ("lib", False, "symbol"),
    ("src", True, "module"), ("src", True, "symbol"),
])
def test_repository_namespace_and_immediate_source_roots_establish_missing_submodule_or_symbol(
        tmp_path, source_root, namespace, missing):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package_name = "docs_src" if namespace else "httpx"
    package = workspace / source_root / package_name
    package.mkdir(parents=True)
    if not namespace:
        (package / "__init__.py").write_text("VALUE = 1\n")
    (package / "example.py").write_text("VALUE = 1\n")
    text = ("ModuleNotFoundError: No module named '" + package_name + ".added_by_patch'"
            if missing == "module" else "ImportError: cannot import name 'added_by_patch' from '"
            + package_name + "' (unknown location)")
    assert real.test_failure_diagnostics(_error_xml(text), workspace) == {
        "repository_errors": 1, "external_dependency_errors": 0,
        "unclassified_errors": 0, "external_dependency_failures": 0}


@pytest.mark.parametrize("kind", ["package_symlink", "source_symlink", "source_package_symlink",
                                  "hidden_source", "hardlinked_module", "nonregular_module",
                                  "traversal_module", "workspace_symlink", "test_source", "test_named_package",
                                  "namespace_initializer_symlink", "namespace_initializer_hardlink",
                                  "namespace_with_symlink_module", "namespace_with_hardlinked_module"])
def test_repository_module_inference_rejects_unsafe_paths(tmp_path, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "__init__.py").write_text("VALUE = 1\n")
    (outside / "fixture_package.py").write_text("VALUE = 1\n")
    name = "fixture_package.added_by_patch"
    if kind == "package_symlink":
        (workspace / "fixture_package").symlink_to(outside, target_is_directory=True)
    elif kind == "source_symlink":
        (outside / "fixture_package").mkdir()
        (workspace / "src").symlink_to(outside, target_is_directory=True)
    elif kind == "source_package_symlink":
        (workspace / "src").mkdir()
        (workspace / "src/fixture_package").symlink_to(outside, target_is_directory=True)
    elif kind == "hidden_source":
        (workspace / ".hidden/fixture_package").mkdir(parents=True)
    elif kind == "test_source":
        (workspace / "tests/fixture_package").mkdir(parents=True)
    elif kind == "test_named_package":
        (workspace / "test_fixture_package").mkdir()
        name = "test_fixture_package.added_by_patch"
    elif kind.startswith("namespace_initializer_"):
        (workspace / "fixture_package").mkdir()
        initializer = workspace / "fixture_package/__init__.py"
        if kind.endswith("symlink"):
            initializer.symlink_to(outside / "__init__.py")
        else:
            os.link(outside / "__init__.py", initializer)
    elif kind.startswith("namespace_with_"):
        (workspace / "fixture_package").mkdir()
        module = workspace / "fixture_package.py"
        if kind == "namespace_with_symlink_module":
            module.symlink_to(outside / "fixture_package.py")
        else:
            os.link(outside / "fixture_package.py", module)
    elif kind == "hardlinked_module":
        (workspace / "src").mkdir()
        os.link(outside / "fixture_package.py", workspace / "src/fixture_package.py")
    elif kind == "nonregular_module":
        (workspace / "fixture_package.py").mkdir()
    elif kind == "traversal_module":
        name = "../outside.fixture_package.added_by_patch"
    else:
        (workspace / "fixture_package").mkdir()
        alias = tmp_path / "workspace_alias"
        alias.symlink_to(workspace, target_is_directory=True)
        workspace = alias
    text = "ModuleNotFoundError: No module named '" + name + "'"
    diagnostic = real.test_failure_diagnostics(_error_xml(text), workspace)
    assert diagnostic["repository_errors"] == 0
    assert diagnostic["external_dependency_failures"] == 0


@pytest.mark.parametrize("source_root,namespace", [("", True), ("src", False)])
def test_existing_top_level_repository_module_missing_remains_inconclusive(tmp_path, source_root, namespace):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / source_root / "fixture_package"
    package.mkdir(parents=True)
    if not namespace:
        (package / "__init__.py").write_text("VALUE = 1\n")
    diagnostic = real.test_failure_diagnostics(
        _error_xml("ModuleNotFoundError: No module named 'fixture_package'"), workspace)
    assert diagnostic == {"repository_errors": 0, "external_dependency_errors": 0,
                          "unclassified_errors": 1, "external_dependency_failures": 0}


@pytest.mark.parametrize("name", ["pytest_timeout", "fixture_external_plugin.submodule"])
def test_explicit_missing_external_plugin_stays_infrastructure(
        tmp_path, native_verification_functions, name):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "fixture_package.py").write_text("VALUE = 1\n")
    xml = _error_xml("fixture_package.py:1: in <module>\n"
                     "ModuleNotFoundError: No module named '" + name + "'")
    diagnostics = real.test_failure_diagnostics(xml, workspace)
    assert diagnostics["external_dependency_errors"] == 1
    patch = ("diff --git a/test_fixture.py b/test_fixture.py\nnew file mode 100644\n"
             "--- /dev/null\n+++ b/test_fixture.py\n@@ -0,0 +1,2 @@\n"
             "+def test_fixture():\n+    assert True\n")
    receipt = real.test_execution_observation({"pytest_invocations": [{"targets": "test_fixture.py",
        "exit_code": 2, "junit_xml": xml, "failure_diagnostics": diagnostics}]},
        {"resolved": False, "test_exit_code": 2}, patch)
    assert receipt["status"] == "infrastructure_error"
    assert receipt["reason"] == "missing_external_test_dependency"
    assert receipt["required_tests_executed"] is False


@pytest.mark.parametrize("case", ["installed_assertion", "installed_generic_import",
                                  "namespace_missing_module", "namespace_missing_symbol", "src_missing_module",
                                  "missing_external_plugin"])
def test_native_pytest_a_b_s_failures_remain_faithful_no_patch_controls(
        dependency_sandbox, native_verification_functions, tmp_path, case):
    """Actual pinned pytest command and validator on public synthetic code only."""
    manager, _, paths, _ = dependency_sandbox
    models, verification = native_verification_functions
    repo = tmp_path / "snapshot_repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True, capture_output=True)
    if case == "missing_external_plugin":
        _wheel(paths["wheels_root"], "fixture-missing-plugin", "1.0", {
            "fixture_missing_plugin.py": b"import fixture_unavailable_plugin_dependency\n",
            "fixture_missing_plugin-1.0.dist-info/entry_points.txt":
                b"[pytest11]\nfixture_missing_plugin = fixture_missing_plugin\n"})
        paths["public_support_identity"] = _identity(paths)
        (repo / "fixture_package.py").write_text("def answer():\n    return 42\n")
        import_line = "from fixture_package import answer"
    elif case.startswith("installed_"):
        callback = (b"def check():\n    assert False, 'synthetic assertion'\n" if case == "installed_assertion"
                    else b"def check():\n    raise ImportError('synthetic repository import contract')\n")
        _wheel(paths["wheels_root"], "fixture-callback", "1.0", {"fixture_callback.py": callback})
        paths["public_support_identity"] = _identity(paths)
        source = ("from fixture_callback import check\ndef answer():\n    check()\n" if case == "installed_assertion"
                  else "from fixture_callback import check\ncheck()\ndef answer():\n    return 42\n")
        (repo / "fixture_package.py").write_text(source)
        import_line = "from fixture_package import answer"
    elif case.startswith("namespace_"):
        (repo / "docs_src").mkdir()
        source = ("import docs_src.added_by_patch\n" if case == "namespace_missing_module"
                  else "from docs_src import added_by_patch\n")
        (repo / "docs_src/example.py").write_text(source + "def answer():\n    return 42\n")
        import_line = "from docs_src.example import answer"
    else:
        (repo / "src/httpx").mkdir(parents=True)
        (repo / "src/httpx/__init__.py").write_text("import httpx.added_by_patch\ndef answer():\n    return 42\n")
        # Native verification overwrites pytest.ini, so use the fixture's
        # admitted public fast-path setup to expose its src layout instead.
        script_path = paths["setup_root"] / "setup.py"
        script = script_path.read_text().replace("str(workspace) + '\\n'",
                    "str(workspace) + '\\n' + str(workspace / 'src') + '\\n'")
        script_path.write_text(script)
        paths["public_support_identity"] = _identity(paths)
        import_line = "from httpx import answer"
    snapshot = paths["public_root"] / "snapshots/fastapi_1.tgz"
    snapshot.parent.mkdir(mode=0o700)
    with tarfile.open(snapshot, "w:gz") as archive:
        for path in sorted(repo.iterdir()):
            archive.add(path, arcname=path.name)
    snapshot.chmod(0o600)
    private_patch = ("diff --git a/test_fixture.py b/test_fixture.py\nnew file mode 100644\n"
                     "--- /dev/null\n+++ b/test_fixture.py\n@@ -0,0 +1,3 @@\n"
                     "+" + import_line + "\n+def test_expected():\n+    assert answer() == 42\n")
    ref = PublicAssetRef("snapshot", "fastapi_1", "snapshots/fastapi_1.tgz",
                         hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot.stat().st_size)
    task = SolverTask(1, "fastapi_1", "fastapi/fastapi", "1" * 40, "synthetic public fixture", "", ref)
    observations = real.observe_manager(manager, paths, task)
    native_task = models["Task"](instance_id=task.instance_id, repo=task.repo, base_commit=task.base_commit,
                                problem_statement="", test_patch=private_patch)
    config = SimpleNamespace(wheels_dir=paths["wheels_root"], tasks_path=paths["public_root"] / "unopened.jsonl",
                             snapshots_dir=snapshot.parent, submission_dir=None, results_dir=None, sandbox="subprocess",
                             harness=SimpleNamespace(command_timeout_seconds=60))
    result = asyncio.run(verification["verify_task"](manager, config, native_task, snapshot,
                                                     agent_patch="", agent_error=None))
    native = {"resolved": result.resolved, "test_exit_code": result.test_exit_code}
    receipt = real.test_execution_observation(observations, native, private_patch)
    assert result.resolved is False
    assert result.test_exit_code == (1 if case in ("installed_assertion", "missing_external_plugin") else 2)
    status = ("executed" if case == "installed_assertion" else "infrastructure_error"
              if case == "missing_external_plugin" else "repository_failure")
    assert receipt["status"] == status
    assert receipt["required_tests_executed"] is (case == "installed_assertion")
    if case == "missing_external_plugin":
        assert receipt["reason"] == "junit_missing_or_invalid"
    control = {"resolved": False, "verification_observations": {
        "apply_status": "not_needed", "apply_error": None, "required_tests_passed": False,
        "test_execution": receipt}, "verifier_result": {"native_result_json": json.dumps(native)}}
    assert control_passes("no_patch", control) is (case != "missing_external_plugin")
    assert control_passes("known_patch", control) is False
    assert observations["sandbox_ids"] == observations["stopped_ids"]
