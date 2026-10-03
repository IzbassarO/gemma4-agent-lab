import copy
import json

import pytest

from tools.h23_v4.schema import PolicyError, canonical_json, strict_json, validate_manifest


def _manifest():
    return {"protocol": "H23_CAPTURE_PROTOCOL_V4", "captured_utc": "20261002T120000Z",
            "capture_program": {"sha256": "a" * 64, "notebook_sha256": None},
            "interpreter": {"python_version": [3, 12, 1], "scheme": "posix_prefix", "prefix": "/observed/python", "site_packages": ["/observed/python/lib/python3.12/site-packages"], "scripts_directory": "/observed/python/bin", "executable": "/observed/python/bin/python"},
            "bootstrap": {"recipe_id": "NOT_RUN", "wheel_references": [], "argv": [], "executed": False, "return_code": None, "stdout_byte_count": 0, "stderr_byte_count": 0, "stdout_sha256": None, "stderr_sha256": None, "diagnostic": "NOT_RUN"},
            "wheels": [], "distributions": [], "record_evidence": [], "file_observations": [], "payload_references": []}


def test_literal_v4_manifest_and_deterministic_serialization():
    manifest = _manifest()
    assert validate_manifest(strict_json(canonical_json(manifest))) == manifest
    assert canonical_json(manifest) == canonical_json(dict(reversed(list(manifest.items()))))
    assert b"H23_CAPTURE_PROTOCOL_V4" in canonical_json(manifest)


@pytest.mark.parametrize("raw,code", [
    (b'{"protocol":"old","protocol":"new"}', "JSON_DUPLICATE_KEY"),
    (b'{"a":{"same":1,"same":2}}', "JSON_DUPLICATE_KEY"),
    (b'{"number":NaN}', "JSON_NONFINITE"), (b'{"number":Infinity}', "JSON_NONFINITE"),
    (b'{"number":-Infinity}', "JSON_NONFINITE"), (b'{"number":1.5}', "JSON_TYPE"),
    (b'{"number":1e309}', "JSON_TYPE"), (b'{"invalid":"\xff"}', "JSON_SYNTAX"),
    (b'{"a":', "JSON_SYNTAX"),
    (json.dumps({"oversize": "x" * 4097}).encode(), "JSON_LIMIT"),
    (("[" * 20 + "0" + "]" * 20).encode(), "JSON_LIMIT"),
    (b'{"string":"\\ud800"}', "JSON_LIMIT"),
    (("{\"integer\":" + "1" * 5000 + "}").encode(), "JSON_SYNTAX"),
])
def test_strict_json_adversarial_literals(raw, code):
    with pytest.raises(PolicyError) as caught:
        strict_json(raw)
    assert caught.value.code == code


@pytest.mark.parametrize("mutate,code", [
    (lambda value: value.update(protocol=3), "PROTOCOL_UNSUPPORTED"),
    (lambda value: value.update(protocol="H23_CAPTURE_PROTOCOL_V3"), "PROTOCOL_UNSUPPORTED"),
    (lambda value: value.update(captured_utc="20260231T120000Z"), "SCHEMA_TIMESTAMP"),
    (lambda value: value.update(evidence_strength="VERSION-SPECIFIC"), "SCHEMA_FIELDS"),
    (lambda value: value["bootstrap"].update(return_code=False), "SCHEMA_INTEGER"),
    (lambda value: value["bootstrap"].update(executed=1), "SCHEMA_BOOLEAN"),
    (lambda value: value["bootstrap"].update(stdout="secret-log"), "SCHEMA_FIELDS"),
    (lambda value: value["interpreter"].update(environment={"PASSWORD": "secret"}), "SCHEMA_FIELDS"),
    (lambda value: value["interpreter"].update(site_packages=[[]]), "SCHEMA_STRING"),
    (lambda value: value["interpreter"].update(prefix="relative/root"), "SCHEMA_PATH"),
    (lambda value: value["interpreter"].update(prefix="/observed/../python"), "SCHEMA_PATH"),
    (lambda value: value.update(payload_references=["b" * 64, "a" * 64]), "SCHEMA_PAYLOAD_REFERENCE"),
    (lambda value: value.update(payload_references=["a" * 64, "a" * 64]), "SCHEMA_PAYLOAD_REFERENCE"),
])
def test_manifest_rejects_legacy_claims_unknown_fields_and_wrong_types(mutate, code):
    manifest = _manifest()
    mutate(manifest)
    with pytest.raises(PolicyError) as caught:
        validate_manifest(manifest)
    assert caught.value.code == code


