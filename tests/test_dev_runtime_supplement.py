"""Separate synthetic wheel admission and actual offline isolated provisioning."""
from __future__ import annotations

import hashlib
import os

import pytest

from eval import runtime_real as real, runtime_supplement as supplement
from eval._contracts import ContractError, canonical_json
from test_dev_runtime_dependencies import (
    _wheel, dependency_sandbox, local_pytest_wheels, native_manager_class,
)
from tools.common import tree_sha256
from tools.h23_v4.schema import PolicyError


def _manifest(root, original, rows):
    return {"schema_version": 1, "supplement_id": "SYNTHETIC-PUBLIC-SUPPLEMENT-V1",
            "target": {"python": "3.12", "platform": "linux_x86_64"},
            "original_wheels_tree_sha256": tree_sha256(real._tree(original)),
            "wheels_tree_sha256": tree_sha256({row["filename"]: row["sha256"] for row in rows}),
            "wheels": rows,
            "install_requirements": [row["distribution"] + "==" + row["version"] for row in rows]}


def _row(wheel, distribution="fixture-supplement", version="1.0", requires=(), requires_python=""):
    return {"filename": wheel.name, "distribution": distribution, "version": version,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "requires_python": requires_python, "requires_dist": list(requires),
            "source_url": "https://files.pythonhosted.org/packages/synthetic-test-fixture/" + wheel.name,
            "reason": "Synthetic public CPU test fixture, never an acquired or admitted package",
            "role": "test_fixture"}


def _seal(root, value):
    data = canonical_json(value).encode()
    (root / "manifest.json").write_bytes(data)
    (root / "manifest.json").chmod(0o600)
    return hashlib.sha256(data).hexdigest()


def _fixture(root, original, *, requires=()):
    root.mkdir(mode=0o700)
    wheels = root / "wheels"
    wheels.mkdir(mode=0o700)
    wheel = _wheel(wheels, "fixture-supplement", "1.0",
                   {"fixture_supplement.py": b"PUBLIC_ONLY = True\n"}, requires=requires)
    value = _manifest(root, original, [_row(wheel, requires=requires)])
    return root, value, _seal(root, value)


@pytest.fixture
def admitted_fixture(tmp_path):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    _wheel(original, "fixture-original", "1.0", {"fixture_original.py": b"PUBLIC = True\n"})
    root, value, digest = _fixture(tmp_path / "supplement", original)
    return root, original, value, digest


def test_public_supplement_validation_binds_actual_bytes_without_merging_original(admitted_fixture):
    root, original, value, digest = admitted_fixture
    original_before = real._tree(original)
    identity = supplement.validate_supplement(root, digest, original_wheels_root=original)
    assert identity == {"manifest_sha256": digest, "wheels_tree_sha256": value["wheels_tree_sha256"],
                        "supplement_id": value["supplement_id"]}
    assert real._tree(original) == original_before
    assert not (original / value["wheels"][0]["filename"]).exists()


@pytest.mark.parametrize("mutation", ["missing_manifest", "missing_wheel", "corrupt_wheel",
                                     "extra_root", "extra_wheel", "manifest_changed", "wrong_identity"])
def test_missing_corrupt_or_unreviewed_artifacts_fail_closed(admitted_fixture, mutation):
    root, original, value, digest = admitted_fixture
    wheel = root / "wheels" / value["wheels"][0]["filename"]
    if mutation == "missing_manifest":
        (root / "manifest.json").unlink()
    elif mutation == "missing_wheel":
        wheel.unlink()
    elif mutation == "corrupt_wheel":
        wheel.write_bytes(b"corrupt archive")
    elif mutation == "extra_root":
        (root / "unreviewed.json").write_text("{}")
    elif mutation == "extra_wheel":
        _wheel(root / "wheels", "fixture-unreviewed", "1.0", {})
    elif mutation == "manifest_changed":
        (root / "manifest.json").write_text("{}")
    else:
        digest = "a" * 64
    with pytest.raises((ContractError, PolicyError, OSError)):
        supplement.validate_supplement(root, digest, original_wheels_root=original)


