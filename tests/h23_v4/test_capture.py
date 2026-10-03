"""Small independent installed files and fake pip; never invoke a real installer."""

import base64
import csv
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import sysconfig
from types import SimpleNamespace
import zipfile

import pytest

from tools.h23_v4.archive import read_archive
from tools.h23_v4.capture import CaptureConfig, _wheel_observation, run_capture
from tools.h23_v4.filesystem import anchor_directory
from tools.h23_v4.importer import validate_snapshot
from tools.h23_v4.record_policy import derive_distribution_rows
from tools.h23_v4.schema import PolicyError


SECRET = b"PRIVATE_CREDENTIAL_SENTINEL_84721"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def record_line(path, raw):
    encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")
    return [path, "sha256=" + encoded, str(len(raw))]


def write_wheel(root, filename, name, version):
    with zipfile.ZipFile(root / filename, "w") as wheel:
        wheel.writestr(name.replace("-", "_") + "-" + version + ".dist-info/METADATA",
                       f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\nnot projected\n")
        wheel.writestr(name.replace("-", "_") + "/__init__.py", "raise AssertionError('never import targets')\n")


@pytest.fixture
def local_capture(tmp_path):
    prefix = tmp_path / "python"
    site = prefix / "lib/python3.11/site-packages"
    scripts = prefix / "bin"
    wheelhouse, output = tmp_path / "wheels", tmp_path / "output"
    for directory in (site, scripts, wheelhouse, output):
        directory.mkdir(parents=True, exist_ok=True)
    dist = "swegemma-1.2.3.dist-info"
    files = {
        "swegemma/__init__.py": b"raise AssertionError('target imported')\n",
        "swegemma/data.json": b'{"resource":true}\n',
        "swegemma/native.so": b"opaque omitted bytes",
        dist + "/METADATA": b"Metadata-Version: 2.1\nName: swegemma\nVersion: 1.2.3\nRequires-Python: >=3.11\nPassword: " + SECRET + b"\n\n" + SECRET,
        dist + "/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        dist + "/INSTALLER": b"pip\n",
        dist + "/direct_url.json": b'{"url":"https://user:pass@example.com/?token=' + SECRET + b'"}',
        dist + "/entry_points.txt": b"[console_scripts]\nSWE-Run = swegemma.cli:main\n",
    }
    for relative, raw in files.items():
        destination = site / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    script = scripts / "SWE-Run"
    script.write_bytes(b"#!/usr/bin/python3\nprint('fixture script; never execute')\n")
    rows = [record_line(relative, raw) for relative, raw in sorted(files.items())]
    rows.append(record_line(os.path.relpath(script, site), script.read_bytes()))
    rows.append([dist + "/RECORD", "", ""])
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    (site / dist / "RECORD").write_bytes(record.getvalue().encode("utf-8"))
    write_wheel(wheelhouse, "swegemma-1.2.3-py3-none-any.whl", "swegemma", "1.2.3")
    write_wheel(wheelhouse, "dependency-2.0-py3-none-any.whl", "dependency", "2.0")
    write_wheel(wheelhouse, "vllm-0.19.1cu128-py3-none-any.whl", "vllm", "0.19.1+cu128")
    write_wheel(wheelhouse, "cutlass-4.0-py3-none-any.whl", "cutlass", "4.0")
    config = CaptureConfig(wheelhouse=wheelhouse, output_directory=output, site_roots=(site,),
                           prefix=prefix, scripts_directory=scripts, executable=scripts / "python3",
                           python_version=(3, 11, 9), scheme="posix_prefix")
    return SimpleNamespace(config=config, site=site, scripts=scripts, dist=dist, files=files)


def fake_pip(return_code, calls):
    def run(argv, *, stdout, stderr, check, env):
        calls.append((argv, check, env))
        stdout.write(b"pip stdout " + SECRET)
        stderr.write(b"pip stderr password=" + SECRET)
        return SimpleNamespace(returncode=return_code)
    return run


@pytest.mark.parametrize("observed,expected", [("venv", "posix_venv"), ("posix_prefix", "posix_prefix"), ("posix_local", "posix_local")])
def test_default_scheme_reports_observation_with_only_documented_alias(monkeypatch, observed, expected):
    monkeypatch.setattr(sysconfig, "get_default_scheme", lambda: observed)
    assert CaptureConfig().scheme == expected


def test_default_executable_keeps_actual_invocation_and_final_symlink(tmp_path, monkeypatch):
    target = tmp_path / "real-python"
    target.write_bytes(b"fixture; never execute")
    invoked = tmp_path / "python-link"
    invoked.symlink_to(target)
    monkeypatch.setattr(sys, "executable", str(invoked))
    assert CaptureConfig().executable == invoked
    assert CaptureConfig().executable != target


@pytest.mark.parametrize("observed", ["posix_home", "posix_local"])
def test_unsupported_or_mismatched_observed_scheme_rejects_before_bootstrap(local_capture, monkeypatch, observed):
    # posix_home is unsupported; posix_local requires its own Debian roots, not this prefix-layout tree.
    monkeypatch.setattr(sysconfig, "get_default_scheme", lambda: observed)
    observed_scheme = CaptureConfig().scheme
    calls = []
    with pytest.raises(PolicyError) as caught:
        run_capture(replace(local_capture.config, scheme=observed_scheme), pip_runner=fake_pip(0, calls))
    assert caught.value.code == "INTERPRETER_SCHEME"
    assert calls == []
    assert not list(local_capture.config.output_directory.iterdir())


@pytest.mark.parametrize("return_code,expected_diagnostic", [(0, "EXIT_ZERO"), (1, "EXIT_NONZERO")])
def test_bootstrap_outcome_is_only_a_typed_observation(local_capture, return_code, expected_diagnostic):
    calls = []
    path = run_capture(local_capture.config, pip_runner=fake_pip(return_code, calls))
    contents = read_archive(path.read_bytes())
    _validated_contents, bounded_metrics = validate_snapshot(path.read_bytes())
    assert bounded_metrics["record_coverage_complete"] is True
    manifest = contents.manifest
    assert manifest["protocol"] == "H23_CAPTURE_PROTOCOL_V4"
    bootstrap = manifest["bootstrap"]
    assert bootstrap["executed"] is True
    assert bootstrap["return_code"] == return_code
    assert bootstrap["diagnostic"] == expected_diagnostic
    assert bootstrap["recipe_id"] == "OFFICIAL_NOTEBOOK_CELL_2"
    assert len(calls) == 1
    assert calls[0][1] is False
    assert bootstrap["argv"][:7] == [str(local_capture.config.executable), "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall"]
    filenames = [Path(value).name for value in bootstrap["argv"][7:]]
    assert filenames == ["dependency-2.0-py3-none-any.whl", "swegemma-1.2.3-py3-none-any.whl", "vllm-0.19.1+cu128-py3-none-any.whl"]
    assert bootstrap["wheel_references"] == ["dependency-2.0-py3-none-any.whl", "swegemma-1.2.3-py3-none-any.whl", "vllm-0.19.1cu128-py3-none-any.whl"]
    assert calls[0][2]["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert bootstrap["stdout_byte_count"] == len(b"pip stdout " + SECRET)
    assert bootstrap["stdout_sha256"] == digest(b"pip stdout " + SECRET)
    assert bootstrap["stderr_sha256"] == digest(b"pip stderr password=" + SECRET)
    assert set(bootstrap) == {"recipe_id", "wheel_references", "argv", "executed", "return_code", "stdout_byte_count", "stderr_byte_count", "stdout_sha256", "stderr_sha256", "diagnostic"}
    assert not ({"evidence_strength", "source_status", "fingerprints", "origin", "human_attestations"} & manifest.keys())
    # The original RECORD plus source/resource and console bytes are required.
    distribution = manifest["distributions"][0]
    record_hash = manifest["record_evidence"][0]["blob"]
    derived = derive_distribution_rows(distribution, manifest["interpreter"], contents.blobs[record_hash], manifest["file_observations"], contents.blobs)
    assert derived["record_coverage_complete"] is True
    assert derived["byte_verified_observation_count"] == 4
    assert derived["digest_only_observation_count"] == 6


def test_not_run_is_explicit_and_executes_nothing(local_capture):
    def forbidden(*args, **kwargs):
        raise AssertionError("the disabled bootstrap must not execute")
    path = run_capture(replace(local_capture.config, execute_bootstrap=False), pip_runner=forbidden)
    contents, _metrics = validate_snapshot(path.read_bytes())
    assert contents.manifest["bootstrap"] == {
        "recipe_id": "NOT_RUN", "wheel_references": [], "argv": [], "executed": False,
        "return_code": None, "stdout_byte_count": 0, "stderr_byte_count": 0,
        "stdout_sha256": None, "stderr_sha256": None, "diagnostic": "NOT_RUN",
    }


def test_bootstrap_does_not_dump_or_mutate_parent_environment(local_capture, monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "parent-process-value")
    run_capture(local_capture.config, pip_runner=fake_pip(0, []))
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "parent-process-value"


def test_arbitrary_metadata_direct_urls_and_logs_are_not_serialized(local_capture, monkeypatch):
    monkeypatch.setenv("UNEXPECTED_SECRET_VARIABLE", SECRET.decode("ascii"))
    path = run_capture(local_capture.config, pip_runner=fake_pip(1, []))
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            assert SECRET not in archive.read(name)
        assert all(name == "manifest.json" or name.startswith("blobs/") for name in archive.namelist())
    contents = read_archive(path.read_bytes())
    distribution = contents.manifest["distributions"][0]
    assert distribution["metadata"]["projection"] == {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "1.2.3"}
    assert distribution["direct_url"] == {"present": True, "type": "REGULAR", "sha256": digest(local_capture.files[local_capture.dist + "/direct_url.json"]), "size": len(local_capture.files[local_capture.dist + "/direct_url.json"])}
    assert distribution["metadata"]["raw_sha256"] == digest(local_capture.files[local_capture.dist + "/METADATA"])
    for observation in contents.manifest["file_observations"]:
        if observation["path"].startswith(local_capture.dist + "/") and not observation["path"].endswith("/RECORD"):
            assert observation["payload"] is None


def test_execution_error_records_enum_without_exception_message(local_capture):
    def denied(*args, **kwargs):
        raise OSError(SECRET.decode("ascii"))
    contents = read_archive(run_capture(local_capture.config, pip_runner=denied).read_bytes())
    assert contents.manifest["bootstrap"]["diagnostic"] == "EXECUTION_ERROR"
    assert contents.manifest["bootstrap"]["executed"] is True
    assert contents.manifest["bootstrap"]["return_code"] is None
    assert SECRET not in json.dumps(contents.manifest).encode("utf-8")


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_console_script_reads_reject_links(local_capture, kind):
    script = local_capture.scripts / "SWE-Run"
    raw = script.read_bytes()
    replacement = local_capture.scripts / "other-script"
    replacement.write_bytes(raw)
    script.unlink()
    if kind == "symlink":
        script.symlink_to(replacement)
    else:
        os.link(replacement, script)
    with pytest.raises(PolicyError) as caught:
        run_capture(local_capture.config, pip_runner=fake_pip(0, []))
    assert caught.value.code in ("LOCAL_FILE_TYPE", "LOCAL_FILE_UNAVAILABLE")
    assert not list(local_capture.config.output_directory.iterdir())


def test_unsafe_record_row_rejected_before_its_file_is_read(local_capture, monkeypatch):
    from tools.h23_v4 import capture
    record = local_capture.site / local_capture.dist / "RECORD"
    record.write_bytes(record.read_bytes() + b"../outside/INSTALLER,,\n")
    requested = []
    real_read = capture.read_regular
    def track(descriptor, path, **kwargs):
        requested.append(path)
        return real_read(descriptor, path, **kwargs)
    monkeypatch.setattr(capture, "read_regular", track)
    with pytest.raises(PolicyError) as caught:
        run_capture(local_capture.config, pip_runner=fake_pip(0, []))
    assert caught.value.code == "CONSOLE_PATH"
    assert "../outside/INSTALLER" not in requested


@pytest.mark.parametrize("member", ["other-9.dist-info/METADATA", "swegemma-9.dist-info/METADATA", "other-9.dist-info/WHEEL"])
def test_wheel_internal_dist_info_identity_cannot_contradict_filename(tmp_path, member):
    filename = "swegemma-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(tmp_path / filename, "w") as wheel:
        # A producer-supplied projection cannot disguise a contradictory path.
        wheel.writestr(member, "Metadata-Version: 2.1\nName: swegemma\nVersion: 1.2.3\n\n")
    with anchor_directory(tmp_path) as descriptor:
        with pytest.raises(PolicyError) as caught:
            _wheel_observation(descriptor, filename)
    assert caught.value.code == "WHEEL_DIST_INFO_IDENTITY"


@pytest.mark.parametrize("name,version", [("other", "1.2.3"), ("swegemma", "9")])
def test_wheel_metadata_projection_must_match_filename_and_own_directory(tmp_path, name, version):
    filename = "swegemma-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(tmp_path / filename, "w") as wheel:
        wheel.writestr("swegemma-1.2.3.dist-info/METADATA",
                       f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n")
    with anchor_directory(tmp_path) as descriptor:
        with pytest.raises(PolicyError) as caught:
            _wheel_observation(descriptor, filename)
    assert caught.value.code == "METADATA_IDENTITY"


def test_wheel_invalid_version_rejects_before_open_or_bootstrap(local_capture):
    filename = "swegemma-SECRET-py3-none-any.whl"
    with zipfile.ZipFile(local_capture.config.wheelhouse / filename, "w") as wheel:
        wheel.writestr("swegemma/__init__.py", "never imported")
    calls = []
    with pytest.raises(PolicyError) as caught:
        run_capture(local_capture.config, pip_runner=fake_pip(0, calls))
    assert caught.value.code == "WHEEL_VERSION"
    assert calls == []
    assert not list(local_capture.config.output_directory.iterdir())


def test_wheel_omitted_metadata_still_has_validated_filename_identity(tmp_path):
    filename = "swegemma-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(tmp_path / filename, "w") as wheel:
        wheel.writestr("swegemma/__init__.py", "never imported")
    with anchor_directory(tmp_path) as descriptor:
        observation = _wheel_observation(descriptor, filename)
    assert observation["dataset_filename"] == filename
    assert observation["installation_filename"] == filename
    assert observation["metadata"] is None


def test_capture_cu128_wheel_projection_and_directory_use_restored_identity(tmp_path):
    source = "vllm-0.19.1cu128-py3-none-any.whl"
    write_wheel(tmp_path, source, "vllm", "0.19.1+cu128")
    with anchor_directory(tmp_path) as descriptor:
        observation = _wheel_observation(descriptor, source)
    assert observation["dataset_filename"] == source
    assert observation["installation_filename"] == "vllm-0.19.1+cu128-py3-none-any.whl"
    assert observation["metadata"] == {"Metadata-Version": "2.1", "Name": "vllm", "Version": "0.19.1+cu128"}


@pytest.mark.parametrize("filename,member,metadata_name,expected_name", [
    ("Flask-2.2.5-py3-none-any.whl", "Flask-2.2.5.dist-info/METADATA", "Flask", "flask"),
    ("some_package-2.2.5-py3-none-any.whl", "Some.Package-2.2.5.dist-info/METADATA", "some-package", "some-package"),
])
def test_wheel_distribution_name_normalization_is_consistent(tmp_path, filename, member, metadata_name, expected_name):
    with zipfile.ZipFile(tmp_path / filename, "w") as wheel:
        wheel.writestr(member, f"Metadata-Version: 2.1\nName: {metadata_name}\nVersion: 2.2.5\n\n")
    with anchor_directory(tmp_path) as descriptor:
        observation = _wheel_observation(descriptor, filename)
    assert observation["metadata"] == {"Metadata-Version": "2.1", "Name": expected_name, "Version": "2.2.5"}
