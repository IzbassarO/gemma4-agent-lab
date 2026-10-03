import base64
import csv
import hashlib
import io

import pytest

from tools.h23_v4.record_policy import classify_record_rows, derive_distribution_rows, parse_record
from tools.h23_v4.schema import PolicyError


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def _bundle(extra=None, console=None):
    info = "swegemma-0.2.7.dist-info"
    metadata = b"Metadata-Version: 2.1\nName: swegemma\nVersion: 0.2.7\n\n"
    files = {"swegemma/__init__.py": b"VALUE = 1\n", info + "/METADATA": metadata}
    files.update(extra or {})
    declarations = console or []
    if declarations:
        files[info + "/entry_points.txt"] = ("[console_scripts]\n" + "\n".join(item["name"] + "=" + item["target"] for item in declarations) + "\n").encode()
        for item in declarations:
            files["../../../bin/" + item["name"]] = b"#!/python\npass\n"
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for path, data in sorted(files.items()):
        writer.writerow((path, _record_hash(data), str(len(data))))
    writer.writerow((info + "/RECORD", "", ""))
    raw = stream.getvalue().encode()
    files[info + "/RECORD"] = raw
    observations, blobs = [], {}
    for path, data in sorted(files.items()):
        # Independent fixture policy: exactly Python source, script and RECORD.
        include = path == "swegemma/__init__.py" or path.startswith("../../../bin/") or path == info + "/RECORD"
        digest = _digest(data)
        observations.append({"distribution": "swegemma", "path": path, "kind": "REGULAR", "size": len(data), "sha256": digest, "payload": digest if include else None, "links": 1})
        if include:
            blobs[digest] = data
    distribution = {"name": "swegemma", "version": "0.2.7", "installation_root": "/observed/python/lib/python3.12/site-packages", "dist_info": info,
                    "metadata": {"raw_sha256": _digest(metadata), "raw_size": len(metadata), "projection": {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "0.2.7"}},
                    "direct_url": {"present": False, "sha256": None, "size": None, "type": "ABSENT"}, "console_scripts": declarations}
    interpreter = {"python_version": [3, 12, 1], "scheme": "posix_prefix", "prefix": "/observed/python", "site_packages": [distribution["installation_root"]], "scripts_directory": "/observed/python/bin", "executable": "/observed/python/bin/python"}
    return distribution, interpreter, raw, observations, blobs


def _change_record(bundle, transform):
    distribution, interpreter, raw, observations, blobs = bundle
    rows = list(csv.reader(io.StringIO(raw.decode())))
    transform(rows)
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    changed = stream.getvalue().encode()
    for observation in observations:
        if observation["path"] == distribution["dist_info"] + "/RECORD":
            blobs.pop(observation["payload"])
            observation.update(size=len(changed), sha256=_digest(changed), payload=_digest(changed))
            blobs[_digest(changed)] = changed
    return distribution, interpreter, changed, observations, blobs


def test_bounded_coverage_distinguishes_byte_and_digest_verification():
    result = derive_distribution_rows(*_bundle())
    assert result == {"categories": {"swegemma/__init__.py": "owned_regular", "swegemma-0.2.7.dist-info/METADATA": "owned_regular", "swegemma-0.2.7.dist-info/RECORD": "excluded_volatile"}, "record_coverage_complete": True, "byte_verified_observation_count": 2, "digest_only_observation_count": 1}
    assert "complete" not in result


@pytest.mark.parametrize("raw,code", [
    (b"a,b\n", "RECORD_COLUMNS"), (b"a,b,c,d\n", "RECORD_COLUMNS"),
    (b"\n", "RECORD_COLUMNS"), (b'"a,b,c\n', "RECORD_CSV"),
    (b"a\x00,b,c\n", "RECORD_NUL"), (b"\xff,b,c\n", "RECORD_UTF8"),
    (b'a"broken,b,c\n', "RECORD_CSV"), (b'"a"broken,b,c\n', "RECORD_CSV"),
    (("x" * 2049 + ",,\n").encode(), "RECORD_FIELD_LIMIT"),
])
def test_strict_original_record_parser(raw, code):
    with pytest.raises(PolicyError) as caught:
        parse_record(raw)
    assert caught.value.code == code


