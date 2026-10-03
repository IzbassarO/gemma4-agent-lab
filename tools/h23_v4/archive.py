"""Bounded v4 ZIP parsing: one manifest and content-addressed payload bytes."""

from dataclasses import dataclass
import hashlib
import io
import json
import re
import stat
import struct
import zipfile
import zlib

from .schema import MAX_MANIFEST_BYTES, PolicyError, strict_json

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_PAYLOADS = 8192
MAX_MEMBERS = MAX_PAYLOADS + 1
_BLOB = re.compile(r"blobs/([0-9a-f]{64})\Z")
_BLOB_BYTES = re.compile(rb"blobs/[0-9a-f]{64}\Z")
_EOCD = struct.Struct("<4s4H2LH")
_CENTRAL = struct.Struct("<4s6H3L5H2L")
_LOCAL = struct.Struct("<4s5H3L2H")


@dataclass(frozen=True)
class ArchiveContents:
    manifest: dict
    blobs: dict
    archive_sha256: str


def _references(manifest):
    if not isinstance(manifest, dict):
        raise PolicyError("SCHEMA_FIELDS")
    references = manifest.get("payload_references")
    if not isinstance(references, list) or len(references) > MAX_PAYLOADS:
        raise PolicyError("PAYLOAD_REFERENCES")
    if any(type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None for item in references):
        raise PolicyError("PAYLOAD_REFERENCES")
    if references != sorted(set(references)):
        raise PolicyError("PAYLOAD_REFERENCES")
    return set(references)


def _preflight_archive(snapshot):
    """Check the closed non-ZIP64 envelope before ZipFile allocates members.

    The only supported topology is contiguous local headers/data, an equally
    ordered contiguous central directory, and a final 22-byte, comment-free
    EOCD. The walk is capped by the independently enforced member limit. Raw
    local headers are checked separately: ZipInfo exposes only central extras.
    """
    if len(snapshot) < _EOCD.size:
        raise PolicyError("ARCHIVE_ENVELOPE")
    end_offset = len(snapshot) - _EOCD.size
    (signature, disk, central_disk, disk_count, count, central_size,
     central_offset, comment_size) = _EOCD.unpack_from(snapshot, end_offset)
    if (signature != b"PK\x05\x06" or comment_size or disk or central_disk
            or disk_count != count):
        raise PolicyError("ARCHIVE_ENVELOPE")
    if not 1 <= count <= MAX_MEMBERS:
        raise PolicyError("ARCHIVE_MEMBERS")
    if (central_offset + central_size != end_offset
            or central_size < count * _CENTRAL.size):
        raise PolicyError("ARCHIVE_ENVELOPE")

    cursor, next_local, expanded = central_offset, 0, 0
    names = set()
    for _ in range(count):
        if cursor + _CENTRAL.size > end_offset:
            raise PolicyError("ARCHIVE_ENVELOPE")
        (signature, _created, needed, flags, method, time, date, crc,
         compressed_size, size, name_size, extra_size, member_comment_size,
         member_disk, _internal, external, local_offset) = _CENTRAL.unpack_from(snapshot, cursor)
        if (signature != b"PK\x01\x02" or extra_size or member_comment_size
                or member_disk):
            raise PolicyError("ARCHIVE_ENVELOPE")
        if name_size not in (13, 70):
            raise PolicyError("ARCHIVE_MEMBER_NAME")
        name_start = cursor + _CENTRAL.size
        cursor = name_start + name_size
        if cursor > end_offset:
            raise PolicyError("ARCHIVE_ENVELOPE")
        name = snapshot[name_start:cursor]
        if name != b"manifest.json" and _BLOB_BYTES.fullmatch(name) is None:
            raise PolicyError("ARCHIVE_MEMBER_NAME")
        if name in names:
            raise PolicyError("ARCHIVE_DUPLICATE_MEMBER")
        names.add(name)
        if stat.S_IFMT(external >> 16) not in (0, stat.S_IFREG):
            raise PolicyError("ARCHIVE_MEMBER_TYPE")
        if flags & 1 or method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PolicyError("ARCHIVE_COMPRESSION")
        # ASCII names need no flags. Descriptors, strong encryption, ZIP64 and
        # other extraction profiles have no representation in this contract.
        if flags or needed not in (10, 20) or (method == zipfile.ZIP_DEFLATED and needed != 20):
            raise PolicyError("ARCHIVE_ENVELOPE")
        limit = MAX_MANIFEST_BYTES if name == b"manifest.json" else MAX_MEMBER_BYTES
        if size > limit or compressed_size > MAX_ARCHIVE_BYTES:
            raise PolicyError("ARCHIVE_MEMBER_SIZE")
        if method == zipfile.ZIP_STORED and compressed_size != size:
            raise PolicyError("ARCHIVE_ENVELOPE")
        expanded += size
        if expanded > MAX_EXPANDED_BYTES:
            raise PolicyError("ARCHIVE_EXPANDED_SIZE")

        if local_offset != next_local or local_offset + _LOCAL.size > central_offset:
            raise PolicyError("ARCHIVE_ENVELOPE")
        (local_signature, local_needed, local_flags, local_method, local_time,
         local_date, local_crc, local_compressed, local_size,
         local_name_size, local_extra_size) = _LOCAL.unpack_from(snapshot, local_offset)
        if (local_signature != b"PK\x03\x04" or local_extra_size
                or (local_needed, local_flags, local_method, local_time, local_date,
                    local_crc, local_compressed, local_size, local_name_size)
                != (needed, flags, method, time, date, crc, compressed_size, size, name_size)):
            raise PolicyError("ARCHIVE_ENVELOPE")
        local_name_start = local_offset + _LOCAL.size
        payload_start = local_name_start + name_size
        next_local = payload_start + compressed_size
        if next_local > central_offset or snapshot[local_name_start:payload_start] != name:
            raise PolicyError("ARCHIVE_ENVELOPE")

    if cursor != end_offset or next_local != central_offset:
        raise PolicyError("ARCHIVE_ENVELOPE")
    if b"manifest.json" not in names:
        raise PolicyError("ARCHIVE_MANIFEST")
    return count


