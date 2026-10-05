"""Pure fake verification admission checks; never execute the installed harness."""
from contextlib import nullcontext
from pathlib import Path
import shlex
from types import SimpleNamespace

import pytest

from tools.harness_cert import _synthetic_verification as verification
from tools.harness_cert._probe_safety import ProbeRefused
from tools.harness_cert.run_h04_h05_h18 import CHANGED, INITIAL, expected_patch


class Guard:
    def __init__(self, root):
        self.run_root = root
        self.violations = []

    def refuse(self, reason):
        self.violations.append(reason)
        raise ProbeRefused(reason)

    def processes_allowed(self, kind):
        assert kind in {"venv", "shell"}
        return nullcontext()


@pytest.fixture
def support_source(tmp_path, monkeypatch):
    """Small stand-in manifest; production's 501-file pin remains immutable."""
    repo = tmp_path.resolve() / "repo"
    root = tmp_path.resolve() / "run"
    root.mkdir()
    source = repo / ".venv/lib/python3.14/site-packages"
    (source / "pytest").mkdir(parents=True)
    (source / "pytest/__init__.py").write_text("# Pure Python fixture.\n")
    metadata = source / "pytest-9.1.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: pytest\nVersion: 9.1.1\nRequires-Python: >=3.10\n")
    (metadata / "WHEEL").write_text("Root-Is-Purelib: true\nTag: py3-none-any\n")
    monkeypatch.setattr(verification, "SUPPORT_ROOTS", ("pytest", "pytest-9.1.1.dist-info"))
    monkeypatch.setattr(verification, "SUPPORT_VERSIONS", {"pytest": "9.1.1"})
    manifest = verification._collect(source, caches=True)
    monkeypatch.setattr(verification, "SUPPORT_MANIFEST_SHA256", verification._manifest_hash(manifest))
    return repo, root, source


def test_support_pin_and_exact_pure_python_closure_are_explicit():
    assert verification.SUPPORT_VERSIONS == {
        "pytest": "9.1.1", "iniconfig": "2.3.0", "packaging": "26.3",
        "pluggy": "1.6.0", "pygments": "2.21.0",
    }
    assert verification.SUPPORT_MANIFEST_SHA256 == "3694df089cbd36a34d0d79ae5cb5ac7bbb67963aef1250661377400a10263b76"
    assert "py.py" in verification.SUPPORT_ROOTS
    assert all(".pth" not in name for name in verification.SUPPORT_ROOTS)


def test_support_preflight_stages_only_the_rehashed_manifest(support_source):
    repo, root, source = support_source
    support = verification.prepare_support(repo, root)
    assert support["root"] == str(root / "pytest_support")
    assert support["versions"] == {"pytest": "9.1.1"}
    assert support["manifest_sha256"] == verification._manifest_hash(support["manifest"])
    for item in support["manifest"]:
        assert (root / "pytest_support" / item["path"]).read_bytes() == (source / item["path"]).read_bytes()


@pytest.mark.parametrize("relative", ["pytest/extra.py", "pytest-9.1.1.dist-info/REQUESTED"])
def test_new_files_change_the_pinned_support_manifest(support_source, relative):
    repo, root, source = support_source
    (source / relative).write_text("unexpected\n")
    with pytest.raises(ProbeRefused, match="reviewed pure-Python closure"):
        verification.prepare_support(repo, root)


@pytest.mark.parametrize("relative", ["pytest/evil.pth", "pytest/native.so", "pytest/source.pyc", "pytest/data.txt"])
def test_support_rejects_pth_binaries_and_nonstandard_files(support_source, relative):
    repo, root, source = support_source
    (source / relative).write_text("unexpected\n")
    with pytest.raises(ProbeRefused, match="non-Python support file"):
        verification.prepare_support(repo, root)


def test_support_ignores_only_conventional_bytecode_cache(support_source):
    repo, root, source = support_source
    cache = source / "pytest/__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-314.pyc").write_bytes(b"unused bytecode")
    support = verification.prepare_support(repo, root)
    assert not any("__pycache__" in item["path"] for item in support["manifest"])


def test_support_rejects_even_a_symlink_inside_excluded_cache(support_source):
    repo, root, source = support_source
    cache = source / "pytest/__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-314.pyc").symlink_to(source / "pytest/__init__.py")
    with pytest.raises(ProbeRefused, match="special support entry"):
        verification.prepare_support(repo, root)


@pytest.mark.parametrize("location", ["pytest/__init__.py", "pytest", "pytest-9.1.1.dist-info"])
def test_support_rejects_file_and_directory_aliases(support_source, location):
    repo, root, source = support_source
    path = source / location
    moved = root / "original"
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ProbeRefused, match="symlink|special"):
        verification.prepare_support(repo, root)