@pytest.mark.parametrize("path", [
    "/tmp/INSTALLER", "swegemma/../INSTALLER", "swegemma//INSTALLER", "swegemma/./INSTALLER", "C:/INSTALLER", "swegemma\\INSTALLER", "swegemma/control\x1f.py", ".secret/file", "swegemma/__pycache__/orphan.cpython-312.pyc", "swegemma/arbitrary.pyc", "swegemma/arbitrary.pyo", "other_package/file.py", "swegemma_sibling/file.py", "other.dist-info/INSTALLER",
])
def test_path_safety_and_ownership_precede_volatile_category(path):
    distribution, interpreter, raw, _observations, _blobs = _bundle(extra={path: b"observed"})
    with pytest.raises(PolicyError):
        classify_record_rows(distribution, interpreter, raw)


@pytest.mark.parametrize("extra", [
    {"swegemma/__init__.PY": b"collision"},
    {"swegemma/a": b"file", "swegemma/a.b": b"intermediate-sorting", "swegemma/a/b.py": b"nested"},
])
def test_case_and_prefix_collisions_reject(extra):
    distribution, interpreter, raw, _observations, _blobs = _bundle(extra=extra)
    with pytest.raises(PolicyError) as caught:
        classify_record_rows(distribution, interpreter, raw)
    assert caught.value.code == "RECORD_COLLISION"


def test_duplicate_canonical_row_rejects():
    bundle = _change_record(_bundle(), lambda rows: rows.append(list(rows[0])))
    with pytest.raises(PolicyError) as caught:
        derive_distribution_rows(*bundle)
    assert caught.value.code == "RECORD_COLLISION"


def test_supported_cache_is_excluded_only_with_its_owned_source():
    distribution, interpreter, raw, _observations, _blobs = _bundle(extra={"swegemma/__pycache__/__init__.cpython-312.pyc": b"bytecode"})
    categories = {row.path: category for row, category in classify_record_rows(distribution, interpreter, raw)}
    assert categories["swegemma/__pycache__/__init__.cpython-312.pyc"] == "excluded_volatile"


@pytest.mark.parametrize("hash_value", ["md5=" + "A" * 43, "sha256=" + "A" * 43 + "=", "sha256=" + "!" * 43, "sha256=" + "A" * 42 + "B"])
def test_noncanonical_hashes_reject(hash_value):
    bundle = _change_record(_bundle(), lambda rows: rows[0].__setitem__(1, hash_value))
    with pytest.raises(PolicyError) as caught:
        derive_distribution_rows(*bundle)
    assert caught.value.code == "RECORD_HASH"


@pytest.mark.parametrize("size", ["+10", " 10", "1e1", "010", "2147483649"])
def test_size_syntax_is_bounded_ascii_decimal(size):
    bundle = _change_record(_bundle(), lambda rows: rows[0].__setitem__(2, size))
    with pytest.raises(PolicyError) as caught:
        derive_distribution_rows(*bundle)
    assert caught.value.code == "RECORD_SIZE"


def test_actual_payload_and_metadata_observation_relationships():
    bundle = _bundle()
    bundle[0]["metadata"]["raw_sha256"] = "a" * 64
    with pytest.raises(PolicyError) as caught:
        derive_distribution_rows(*bundle)
    assert caught.value.code == "METADATA_OBSERVATION"
    bundle = _bundle()
    source = next(item for item in bundle[3] if item["path"] == "swegemma/__init__.py")
    bundle[4][source["payload"]] = b"different"
    with pytest.raises(PolicyError) as caught:
        derive_distribution_rows(*bundle)
    assert caught.value.code == "PAYLOAD_BYTES"


def test_omitted_source_payload_is_rejected_without_claiming_exhaustive_install():
    bundle = _bundle()
    next(item for item in bundle[3] if item["path"] == "swegemma/__init__.py")["payload"] = None
    with pytest.raises(PolicyError) as caught:
        derive_distribution_rows(*bundle)
    assert caught.value.code == "PAYLOAD_REQUIRED"
