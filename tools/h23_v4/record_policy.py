"""Bounded RECORD policy; categories are derived after safety and ownership."""

import base64
import csv
from dataclasses import dataclass
import hashlib
import io
import posixpath
import re

from tools.h23_v4.metadata_policy import validate_console_scripts
from tools.h23_v4.schema import PolicyError

MAX_RECORD_BYTES = 20 * 1024 * 1024
MAX_RECORD_ROWS = 100_000
MAX_RECORD_FIELD = 2048
MAX_OBSERVED_SIZE = 2 * 2**30
OWNED_PREFIXES = {
    "swegemma": ("swegemma",), "adk-submission": ("adk_submission",),
    "adk-eval-core": ("adk_eval_core",), "google-adk": ("google", "adk"),
    "google-genai": ("google", "genai"), "litellm": ("litellm",),
    "vllm": ("vllm",), "transformers": ("transformers",),
}
HARNESS_DISTRIBUTIONS = frozenset(("swegemma", "adk-submission", "adk-eval-core"))
SOURCE_SUFFIXES = frozenset((".py", ".pyi", ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".cfg", ".ini", ".typed", ".j2", ".jinja"))
_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\Z")
_HASH = re.compile(r"sha256=([A-Za-z0-9_-]{43})\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,9})\Z")


@dataclass(frozen=True)
class RecordRow:
    path: str
    hash: str
    size: str


