"""Literal raw ZIP fixtures: acceptance never comes from the production builder."""

import hashlib
import io
import json
import struct
import zipfile
import zlib

import pytest

from tools.h23_v4 import archive as archive_module
from tools.h23_v4 import importer
from tools.h23_v4.schema import PolicyError


SECRET = b"SYNTHETIC_ENVELOPE_SECRET: api-key=CANARY-ONLY"


def literal_zip(members, *, archive_comment=b"", member_comment=b"", central_extra=b"",
                local_extra=b"", prefix=b"", trailer=b"", local_gap=b"",
                central_gap=b"", descriptor=False, method=0, compressed_payload=None):
    """Write classic ZIP wire bytes without ZipFile or production policy constants."""
    local_records, central_records = [], []
    offset = len(prefix)
    for index, (name, data) in enumerate(members):
        encoded = name.encode("ascii")
        checksum = zipfile.crc32(data)
        flags = 8 if descriptor else 0
        extra = local_extra if index == 0 else b""
        encoded_payload = raw_deflate(data) if method == 8 else data
        if index == 0 and compressed_payload is not None:
            encoded_payload = compressed_payload
        sizes = (0, 0, 0) if descriptor else (checksum, len(encoded_payload), len(data))
        local = (struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, flags, method, 0, 33,
                             *sizes, len(encoded), len(extra)) + encoded + extra + encoded_payload)
        if descriptor:
            local += struct.pack("<4s3L", b"PK\x07\x08", checksum, len(encoded_payload), len(data))
        central = (struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 0x0314, 20, flags, method, 0, 33,
                               checksum, len(encoded_payload), len(data), len(encoded),
                               len(central_extra) if index == 0 else 0,
                               len(member_comment) if index == 0 else 0,
                               0, 0, 0x81800000, offset)
                   + encoded + (central_extra + member_comment if index == 0 else b""))
        local_records.append(local)
        central_records.append(central)
        offset += len(local)
    locals_raw = prefix + b"".join(local_records) + local_gap
    directory = b"".join(central_records)
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, len(members), len(members),
                      len(directory), len(locals_raw), len(archive_comment))
    return locals_raw + directory + central_gap + end + archive_comment + trailer


def raw_deflate(data):
    encoder = zlib.compressobj(wbits=-15)
    return encoder.compress(data) + encoder.flush()


def minimal_manifest():
    return {"protocol": "H23_CAPTURE_PROTOCOL_V4", "captured_utc": "20261002T120000Z",
            "capture_program": {"sha256": "a" * 64, "notebook_sha256": None},
            "interpreter": {"python_version": [3, 12, 1], "scheme": "posix_prefix",
                            "prefix": "/observed/python",
                            "site_packages": ["/observed/python/lib/python3.12/site-packages"],
                            "scripts_directory": "/observed/python/bin",
                            "executable": "/observed/python/bin/python"},
            "bootstrap": {"recipe_id": "NOT_RUN", "wheel_references": [], "argv": [],
                          "executed": False, "return_code": None, "stdout_byte_count": 0,
                          "stderr_byte_count": 0, "stdout_sha256": None,
                          "stderr_sha256": None, "diagnostic": "NOT_RUN"},
            "wheels": [], "distributions": [], "record_evidence": [],
            "file_observations": [], "payload_references": []}


def baseline():
    return literal_zip([("manifest.json", b'{"payload_references":[]}')])


def changed_field(raw, record, offset, value, field_format="<H"):
    data = bytearray(raw)
    if record == "local":
        start = 0
    elif record == "central":
        start = struct.unpack_from("<L", raw, len(raw) - 6)[0]
    else:
        start = len(raw) - 22
    struct.pack_into(field_format, data, start + offset, value)
    return bytes(data)


def reject_before_zipfile(raw, monkeypatch, code="ARCHIVE_ENVELOPE"):
    def forbidden(*_args, **_kwargs):
        pytest.fail("ZipFile materialized an archive rejected by raw preflight")
    monkeypatch.setattr(archive_module.zipfile, "ZipFile", forbidden)
    with pytest.raises(PolicyError) as caught:
        archive_module.read_archive(raw)
    assert caught.value.code == code


