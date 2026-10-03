"""Literal interpreter-scheme profiles; the Debian layout mirrors recorded Kaggle output.

Nothing here installs, imports a target, or runs pip. The synthetic tree only
stands in for what a Debian-patched CPython reports through sys and sysconfig.
"""

import copy
import csv
import io
from types import SimpleNamespace

import pytest

from tools.h23_v4 import capture
from tools.h23_v4.capture import CaptureConfig, run_capture
from tools.h23_v4.importer import validate_snapshot
from tools.h23_v4.record_policy import console_script_path, validate_interpreter_roots
from tools.h23_v4.schema import PolicyError
from .fixtures import archive_bytes, fixture
from .test_capture import fake_pip, record_line, write_wheel

KAGGLE = {"python_version": [3, 12, 13], "scheme": "posix_local", "prefix": "/usr",
          "site_packages": ["/usr/local/lib/python3.12/dist-packages"],
          "scripts_directory": "/usr/local/bin", "executable": "/usr/bin/python3"}
PREFIX = {"python_version": [3, 12, 1], "scheme": "posix_prefix", "prefix": "/observed/python",
          "site_packages": ["/observed/python/lib/python3.12/site-packages"],
          "scripts_directory": "/observed/python/bin", "executable": "/observed/python/bin/python"}


def _code(interpreter):
    try:
        validate_interpreter_roots(interpreter)
    except PolicyError as error:
        return error.code
    return None


def _kaggle_fixture(**changes):
    manifest, blobs = fixture(console={"swegemma": [{"name": "swe-run", "target": "swegemma.cli:main"}]})
    manifest["interpreter"] = dict(copy.deepcopy(KAGGLE), **changes)
    for distribution in manifest["distributions"]:
        distribution["installation_root"] = KAGGLE["site_packages"][0]
    return manifest, blobs


def test_recorded_kaggle_python312_posix_local_layout_is_accepted():
    assert _code(KAGGLE) is None


@pytest.mark.parametrize("change", [
    # Invoked through the scheme's own scripts directory instead of {base}/bin.
    {"executable": "/usr/local/bin/python3"},
    # The same templates for another minor version and another base: no fixed absolute path.
    {"python_version": [3, 11, 9], "site_packages": ["/usr/local/lib/python3.11/dist-packages"]},
    {"prefix": "/opt/debian", "site_packages": ["/opt/debian/local/lib/python3.12/dist-packages"],
     "scripts_directory": "/opt/debian/local/bin", "executable": "/opt/debian/bin/python3.12"},
])
def test_posix_local_follows_scheme_templates_not_one_absolute_path(change):
    assert _code(dict(KAGGLE, **change)) is None


@pytest.mark.parametrize("change", [
    # Correct dist-packages root with a wrong scripts directory.
    {"scripts_directory": "/usr/bin"},
    {"scripts_directory": "/usr/local/sbin"},
    {"scripts_directory": "/usr/local/lib/python3.12/bin"},
    # Wrong Python minor version in the path.
    {"site_packages": ["/usr/local/lib/python3.11/dist-packages"]},
    {"python_version": [3, 13, 0]},
    # Arbitrary neighbouring /usr/local/lib/... roots.
    {"site_packages": ["/usr/local/lib/python3.12/site-packages"]},
    {"site_packages": ["/usr/local/lib/python3/dist-packages"]},
    {"site_packages": ["/usr/local/lib/python3.12/dist-packages/vendored"]},
    {"site_packages": ["/usr/local/lib64/python3.12/dist-packages"]},
    {"site_packages": ["/usr/local/lib/python3.12"]},
    # A prefix that merely looks similar.
    {"prefix": "/usr/local"},
    {"prefix": "/opt/usr"},
    # Mixed posix_local / posix_prefix observations.
    {"site_packages": ["/usr/local/lib/python3.12/dist-packages", "/usr/lib/python3.12/site-packages"]},
    {"site_packages": ["/usr/lib/python3.12/dist-packages"]},
    {"site_packages": ["/usr/lib/python3/dist-packages"]},
    {"scheme": "posix_prefix"},
    {"scheme": "posix_venv"},
    # Interpreter outside both {base}/bin and the scheme's scripts directory.
    {"executable": "/opt/python/bin/python3"},
    {"executable": "/usr/local/lib/python3"},
    {"site_packages": []},
])
def test_inconsistent_posix_local_observations_reject(change):
    assert _code(dict(KAGGLE, **change)) == "INTERPRETER_SCHEME"


@pytest.mark.parametrize("scheme", ["posix_prefix", "posix_venv"])
@pytest.mark.parametrize("root", [
    "/observed/python/lib/python3.12/site-packages", "/observed/python/lib64/python3.12/site-packages",
    "/observed/python/lib/python3.12/dist-packages", "/observed/python/lib64/python3.12/dist-packages",
])
def test_existing_prefix_and_venv_profiles_still_accept(scheme, root):
    assert _code(dict(PREFIX, scheme=scheme, site_packages=[root])) is None


@pytest.mark.parametrize("scheme", ["posix_prefix", "posix_venv"])
@pytest.mark.parametrize("change", [
    {"scripts_directory": "/observed/python/local/bin"},
    {"executable": "/observed/python/local/bin/python"},
    {"site_packages": ["/observed/python/local/lib/python3.12/dist-packages"]},
    {"site_packages": ["/observed/python/lib/python3.11/site-packages"]},
    {"scripts_directory": "/other/bin"},
    {"executable": "/other/bin/python"},
])
def test_existing_prefix_and_venv_profiles_gain_no_debian_allowance(scheme, change):
    assert _code(dict(PREFIX, scheme=scheme, **change)) == "INTERPRETER_SCHEME"