def _manager(root):
    venv = root / "sandbox/venv"
    (venv / "lib/python3.12/site-packages/pip").mkdir(parents=True)
    return SimpleNamespace(system_site_packages=False, sandboxes={"sid": {"venv": venv}})


def test_stage_support_keeps_isolated_venv_and_hashes_exact_files(support_source):
    repo, root, _ = support_source
    support = verification.prepare_support(repo, root)
    manager = _manager(root)
    files = verification.stage_support(manager, "sid", support, Guard(root))
    assert manager.system_site_packages is False
    assert files == support["manifest"]
    target = manager.sandboxes["sid"]["venv"] / "lib/python3.12/site-packages"
    assert not list(target.rglob("*.pth"))
    assert {p.name for p in target.iterdir()} == {"pip", *verification.SUPPORT_ROOTS}


@pytest.mark.parametrize("fault", ["changed_source", "new_source", "symlink_source", "system_site", "host_pth", "extra_module", "outside_venv"])
def test_stage_support_fails_closed_on_tamper_or_broader_venv(support_source, fault, tmp_path):
    repo, root, _ = support_source
    support = verification.prepare_support(repo, root)
    manager = _manager(root)
    staged = Path(support["root"])
    target = manager.sandboxes["sid"]["venv"] / "lib/python3.12/site-packages"
    if fault == "changed_source":
        (staged / "pytest/__init__.py").write_text("changed\n")
    elif fault == "new_source":
        (staged / "evil.py").write_text("extra\n")
    elif fault == "symlink_source":
        (staged / "pytest/__init__.py").unlink()
        (staged / "pytest/__init__.py").symlink_to(repo / ".venv/lib/python3.14/site-packages/pytest/__init__.py")
    elif fault == "system_site":
        manager.system_site_packages = True
    elif fault == "host_pth":
        (target / "_host_env.pth").write_text("/host\n")
    elif fault == "extra_module":
        (target / "evil").mkdir()
    else:
        manager.sandboxes["sid"]["venv"] = tmp_path.resolve() / "outside"
    with pytest.raises(ProbeRefused):
        verification.stage_support(manager, "sid", support, Guard(root))


