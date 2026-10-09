"""CPU-only dependency setup in actual isolated venvs and local wheelhouses."""
from __future__ import annotations

import ast
import asyncio
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import threading
from types import ModuleType, SimpleNamespace
import shutil
import zipfile

import pytest

from eval import runtime_real as real
from tools.common import REPO_ROOT, tree_sha256


def _wheel(path, distribution, version, contents, requires=()):
    name = distribution.replace("-", "_")
    info = name + "-" + version + ".dist-info"
    contents = dict(contents)
    contents.setdefault(info + "/METADATA", ("Metadata-Version: 2.1\nName: " + distribution
                        + "\nVersion: " + version + "\n"
                        + "".join("Requires-Dist: " + value + "\n" for value in requires)).encode())
    contents[info + "/WHEEL"] = (b"Wheel-Version: 1.0\nGenerator: offline-cpu-fixture\n"
                                 b"Root-Is-Purelib: true\nTag: py3-none-any\n")
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for filename, data in sorted(contents.items()):
        writer.writerow((filename, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="),
                         len(data)))
    writer.writerow((info + "/RECORD", "", ""))
    contents[info + "/RECORD"] = record.getvalue().encode()
    wheel = path / (name + "-" + version + "-py3-none-any.whl")
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, data in sorted(contents.items()):
            archive.writestr(filename, data)
    return wheel


@pytest.fixture(scope="module")
def local_pytest_wheels(tmp_path_factory):
    """Repack existing pure Python distributions; no index or build service."""
    wheelhouse = tmp_path_factory.mktemp("dependency-wheels")
    for name in ("pytest", "iniconfig", "packaging", "pluggy", "pygments"):
        dist = importlib.metadata.distribution(name)
        contents = {}
        for file in dist.files or ():
            relative = PurePosixPath(str(file))
            if relative.is_absolute() or ".." in relative.parts or relative.suffix == ".pyc":
                continue
            if relative.name in ("RECORD", "INSTALLER", "direct_url.json"):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file():
                contents[relative.as_posix()] = source.read_bytes()
        _wheel(wheelhouse, name, dist.version, contents)
    return wheelhouse


@pytest.fixture(scope="module")
def native_manager_class():
    """Use pinned manager source with minimal base/transport test shims."""
    name = "swegemma-0.2.7-py3-none-any.whl"
    wheel = REPO_ROOT / "artifacts/harness_wheels/v28" / name
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == real.SOURCE_WHEELS[name]
    with zipfile.ZipFile(wheel) as archive:
        source = archive.read("swegemma/sandbox/subprocess.py")
    tree = ast.parse(source)
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.ImportFrom) and node.module == "adk_eval_core.sandbox")]
    def sanitized_env(*, work_dir, tmp_dir, extra_env):
        return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(tmp_dir),
                "PYTHONDONTWRITEBYTECODE": "1",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "CPU fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "CPU fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                **extra_env}
    def execute(*, command, cwd, env, timeout, shell):
        result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout, shell=shell,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, close_fds=True)
        return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    namespace = {"__name__": "cpu_fixture_native_subprocess", "BaseSandbox": object,
                 "ExecutionResult": SimpleNamespace, "build_sanitized_env": sanitized_env,
                 "execute_subprocess_command": execute}
    exec(compile(tree, "pinned_swegemma_subprocess.py", "exec"), namespace)
    return namespace["SubprocessManager"]