def _member_bytes(snapshot, member, limit):
    """Decode exactly the preflighted compressed extent, including its end.

    ZipExtFile can stop at the reported file size and ignore unused deflate
    bytes or a missing stream terminator. Those bytes have no V4 payload role.
    A raw decoder provides a bounded independent check of the entire extent.
    """
    start = member.header_offset + _LOCAL.size + len(member.filename.encode("ascii"))
    end = start + member.compress_size
    if member.compress_type == zipfile.ZIP_STORED:
        data = snapshot[start:end]
    else:
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        data = decoder.decompress(memoryview(snapshot)[start:end], limit + 1)
        if len(data) > limit:
            raise PolicyError("ARCHIVE_MEMBER_SIZE")
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise PolicyError("ARCHIVE_ENVELOPE")
    if len(data) > limit or len(data) != member.file_size:
        raise PolicyError("ARCHIVE_MEMBER_SIZE")
    if zlib.crc32(data) != member.CRC:
        raise PolicyError("ARCHIVE_MALFORMED")
    return data


def read_archive(snapshot):
    """Hash and parse precisely the same immutable, bounded byte snapshot.

    This enforces the archive envelope. The importer separately validates the
    manifest's typed observations and the relationships between them.
    """
    if type(snapshot) is not bytes or len(snapshot) > MAX_ARCHIVE_BYTES:
        raise PolicyError("ARCHIVE_SIZE")
    count = _preflight_archive(snapshot)
    try:
        with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
            members = archive.infolist()
            if len(members) != count or not 1 <= len(members) <= MAX_MEMBERS:
                raise PolicyError("ARCHIVE_MEMBERS")
            names = [member.filename for member in members]
            if len(set(names)) != len(names):
                raise PolicyError("ARCHIVE_DUPLICATE_MEMBER")
            if names.count("manifest.json") != 1:
                raise PolicyError("ARCHIVE_MANIFEST")
            expanded = 0
            for member in members:
                if member.orig_filename != member.filename:
                    raise PolicyError("ARCHIVE_MEMBER_NAME")
                if member.filename != "manifest.json" and _BLOB.fullmatch(member.filename) is None:
                    raise PolicyError("ARCHIVE_MEMBER_NAME")
                kind = stat.S_IFMT(member.external_attr >> 16)
                if member.is_dir() or kind not in (0, stat.S_IFREG):
                    raise PolicyError("ARCHIVE_MEMBER_TYPE")
                if member.flag_bits & 1 or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise PolicyError("ARCHIVE_COMPRESSION")
                limit = MAX_MANIFEST_BYTES if member.filename == "manifest.json" else MAX_MEMBER_BYTES
                if member.file_size > limit or member.compress_size > MAX_ARCHIVE_BYTES:
                    raise PolicyError("ARCHIVE_MEMBER_SIZE")
                expanded += member.file_size
                if expanded > MAX_EXPANDED_BYTES:
                    raise PolicyError("ARCHIVE_EXPANDED_SIZE")
            raw_members = {}
            for member in members:
                limit = MAX_MANIFEST_BYTES if member.filename == "manifest.json" else MAX_MEMBER_BYTES
                raw_members[member.filename] = _member_bytes(snapshot, member, limit)
    except PolicyError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError, EOFError, ValueError,
            NotImplementedError, zlib.error):
        raise PolicyError("ARCHIVE_MALFORMED") from None
    manifest = strict_json(raw_members.pop("manifest.json"))
    references = _references(manifest)
    blobs = {}
    for name, data in raw_members.items():
        digest = name[6:]
        if hashlib.sha256(data).hexdigest() != digest:
            raise PolicyError("BLOB_HASH")
        blobs[digest] = data
    if references - blobs.keys():
        raise PolicyError("BLOB_MISSING")
    if blobs.keys() - references:
        raise PolicyError("BLOB_ORPHAN")
    return ArchiveContents(manifest, blobs, hashlib.sha256(snapshot).hexdigest())