def test_literal_canonical_stored_envelope_is_accepted():
    raw = baseline()
    contents = archive_module.read_archive(raw)
    assert contents.manifest == {"payload_references": []}
    assert contents.blobs == {}
    assert contents.archive_sha256 == hashlib.sha256(raw).hexdigest()


def test_literal_single_complete_deflate_stream_is_accepted():
    raw = literal_zip([("manifest.json", b'{"payload_references":[]}')], method=8)
    assert archive_module.read_archive(raw).manifest == {"payload_references": []}


@pytest.mark.parametrize("channel,options", [
    ("archive_comment", {"archive_comment": SECRET}),
    ("member_comment", {"member_comment": SECRET}),
    ("central_extra", {"central_extra": struct.pack("<HH", 0xcafe, len(SECRET)) + SECRET}),
    ("local_only_extra", {"local_extra": struct.pack("<HH", 0xcafe, len(SECRET)) + SECRET}),
    ("preamble", {"prefix": SECRET}),
    ("trailer", {"trailer": SECRET}),
    ("local_gap", {"local_gap": SECRET}),
    ("central_gap", {"central_gap": SECRET}),
    ("disagreeing_extras", {"local_extra": SECRET, "central_extra": b"different"}),
])
def test_undeclared_secret_channels_reject_before_parse_and_publication(tmp_path, monkeypatch,
                                                                      channel, options):
    raw = literal_zip([("manifest.json", json.dumps(minimal_manifest()).encode())], **options)
    assert SECRET in raw
    temporary = tmp_path.resolve()
    dataset = temporary / "dataset-standin"
    dataset.mkdir()
    evidence = temporary / "evidence"
    evidence.mkdir(mode=0o700)
    path = temporary / (channel + ".zip")
    path.write_bytes(raw)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Rejected secret envelope reached parsing or publication")
    monkeypatch.setattr(archive_module.zipfile, "ZipFile", forbidden)
    monkeypatch.setattr(importer, "publish_evidence", forbidden)
    result = importer.import_capture(path, harness_root=str(evidence), dataset_root=str(dataset))
    assert result.result == "CAPTURE_REJECTED"
    assert result.reason_codes == ("ARCHIVE_ENVELOPE",)
    assert result.evidence_path is None
    assert not list(evidence.iterdir())
    assert SECRET.decode() not in json.dumps(result.as_dict())


@pytest.mark.parametrize("attack", ["unused_padding", "concatenated_stream", "truncated_stream",
                                    "underreported_expanded_size"])
def test_deflate_extent_must_be_one_complete_stream_before_publication(tmp_path, monkeypatch, attack):
    payload = json.dumps(minimal_manifest()).encode()
    compressed = raw_deflate(payload)
    if attack == "unused_padding":
        compressed += SECRET
    elif attack == "concatenated_stream":
        compressed += raw_deflate(SECRET)
    elif attack == "truncated_stream":
        compressed = compressed[:-1]
    else:
        compressed = raw_deflate(payload + SECRET)
    raw = literal_zip([("manifest.json", payload)], method=8, compressed_payload=compressed)
    temporary = tmp_path.resolve()
    dataset = temporary / "dataset-standin"
    dataset.mkdir()
    evidence = temporary / "evidence"
    evidence.mkdir(mode=0o700)
    path = temporary / (attack + ".zip")
    path.write_bytes(raw)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid compressed extent reached evidence publication")
    monkeypatch.setattr(importer, "publish_evidence", forbidden)
    result = importer.import_capture(path, harness_root=str(evidence), dataset_root=str(dataset))
    assert result.result == "CAPTURE_REJECTED"
    expected = "ARCHIVE_MEMBER_SIZE" if attack == "underreported_expanded_size" else "ARCHIVE_ENVELOPE"
    assert result.reason_codes == (expected,)
    assert result.evidence_path is None
    assert not list(evidence.iterdir())
    assert SECRET.decode() not in json.dumps(result.as_dict())