@pytest.fixture
def dependency_sandbox(tmp_path, native_manager_class, local_pytest_wheels):
    worker = tmp_path / "worker"
    worker.mkdir(mode=0o700)
    manager = native_manager_class(timeout_seconds=60, base_dir=worker / "sandboxes", system_site_packages=False)
    identifier = manager.start()
    public = worker / "public"
    wheels = public / "wheels"
    setup = public / "sandbox"
    wheels.mkdir(mode=0o700, parents=True)
    public.chmod(0o700)
    setup.mkdir(mode=0o700)
    for wheel in local_pytest_wheels.iterdir():
        shutil.copyfile(wheel, wheels / wheel.name)
    workspace = manager.sandboxes[identifier]["workspace"]
    script = ("def execute_fast_path(workspace=None, repo='', site_packages_dir=None):\n"
              "    import sysconfig\n"
              "    from pathlib import Path\n"
              "    site = Path(sysconfig.get_paths()['purelib'])\n"
              "    workspace = Path(workspace) if workspace is not None else Path.cwd()\n"
              "    (site / 'fixture_workspace.pth').write_text(str(workspace) + '\\n')\n"
              "    (workspace / 'fixture_setup_invoked').write_text('fast-only')\n"
              "def main():\n"
              "    raise RuntimeError('online fallback must never execute')\n")
    (setup / "setup.py").write_text(script)
    paths = {"worker_root": worker, "public_root": public, "wheels_root": wheels, "setup_root": setup}
    identity = {"setup_py_sha256": hashlib.sha256((setup / "setup.py").read_bytes()).hexdigest(),
                "wheels_tree_sha256": tree_sha256({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                   for p in wheels.iterdir()})}
    paths["public_support_identity"] = identity
    task = SimpleNamespace(instance_id="fastapi_1", repo="fastapi/fastapi")
    try:
        yield manager, identifier, paths, task
    finally:
        manager.cleanup_all()


def _identity(paths):
    return {"setup_py_sha256": hashlib.sha256((paths["setup_root"] / "setup.py").read_bytes()).hexdigest(),
            "wheels_tree_sha256": tree_sha256({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in paths["wheels_root"].iterdir()})}


