"""Closed real-runtime provenance and secret-free evidence; no private intake.

Workers copy admitted source identities and verify staged records. They never
query the coordinator's Git directory, private dataset or parent environment.
"""
from __future__ import annotations

from importlib import metadata
import ipaddress
import platform
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from ._contracts import ContractError, canonical_json, closed_dict, hex_digest, text
from tools.common import tree_sha256
from tools.h23_v4.filesystem import anchor_directory, read_regular
from tools.h23_v4.schema import PolicyError

PACKAGES = ("swegemma", "adk-submission", "adk-eval-core", "google-adk", "google-genai", "litellm", "vllm")
IDENTITY_FIELDS = ("eval_infra_source_identity", "candidate_identity", "preregistration_identity",
                   "model_endpoint_identity")
FINGERPRINT_FIELDS = ("schema_version", "phase", *IDENTITY_FIELDS, "platform", "python_version",
                      "package_versions", "public_identities", "model_server_identity", "harness_lock_sha256",
                      "confinement")
_SECRET_KEY = re.compile(
    r"(?:^|[_\- ])(?:api[_\- ]?key|authorization|proxy[_\- ]?authorization|password|passwd|"
    r"secret|credential|access[_\- ]?token|refresh[_\- ]?token|bearer[_\- ]?token|"
    r"auth[_\- ]?token|cookie|set[_\- ]?cookie|environment|environ|env)(?:$|[_\- ])", re.I,
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:\bbearer\s+\S+|\bbasic\s+[A-Za-z0-9+/=]{8,}|"
    r"\b(?:api[_-]?key|authorization|password|passwd|access[_-]?token|refresh[_-]?token|secret)"
    r"(?:\\?[\"'])?\s*[=:]\s*(?:\\\"[^\"\r\n]*\\\"|"
    r"\"[^\"\r\n]*\"|'[^'\r\n]*'|(?:bearer|basic)\s+[^\s,;\"']+|[^\s,;\"']+)|"
    r"--(?:api[_-]?key|authorization|password|access[_-]?token)\s+[^\s]+|"
    r"\bsk-[A-Za-z0-9_-]{8,}|\b(?:ghp|github_pat|hf)_[A-Za-z0-9_]{10,}|SYNTHETIC_DUMMY)"
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_URL_SECRET_KEYS = frozenset(("api_key", "apikey", "key", "token", "access_token", "password", "passwd",
                              "secret", "authorization", "auth", "signature", "sig"))
REDACTED = "[REDACTED]"


def normalize_endpoint(endpoint: str) -> str:
    """Admit only explicit loopback HTTP(S), without auth/query/fragment state."""
    text(endpoint, "model endpoint", nonempty=True)
    if any(ord(char) <= 32 or ord(char) == 127 for char in endpoint) or "\\" in endpoint:
        raise ContractError("model endpoint is not a canonical loopback URL")
    try:
        parsed = urlsplit(endpoint)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ContractError("model endpoint is malformed") from None
    if (parsed.scheme not in ("http", "https") or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or "@" in parsed.netloc):
        raise ContractError("model endpoint must be loopback and credential-free")
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError:
            raise ContractError("model endpoint must be loopback") from None
    if parsed.path not in ("", "/", "/v1", "/v1/"):
        raise ContractError("model endpoint requires the admitted API root")
    host = f"[{host}]" if ":" in host else host
    # Omitted/default ports and optional /v1 slash receive one identity.
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= effective_port <= 65535:
        raise ContractError("model endpoint has an invalid port")
    return urlunsplit((parsed.scheme, f"{host}:{effective_port}", "/v1", "", ""))


def _sanitize_string(value: str) -> str:
    def clean_fields(fields: str, *, assignment_only: bool = False) -> str:
        # Match decoded field names exactly, keeping benign URL bytes intact.
        # Bare fragment anchors such as #token are headings, not auth fields.
        kept = []
        for field in fields.split("&"):
            key, assignment, _ = field.partition("=")
            if (unquote_plus(key).casefold() in _URL_SECRET_KEYS
                    and (assignment or not assignment_only)):
                continue
            kept.append(field)
        return "&".join(kept)

    def clean_url(match):
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
            authority = parsed.netloc
            if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
                host = parsed.hostname
                if not host:
                    return REDACTED
                host = f"[{host}]" if ":" in host else host
                authority = host + (f":{parsed.port}" if parsed.port is not None else "")
            query = clean_fields(parsed.query)
            anchor, separator, fragment_fields = parsed.fragment.partition("?")
            cleaned_anchor = clean_fields(anchor, assignment_only=True)
            if separator:
                cleaned_fields = clean_fields(fragment_fields)
                fragment = (parsed.fragment if (cleaned_anchor, cleaned_fields) == (anchor, fragment_fields)
                            else cleaned_anchor + ("?" + cleaned_fields if cleaned_fields else ""))
            else:
                fragment = cleaned_anchor
            if (authority, query, fragment) != (parsed.netloc, parsed.query, parsed.fragment):
                return urlunsplit((parsed.scheme, authority, parsed.path, query, fragment))
        except ValueError:
            return REDACTED
        return raw
    return _SECRET_VALUE.sub(REDACTED, _URL.sub(clean_url, value))


def sanitize_evidence(value):
    """Return finite JSON with auth fields/value patterns redacted, no env reads.

    Closed protocol/identity writers should use assert_secret_free instead;
    modifying returned patch bytes would violate native patch authority.
    """
    canonical_json(value)

    def clean(item):
        if type(item) is dict:
            return {key: REDACTED if (_SECRET_KEY.search(key)
                                     or re.fullmatch(r"(?:.*[_\- ])?token", key, re.I)) else clean(child)
                    for key, child in item.items()}
        if type(item) is list:
            return [clean(child) for child in item]
        if type(item) is str:
            return _sanitize_string(item)
        return item
    return clean(value)


def assert_secret_free(value) -> None:
    """Reject rather than serialize detected auth; never echo offending values."""
    if sanitize_evidence(value) != value:
        raise ContractError("runtime evidence contains authentication material")


assert_evidence_safe = assert_secret_free


def _source_paths(repo_root: Path) -> tuple[str, ...]:
    # Every eval module participates: new untracked runtime source is included,
    # not silently omitted from a dirty-tree fingerprint. Supporting modules
    # used by staged code and the closed driver participate too.
    paths = {path.relative_to(repo_root).as_posix() for path in (repo_root / "eval").glob("*.py")}
    for relative in ("tools/__init__.py", "tools/common.py", "tools/dev_eval.py"):
        if (repo_root / relative).exists():
            paths.add(relative)
    for directory in ("tools/h23_v4", "tools/harness_cert"):
        paths.update(path.relative_to(repo_root).as_posix() for path in (repo_root / directory).glob("*.py"))
    if not paths:
        raise ContractError("runtime source set is empty")
    return tuple(sorted(paths))


def runtime_source_records(repo_root: Path, paths=None) -> dict[str, dict]:
    if not isinstance(repo_root, Path) or not repo_root.is_absolute():
        raise ContractError("runtime source requires an explicit repository root")
    paths = _source_paths(repo_root) if paths is None else tuple(paths)
    if not paths or len(set(paths)) != len(paths):
        raise ContractError("runtime source set is empty or repeated")
    records = {}
    try:
        with anchor_directory(repo_root) as descriptor:
            for relative in sorted(paths):
                if type(relative) is not str or not relative.endswith(".py"):
                    raise ContractError("runtime source records require Python relative paths")
                observed = read_regular(descriptor, relative, max_bytes=4 * 1024 * 1024,
                                        include=False, reject_hardlinks=True)
                records[relative] = {"sha256": observed.sha256, "size_bytes": observed.size}
    except PolicyError:
        raise ContractError("runtime source admission failed") from None
    return records


def runtime_source_identity(repo_root: Path, paths=None) -> dict:
    records = runtime_source_records(repo_root, paths)
    try:
        def git(*argv):
            completed = subprocess.run(["git", "-C", str(repo_root), *argv], check=True,
                                       capture_output=True, text=True, timeout=10,
                                       close_fds=True)
            return completed.stdout.strip()
        head = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
    except (OSError, subprocess.SubprocessError):
        raise ContractError("runtime Git identity is unavailable") from None
    hex_digest(head, "git_head", length=40)
    return {"git_head": head, "source_dirty": dirty,
            "runtime_source_sha256": tree_sha256({key: value["sha256"] for key, value in records.items()})}


def _file_identity(path: Path) -> dict:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ContractError("provenance files require explicit absolute paths")
    try:
        with anchor_directory(path.parent) as descriptor:
            observed = read_regular(descriptor, path.name, max_bytes=64 * 1024 * 1024,
                                    include=False, reject_hardlinks=True)
    except PolicyError:
        raise ContractError("provenance file admission failed") from None
    return {"sha256": observed.sha256, "size_bytes": observed.size}


def package_versions() -> dict:
    versions = {}
    for package in PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


_observed_package_versions = package_versions


def validate_real_fingerprint(value: dict, *, phase: str | None = None) -> dict:
    closed_dict(value, allowed=FINGERPRINT_FIELDS, required=FINGERPRINT_FIELDS, name="real fingerprint")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("real fingerprint schema must be 1")
    if value["phase"] not in ("solver", "verifier", "driver") or (phase is not None and value["phase"] != phase):
        raise ContractError("real fingerprint has the wrong phase")
    source = closed_dict(value["eval_infra_source_identity"],
                         allowed=("git_head", "source_dirty", "runtime_source_sha256"),
                         required=("git_head", "source_dirty", "runtime_source_sha256"), name="runtime source identity")
    hex_digest(source["git_head"], "git_head", length=40)
    hex_digest(source["runtime_source_sha256"], "runtime_source_sha256")
    if type(source["source_dirty"]) is not bool:
        raise ContractError("runtime source dirty state must be boolean")
    for field in ("candidate_identity", "preregistration_identity"):
        identity = closed_dict(value[field], allowed=("sha256", "size_bytes"),
                               required=("sha256", "size_bytes"), name=field)
        hex_digest(identity["sha256"], field)
        if type(identity["size_bytes"]) is not int or identity["size_bytes"] <= 0:
            raise ContractError("fingerprint file identity requires a positive byte size")
    if value["model_endpoint_identity"] != normalize_endpoint(value["model_endpoint_identity"]):
        raise ContractError("fingerprint model endpoint is not normalized")
    for field in ("platform", "python_version"):
        text(value[field], field, nonempty=True)
    versions = closed_dict(value["package_versions"], allowed=PACKAGES, required=PACKAGES, name="package versions")
    for version in versions.values():
        if version is not None:
            text(version, "package version", nonempty=True)
    if (type(value["public_identities"]) is not dict or type(value["model_server_identity"]) is not dict
            or type(value["confinement"]) is not dict):
        raise ContractError("fingerprint observation identities must be objects")
    if value["harness_lock_sha256"] is not None:
        hex_digest(value["harness_lock_sha256"], "harness_lock_sha256")
    assert_secret_free(value)
    return value


def build_real_fingerprint(repo_root: Path, candidate_path: Path, preregistration_path: Path,
                           endpoint: str, *, phase: str, package_versions: dict | None = None,
                           model_server_identity: dict | None = None, public_identities: dict | None = None,
                           harness_lock_sha256: str | None = None, confinement: dict | None = None) -> dict:
    value = {
        "schema_version": 1, "phase": phase,
        "eval_infra_source_identity": runtime_source_identity(repo_root),
        "candidate_identity": _file_identity(candidate_path),
        "preregistration_identity": _file_identity(preregistration_path),
        "model_endpoint_identity": normalize_endpoint(endpoint),
        "platform": platform.system(), "python_version": platform.python_version(),
        "package_versions": _observed_package_versions() if package_versions is None else package_versions,
        "public_identities": {} if public_identities is None else public_identities,
        "model_server_identity": {} if model_server_identity is None else model_server_identity,
        "harness_lock_sha256": harness_lock_sha256,
        "confinement": {"status": "NOT_OBSERVED", "reason": "worker confinement is observed during preflight/run"}
                       if confinement is None else confinement,
    }
    return validate_real_fingerprint(value, phase=phase)


def real_fingerprint_from_request(request: dict, *, phase: str,
                                  package_versions: dict | None = None,
                                  model_server_identity: dict | None = None) -> dict:
    """Fingerprint a worker without privileged Git/private/environment reads."""
    if type(request) is not dict or type(request.get("provenance")) is not dict:
        raise ContractError("real worker fingerprint requires admitted provenance")
    provenance = request["provenance"]
    if any(key not in provenance for key in (*IDENTITY_FIELDS, "confinement")):
        raise ContractError("real worker fingerprint is missing admitted identities")
    value = {
        "schema_version": 1, "phase": phase,
        **{key: provenance[key] for key in IDENTITY_FIELDS},
        "platform": platform.system(), "python_version": platform.python_version(),
        "package_versions": _observed_package_versions() if package_versions is None else package_versions,
        "public_identities": provenance.get("public_identities", {}),
        "model_server_identity": {} if model_server_identity is None else model_server_identity,
        "harness_lock_sha256": provenance.get("harness_lock_sha256"),
        "confinement": provenance["confinement"],
    }
    return validate_real_fingerprint(value, phase=phase)


def crosscheck_real_fingerprints(solver: dict, verifier: dict, expected: dict | None = None) -> None:
    """Both phases mandatory; compare every contract-authoritative core identity."""
    validate_real_fingerprint(solver, phase="solver")
    validate_real_fingerprint(verifier, phase="verifier")
    if expected is not None:
        if type(expected) is not dict or any(key not in expected for key in IDENTITY_FIELDS):
            raise ContractError("coordinator fingerprint expectations are incomplete")
        assert_secret_free(expected)
    for key in IDENTITY_FIELDS:
        if solver[key] != verifier[key] or (expected is not None and solver[key] != expected[key]):
            raise ContractError("real solver/verifier runtime fingerprint mismatch")
    # Extra admitted identities and lock must also agree; actual server phase
    # observations may differ while core endpoint identity remains authoritative.
    for key in ("public_identities", "harness_lock_sha256", "confinement"):
        if solver[key] != verifier[key]:
            raise ContractError("real solver/verifier evidence identity mismatch")