def test_actual_deflate_output_is_bounded_even_when_header_underreports_size():
    compressed = raw_deflate(b"x" * (8 * 1024 * 1024 + 1))
    raw = literal_zip([("manifest.json", b"{}")], method=8, compressed_payload=compressed)
    with pytest.raises(PolicyError) as caught:
        archive_module.read_archive(raw)
    assert caught.value.code == "ARCHIVE_MEMBER_SIZE"


@pytest.mark.parametrize("method", [0, 8])
def test_stored_and_deflated_crc_are_verified_independently(method):
    raw = literal_zip([("manifest.json", b'{"payload_references":[]}')], method=method)
    raw = changed_field(raw, "central", 16, 0, "<L")
    raw = changed_field(raw, "local", 14, 0, "<L")
    with pytest.raises(PolicyError) as caught:
        archive_module.read_archive(raw)
    assert caught.value.code == "ARCHIVE_MALFORMED"


@pytest.mark.parametrize("record,offset,value,field_format", [
    ("eocd", 0, 0, "<L"),  # EOCD signature
    ("eocd", 4, 1, "<H"),  # multiple disks
    ("eocd", 6, 1, "<H"),
    ("eocd", 8, 2, "<H"),  # per-disk count contradicts total
    ("eocd", 12, 45, "<L"),  # central size misses bytes
    ("eocd", 16, 0, "<L"),  # central offset contradicts local region
    ("eocd", 16, 0xffffffff, "<L"),  # ZIP64 offset sentinel / out of bounds
    ("eocd", 12, 0xffffffff, "<L"),
    ("eocd", 20, 1, "<H"),  # declared comment without appended bytes
    ("central", 0, 0, "<L"),
    ("central", 6, 45, "<H"),  # ZIP64 extraction profile
    ("central", 8, 8, "<H"),  # descriptor flag
    ("central", 8, 64, "<H"),  # strong encryption flag
    ("central", 8, 2048, "<H"),  # unsupported flag with ASCII names
    ("central", 34, 1, "<H"),  # member starts on a different disk
    ("central", 42, 1, "<L"),  # preamble/local pointer disagreement
    ("local", 0, 0, "<L"),
    ("local", 4, 10, "<H"),  # version needed contradicts central
    ("local", 6, 1, "<H"),
    ("local", 8, 8, "<H"),  # compression method contradicts central
    ("local", 10, 1, "<H"),  # time contradiction
    ("local", 12, 34, "<H"),  # date contradiction
    ("local", 14, 1, "<L"),  # CRC contradiction
    ("local", 18, 1, "<L"),  # compressed size contradiction
    ("local", 22, 1, "<L"),  # expanded size contradiction
    ("local", 26, 12, "<H"),  # filename length contradiction
    ("local", 28, 1, "<H"),  # local-only declared extra
])
def test_raw_header_and_eocd_facts_are_checked_before_zipfile(monkeypatch, record, offset,
                                                           value, field_format):
    reject_before_zipfile(changed_field(baseline(), record, offset, value, field_format), monkeypatch)


def test_local_filename_bytes_must_equal_central_filename(monkeypatch):
    raw = bytearray(baseline())
    raw[30:43] = b"manifeso.json"
    reject_before_zipfile(bytes(raw), monkeypatch)


def test_stored_compressed_and_expanded_sizes_must_agree(monkeypatch):
    raw = changed_field(baseline(), "central", 20, 1, "<L")
    raw = changed_field(raw, "local", 18, 1, "<L")
    reject_before_zipfile(raw, monkeypatch)


def test_actual_data_descriptor_is_unsupported(monkeypatch):
    raw = literal_zip([("manifest.json", b'{"payload_references":[]}')], descriptor=True)
    assert b"PK\x07\x08" in raw
    reject_before_zipfile(raw, monkeypatch)


