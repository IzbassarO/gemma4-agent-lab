"""Literal protocol-envelope expectations, independent of producer constants."""

import hashlib
import io
import json
import stat
import warnings
import zipfile

import pytest

from tools.h23_v4.archive import build_archive, read_archive
from tools.h23_v4.schema import PolicyError


def envelope(members):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in members:
                archive.writestr(name, data)
    return output.getvalue()


def manifest(references=()):
    return json.dumps({"payload_references": list(references)}, separators=(",", ":")).encode()


def rejected(raw, code):
    with pytest.raises(PolicyError) as raised:
        read_archive(raw)
    assert raised.value.code == code


def test_blob_is_rehashed_from_exact_snapshot():
    payload = b"SOURCE = 42\n"
    digest = hashlib.sha256(payload).hexdigest()
    raw = envelope([("manifest.json", manifest([digest])), ("blobs/" + digest, payload)])
    result = read_archive(raw)
    assert result.blobs == {digest: payload}
    assert result.archive_sha256 == hashlib.sha256(raw).hexdigest()


def test_builder_deterministic_and_regular_nonexecutable_members():
    content = b"payload"
    digest = hashlib.sha256(content).hexdigest()
    data = {"payload_references": [digest]}
    first = build_archive(data, {digest: content})
    assert build_archive(data, {digest: content}) == first
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["manifest.json", "blobs/" + digest]
        assert all(stat.S_IMODE(info.external_attr >> 16) == 0o600 for info in archive.infolist())


@pytest.mark.parametrize("raw", [b"", b"not a zip", b"PK\x03\x04", b"PK\x05\x06" + b"\0" * 18])
def test_malformed_or_empty_archive(raw):
    with pytest.raises(PolicyError):
        read_archive(raw)


def test_duplicate_zip_member():
    rejected(envelope([("manifest.json", manifest()), ("manifest.json", manifest())]),
             "ARCHIVE_DUPLICATE_MEMBER")


def test_nul_in_raw_zip_member_name_cannot_hide_suffix():
    raw = envelope([("manifest.jsonXYZ", manifest())])
    raw = raw.replace(b"manifest.jsonXYZ", b"manifest.json\x00YZ")
    rejected(raw, "ARCHIVE_MEMBER_NAME")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "blobs/../escape", "blobs/ABC", "blobs/" + "A" * 64,
                                 "blobs\\" + "a" * 64, "README.md", "sources/module.py", "blobs/"])
def test_extra_or_unsafe_members(name):
    rejected(envelope([("manifest.json", manifest()), (name, b"x")]), "ARCHIVE_MEMBER_NAME")


def test_symlink_member_rejected():
    info = zipfile.ZipInfo("blobs/" + "a" * 64)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    rejected(envelope([("manifest.json", manifest()), (info, b"../outside")]), "ARCHIVE_MEMBER_TYPE")


def test_wrong_blob_hash():
    rejected(envelope([("manifest.json", manifest(["a" * 64])), ("blobs/" + "a" * 64, b"x")]), "BLOB_HASH")


def test_missing_blob():
    rejected(envelope([("manifest.json", manifest(["a" * 64]))]), "BLOB_MISSING")


def test_orphan_blob():
    digest = hashlib.sha256(b"x").hexdigest()
    rejected(envelope([("manifest.json", manifest()), ("blobs/" + digest, b"x")]), "BLOB_ORPHAN")


def test_duplicate_logical_reference():
    rejected(envelope([("manifest.json", manifest(["a" * 64, "a" * 64]))]), "PAYLOAD_REFERENCES")


def test_duplicate_manifest_json_key():
    rejected(envelope([("manifest.json", b'{"payload_references":[],"payload_references":[]}')]),
             "JSON_DUPLICATE_KEY")


def test_archive_bytes_limit_checked_before_zip_parse(monkeypatch):
    import tools.h23_v4.archive as module
    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", 8)
    rejected(b"012345678", "ARCHIVE_SIZE")


def test_expansion_limit_checked_before_decompression(monkeypatch):
    import tools.h23_v4.archive as module
    payload = b"x" * 1000
    digest = hashlib.sha256(payload).hexdigest()
    raw = envelope([("manifest.json", manifest([digest])), ("blobs/" + digest, payload)])
    monkeypatch.setattr(module, "MAX_EXPANDED_BYTES", 128)
    rejected(raw, "ARCHIVE_EXPANDED_SIZE")


def test_member_count_limit(monkeypatch):
    import tools.h23_v4.archive as module
    raw = envelope([("manifest.json", manifest()), ("README.md", b"x")])
    monkeypatch.setattr(module, "MAX_MEMBERS", 1)
    rejected(raw, "ARCHIVE_MEMBERS")


def test_member_size_limit(monkeypatch):
    import tools.h23_v4.archive as module
    payload = b"abc"
    digest = hashlib.sha256(payload).hexdigest()
    raw = envelope([("manifest.json", manifest([digest])), ("blobs/" + digest, payload)])
    monkeypatch.setattr(module, "MAX_MEMBER_BYTES", 2)
    rejected(raw, "ARCHIVE_MEMBER_SIZE")


def test_unsupported_compression_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr("manifest.json", manifest())
    rejected(output.getvalue(), "ARCHIVE_COMPRESSION")