def parse_record(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_RECORD_BYTES:
        raise PolicyError("RECORD_LIMIT")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PolicyError("RECORD_UTF8") from None
    if "\x00" in text:
        raise PolicyError("RECORD_NUL")
    _validate_csv_quoting(text)
    rows = []
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', strict=True)
        for fields in reader:
            if len(fields) != 3:
                raise PolicyError("RECORD_COLUMNS")
            if any(len(field) > MAX_RECORD_FIELD for field in fields):
                raise PolicyError("RECORD_FIELD_LIMIT")
            rows.append(RecordRow(*fields))
            if len(rows) > MAX_RECORD_ROWS:
                raise PolicyError("RECORD_ROW_LIMIT")
    except csv.Error:
        raise PolicyError("RECORD_CSV") from None
    if not rows:
        raise PolicyError("RECORD_COLUMNS")
    return rows


def _validate_csv_quoting(text):
    # Python's strict reader still accepts quote characters in unquoted fields.
    state = "start"
    for index, char in enumerate(text):
        if char == "\r" and (index + 1 == len(text) or text[index + 1] != "\n"):
            raise PolicyError("RECORD_CSV")
        if state == "quoted":
            if char == '"':
                state = "after_quote"
        elif state == "after_quote":
            if char == '"':
                state = "quoted"
            elif char == "," or char in "\r\n":
                state = "start"
            else:
                raise PolicyError("RECORD_CSV")
        elif char == '"':
            if state != "start":
                raise PolicyError("RECORD_CSV")
            state = "quoted"
        elif char == "," or char in "\r\n":
            state = "start"
        else:
            state = "unquoted"
    if state == "quoted":
        raise PolicyError("RECORD_CSV")


def canonical_components(path):
    if type(path) is not str or not path or len(path) > MAX_RECORD_FIELD or path.startswith("/") or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise PolicyError("RECORD_PATH")
    parts = tuple(path.split("/"))
    if any(part in ("", ".", "..") or _COMPONENT.fullmatch(part) is None for part in parts):
        raise PolicyError("RECORD_PATH")
    return parts


def decode_record_hash(value):
    match = _HASH.fullmatch(value)
    if match is None:
        raise PolicyError("RECORD_HASH")
    try:
        decoded = base64.b64decode(match[1] + "=", altchars=b"-_", validate=True)
    except ValueError:
        raise PolicyError("RECORD_HASH") from None
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != match[1]:
        raise PolicyError("RECORD_HASH")
    return decoded.hex()


def decode_record_size(value):
    if _DECIMAL.fullmatch(value) is None:
        raise PolicyError("RECORD_SIZE")
    size = int(value)
    if size > MAX_OBSERVED_SIZE:
        raise PolicyError("RECORD_SIZE")
    return size


def validate_interpreter_roots(interpreter):
    """Each scheme's own sysconfig templates, anchored at the observed prefix.

    posix_local is Debian's default scheme: purelib/platlib are
    {base}/local/lib/python{X.Y}/dist-packages and scripts are {base}/local/bin,
    while the interpreter stays in {base}/bin or is invoked through that scripts
    directory. No absolute path is trusted; a neighbouring root never matches.
    """
    prefix = interpreter["prefix"]
    major, minor, _micro = interpreter["python_version"]
    version = f"python{major}.{minor}"
    if interpreter["scheme"] in ("posix_prefix", "posix_venv"):
        expected = {f"{prefix}/{lib}/{version}/{suffix}" for lib in ("lib", "lib64") for suffix in ("site-packages", "dist-packages")}
        scripts = prefix + "/bin"
        executables = {scripts}
    elif interpreter["scheme"] == "posix_local":
        expected = {f"{prefix}/local/lib/{version}/dist-packages"}
        scripts = prefix + "/local/bin"
        executables = {prefix + "/bin", scripts}
    else:
        raise PolicyError("INTERPRETER_SCHEME")
    if interpreter["scripts_directory"] != scripts or not interpreter["site_packages"] or not set(interpreter["site_packages"]) <= expected:
        raise PolicyError("INTERPRETER_SCHEME")
    if posixpath.dirname(interpreter["executable"]) not in executables:
        raise PolicyError("INTERPRETER_SCHEME")


def _owned(parts, distribution):
    prefix = OWNED_PREFIXES[distribution["name"]]
    return parts[:len(prefix)] == prefix and len(parts) > len(prefix) or parts[0] == distribution["dist_info"] and len(parts) > 1


def console_script_path(distribution, interpreter, basename):
    validate_interpreter_roots(interpreter)
    relative = posixpath.relpath(interpreter["scripts_directory"] + "/" + basename, distribution["installation_root"])
    if re.fullmatch(r"(?:\.\./)+bin/[A-Za-z0-9_][A-Za-z0-9_.-]*", relative) is None:
        raise PolicyError("CONSOLE_PATH")
    return relative


def _check_collision(paths):
    folded = {path.casefold() for path in paths}
    if len(folded) != len(paths):
        raise PolicyError("RECORD_COLLISION")
    for path in folded:
        parts = path.split("/")
        if any("/".join(parts[:index]) in folded for index in range(1, len(parts))):
            raise PolicyError("RECORD_COLLISION")


def classify_record_rows(distribution, interpreter, record_bytes):
    """Classify without reading any RECORD-supplied filesystem path."""
    validate_interpreter_roots(interpreter)
    name = distribution["name"]
    if name not in OWNED_PREFIXES or distribution["installation_root"] not in interpreter["site_packages"] or distribution["dist_info"] != name.replace("-", "_") + "-" + distribution["version"] + ".dist-info":
        raise PolicyError("DISTRIBUTION_ROOT")
    validate_console_scripts(distribution["console_scripts"])
    declarations = {item["name"]: item["target"] for item in distribution["console_scripts"]}
    rows = parse_record(record_bytes)
    paths = [row.path for row in rows]
    path_set = set(paths)
    _check_collision(paths)
    own_record = distribution["dist_info"] + "/RECORD"
    own_metadata = distribution["dist_info"] + "/METADATA"
    if paths.count(own_record) != 1 or paths.count(own_metadata) != 1:
        raise PolicyError("RECORD_REQUIRED")
    classified = []
    script_names = set()
    for row in rows:
        if row.path.startswith("../"):
            basename = row.path.rsplit("/", 1)[-1]
            if basename not in declarations or row.path != console_script_path(distribution, interpreter, basename):
                raise PolicyError("CONSOLE_PATH")
            script_names.add(basename)
            category = "verified_console_script"
        else:
            parts = canonical_components(row.path)
            if not _owned(parts, distribution):
                raise PolicyError("RECORD_OWNERSHIP")
            category = "owned_regular"
            if row.path == own_record:
                if row.hash != "" or row.size != "":
                    raise PolicyError("RECORD_SELF")
                category = "excluded_volatile"
            elif len(parts) == 2 and parts[0] == distribution["dist_info"] and parts[1] in ("INSTALLER", "REQUESTED", "direct_url.json"):
                category = "excluded_volatile"
            elif "__pycache__" in parts or row.path.endswith((".pyc", ".pyo")):
                major, minor, _micro = interpreter["python_version"]
                cache = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\.cpython-" + str(major) + str(minor) + r"(?:\.opt-[12])?\.pyc", parts[-1])
                source = "/".join(parts[:-2] + ((cache[1] + ".py",) if cache else ()))
                package_prefix = OWNED_PREFIXES[name]
                if len(parts) < len(package_prefix) + 2 or parts[-2] != "__pycache__" or "__pycache__" in parts[:-2] or cache is None or source not in path_set or parts[:len(package_prefix)] != package_prefix:
                    raise PolicyError("RECORD_BYTECODE")
                category = "excluded_volatile"
        if category in ("owned_regular", "verified_console_script"):
            decode_record_hash(row.hash)
            decode_record_size(row.size)
        elif row.path != own_record:
            if bool(row.hash) != bool(row.size):
                raise PolicyError("RECORD_HASH_SIZE_PAIR")
            if row.hash:
                decode_record_hash(row.hash)
                decode_record_size(row.size)
        classified.append((row, category))
    if script_names != set(declarations):
        raise PolicyError("CONSOLE_RECORD_COVERAGE")
    if declarations and distribution["dist_info"] + "/entry_points.txt" not in paths:
        raise PolicyError("CONSOLE_ENTRY_POINTS")
    return classified


def payload_required(distribution, path, category):
    if category == "verified_console_script" or path == distribution["dist_info"] + "/RECORD":
        return True
    parts = tuple(path.split("/"))
    prefix = OWNED_PREFIXES[distribution["name"]]
    return distribution["name"] in HARNESS_DISTRIBUTIONS and parts[:len(prefix)] == prefix and category == "owned_regular" and posixpath.splitext(path)[1] in SOURCE_SUFFIXES


def derive_distribution_rows(distribution, interpreter, record_bytes, observation_list, blobs):
    classified = classify_record_rows(distribution, interpreter, record_bytes)
    observations = {}
    for observation in observation_list:
        if observation["distribution"] != distribution["name"]:
            raise PolicyError("OBSERVATION_DISTRIBUTION")
        if observation["path"] in observations:
            raise PolicyError("OBSERVATION_DUPLICATE")
        observations[observation["path"]] = observation
    if set(observations) != {row.path for row, _category in classified}:
        raise PolicyError("OBSERVATION_RECORD_COVERAGE")
    byte_verified, digest_only = 0, 0
    categories = {}
    for row, category in classified:
        observation = observations[row.path]
        if observation["kind"] != "REGULAR" or type(observation["size"]) is not int or not 0 <= observation["size"] <= MAX_OBSERVED_SIZE or type(observation["links"]) is not int or observation["links"] < 1 or re.fullmatch(r"[0-9a-f]{64}", observation["sha256"]) is None:
            raise PolicyError("OBSERVATION_REGULAR")
        if row.hash and decode_record_hash(row.hash) != observation["sha256"]:
            raise PolicyError("RECORD_HASH_MISMATCH")
        if row.size and decode_record_size(row.size) != observation["size"]:
            raise PolicyError("RECORD_SIZE_MISMATCH")
        if category == "verified_console_script" and observation["links"] != 1:
            raise PolicyError("CONSOLE_HARDLINK")
        payload = observation["payload"]
        required = payload_required(distribution, row.path, category)
        if required and payload is None:
            raise PolicyError("PAYLOAD_REQUIRED")
        if row.path.startswith(distribution["dist_info"] + "/") and row.path != distribution["dist_info"] + "/RECORD" and payload is not None:
            raise PolicyError("METADATA_PAYLOAD_FORBIDDEN")
        if category == "excluded_volatile" and row.path != distribution["dist_info"] + "/RECORD" and payload is not None:
            raise PolicyError("VOLATILE_PAYLOAD_FORBIDDEN")
        if payload is not None:
            if payload != observation["sha256"] or payload not in blobs:
                raise PolicyError("PAYLOAD_REFERENCE")
            data = blobs[payload]
            if hashlib.sha256(data).hexdigest() != payload or len(data) != observation["size"]:
                raise PolicyError("PAYLOAD_BYTES")
            byte_verified += 1
        else:
            digest_only += 1
        if row.path == distribution["dist_info"] + "/RECORD" and (payload != hashlib.sha256(record_bytes).hexdigest() or blobs[payload] != record_bytes):
            raise PolicyError("RECORD_PAYLOAD")
        if row.path == distribution["dist_info"] + "/METADATA" and (observation["sha256"] != distribution["metadata"]["raw_sha256"] or observation["size"] != distribution["metadata"]["raw_size"]):
            raise PolicyError("METADATA_OBSERVATION")
        if row.path == distribution["dist_info"] + "/direct_url.json" and (not distribution["direct_url"]["present"] or observation["sha256"] != distribution["direct_url"]["sha256"] or observation["size"] != distribution["direct_url"]["size"]):
            raise PolicyError("DIRECT_URL_OBSERVATION")
        categories[row.path] = category
    has_direct_url = distribution["dist_info"] + "/direct_url.json" in observations
    if has_direct_url != distribution["direct_url"]["present"]:
        raise PolicyError("DIRECT_URL_OBSERVATION")
    return {"categories": categories, "record_coverage_complete": True,
            "byte_verified_observation_count": byte_verified,
            "digest_only_observation_count": digest_only}


def validate_unique_console_ownership(distributions, interpreter):
    owners = set()
    for distribution in distributions:
        validate_console_scripts(distribution["console_scripts"])
        for declaration in distribution["console_scripts"]:
            identity = (interpreter["scripts_directory"], declaration["name"].casefold())
            if identity in owners:
                raise PolicyError("CONSOLE_OWNERSHIP")
            owners.add(identity)
