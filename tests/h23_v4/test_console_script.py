import copy

import pytest

from tests.h23_v4.test_record import _bundle, _change_record
from tools.h23_v4.record_policy import classify_record_rows, derive_distribution_rows, validate_unique_console_ownership
from tools.h23_v4.schema import PolicyError


def _script_bundle():
    return _bundle(console=[{"name": "MiXeD", "target": "unimportable.module:main"}])


def test_exact_case_console_bytes_are_independently_verified():
    result = derive_distribution_rows(*_script_bundle())
    assert result["categories"]["../../../bin/MiXeD"] == "verified_console_script"
    assert result["byte_verified_observation_count"] == 3
    assert result["digest_only_observation_count"] == 2


@pytest.mark.parametrize("path", ["../../../bin/mixed", "../../bin/MiXeD", "../../../../bin/MiXeD", "../../../bin/nested/MiXeD", "../../../sbin/MiXeD"])
def test_external_exception_is_exact_not_a_traversal_exemption(path):
    def change(rows):
        next(row for row in rows if row[0] == "../../../bin/MiXeD")[0] = path
    bundle = _change_record(_script_bundle(), change)
    with pytest.raises(PolicyError) as caught:
        classify_record_rows(*bundle[:3])
    assert caught.value.code == "CONSOLE_PATH"


def test_contradictory_scripts_directory_rejects():
    bundle = _script_bundle()
    bundle[1]["scripts_directory"] = "/other/bin"
    with pytest.raises(PolicyError) as caught:
        classify_record_rows(*bundle[:3])
    assert caught.value.code == "INTERPRETER_SCHEME"


def test_duplicate_declarations_reject():
    bundle = _script_bundle()
    bundle[0]["console_scripts"].append(dict(bundle[0]["console_scripts"][0]))
    with pytest.raises(PolicyError) as caught:
        classify_record_rows(*bundle[:3])
    assert caught.value.code == "CONSOLE_DECLARATION_DUPLICATE"


@pytest.mark.parametrize("field,value,code", [("kind", "SYMLINK", "OBSERVATION_REGULAR"), ("links", 2, "CONSOLE_HARDLINK"), ("size", 999, "RECORD_SIZE_MISMATCH"), ("sha256", "a" * 64, "RECORD_HASH_MISMATCH"), ("payload", None, "PAYLOAD_REQUIRED")])
def test_console_regular_unique_hash_size_and_payload_are_all_required(field, value, code):
    bundle = _script_bundle()
    next(item for item in bundle[3] if item["path"] == "../../../bin/MiXeD")[field] = value
    with pytest.raises(PolicyError) as caught:
        derive_distribution_rows(*bundle)
    assert caught.value.code == code


def test_cross_distribution_script_ownership_is_unique():
    distribution, interpreter, *_rest = _script_bundle()
    other = copy.deepcopy(distribution)
    other.update(name="adk-submission", version="0.2.12", dist_info="adk_submission-0.2.12.dist-info")
    with pytest.raises(PolicyError) as caught:
        validate_unique_console_ownership([distribution, other], interpreter)
    assert caught.value.code == "CONSOLE_OWNERSHIP"