def test_isolated_native_sandbox_missing_pytest_is_fixed_only_from_local_wheels(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    before = manager.exec(identifier, "python3 -I -m pytest --version")
    assert before.exit_code != 0 and "No module named pytest" in before.stderr
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    observed = real._provision_dependencies(manager, identifier, paths, task)
    after = manager.exec(identifier, "python3 -I -m pytest --version")
    assert after.exit_code == 0 and "pytest " in after.stdout
    assert any(name.startswith("pytest-") for name in observed["selected_wheels"])
    sandbox = manager.sandboxes[identifier]
    assert (sandbox["workspace"] / "fixture_setup_invoked").read_text() == "fast-only"
    probe = manager.exec(identifier, "python3 -I -c 'import sys,pytest; print(sys.prefix); print(pytest.__file__)'")
    assert probe.exit_code == 0
    assert all(line.startswith(str(sandbox["venv"])) for line in probe.stdout.splitlines())


def test_missing_pytest_wheel_fails_before_verification(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    for wheel in paths["wheels_root"].glob("pytest-*.whl"):
        wheel.unlink()
    paths["public_support_identity"] = _identity(paths)
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    with pytest.raises(real.RealAdmissionError):
        real._provision_dependencies(manager, identifier, paths, task)
    assert not (manager.sandboxes[identifier]["workspace"] / "fixture_setup_invoked").exists()


def test_incomplete_local_dependency_set_fails_resolution_without_online_fallback(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    _wheel(paths["wheels_root"], "fixture-incomplete", "1.0", {"fixture_incomplete.py": b"VALUE = 1\n"},
           requires=("fixture-never-provided>=1",))
    paths["public_support_identity"] = _identity(paths)
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    with pytest.raises(real.RealAdmissionError):
        real._provision_dependencies(manager, identifier, paths, task)
    assert not (manager.sandboxes[identifier]["workspace"] / "fixture_setup_invoked").exists()


def test_mounted_wheels_are_fresh_bytes_and_mutation_refuses_setup(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    mounted = manager.sandboxes[identifier]["wheels"]
    for source in paths["wheels_root"].iterdir():
        copy = mounted / source.name
        assert copy.read_bytes() == source.read_bytes()
        assert copy.stat().st_ino != source.stat().st_ino and copy.stat().st_nlink == 1
    next(mounted.iterdir()).write_bytes(b"changed mounted fixture")
    with pytest.raises(real.RealAdmissionError):
        real._provision_dependencies(manager, identifier, paths, task)


def test_original_public_wheel_identity_remains_authority_after_mount(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    next(paths["wheels_root"].iterdir()).write_bytes(b"changed source fixture")
    with pytest.raises(real.RealAdmissionError):
        real._provision_dependencies(manager, identifier, paths, task)


def test_staged_setup_mutation_refuses_dependency_provisioning(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    script = paths["setup_root"] / "setup.py"
    script.write_bytes(script.read_bytes() + b"\n# changed staged fixture\n")
    with pytest.raises(real.RealAdmissionError):
        real._provision_dependencies(manager, identifier, paths, task)
    assert not (manager.sandboxes[identifier]["workspace"] / "fixture_setup_invoked").exists()


def test_system_site_packages_venv_is_rejected_before_installation(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    config = manager.sandboxes[identifier]["venv"] / "pyvenv.cfg"
    original = config.read_text()
    assert "include-system-site-packages = false" in original
    config.write_text(original.replace("include-system-site-packages = false", "include-system-site-packages = true"))
    with pytest.raises(real.RealAdmissionError, match="venv probe"):
        real._provision_dependencies(manager, identifier, paths, task)
    assert not (manager.sandboxes[identifier]["workspace"] / "fixture_setup_invoked").exists()


def test_incompatible_public_wheel_refuses_dependency_setup(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    wheel = _wheel(paths["wheels_root"], "fixture-incompatible", "1.0",
                   {"fixture_incompatible.py": b"VALUE = 1\n"})
    wheel.rename(paths["wheels_root"] / "fixture_incompatible-1.0-cp311-cp311-win_amd64.whl")
    paths["public_support_identity"] = _identity(paths)
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    with pytest.raises(real.RealAdmissionError):
        real._provision_dependencies(manager, identifier, paths, task)


def test_direct_url_dependency_never_uses_network_fallback(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_error(404)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:" + str(server.server_port) + "/fixture_network_child-1.0-py3-none-any.whl"
        _wheel(paths["wheels_root"], "fixture-direct-url", "1.0", {"fixture_direct_url.py": b"VALUE = 1\n"},
               requires=("fixture-network-child @ " + url,))
        paths["public_support_identity"] = _identity(paths)
        real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
        with pytest.raises(real.RealAdmissionError):
            real._provision_dependencies(manager, identifier, paths, task)
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dependency_paths_cannot_escape_actual_worker_sandbox(dependency_sandbox, tmp_path):
    manager, identifier, paths, _ = dependency_sandbox
    outside = tmp_path / "outside_wheels"
    outside.mkdir()
    manager._sandboxes[identifier]["wheels"] = outside
    with pytest.raises(real.RealAdmissionError):
        real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])


def test_actual_expected_pytest_executes_and_workspace_imports_match_setup(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    real._provision_dependencies(manager, identifier, paths, task)
    workspace = manager.sandboxes[identifier]["workspace"]
    (workspace / "fixture_package.py").write_text("def answer():\n    return 42\n")
    (workspace / "test_fixture.py").write_text("from fixture_package import answer\ndef test_answer():\n    assert answer() == 42\n")
    result = manager.exec(identifier, "cd /workspace && PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 python3 -s -m pytest test_fixture.py --junitxml=/tmp/_swegemma_junit_fixture.xml -p no:anyio -o timeout=0 -o python_classes='Test* *Test' -q")
    assert result.exit_code == 0 and "1 passed" in result.stdout
    junit = manager.exec(identifier, "cat /tmp/_swegemma_junit_fixture.xml")
    assert junit.exit_code == 0 and 'name="test_answer"' in junit.stdout


@pytest.fixture
def native_verification_functions(monkeypatch, native_manager_class):
    """Original pinned verification/setup functions; optional model imports bypassed."""
    wheel = REPO_ROOT / "artifacts/harness_wheels/v28/swegemma-0.2.7-py3-none-any.whl"
    def load(relative, namespace, *, excluded_classes=()):
        module = ModuleType(namespace["__name__"])
        module.__dict__.update(namespace)
        monkeypatch.setitem(sys.modules, namespace["__name__"], module)
        namespace = module.__dict__
        with zipfile.ZipFile(wheel) as archive:
            tree = ast.parse(archive.read(relative))
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.ImportFrom) and node.module
            and node.module.split(".")[0] in ("swegemma", "adk_eval_core", "pydantic"))
            and not (isinstance(node, ast.ClassDef) and node.name in excluded_classes)
            and not (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
                     and t.id == "INSTALL_DEPS_SCRIPT" for t in node.targets))]
        exec(compile(tree, relative, "exec"), namespace)
        return namespace
    models = load("swegemma/models/task.py", {"__name__": "fixture_native_models"},
                  excluded_classes=("TaskResult", "EvaluationResult"))
    setup = load("swegemma/harness/container_setup.py", {"__name__": "fixture_native_setup",
                                                        "INSTALL_DEPS_SCRIPT": None})
    async def start(manager):
        return await asyncio.to_thread(manager.start)
    async def execute(manager, identifier, command, timeout=None):
        return await asyncio.to_thread(manager.exec, identifier, command, timeout=timeout)
    async def stop(manager, identifier):
        return await asyncio.to_thread(manager.stop, identifier)
    verification = load("swegemma/harness/verification.py", {
        "__name__": "fixture_native_verification", **{name: value for name, value in setup.items() if not name.startswith("__")},
        "Task": models["Task"], "TaskResult": SimpleNamespace,
        "extract_test_files_from_patch": models["extract_test_files_from_patch"],
        "sandbox_start": start, "sandbox_exec": execute, "sandbox_stop": stop})
    # The user forbids Git add/commit even in a temporary fixture. Keep the
    # original patch application, test invocation and JUnit validator; bypass
    # only the native baseline routine that would run those Git mutations.
    verification["setup_baseline_commit"] = lambda *args, **kwargs: None
    for name in ("swegemma", "swegemma.harness"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    for name, namespace in (("swegemma.models", models), ("swegemma.models.task", models), ("swegemma.harness.container_setup", setup),
                            ("swegemma.harness.verification", verification)):
        module = ModuleType(name)
        module.__dict__.update(namespace)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    return models, verification


@pytest.mark.parametrize("known_patch", [False, True])
def test_original_native_verifier_control_executes_expected_tests_offline(
        dependency_sandbox, native_verification_functions, tmp_path, known_patch):
    from eval.solver_task import PublicAssetRef, SolverTask
    from tools.dev_eval import control_passes
    manager, _, paths, _ = dependency_sandbox
    models, verification = native_verification_functions
    repo = tmp_path / "snapshot_repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "fixture_package.py").write_text("def answer():\n    return 41\n")
    snapshot = paths["public_root"] / "snapshots/fastapi_1.tgz"
    snapshot.parent.mkdir(mode=0o700)
    with tarfile.open(snapshot, "w:gz") as archive:
        for path in sorted(repo.iterdir()):
            archive.add(path, arcname=path.name)
    snapshot.chmod(0o600)
    private_patch = ("diff --git a/test_fixture.py b/test_fixture.py\nnew file mode 100644\n"
                     "--- /dev/null\n+++ b/test_fixture.py\n@@ -0,0 +1,3 @@\n"
                     "+from fixture_package import answer\n+def test_answer():\n+    assert answer() == 42\n")
    patch = ("diff --git a/fixture_package.py b/fixture_package.py\n"
             "--- a/fixture_package.py\n+++ b/fixture_package.py\n@@ -1,2 +1,2 @@\n"
             " def answer():\n-    return 41\n+    return 42\n") if known_patch else ""
    ref = PublicAssetRef("snapshot", "fastapi_1", "snapshots/fastapi_1.tgz",
                         hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot.stat().st_size)
    public_task = SolverTask(1, "fastapi_1", "fastapi/fastapi", "1" * 40, "synthetic public fixture", "", ref)
    observations = real.observe_manager(manager, paths, public_task)
    native_task = models["Task"](instance_id=public_task.instance_id, repo=public_task.repo,
                                 base_commit=public_task.base_commit, problem_statement="", test_patch=private_patch)
    config = SimpleNamespace(wheels_dir=paths["wheels_root"], tasks_path=paths["public_root"] / "unopened.jsonl",
                             snapshots_dir=snapshot.parent, submission_dir=None, results_dir=None, sandbox="subprocess",
                             harness=SimpleNamespace(command_timeout_seconds=60))
    result = asyncio.run(verification["verify_task"](manager, config, native_task, snapshot,
                                                     agent_patch=patch, agent_error=None))
    assert result.resolved is known_patch
    assert result.test_exit_code == (0 if known_patch else 1)
    assert ("1 passed" if known_patch else "1 failed") in result.test_output
    assert observations["dependency_setup"]
    assert len(observations["pytest_invocations"]) == 1
    assert observations["junit_xml"] and 'name="test_answer"' in observations["junit_xml"]
    native_data = {"resolved": result.resolved, "test_exit_code": result.test_exit_code}
    execution = real.test_execution_observation(observations, native_data, private_patch)
    assert execution["status"] == "executed" and execution["required_tests_executed"] is True
    control = {"resolved": result.resolved, "verification_observations": {
        "apply_status": "applied" if known_patch else "not_needed", "apply_error": None,
        "required_tests_passed": result.resolved, "test_execution": execution},
        "verifier_result": {"native_result_json": json.dumps(native_data)}}
    assert control_passes("known_patch" if known_patch else "no_patch", control) is True
    assert observations["sandbox_ids"] == observations["stopped_ids"]


def test_solver_and_verifier_share_fresh_dependency_setup_and_fast_path_refresh(
        dependency_sandbox, native_verification_functions, native_manager_class):
    from eval.solver_task import PublicAssetRef, SolverTask
    _, _, paths, _ = dependency_sandbox
    _, verification = native_verification_functions
    task = SolverTask(1, "fastapi_1", "fastapi/fastapi", "1" * 40, "synthetic public fixture", "",
                      PublicAssetRef("snapshot", "fastapi_1", "snapshots/fastapi_1.tgz", "2" * 64, 1))
    observed_setups, prefixes = [], []
    for phase in ("solver", "verifier"):
        manager = native_manager_class(timeout_seconds=60,
                                       base_dir=paths["worker_root"] / "sandboxes" / phase,
                                       system_site_packages=False)
        observations = real.observe_manager(manager, paths, task)
        try:
            identifier = manager.start()
            verification["setup_workspace_test_config"](manager, identifier, repo=task.repo)
            sandbox = manager.sandboxes[identifier]
            marker = sandbox["workspace"] / "fixture_setup_invoked"
            assert marker.read_text() == "fast-only"
            marker.unlink()
            verification["setup_workspace_test_config"](manager, identifier, repo=task.repo, overwrite=True)
            assert marker.read_text() == "fast-only"
            assert len(observations["dependency_setup"]) == 1
            setup_commands = [entry for entry in observations["commands"]
                              if entry["purpose"] == "dependency_setup"]
            assert setup_commands and all(entry["network_attempt"] is False for entry in setup_commands)
            assert not observations["setup_error"]
            observed_setups.append(observations["dependency_setup"][identifier])
            probe = manager.exec(identifier, "python3 -I -c 'import sys,pytest; print(sys.prefix)' ")
            assert probe.exit_code == 0
            prefixes.append(probe.stdout.strip())
        finally:
            manager.cleanup_all()
    assert observed_setups[0] == observed_setups[1]
    assert prefixes[0] != prefixes[1]


def _junit_case(*, name="test_expected", body="<failure message='fixture assertion'/>",
                tests="1", failures="1", errors="0", skipped="0"):
    return ("<testsuites><testsuite tests='" + tests + "' failures='" + failures + "' errors='" + errors
            + "' skipped='" + skipped + "'><testcase name='" + name + "'>" + body
            + "</testcase></testsuite></testsuites>")


@pytest.mark.parametrize("targets,code,xml", [
    ("test_fixture.py", 1, None),
    ("test_fixture.py", 1, ""),
    ("test_fixture.py", 1, "<malformed>"),
    ("test_fixture.py", 1, _junit_case(body="<error message='fixture collection error'/>", failures="0", errors="1")),
    ("test_fixture.py", 0, _junit_case(body="<skipped/>", failures="0", skipped="1")),
    ("test_fixture.py", 1, _junit_case(name="test_unrelated")),
    ("test_fixture.py", 1, "<testsuites><testsuite tests='2' failures='1' errors='0' skipped='1'>"
     "<testcase name='test_expected'><skipped/></testcase><testcase name='test_unrelated'><failure/>"
     "</testcase></testsuite></testsuites>"),
    ("test_wrong.py", 1, _junit_case()),
    ("test_fixture.py", 2, _junit_case()),
    ("test_fixture.py", 5, _junit_case()),
    ("test_fixture.py", 1, _junit_case(tests="2")),
    ("test_fixture.py", 1, _junit_case(failures="2")),
    ("test_fixture.py", 1, _junit_case(failures="-1")),
], ids=["missing_pytest_no_junit", "empty_junit", "malformed_junit", "collection_error", "all_skipped",
        "required_node_missing", "required_node_skipped", "wrong_targets", "pytest_interrupted",
        "pytest_no_tests", "test_count_mismatch", "failure_count_mismatch", "negative_counter"])
def test_native_execution_receipt_rejects_nonexecution_and_inconsistent_junit(
        native_verification_functions, targets, code, xml):
    private_patch = ("diff --git a/test_fixture.py b/test_fixture.py\nnew file mode 100644\n"
                     "--- /dev/null\n+++ b/test_fixture.py\n@@ -0,0 +1,2 @@\n"
                     "+def test_expected():\n+    assert True\n")
    native = {"resolved": False, "test_exit_code": code}
    observations = {"pytest_invocations": [{"targets": targets, "exit_code": code, "junit_xml": xml}]}
    receipt = real.test_execution_observation(observations, native, private_patch)
    assert receipt["status"] == "infrastructure_error"
    assert receipt["required_tests_executed"] is False
    assert native == {"resolved": False, "test_exit_code": code}


def test_offline_resolver_respects_constraints_instead_of_highest_version(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    for version in ("0.48.0", "1.6.0"):
        _wheel(paths["wheels_root"], "starlette", version, {"starlette.py": ("VERSION = " + repr(version)).encode()})
    _wheel(paths["wheels_root"], "fixture-fastapi", "1.0", {"fixture_fastapi.py": b"VALUE = 1"},
           requires=("starlette>=0.40,<0.49",))
    paths["public_support_identity"] = _identity(paths)
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    observed = real._provision_dependencies(manager, identifier, paths, task)
    assert observed["selected_versions"]["starlette"] == "0.48.0"
    assert "starlette-1.6.0-py3-none-any.whl" not in observed["selected_wheels"]
    assert observed["wheel_sha256"]["starlette-0.48.0-py3-none-any.whl"] == hashlib.sha256(
        (paths["wheels_root"] / "starlette-0.48.0-py3-none-any.whl").read_bytes()).hexdigest()
    result = manager.exec(identifier, "python3 -I -c 'import starlette; print(starlette.VERSION)'")
    assert result.exit_code == 0 and result.stdout.strip() == "0.48.0"


def test_pip_check_is_diagnostic_and_does_not_change_native_acceptance(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    original = manager.exec
    def execute(identifier, command, timeout=None):
        if command.endswith("-m pip --isolated check"):
            return SimpleNamespace(exit_code=1, stdout="unused package metadata conflict", stderr="")
        return original(identifier, command, timeout=timeout)
    manager.exec = execute
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    observed = real._provision_dependencies(manager, identifier, paths, task)
    assert observed["pip_check_passed"] is False
    assert observed["pip_check_exit_code"] == 1 and observed["pip_check_diagnostic_only"] is True
    assert manager.exec(identifier, "python3 -I -m pytest --version").exit_code == 0


def _workspace_build_wheels(paths):
    # Existing admitted pure wheels only; never download or source-build a backend.
    public = REPO_ROOT.parent / "gemma-4-developer-agent/wheels"
    for name in ("setuptools-83.0.0-py3-none-any.whl", "wheel-0.47.0-py3-none-any.whl"):
        source = public / name
        assert source.is_file(), "public fixture backend wheel missing"
        shutil.copyfile(source, paths["wheels_root"] / name)


def test_editable_metadata_constraints_override_cached_newer_versions(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    _workspace_build_wheels(paths)
    for version in ("0.48.0", "1.6.0"):
        _wheel(paths["wheels_root"], "starlette", version, {"starlette.py": ("VERSION = " + repr(version)).encode()})
    workspace = manager.sandboxes[identifier]["workspace"]
    (workspace / "setup.py").write_text("from setuptools import setup; setup()\n")
    (workspace / "setup.cfg").write_text("[metadata]\nname = fixture-workspace\nversion = 1.0\n"
                                       "[options]\npy_modules = fixture_package\ninstall_requires = starlette>=0.40,<0.49\n")
    (workspace / "fixture_package.py").write_text("VALUE = 42\n")
    paths["public_support_identity"] = _identity(paths)
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    observed = real._provision_dependencies(manager, identifier, paths, task)
    assert observed["selected_versions"]["starlette"] == "0.48.0"
    probe = manager.exec(identifier, "python3 -I -c 'from importlib.metadata import version; print(version(\"fixture-workspace\"))'")
    assert probe.exit_code == 0 and probe.stdout.strip() == "1.0"


def test_dynamic_editable_direct_url_is_rejected_before_dependency_resolution(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    _workspace_build_wheels(paths)
    workspace = manager.sandboxes[identifier]["workspace"]
    (workspace / "setup.py").write_text("from setuptools import setup; setup()\n")
    (workspace / "setup.cfg").write_text("[metadata]\nname = fixture-workspace\nversion = 1.0\n"
                                       "[options]\npy_modules = fixture_package\n"
                                       "install_requires = fixture-child @ http://127.0.0.1:9/unadmitted.whl\n")
    (workspace / "fixture_package.py").write_text("VALUE = 42\n")
    paths["public_support_identity"] = _identity(paths)
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    original, commands = manager.exec, []
    def execute(identifier, command, timeout=None):
        commands.append(command)
        return original(identifier, command, timeout=timeout)
    manager.exec = execute
    with pytest.raises(real.RealAdmissionError, match="direct URLs"):
        real._provision_dependencies(manager, identifier, paths, task)
    assert not any("_dependency_report.json" in command for command in commands)


def test_setup_failure_is_observed_even_if_native_caller_swallows_it(
        dependency_sandbox, native_verification_functions, native_manager_class):
    from eval.solver_task import PublicAssetRef, SolverTask
    _, _, paths, _ = dependency_sandbox
    _, verification = native_verification_functions
    for wheel in paths["wheels_root"].glob("pytest-*.whl"):
        wheel.unlink()
    paths["public_support_identity"] = _identity(paths)
    task = SolverTask(1, "fastapi_1", "fastapi/fastapi", "1" * 40, "public fixture", "",
                      PublicAssetRef("snapshot", "fastapi_1", "snapshots/fastapi_1.tgz", "2" * 64, 1))
    manager = native_manager_class(timeout_seconds=60, base_dir=paths["worker_root"] / "sandboxes/failure",
                                   system_site_packages=False)
    observations = real.observe_manager(manager, paths, task, observing=False)
    try:
        identifier = manager.start()
        try:
            verification["setup_workspace_test_config"](manager, identifier, repo=task.repo)
        except real.RealAdmissionError:
            pass  # Native run_agent_sandbox catches this same exception.
        assert observations["setup_error"] == {"phase": "dependency provisioning", "exception_type": "RealAdmissionError"}
        assert not observations["dependency_setup"]
    finally:
        manager.cleanup_all()