def test_unknown_nested_wheel_field_rejects():
    manifest = _manifest()
    manifest["wheels"] = [{"dataset_filename": "x-1-py3-none-any.whl", "installation_filename": "x-1-py3-none-any.whl", "size": 100, "sha256": "a" * 64, "metadata": None, "official": True}]
    with pytest.raises(PolicyError) as caught:
        validate_manifest(manifest)
    assert caught.value.code == "SCHEMA_FIELDS"


def test_opaque_metadata_cannot_smuggle_extra_text_or_url_fields():
    from tests.h23_v4.test_record import _bundle
    distribution, _interpreter, raw, observations, blobs = _bundle()
    manifest = _manifest()
    manifest.update(distributions=[distribution], file_observations=observations,
                    record_evidence=[{"distribution": "swegemma", "blob": next(item["payload"] for item in observations if item["path"].endswith("/RECORD"))}], payload_references=sorted(blobs))
    assert validate_manifest(manifest) == manifest
    changed = copy.deepcopy(manifest)
    changed["distributions"][0]["direct_url"]["url"] = "https://user:SECRET@example.org/wheel"
    with pytest.raises(PolicyError) as caught:
        validate_manifest(changed)
    assert caught.value.code == "SCHEMA_FIELDS"
    changed = copy.deepcopy(manifest)
    changed["distributions"][0]["metadata"]["projection"]["Summary"] = "SECRET"
    with pytest.raises(PolicyError) as caught:
        validate_manifest(changed)
    assert caught.value.code == "SCHEMA_FIELDS"


@pytest.mark.parametrize("projection_field", ["Requires-Python", "Summary"])
def test_wheel_projection_rejects_all_unsupported_fields(projection_field):
    manifest = _manifest()
    manifest["wheels"] = [{"dataset_filename": "swegemma-1-py3-none-any.whl", "installation_filename": "swegemma-1-py3-none-any.whl",
                          "size": 100, "sha256": "a" * 64,
                          "metadata": {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "1", projection_field: "unsupported"}}]
    with pytest.raises(PolicyError) as caught:
        validate_manifest(manifest)
    assert caught.value.code == "SCHEMA_FIELDS"


@pytest.mark.parametrize("metadata", [None, {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "1"}])
def test_wheel_version_is_checked_independently_of_metadata_presence(metadata):
    manifest = _manifest()
    manifest["wheels"] = [{"dataset_filename": "swegemma-SECRET-py3-none-any.whl", "installation_filename": "swegemma-SECRET-py3-none-any.whl",
                          "size": 100, "sha256": "a" * 64, "metadata": metadata}]
    with pytest.raises(PolicyError) as caught:
        validate_manifest(manifest)
    assert caught.value.code == "WHEEL_VERSION"


@pytest.mark.parametrize("field,value,code", [("Name", "other", "METADATA_IDENTITY"), ("Version", "9", "METADATA_IDENTITY"), ("Version", None, "SCHEMA_STRING")])
def test_wheel_projection_identity_agrees_with_filename(field, value, code):
    manifest = _manifest()
    metadata = {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "1"}
    metadata[field] = value
    manifest["wheels"] = [{"dataset_filename": "swegemma-1-py3-none-any.whl", "installation_filename": "swegemma-1-py3-none-any.whl",
                          "size": 100, "sha256": "a" * 64, "metadata": metadata}]
    with pytest.raises(PolicyError) as caught:
        validate_manifest(manifest)
    assert caught.value.code == code


def test_distribution_projection_rejects_requires_python():
    from tests.h23_v4.fixtures import fixture
    manifest, _blobs = fixture()
    manifest["distributions"][0]["metadata"]["projection"]["Requires-Python"] = ">=3.11.*"
    with pytest.raises(PolicyError) as caught:
        validate_manifest(manifest)
    assert caught.value.code == "SCHEMA_FIELDS"