parse_archive = read_archive


def _member(name, data):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.flag_bits = 0
    info.extra = b""
    info.comment = b""
    info.internal_attr = 0
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info, data


def build_archive(manifest, blobs):
    """Create deterministic envelope bytes, rejecting invalid blob commitments."""
    if not isinstance(blobs, dict):
        raise PolicyError("PAYLOAD_REFERENCES")
    try:
        raw_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                  allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise PolicyError("JSON_TYPE") from None
    if len(blobs) > MAX_PAYLOADS:
        raise PolicyError("ARCHIVE_MEMBERS")
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise PolicyError("ARCHIVE_MEMBER_SIZE")
    total = len(raw_manifest)
    for digest, data in blobs.items():
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None or type(data) is not bytes:
            raise PolicyError("BLOB_HASH")
        if len(data) > MAX_MEMBER_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise PolicyError("BLOB_HASH")
        total += len(data)
        if total > MAX_EXPANDED_BYTES:
            raise PolicyError("ARCHIVE_EXPANDED_SIZE")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=False) as archive:
        archive.comment = b""
        archive.writestr(*_member("manifest.json", raw_manifest), compresslevel=9)
        for digest, data in sorted(blobs.items()):
            archive.writestr(*_member("blobs/" + digest, data), compresslevel=9)
            if output.tell() > MAX_ARCHIVE_BYTES:
                raise PolicyError("ARCHIVE_SIZE")
    result = output.getvalue()
    read_archive(result)
    return result
