"""Strict, bounded JSON and manifest shape for the v4 observation protocol."""

import json
import re
from datetime import datetime

PROTOCOL = "H23_CAPTURE_PROTOCOL_V4"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 12
MAX_STRING_LENGTH = 4096
MAX_LIST_LENGTH = 100_000
MAX_JSON_NODES = 500_000
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class PolicyError(ValueError):
    """A bounded reason code, never untrusted diagnostic text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _nonfinite(_value):
    raise PolicyError("JSON_NONFINITE")


def _bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [MAX_JSON_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_JSON_DEPTH:
        raise PolicyError("JSON_LIMIT")
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH or "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise PolicyError("JSON_LIMIT")
    elif isinstance(value, dict):
        if len(value) > MAX_LIST_LENGTH:
            raise PolicyError("JSON_LIMIT")
        for key, item in value.items():
            _bounded(key, depth + 1, budget)
            _bounded(item, depth + 1, budget)
    elif isinstance(value, list):
        if len(value) > MAX_LIST_LENGTH:
            raise PolicyError("JSON_LIMIT")
        for item in value:
            _bounded(item, depth + 1, budget)
    elif value is not None and type(value) not in (int, bool):
        raise PolicyError("JSON_TYPE")


def strict_json(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES:
        raise PolicyError("JSON_LIMIT")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_nonfinite)
    except PolicyError:
        raise
    except ValueError:
        raise PolicyError("JSON_SYNTAX") from None
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, OverflowError):
        raise PolicyError("JSON_SYNTAX") from None
    _bounded(value)
    return value


def exact_fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or not value.keys() <= set(required) | set(optional):
        raise PolicyError("SCHEMA_FIELDS")


def string(value, *, pattern=None):
    if type(value) is not str or not value or len(value) > MAX_STRING_LENGTH or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PolicyError("SCHEMA_STRING")
    if pattern is not None and re.fullmatch(pattern, value) is None:
        raise PolicyError("SCHEMA_STRING")


def sha256(value):
    string(value)
    if SHA256_RE.fullmatch(value) is None:
        raise PolicyError("SCHEMA_SHA256")


def integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyError("SCHEMA_INTEGER")


def boolean(value):
    if type(value) is not bool:
        raise PolicyError("SCHEMA_BOOLEAN")


def sequence(value, maximum=MAX_LIST_LENGTH):
    if not isinstance(value, list) or len(value) > maximum:
        raise PolicyError("SCHEMA_LIST")


def absolute_path(value):
    string(value)
    if not value.startswith("/") or "\\" in value or value.endswith("/") or any(part in ("", ".", "..") for part in value[1:].split("/")):
        raise PolicyError("SCHEMA_PATH")


def _nullable_hash(value):
    if value is not None:
        sha256(value)


def validate_manifest(manifest):
    # Local import avoids a metadata/schema module dependency cycle.
    from tools.h23_v4.metadata_policy import (validate_metadata_observation,
                                             validate_console_scripts,
                                             validate_wheel_observation_identity)

    _bounded(manifest)
    exact_fields(manifest, ("protocol", "captured_utc", "capture_program", "interpreter", "bootstrap", "wheels", "distributions", "record_evidence", "file_observations", "payload_references"))
    if manifest["protocol"] != PROTOCOL:
        raise PolicyError("PROTOCOL_UNSUPPORTED")
    string(manifest["captured_utc"], pattern=r"[0-9]{8}T[0-9]{6}Z")
    try:
        datetime.strptime(manifest["captured_utc"], "%Y%m%dT%H%M%SZ")
    except ValueError:
        raise PolicyError("SCHEMA_TIMESTAMP") from None
    program = manifest["capture_program"]
    exact_fields(program, ("sha256", "notebook_sha256"))
    sha256(program["sha256"])
    _nullable_hash(program["notebook_sha256"])
    interpreter = manifest["interpreter"]
    exact_fields(interpreter, ("python_version", "scheme", "prefix", "site_packages", "scripts_directory", "executable"))
    sequence(interpreter["python_version"], 3)
    if len(interpreter["python_version"]) != 3:
        raise PolicyError("SCHEMA_PYTHON_VERSION")
    for number in interpreter["python_version"]:
        integer(number, 0, 255)
    if interpreter["python_version"][0] != 3 or interpreter["scheme"] not in ("posix_prefix", "posix_venv", "posix_local"):
        raise PolicyError("SCHEMA_INTERPRETER")
    absolute_path(interpreter["prefix"])
    absolute_path(interpreter["scripts_directory"])
    absolute_path(interpreter["executable"])
    sequence(interpreter["site_packages"], 4)
    if not interpreter["site_packages"]:
        raise PolicyError("SCHEMA_INTERPRETER")
    for root in interpreter["site_packages"]:
        absolute_path(root)
    if len(set(interpreter["site_packages"])) != len(interpreter["site_packages"]):
        raise PolicyError("SCHEMA_INTERPRETER")
    bootstrap = manifest["bootstrap"]
    exact_fields(bootstrap, ("recipe_id", "wheel_references", "argv", "executed", "return_code", "stdout_byte_count", "stderr_byte_count", "stdout_sha256", "stderr_sha256", "diagnostic"))
    if bootstrap["recipe_id"] not in ("OFFICIAL_NOTEBOOK_CELL_2", "NOT_RUN") or bootstrap["diagnostic"] not in ("NOT_RUN", "EXIT_ZERO", "EXIT_NONZERO", "EXECUTION_ERROR"):
        raise PolicyError("SCHEMA_BOOTSTRAP")
    sequence(bootstrap["wheel_references"], 256)
    sequence(bootstrap["argv"], 300)
    for value in bootstrap["wheel_references"] + bootstrap["argv"]:
        string(value)
    boolean(bootstrap["executed"])
    if bootstrap["return_code"] is not None:
        integer(bootstrap["return_code"], -2**31, 2**31 - 1)
    for field in ("stdout_byte_count", "stderr_byte_count"):
        integer(bootstrap[field], 0, 2**40)
    for field in ("stdout_sha256", "stderr_sha256"):
        _nullable_hash(bootstrap[field])
    sequence(manifest["wheels"], 256)
    wheel_names = set()
    for wheel in manifest["wheels"]:
        exact_fields(wheel, ("dataset_filename", "installation_filename", "size", "sha256", "metadata"))
        for field in ("dataset_filename", "installation_filename"):
            string(wheel[field], pattern=r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.whl")
        if wheel["dataset_filename"] in wheel_names:
            raise PolicyError("SCHEMA_DUPLICATE_WHEEL")
        wheel_names.add(wheel["dataset_filename"])
        integer(wheel["size"], 1, 16 * 2**30)
        sha256(wheel["sha256"])
        validate_wheel_observation_identity(wheel["dataset_filename"],
                                            wheel["installation_filename"], wheel["metadata"])
    sequence(manifest["distributions"], 8)
    target_names = {"swegemma", "adk-submission", "adk-eval-core", "google-adk", "google-genai", "litellm", "vllm", "transformers"}
    distribution_names = set()
    for distribution in manifest["distributions"]:
        exact_fields(distribution, ("name", "version", "installation_root", "dist_info", "metadata", "direct_url", "console_scripts"))
        name = distribution["name"]
        if type(name) is not str or name not in target_names or name in distribution_names:
            raise PolicyError("SCHEMA_DISTRIBUTION")
        distribution_names.add(name)
        string(distribution["version"])
        absolute_path(distribution["installation_root"])
        string(distribution["dist_info"], pattern=r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.dist-info")
        validate_metadata_observation(distribution["metadata"], name, distribution["version"])
        direct = distribution["direct_url"]
        exact_fields(direct, ("present", "sha256", "size", "type"))
        boolean(direct["present"])
        if direct["present"]:
            if direct["type"] != "REGULAR":
                raise PolicyError("SCHEMA_DIRECT_URL")
            sha256(direct["sha256"])
            integer(direct["size"], 0, 512 * 1024)
        elif direct["type"] != "ABSENT" or direct["sha256"] is not None or direct["size"] is not None:
            raise PolicyError("SCHEMA_DIRECT_URL")
        validate_console_scripts(distribution["console_scripts"])
    sequence(manifest["record_evidence"], 8)
    records = set()
    for record in manifest["record_evidence"]:
        exact_fields(record, ("distribution", "blob"))
        if type(record["distribution"]) is not str or record["distribution"] not in distribution_names or record["distribution"] in records:
            raise PolicyError("SCHEMA_RECORD_REFERENCE")
        records.add(record["distribution"])
        sha256(record["blob"])
    if records != distribution_names:
        raise PolicyError("SCHEMA_RECORD_REFERENCE")
    sequence(manifest["file_observations"])
    observation_keys = set()
    for observation in manifest["file_observations"]:
        exact_fields(observation, ("distribution", "path", "kind", "size", "sha256", "payload", "links"))
        if type(observation["distribution"]) is not str or observation["distribution"] not in distribution_names:
            raise PolicyError("SCHEMA_OBSERVATION")
        string(observation["path"])
        key = (observation["distribution"], observation["path"])
        if key in observation_keys or observation["kind"] != "REGULAR":
            raise PolicyError("SCHEMA_OBSERVATION")
        observation_keys.add(key)
        integer(observation["size"], 0, 2 * 2**30)
        sha256(observation["sha256"])
        _nullable_hash(observation["payload"])
        integer(observation["links"], 1, 2**20)
    sequence(manifest["payload_references"])
    for reference in manifest["payload_references"]:
        sha256(reference)
    if manifest["payload_references"] != sorted(set(manifest["payload_references"])):
        raise PolicyError("SCHEMA_PAYLOAD_REFERENCE")
    return manifest


def canonical_json(value):
    _bounded(value)
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