@pytest.mark.parametrize("marker", [
    b"PK\x06\x06" + b"\0" * 52,  # ZIP64 EOCD
    b"PK\x06\x07" + b"\0" * 16,  # ZIP64 locator
    b"PK\x05\x05" + b"\0\0",  # archive digital signature
])
def test_unsupported_zip_end_records_are_not_hidden_in_a_gap(monkeypatch, marker):
    reject_before_zipfile(literal_zip([("manifest.json", b'{"payload_references":[]}')],
                                     central_gap=marker), monkeypatch)


@pytest.mark.parametrize("disk_count,total_count", [(65535, 65535), (0, 0)])
def test_zip64_count_sentinel_and_empty_profile_reject_before_materialization(monkeypatch,
                                                                           disk_count, total_count):
    raw = changed_field(baseline(), "eocd", 8, disk_count)
    raw = changed_field(raw, "eocd", 10, total_count)
    reject_before_zipfile(raw, monkeypatch, "ARCHIVE_MEMBERS")


@pytest.mark.parametrize("flags", [1, 9])
def test_encrypted_profile_rejects_before_materialization(monkeypatch, flags):
    raw = changed_field(baseline(), "central", 8, flags)
    raw = changed_field(raw, "local", 6, flags)
    reject_before_zipfile(raw, monkeypatch, "ARCHIVE_COMPRESSION")


def member_count_fixture(count):
    blobs = {hashlib.sha256(str(index).encode()).hexdigest(): str(index).encode()
             for index in range(count - 1)}
    manifest = json.dumps({"payload_references": sorted(blobs)}, separators=(",", ":")).encode()
    return literal_zip([("manifest.json", manifest)] + [("blobs/" + name, data)
                                                       for name, data in sorted(blobs.items())])


@pytest.mark.parametrize("count", [8192, 8193, 8194])
def test_actual_member_count_limit_minus_exact_plus_one(monkeypatch, count):
    raw = member_count_fixture(count)
    if count == 8194:
        reject_before_zipfile(raw, monkeypatch, "ARCHIVE_MEMBERS")
    else:
        result = archive_module.read_archive(raw)
        assert len(result.blobs) == count - 1


def test_underreported_count_cannot_hide_actual_members(monkeypatch):
    raw = member_count_fixture(8194)
    raw = changed_field(raw, "eocd", 8, 8193)
    raw = changed_field(raw, "eocd", 10, 8193)
    reject_before_zipfile(raw, monkeypatch)


def test_high_actual_central_directory_is_rejected_without_zipfile_materialization(monkeypatch):
    raw = baseline()
    central_offset = struct.unpack_from("<L", raw, len(raw) - 6)[0]
    entry = raw[central_offset:-22]
    directory = entry * 100000
    # The classic count saturates at 65535; 100000 entries fit in a 5.9 MB
    # snapshot. No ZipFile object may be allocated for this hostile directory.
    hostile = directory + struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 65535, 65535,
                                      len(directory), 0, 0)
    assert len(hostile) < 128 * 1024 * 1024
    reject_before_zipfile(hostile, monkeypatch, "ARCHIVE_MEMBERS")


def test_builder_emits_explicit_supported_structure_and_deterministic_settings():
    payload = b"literal payload"
    digest = hashlib.sha256(payload).hexdigest()
    raw = archive_module.build_archive({"payload_references": [digest]}, {digest: payload})
    assert raw == archive_module.build_archive({"payload_references": [digest]}, {digest: payload})
    assert raw[:4] == b"PK\x03\x04"
    assert raw[-22:-18] == b"PK\x05\x06"
    assert struct.unpack_from("<H", raw, len(raw) - 2)[0] == 0
    with zipfile.ZipFile(io.BytesIO(raw)) as parsed:
        assert parsed.comment == b""
        assert parsed.namelist() == ["manifest.json", "blobs/" + digest]
        for info in parsed.infolist():
            assert info.comment == info.extra == b""
            assert info.flag_bits == 0
            assert info.extract_version == info.create_version == 20
            assert info.create_system == 3
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == 8
            assert info.external_attr == 0x81800000
            assert struct.unpack_from("<H", raw, info.header_offset + 28)[0] == 0
    assert archive_module.read_archive(raw).blobs == {digest: payload}
