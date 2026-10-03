import hashlib
import json

import pytest

from tools.h23_v4.metadata_policy import (parse_console_scripts, parse_metadata,
                                         validate_metadata, validate_projection,
                                         validate_wheel_observation_identity,
                                         wheel_filename_identity, wheel_installation_filename)
from tools.h23_v4.schema import PolicyError


def test_only_canonical_allowlisted_metadata_is_serialized():
    raw = (b"Metadata-Version: 2.1\nName: Adk_Submission\nVersion: 0.2.12\nRequires-Python: >=3.11\n"
           b"Summary: SECRET-summary\nX-Api-Key: SECRET-key\nProvides-Extra: hidden\n"
           b"Description: SECRET-first\n SECRET-continuation\n\nSECRET-body\n")
    result = parse_metadata(raw, "adk-submission", "0.2.12")
    assert result == {"raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_size": len(raw),
                      "projection": {"Metadata-Version": "2.1", "Name": "adk-submission", "Version": "0.2.12"}}
    assert "SECRET" not in json.dumps(result)
    assert "Provides-Extra" not in json.dumps(result)
    assert "Requires-Python" not in json.dumps(result)


@pytest.mark.parametrize("requires", [">=3.11", ">=3.11.*", "~=3.*", "~=3", ">=3.11\u2003,\u2003<4", "SECRET-free-text"])
def test_raw_requires_python_is_omitted_without_parsing_its_value(requires):
    raw = ("Metadata-Version: 2.1\nName: swegemma\nVersion: 0.2.7\nRequires-Python: " + requires + "\n\n").encode()
    assert parse_metadata(raw)["projection"] == {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "0.2.7"}


@pytest.mark.parametrize("requires", [">=3.11", ">=3.11.*", "~=3.*", "~=3", ">=3.11\u2003,\u2003<4", ">=3.11\n,<4"])
def test_requires_python_projection_is_an_unsupported_field(requires):
    projection = {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "0.2.7", "Requires-Python": requires}
    with pytest.raises(PolicyError) as caught:
        validate_projection(projection)
    assert caught.value.code == "SCHEMA_FIELDS"


@pytest.mark.parametrize("raw,code", [
    (b"Metadata-Version: 2.1\nName: swegemma\nName: swegemma\nVersion: 1\n", "METADATA_DUPLICATE"),
    (b"Metadata-Version: 2.1\nName: swegemma\nname: swegemma\nVersion: 1\n", "METADATA_DUPLICATE"),
    (b"Metadata-Version: 2.1\nName: swegemma\nVersion: 1\nSummary: first\nSummary: second\n", "METADATA_DUPLICATE"),
    ("Metadata-Version: 2.1\nName: swegemma\nVersion: 1+cuİ28\n".encode(), "METADATA_PACKAGE_VERSION"),
    (b"Metadata-Version: 8.1\nName: swegemma\nVersion: 1\n", "METADATA_VERSION_UNSUPPORTED"),
    (b"Metadata-Version: 2.1\nName: other\nVersion: 1\n", "METADATA_IDENTITY"),
    (b"Metadata-Version: 2.1\nName: swegemma\nVersion: 2\n", "METADATA_IDENTITY"),
    (b"Metadata-Version: 2.1\nName: swegemma\nVersion: SECRET-secret\n", "METADATA_PACKAGE_VERSION"),
    (b"Metadata-Version: 2.1\nName: swegemma\nVersion: 1\nRequires-Python: >=3\nrequires-python: arbitrary\n", "METADATA_DUPLICATE"),
    (b"Metadata-Version: 2.1\nName swegemma\nVersion: 1\n", "METADATA_SYNTAX"),
    (b"Metadata-Version: 2.1\nName: swegemma\n secret\nVersion: 1\n", "METADATA_SYNTAX"),
    (b"Metadata-Version: 2.1\nName: swegemma\nVersion: 1\x00\n", "METADATA_SYNTAX"),
    (b"Metadata-Version: 2.1\nName: swegemma\nVersion: 1\xff\n", "METADATA_SYNTAX"),
])
def test_metadata_is_strict(raw, code):
    with pytest.raises(PolicyError) as caught:
        validate_metadata(raw, "swegemma", "1")
    assert caught.value.code == code


@pytest.mark.parametrize("filename,identity", [
    ("swegemma-0.2.7-py3-none-any.whl", ("swegemma", "0.2.7")),
    ("adk_submission-0.2.12-1abc-py2.py3-none-any.whl", ("adk-submission", "0.2.12")),
    ("vllm-0.19.1cu128-cp312-cp312-manylinux_2_28_x86_64.whl", ("vllm", "0.19.1+cu128")),
    ("vllm-0.19.1+cu128-cp312-cp312-manylinux_2_28_x86_64.whl", ("vllm", "0.19.1+cu128")),
])
def test_wheel_identity_has_literal_expected_name_and_effective_version(filename, identity):
    assert wheel_filename_identity(filename) == identity


@pytest.mark.parametrize("filename,code", [
    ("swegemma-SECRET-py3-none-any.whl", "WHEEL_VERSION"),
    ("swegemma-1.0cu128SECRET-py3-none-any.whl", "WHEEL_VERSION"),
    ("swegemma--py3-none-any.whl", "WHEEL_VERSION"),
    ("swegemma-1-abc-py3-none-any.whl", "WHEEL_FILENAME"),
    ("swegemma-1-py3..py2-none-any.whl", "WHEEL_FILENAME"),
    ("swegemma-1-py3-none-.whl", "WHEEL_FILENAME"),
    ("../swegemma-1-py3-none-any.whl", "WHEEL_FILENAME"),
])
def test_wheel_filename_structure_and_version_are_checked_without_metadata(filename, code):
    with pytest.raises(PolicyError) as caught:
        wheel_installation_filename(filename)
    assert caught.value.code == code


def test_supported_cu128_rename_and_projection_identity_are_literal():
    source = "vllm-0.19.1cu128-py3-none-any.whl"
    installed = "vllm-0.19.1+cu128-py3-none-any.whl"
    projection = {"Metadata-Version": "2.1", "Name": "vllm", "Version": "0.19.1+cu128"}
    assert wheel_installation_filename(source) == installed
    assert validate_wheel_observation_identity(source, installed, projection) == ("vllm", "0.19.1+cu128")
    assert validate_wheel_observation_identity(source, installed, None) == ("vllm", "0.19.1+cu128")
    with pytest.raises(PolicyError) as caught:
        validate_wheel_observation_identity(source, source, None)
    assert caught.value.code == "WHEEL_RENAME"


def test_console_parser_preserves_case_without_importing_target():
    assert parse_console_scripts(b"[console_scripts]\nMiXeD = unimportable.module:SomeClass.main\n[other]\nignored = arbitrary-secret\n") == [{"name": "MiXeD", "target": "unimportable.module:SomeClass.main"}]


@pytest.mark.parametrize("raw", [
    b"[console_scripts]\nrun = a:main\nrun = b:main\n",
    b"[console_scripts]\nrun = a:main\nRUN = b:main\n",
    b"[console_scripts]\nrun = a:main()\n",
    b"[console_scripts]\n../run = a:main\n",
    b"[DEFAULT]\nrun = a:main\n[console_scripts]\n",
    b"[gui_scripts]\nrun = a:main\n",
])
def test_console_declarations_reject_ambiguity_or_executable_syntax(raw):
    with pytest.raises(PolicyError):
        parse_console_scripts(raw)
