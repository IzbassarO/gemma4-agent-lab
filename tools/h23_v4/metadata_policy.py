"""Metadata is projected, never redacted or copied as arbitrary free text."""

import hashlib
import configparser
import re

from tools.h23_v4.schema import PolicyError, exact_fields, integer, sha256, string

MAX_METADATA_BYTES = 512 * 1024
SUPPORTED_METADATA_VERSIONS = frozenset(("1.0", "1.1", "1.2", "2.1", "2.2", "2.3", "2.4"))
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z")
# Intentionally bounded ASCII packaging profile, including wheel local versions.
_VERSION = re.compile(r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?\Z", re.I | re.ASCII)
_HEADER = re.compile(r"([A-Za-z][A-Za-z0-9-]*):[ \t]*(.*)\Z")
_PROJECTION_FIELDS = frozenset(("metadata-version", "name", "version"))
_SINGLETONS = _PROJECTION_FIELDS | frozenset(("requires-python", "summary", "description", "description-content-type", "keywords", "home-page", "download-url", "author", "author-email", "maintainer", "maintainer-email", "license", "license-expression"))


def canonical_name(name):
    if type(name) is not str or len(name) > 128 or _NAME.fullmatch(name) is None:
        raise PolicyError("METADATA_NAME")
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_projection(projection, expected_name=None, expected_version=None):
    exact_fields(projection, ("Metadata-Version", "Name", "Version"))
    for value in projection.values():
        string(value)
    if projection["Metadata-Version"] not in SUPPORTED_METADATA_VERSIONS:
        raise PolicyError("METADATA_VERSION_UNSUPPORTED")
    name = canonical_name(projection["Name"])
    if projection["Name"] != name:
        raise PolicyError("METADATA_NAME_NONCANONICAL")
    version = projection["Version"]
    if len(version) > 128 or _VERSION.fullmatch(version) is None:
        raise PolicyError("METADATA_PACKAGE_VERSION")
    if expected_name is not None and name != canonical_name(expected_name):
        raise PolicyError("METADATA_IDENTITY")
    if expected_version is not None and version != expected_version:
        raise PolicyError("METADATA_IDENTITY")
    return projection


def _wheel_parts(filename):
    if type(filename) is not str or len(filename) > 4096 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.whl", filename) is None:
        raise PolicyError("WHEEL_FILENAME")
    parts = filename[:-4].split("-")
    if len(parts) not in (5, 6) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.]*", parts[0]) is None:
        raise PolicyError("WHEEL_FILENAME")
    if len(parts) == 6 and re.fullmatch(r"[0-9][A-Za-z0-9_]*", parts[2]) is None:
        raise PolicyError("WHEEL_FILENAME")
    if any(re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", part) is None for part in parts[-3:]):
        raise PolicyError("WHEEL_FILENAME")
    return parts


def _wheel_version(version):
    # The official wheelhouse strips '+' before the fixed cu128 local suffix.
    if "+" not in version and version.endswith("cu128"):
        version = version[:-5] + "+cu128"
    if len(version) > 128 or _VERSION.fullmatch(version) is None:
        raise PolicyError("WHEEL_VERSION")
    return version


def wheel_filename_identity(filename):
    """Return bounded filename identity, including the supported cu128 alias."""
    parts = _wheel_parts(filename)
    return canonical_name(parts[0]), _wheel_version(parts[1])


def wheel_installation_filename(dataset_filename):
    """Apply the sole supported wheelhouse rename and validate its identity."""
    parts = _wheel_parts(dataset_filename)
    wheel_filename_identity(dataset_filename)
    parts[1] = _wheel_version(parts[1])
    return "-".join(parts) + ".whl"


def validate_wheel_observation_identity(dataset_filename, installation_filename, projection):
    """Check wheel commitments internally, without an installed-package claim."""
    expected_filename = wheel_installation_filename(dataset_filename)
    name, version = wheel_filename_identity(installation_filename)
    if installation_filename != expected_filename:
        raise PolicyError("WHEEL_RENAME")
    if projection is not None:
        validate_projection(projection, name, version)
    return name, version


def validate_wheel_dist_info(directory, expected_name, expected_version):
    """Compare internal and filename identity using the same name normalization."""
    if type(directory) is not str or not directory.endswith(".dist-info"):
        raise PolicyError("WHEEL_DIST_INFO_IDENTITY")
    parts = directory[:-10].rsplit("-", 1)
    if len(parts) != 2 or len(parts[1]) > 128 or _VERSION.fullmatch(parts[1]) is None:
        raise PolicyError("WHEEL_DIST_INFO_IDENTITY")
    try:
        name = canonical_name(parts[0])
    except PolicyError:
        raise PolicyError("WHEEL_DIST_INFO_IDENTITY") from None
    if name != canonical_name(expected_name) or parts[1] != expected_version:
        raise PolicyError("WHEEL_DIST_INFO_IDENTITY")
    return directory


def validate_metadata_observation(observation, expected_name=None, expected_version=None):
    exact_fields(observation, ("raw_sha256", "raw_size", "projection"))
    sha256(observation["raw_sha256"])
    integer(observation["raw_size"], 1, MAX_METADATA_BYTES)
    validate_projection(observation["projection"], expected_name, expected_version)
    return observation


def parse_metadata(raw, expected_name=None, expected_version=None):
    if type(raw) is not bytes or not raw or len(raw) > MAX_METADATA_BYTES:
        raise PolicyError("METADATA_LIMIT")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PolicyError("METADATA_SYNTAX") from None
    if "\x00" in text or "\r" in text.replace("\r\n", ""):
        raise PolicyError("METADATA_SYNTAX")
    values = {}
    seen_singletons = set()
    previous = None
    for line in text.replace("\r\n", "\n").split("\n"):
        if line == "":
            break  # Description body is not metadata projection evidence.
        if len(line) > 8192 or any(ord(c) < 32 and c != "\t" for c in line):
            raise PolicyError("METADATA_SYNTAX")
        if line[:1] in (" ", "\t"):
            if previous is None or previous in _PROJECTION_FIELDS:
                raise PolicyError("METADATA_SYNTAX")
            continue  # Valid continuation for an omitted field.
        match = _HEADER.fullmatch(line)
        if match is None:
            raise PolicyError("METADATA_SYNTAX")
        key, value = match.groups()
        previous = key.lower()
        if previous in _SINGLETONS:
            if previous in seen_singletons:
                raise PolicyError("METADATA_DUPLICATE")
            seen_singletons.add(previous)
        if previous in _PROJECTION_FIELDS:
            values[previous] = value
    if not {"metadata-version", "name", "version"} <= values.keys():
        raise PolicyError("METADATA_REQUIRED")
    projection = {"Metadata-Version": values["metadata-version"],
                  "Name": canonical_name(values["name"]), "Version": values["version"]}
    validate_projection(projection, expected_name, expected_version)
    return {"raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_size": len(raw), "projection": projection}


def validate_metadata(raw, expected_name=None, expected_version=None):
    return parse_metadata(raw, expected_name, expected_version)["projection"]


def validate_console_scripts(declarations):
    if not isinstance(declarations, list) or len(declarations) > 128:
        raise PolicyError("CONSOLE_DECLARATION_LIMIT")
    seen = set()
    for declaration in declarations:
        exact_fields(declaration, ("name", "target"))
        name, target = declaration["name"], declaration["target"]
        if type(name) is not str or len(name) > 128 or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name) is None:
            raise PolicyError("CONSOLE_DECLARATION")
        if name.casefold() in seen:
            raise PolicyError("CONSOLE_DECLARATION_DUPLICATE")
        seen.add(name.casefold())
        if type(target) is not str or len(target) > 256 or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", target) is None:
            raise PolicyError("CONSOLE_TARGET")
    return declarations


def parse_console_scripts(raw):
    if type(raw) is not bytes or len(raw) > MAX_METADATA_BYTES:
        raise PolicyError("ENTRY_POINTS_LIMIT")
    try:
        text = raw.decode("utf-8")
        if "\x00" in text:
            raise PolicyError("ENTRY_POINTS_SYNTAX")
        parser = configparser.ConfigParser(interpolation=None, strict=True, delimiters=("=",), empty_lines_in_values=False)
        parser.optionxform = str
        parser.read_string(text)
        if parser.defaults():
            raise PolicyError("ENTRY_POINTS_SYNTAX")
        if parser.has_section("gui_scripts") and parser.items("gui_scripts"):
            raise PolicyError("GUI_SCRIPTS_UNSUPPORTED")
        declarations = [{"name": name, "target": target.strip()} for name, target in parser.items("console_scripts")] if parser.has_section("console_scripts") else []
    except (UnicodeDecodeError, configparser.Error):
        raise PolicyError("ENTRY_POINTS_SYNTAX") from None
    validate_console_scripts(declarations)
    return sorted(declarations, key=lambda entry: entry["name"])