@pytest.mark.parametrize("mutation", ["root_symlink", "wheel_directory_symlink", "wheel_symlink",
                                     "wheel_hardlink", "manifest_hardlink", "nested_original"])
def test_filesystem_aliases_and_overlapping_original_are_rejected(admitted_fixture, tmp_path, mutation):
    root, original, value, digest = admitted_fixture
    wheel = root / "wheels" / value["wheels"][0]["filename"]
    if mutation == "root_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    elif mutation == "wheel_directory_symlink":
        target = tmp_path / "aliased-wheels"
        (root / "wheels").rename(target)
        (root / "wheels").symlink_to(target, target_is_directory=True)
    elif mutation == "wheel_symlink":
        target = tmp_path / "aliased-wheel.whl"
        wheel.rename(target)
        wheel.symlink_to(target)
    elif mutation == "wheel_hardlink":
        os.link(wheel, tmp_path / "second-wheel-link")
    elif mutation == "manifest_hardlink":
        os.link(root / "manifest.json", tmp_path / "second-manifest-link")
    else:
        original = root / "original"
        original.mkdir(mode=0o700)
    with pytest.raises((ContractError, PolicyError, OSError)):
        supplement.validate_supplement(root, digest, original_wheels_root=original)


@pytest.mark.parametrize("mutation", ["wrong_name", "wrong_version", "wrong_requires_python",
                                     "wrong_requires_dist", "direct_url_dependency", "wrong_source",
                                     "source_query", "source_credentials", "traversal_filename"])
def test_reviewed_metadata_and_provenance_are_required(admitted_fixture, mutation):
    root, original, value, _ = admitted_fixture
    row = value["wheels"][0]
    if mutation == "wrong_name":
        row["distribution"] = "another-project"
    elif mutation == "wrong_version":
        row["version"] = "2.0"
    elif mutation == "wrong_requires_python":
        row["requires_python"] = ">=3.13"
    elif mutation == "wrong_requires_dist":
        row["requires_dist"] = ["fixture-not-reviewed>=1"]
    elif mutation == "direct_url_dependency":
        wheel = root / "wheels" / row["filename"]
        wheel.unlink()
        requires = ("fixture-uncontrolled @ https://example.invalid/unreviewed.whl",)
        wheel = _wheel(root / "wheels", "fixture-supplement", "1.0", {}, requires=requires)
        value["wheels"] = [_row(wheel, requires=requires)]
        value["wheels_tree_sha256"] = tree_sha256({wheel.name: value["wheels"][0]["sha256"]})
    elif mutation == "wrong_source":
        row["source_url"] = "https://example.invalid/" + row["filename"]
    elif mutation == "source_query":
        row["source_url"] += "?unreviewed=1"
    elif mutation == "source_credentials":
        row["source_url"] = "https://secret@files.pythonhosted.org/" + row["filename"]
    else:
        row["filename"] = "../" + row["filename"]
    digest = _seal(root, value)
    with pytest.raises((ContractError, ValueError)):
        supplement.validate_supplement(root, digest, original_wheels_root=original)


@pytest.mark.parametrize("tag", ["cp313-cp313-manylinux_2_28_x86_64",
                                 "cp312-cp312-manylinux_2_28_aarch64",
                                 "cp312-cp312-macosx_14_0_arm64"])
def test_incompatible_python_and_platform_archives_are_rejected(admitted_fixture, tag):
    root, original, value, _ = admitted_fixture
    row = value["wheels"][0]
    old = root / "wheels" / row["filename"]
    name = "fixture_supplement-1.0-" + tag + ".whl"
    old.rename(old.with_name(name))
    row["filename"] = name
    row["source_url"] = "https://files.pythonhosted.org/packages/synthetic-test-fixture/" + name
    value["wheels_tree_sha256"] = tree_sha256({name: row["sha256"]})
    digest = _seal(root, value)
    with pytest.raises(ContractError, match="incompatible"):
        supplement.validate_supplement(root, digest, original_wheels_root=original)