@pytest.fixture
def fake_verifier(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "run"
    root.mkdir()
    (root / "tmp").mkdir()
    site = root / "installed"
    (site / "swegemma/harness").mkdir(parents=True)
    (site / "swegemma/harness/container_setup.py").write_text(
        "def setup_workspace_test_config():\n    script = 'configure fixture'\n"
        "def setup_git_exclude():\n    docker.exec(identifier, 'fixed git exclude')\n"
        "def extract_snapshot():\n    docker.exec(identifier, 'fixed cleanup')\n"
        "def apply_patch_in_container():\n    script = 'apply exact patch'\n"
    )
    monkeypatch.setattr(verification.sysconfig, "get_path", lambda name: str(site))
    monkeypatch.setattr(verification, "stage_support", lambda *args: [{"path": "pytest/__init__.py", "sha256": "fixture"}])
    monkeypatch.setattr(verification, "os", SimpleNamespace(environ={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}))
    workspace = root / "sandbox/workspace"
    workspace.mkdir(parents=True)
    (workspace / ".git/hooks").mkdir(parents=True)
    (workspace / ".git/config").write_text("[core]\n")
    (workspace / "app.py").write_text(INITIAL)
    (workspace / verification.TEST_NAME).write_text(verification.TEST_CONTENT)
    wheels = root / "sandbox/wheels"
    wheels.mkdir()
    sandbox_tmp = root / "sandbox/tmp"
    sandbox_tmp.mkdir()
    snapshot = root / "synthetic.tar.gz"
    snapshot.write_bytes(b"fixed synthetic snapshot")

    class Manager:
        system_site_packages = False

        def __init__(self):
            self.sandboxes = {"sid": {"workspace": workspace, "wheels": wheels, "tmp": sandbox_tmp,
                                      "venv": root / "sandbox/venv"}}
            self.executed = []
            self.copied = []
            self.stopped = []

        def start(self):
            return "sid"

        def exec(self, identifier, command, timeout=None):
            self.executed.append((identifier, command, timeout))
            if command == "ls /wheels/*.whl 2>/dev/null | wc -l":
                stdout = "0\n"
            elif command.startswith("cat "):
                stdout = '<testsuite tests="1"><testcase classname="test_marker" name="test_marker"/></testsuite>'
            elif command == verification._DIFF:
                stdout = expected_patch() if (workspace / "app.py").read_text() == CHANGED else ""
            else:
                stdout = ""
            return SimpleNamespace(exit_code=0, stdout=stdout, stderr="")

        def copy_to(self, identifier, source, destination):
            self.copied.append((identifier, source, destination))

        def stop(self, identifier):
            self.stopped.append(identifier)

    manager = Manager()
    guard = Guard(root)
    observation = verification.instrument_verifier(manager, snapshot, [], guard, {"fixture": True})
    identifier = manager.start()
    return manager, guard, observation, identifier, snapshot, site


def test_verification_static_commands_add_only_official_fixed_operations(fake_verifier):
    manager, _, _, identifier, snapshot, site = fake_verifier
    commands = verification.verification_commands(site, snapshot.name)
    assert any('commit -m "eval_baseline"' in command for command in commands)
    assert any(command.endswith("/workspace synthetic/probe 1") for command in commands)
    assert any("git checkout HEAD -- conftest.py pytest.ini" in command for command in commands)
    assert all("git checkout HEAD -- test_marker.py" not in command for command in commands)
    for command in commands:
        manager.exec(identifier, command)
    assert len(manager.executed) == len(commands)


def test_verifier_copies_only_exact_snapshot_or_fixed_patch_from_synthetic_tmp(fake_verifier):
    manager, guard, observation, identifier, snapshot, _ = fake_verifier
    manager.copy_to(identifier, snapshot, "/tmp")
    patch = guard.run_root / "tmp/tmpabcdefgh.patch"
    patch.write_text(expected_patch())
    manager.copy_to(identifier, patch, "/tmp/")
    assert observation["baseline_before_patch"] == {"app_content": INITIAL, "test_content": verification.TEST_CONTENT}
    assert len(observation["copies"]) == 2


def test_verifier_binds_patch_application_and_cleanup_to_the_exact_copied_file(fake_verifier):
    manager, guard, _, identifier, _, site = fake_verifier
    patch = guard.run_root / "tmp/tmpabcdefgh.patch"
    patch.write_text(expected_patch())
    script = verification._source_script(site, "apply_patch_in_container")
    apply = f"python3 -S -c {shlex.quote(script)} /workspace /tmp/{patch.name}"
    with pytest.raises(ProbeRefused, match="unapproved verification command"):
        manager.exec(identifier, apply)
    manager.copy_to(identifier, patch, "/tmp/")
    manager.exec(identifier, apply)
    manager.exec(identifier, f"rm -f /tmp/{patch.name}*")
    with pytest.raises(ProbeRefused, match="unapproved verification command"):
        manager.exec(identifier, apply.replace(patch.name, "tmpxxxxxxxx.patch"))


def test_verifier_accepts_only_the_official_pytest_command_before_fixed_env_injection(fake_verifier):
    manager, _, _, identifier, _, _ = fake_verifier
    junit = "/tmp/_swegemma_junit_012345abcdef.xml"
    manager.exec(identifier, f"rm -f {junit}")
    injected = _pytest_command(junit).replace(
        "cd /workspace && ", "cd /workspace && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ", 1,
    )
    with pytest.raises(ProbeRefused, match="unapproved verification command"):
        manager.exec(identifier, injected)


@pytest.mark.parametrize("fault", ["wrong_bytes", "wrong_parent", "wrong_name", "wrong_destination", "symlink", "second_patch", "wrong_baseline"])
def test_verifier_refuses_other_patch_inputs(fake_verifier, fault):
    manager, guard, _, identifier, _, _ = fake_verifier
    patch = guard.run_root / "tmp/tmpabcdefgh.patch"
    patch.write_text(expected_patch())
    destination = "/tmp/"
    if fault == "wrong_bytes":
        patch.write_text("arbitrary patch\n")
    elif fault == "wrong_parent":
        new = guard.run_root / patch.name
        patch.rename(new)
        patch = new
    elif fault == "wrong_name":
        new = patch.with_name("evil.patch")
        patch.rename(new)
        patch = new
    elif fault == "wrong_destination":
        destination = "/workspace/"
    elif fault == "symlink":
        new = guard.run_root / "original.patch"
        patch.rename(new)
        patch.symlink_to(new)
    elif fault == "second_patch":
        manager.copy_to(identifier, patch, destination)
    else:
        (manager.sandboxes[identifier]["workspace"] / "app.py").write_text(CHANGED)
    with pytest.raises(ProbeRefused):
        manager.copy_to(identifier, patch, destination)
    assert guard.violations


def _pytest_command(junit):
    return ("cd /workspace && PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 python3 -s -m pytest . "
            f'--junitxml={junit} -p no:anyio -o timeout=0 -o python_classes="Test* *Test" -q')


def test_verifier_binds_junit_path_and_records_xml_and_workspace(fake_verifier):
    manager, _, observation, identifier, _, _ = fake_verifier
    junit = "/tmp/_swegemma_junit_012345abcdef.xml"
    manager.exec(identifier, f"rm -f {junit}")
    manager.exec(identifier, _pytest_command(junit), timeout=20)
    assert manager.executed[-1][1] == _pytest_command(junit).replace(
        "cd /workspace && PYTHONSAFEPATH=1 ",
        "cd /workspace && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONSAFEPATH=1 ", 1,
    )
    assert observation["commands"][-1]["command"] == _pytest_command(junit)
    assert observation["commands"][-1]["executed_command"] == manager.executed[-1][1]
    manager.exec(identifier, f"cat {junit} 2>/dev/null || true")
    manager.stop(identifier)
    assert observation["junit_xml"].startswith('<testsuite tests="1">')
    assert observation["workspace"]["app_content"] == INITIAL
    assert observation["workspace"]["test_content"] == verification.TEST_CONTENT
    assert observation["observer_errors"] == []
    assert manager.stopped == [identifier]


@pytest.mark.parametrize("command", [
    "rm -f /tmp/_swegemma_junit_012345abcdeg.xml", "rm -f /tmp/_swegemma_junit_012345abcdef.xml; echo evil",
    "rm -f /tmp/../outside.xml", "rm -f /dev/null", "python3 -c 'arbitrary code'",
    _pytest_command("/tmp/_swegemma_junit_012345abcdef.xml"),
    "cat /tmp/_swegemma_junit_012345abcdef.xml 2>/dev/null || true",
])
def test_verifier_refuses_unsafe_or_unbound_dynamic_commands(fake_verifier, command):
    manager, guard, _, identifier, _, _ = fake_verifier
    with pytest.raises(ProbeRefused, match="unapproved verification command"):
        manager.exec(identifier, command)
    assert guard.violations


def test_verifier_refuses_junit_name_changes(fake_verifier):
    manager, _, _, identifier, _, _ = fake_verifier
    manager.exec(identifier, "rm -f /tmp/_swegemma_junit_012345abcdef.xml")
    with pytest.raises(ProbeRefused):
        manager.exec(identifier, "rm -f /tmp/_swegemma_junit_ffffffffffff.xml")


@pytest.mark.parametrize("option", ["PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"])
def test_verifier_refuses_ambient_pytest_behavior_changes(fake_verifier, monkeypatch, option):
    manager, guard, _, identifier, _, _ = fake_verifier
    monkeypatch.setitem(verification.os.environ, option, "unexpected")
    junit = "/tmp/_swegemma_junit_012345abcdef.xml"
    manager.exec(identifier, f"rm -f {junit}")
    with pytest.raises(ProbeRefused, match="pytest environment"):
        manager.exec(identifier, _pytest_command(junit))
    assert guard.violations


def test_verifier_requires_same_sandbox_and_single_start(fake_verifier):
    manager, _, _, identifier, _, _ = fake_verifier
    with pytest.raises(ProbeRefused, match="exactly one sandbox"):
        manager.start()
    with pytest.raises(ProbeRefused, match="unapproved verification sandbox"):
        manager.exec("other", "mkdir -p /workspace")


def test_verifier_wheel_staging_is_sticky_infrastructure_failure(fake_verifier):
    manager, guard, _, identifier, _, _ = fake_verifier
    (manager.sandboxes[identifier]["wheels"] / "evil.whl").write_bytes(b"unapproved")
    with pytest.raises(ProbeRefused, match="wheel staging"):
        manager.exec(identifier, "mkdir -p /workspace")
    assert guard.violations


def test_verifier_observer_error_is_preserved_and_cleanup_still_runs(fake_verifier):
    manager, guard, observation, identifier, _, _ = fake_verifier
    (manager.sandboxes[identifier]["workspace"] / ".git/config").write_text('[remote "origin"]\n')
    manager.stop(identifier)
    assert observation["observer_errors"]
    assert guard.violations
    assert manager.stopped == [identifier]


def test_verifier_rejects_changed_synthetic_verification_test(fake_verifier):
    manager, guard, observation, identifier, _, _ = fake_verifier
    (manager.sandboxes[identifier]["workspace"] / verification.TEST_NAME).write_text("def test_fake(): pass\n")
    manager.stop(identifier)
    assert observation["observer_errors"]
    assert "synthetic verification test changed" in guard.violations
    assert manager.stopped == [identifier]


def test_verifier_preserves_failed_baseline_junit_without_success_only_cat(fake_verifier):
    manager, _, observation, identifier, _, _ = fake_verifier
    junit = "/tmp/_swegemma_junit_012345abcdef.xml"
    manager.exec(identifier, f"rm -f {junit}")
    manager.exec(identifier, _pytest_command(junit))
    xml = '<testsuite tests="1" failures="1"><testcase name="test_marker"><failure/></testcase></testsuite>'
    (manager.sandboxes[identifier]["tmp"] / Path(junit).name).write_text(xml)
    manager.stop(identifier)
    assert observation["junit_xml"] == xml
    assert observation["baseline_before_patch"] == {"app_content": INITIAL, "test_content": verification.TEST_CONTENT}
    assert observation["observer_errors"] == []