@pytest.mark.parametrize("scheme", ["deb_system", "posix_home", "posix_user", "osx_framework_library", "nt", "venv", "POSIX_LOCAL", ""])
def test_unknown_schemes_still_reject(scheme):
    assert _code(dict(KAGGLE, scheme=scheme)) == "INTERPRETER_SCHEME"
    with pytest.raises(PolicyError) as caught:
        validate_snapshot(archive_bytes(*_kaggle_fixture(scheme=scheme)))
    assert caught.value.code == "SCHEMA_INTERPRETER"


def test_importer_accepts_kaggle_layout_and_console_script_in_its_scripts_directory():
    manifest, blobs = _kaggle_fixture()
    contents, facts = validate_snapshot(archive_bytes(manifest, blobs))
    assert contents.manifest["interpreter"] == KAGGLE
    assert facts["row_category_counts"]["verified_console_script"] == 1
    assert console_script_path(manifest["distributions"][0], KAGGLE, "swe-run") == "../../../bin/swe-run"


@pytest.mark.parametrize("change", [
    {"scripts_directory": "/usr/bin"},
    {"python_version": [3, 11, 13]},
    {"site_packages": ["/usr/local/lib/python3.12/dist-packages", "/usr/lib/python3.12/site-packages"]},
    {"scheme": "posix_prefix"},
])
def test_importer_rejects_inconsistent_kaggle_observations(change):
    with pytest.raises(PolicyError) as caught:
        validate_snapshot(archive_bytes(*_kaggle_fixture(**change)))
    assert caught.value.code == "INTERPRETER_SCHEME"


@pytest.fixture
def kaggle_tree(tmp_path, monkeypatch):
    """What a Debian-patched CPython 3.12 reports: base /usr, roots under /usr/local."""
    root = tmp_path.resolve()
    base, wheelhouse, output = root / "usr", root / "wheels", root / "output"
    site, scripts = base / "local/lib/python3.12/dist-packages", base / "local/bin"
    for directory in (site, scripts, base / "bin", wheelhouse, output):
        directory.mkdir(parents=True)
    dist = "swegemma-1.2.3.dist-info"
    files = {
        "swegemma/__init__.py": b"raise AssertionError('target imported')\n",
        dist + "/METADATA": b"Metadata-Version: 2.1\nName: swegemma\nVersion: 1.2.3\n\n",
        dist + "/entry_points.txt": b"[console_scripts]\nswe-run = swegemma.cli:main\n",
    }
    for relative, raw in files.items():
        (site / relative).parent.mkdir(parents=True, exist_ok=True)
        (site / relative).write_bytes(raw)
    script = b"#!/usr/bin/python3\nprint('fixture script; never execute')\n"
    (scripts / "swe-run").write_bytes(script)
    rows = [record_line(relative, raw) for relative, raw in sorted(files.items())]
    rows += [record_line("../../../bin/swe-run", script), [dist + "/RECORD", "", ""]]
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    (site / dist / "RECORD").write_bytes(record.getvalue().encode("utf-8"))
    write_wheel(wheelhouse, "swegemma-1.2.3-py3-none-any.whl", "swegemma", "1.2.3")
    paths = {"purelib": str(site), "platlib": str(site), "scripts": str(scripts)}
    monkeypatch.setattr(capture, "sys", SimpleNamespace(
        prefix=str(base), executable=str(base / "bin/python3"), version_info=(3, 12, 13, "final", 0)))
    monkeypatch.setattr(capture, "sysconfig", SimpleNamespace(
        get_default_scheme=lambda: "posix_local", get_path=lambda key: paths[key]))
    return SimpleNamespace(base=base, site=site, scripts=scripts, wheelhouse=wheelhouse, output=output, paths=paths)


def test_synthetic_kaggle_layout_reaches_bootstrap_and_imports(kaggle_tree):
    """Formerly: notebook defaults raised INTERPRETER_SCHEME before pip ran."""
    config = CaptureConfig(wheelhouse=kaggle_tree.wheelhouse, output_directory=kaggle_tree.output)
    assert (config.scheme, config.prefix, config.site_roots, config.scripts_directory) == (
        "posix_local", kaggle_tree.base, (kaggle_tree.site,), kaggle_tree.scripts)
    calls = []
    path = run_capture(config, pip_runner=fake_pip(0, calls))
    assert len(calls) == 1
    assert calls[0][0][0] == str(kaggle_tree.base / "bin/python3")
    contents, facts = validate_snapshot(path.read_bytes())
    interpreter = contents.manifest["interpreter"]
    assert interpreter["scheme"] == "posix_local"
    assert interpreter["site_packages"] == [str(kaggle_tree.site)]
    assert interpreter["scripts_directory"] == str(kaggle_tree.scripts)
    assert facts["row_category_counts"]["verified_console_script"] == 1


def test_synthetic_kaggle_layout_with_wrong_scripts_directory_rejects_before_bootstrap(kaggle_tree):
    kaggle_tree.paths["scripts"] = str(kaggle_tree.base / "bin")
    calls = []
    with pytest.raises(PolicyError) as caught:
        run_capture(CaptureConfig(wheelhouse=kaggle_tree.wheelhouse, output_directory=kaggle_tree.output),
                    pip_runner=fake_pip(0, calls))
    assert caught.value.code == "INTERPRETER_SCHEME"
    assert calls == []
    assert not list(kaggle_tree.output.iterdir())