@pytest.mark.parametrize("requirements", [[], ["fixture-supplement>=1.0"], ["fixture-supplement==2.0"],
    ["fixture-supplement==1.0; python_version >= '3.12'"], ["fixture-supplement[extra]==1.0"],
    ["fixture-supplement==1.0", "fixture-supplement==1.0"]])
def test_every_supplement_distribution_requires_one_exact_unconditional_pin(admitted_fixture, requirements):
    root, original, value, _ = admitted_fixture
    value["install_requirements"] = requirements
    digest = _seal(root, value)
    with pytest.raises(ContractError, match="pin|incomplete"):
        supplement.validate_supplement(root, digest, original_wheels_root=original)


def test_supplement_cannot_replace_an_original_distribution(admitted_fixture):
    root, original, value, _ = admitted_fixture
    first = root / "wheels" / value["wheels"][0]["filename"]
    first.unlink()
    wheel = _wheel(root / "wheels", "fixture-original", "2.0", {})
    value = _manifest(root, original, [_row(wheel, distribution="fixture-original", version="2.0")])
    digest = _seal(root, value)
    with pytest.raises(ContractError, match="replace"):
        supplement.validate_supplement(root, digest, original_wheels_root=original)


def test_multiple_versions_cannot_silently_select_a_supplement_distribution(admitted_fixture):
    root, original, value, _ = admitted_fixture
    wheel = _wheel(root / "wheels", "fixture-supplement", "2.0", {})
    value["wheels"].append(_row(wheel, version="2.0"))
    value["wheels_tree_sha256"] = tree_sha256({row["filename"]: row["sha256"] for row in value["wheels"]})
    value["install_requirements"].append("fixture-supplement==2.0")
    digest = _seal(root, value)
    with pytest.raises(ContractError, match="duplicate"):
        supplement.validate_supplement(root, digest, original_wheels_root=original)


def test_stage_uses_fresh_single_link_files_and_separate_wheelhouse(admitted_fixture, tmp_path):
    root, original, value, digest = admitted_fixture
    before = real._tree(original)
    destination = tmp_path / "staged"
    identity = supplement.stage_supplement(root, destination, digest, original_wheels_root=original)
    assert supplement.validate_supplement(destination, digest, original_wheels_root=original) == identity
    for relative in ("manifest.json", "wheels/" + value["wheels"][0]["filename"]):
        copied, source = destination / relative, root / relative
        assert copied.read_bytes() == source.read_bytes()
        assert copied.stat().st_nlink == 1 and copied.stat().st_ino != source.stat().st_ino
    assert real._tree(original) == before


def _bind_supplement(paths, *, requires=()):
    root, value, digest = _fixture(paths["public_root"] / "dependency_supplement", paths["wheels_root"], requires=requires)
    paths["supplement_root"] = root
    paths["supplement_identity"] = supplement.validate_supplement(root, digest, original_wheels_root=paths["wheels_root"])
    return root, value


def test_native_isolated_install_uses_separate_find_links_and_preserves_original(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    original = real._tree(paths["wheels_root"])
    _, value = _bind_supplement(paths)
    commands = []
    native_exec = manager.exec
    def observed_exec(identifier, command, **kwargs):
        commands.append(command)
        return native_exec(identifier, command, **kwargs)
    manager.exec = observed_exec
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    result = real._provision_dependencies(manager, identifier, paths, task)
    installed = native_exec(identifier, "python3 -I -c 'import fixture_supplement; assert fixture_supplement.PUBLIC_ONLY'")
    assert installed.exit_code == 0
    selected = value["wheels"][0]["filename"]
    assert result["wheel_sources"][selected] == "supplement"
    assert result["wheel_sha256"][selected] == value["wheels"][0]["sha256"]
    assert result["supplement_identity"] == paths["supplement_identity"]
    assert result["pip_check_diagnostic_only"] is True and result["system_site_packages"] is False
    installs = [command for command in commands if " -m pip " in command and " install " in command]
    assert installs and all("--no-index" in command and "--only-binary=:all:" in command for command in installs)
    assert all(command.count("--find-links=") == 2 for command in installs)
    assert any("fixture-supplement==1.0" in command for command in installs)
    sandbox = manager.sandboxes[identifier]
    assert not (sandbox["wheels"] / selected).exists()
    assert (sandbox["root"] / "dependency_supplement/wheels" / selected).is_file()
    assert real._tree(paths["wheels_root"]) == original


def test_native_pytest_loads_reviewed_synthetic_timeout_plugin_with_strict_config(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    root, value = _bind_supplement(paths)
    source = ("def pytest_addoption(parser):\n"
              "    parser.addini('timeout', 'synthetic plugin option', default='0')\n")
    wheel = _wheel(root / "wheels", "pytest-timeout", "2.1.0",
        {"pytest_timeout.py": source.encode(),
         "pytest_timeout-2.1.0.dist-info/entry_points.txt": b"[pytest11]\ntimeout = pytest_timeout\n"},
        requires=("pytest>=5",))
    value["wheels"].append(_row(wheel, distribution="pytest-timeout", version="2.1.0", requires=("pytest>=5",)))
    value["wheels_tree_sha256"] = tree_sha256({row["filename"]: row["sha256"] for row in value["wheels"]})
    value["install_requirements"].append("pytest-timeout==2.1.0")
    paths["supplement_identity"] = supplement.validate_supplement(root, _seal(root, value),
        original_wheels_root=paths["wheels_root"])
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    result = real._provision_dependencies(manager, identifier, paths, task)
    workspace = manager.sandboxes[identifier]["workspace"]
    (workspace / "pytest.ini").write_text("[pytest]\naddopts = --strict-config\ntimeout = 20\n")
    (workspace / "test_public_probe.py").write_text(
        "def test_native_plugin_loaded(request):\n"
        "    assert request.config.pluginmanager.hasplugin('timeout')\n")
    observed = manager.exec(identifier, "cd /workspace && PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 "
        "python3 -s -m pytest test_public_probe.py --junitxml=/tmp/public-probe.xml "
        "-p no:anyio -o timeout=0 -o python_classes='Test* *Test' -q", timeout=60)
    assert observed.exit_code == 0 and "1 passed" in observed.stdout
    assert result["selected_versions"]["pytest-timeout"] == "2.1.0"
    assert (manager.sandboxes[identifier]["tmp"] / "public-probe.xml").is_file()


def test_missing_supplement_transitive_dependency_rejects_setup_offline(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    _bind_supplement(paths, requires=("fixture-unavailable-transitive>=1",))
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    with pytest.raises(real.RealAdmissionError, match="resolution and installation"):
        real._provision_dependencies(manager, identifier, paths, task)
    assert not (manager.sandboxes[identifier]["workspace"] / "fixture_setup_invoked").exists()


@pytest.mark.parametrize("location", ["source", "mounted"])
def test_corruption_after_mount_cannot_be_accepted_by_provisioning(dependency_sandbox, location):
    manager, identifier, paths, task = dependency_sandbox
    root, value = _bind_supplement(paths)
    real._mount_public_wheels(manager, identifier, paths, paths["public_support_identity"])
    target = root if location == "source" else manager.sandboxes[identifier]["root"] / "dependency_supplement"
    (target / "wheels" / value["wheels"][0]["filename"]).write_bytes(b"corrupted after authority")
    with pytest.raises(ContractError, match="hash"):
        real._provision_dependencies(manager, identifier, paths, task)


def test_solver_and_verifier_fresh_native_venvs_select_identical_public_dependencies(dependency_sandbox):
    manager, identifier, paths, task = dependency_sandbox
    _bind_supplement(paths)
    second = manager.start()
    results = []
    for phase_id in (identifier, second):
        real._mount_public_wheels(manager, phase_id, paths, paths["public_support_identity"])
        results.append(real._provision_dependencies(manager, phase_id, paths, task))
    assert manager.sandboxes[identifier]["root"] != manager.sandboxes[second]["root"]
    for field in ("selected_wheels", "selected_versions", "wheel_sha256", "wheel_sources", "supplement_identity"):
        assert results[0][field] == results[1][field]
