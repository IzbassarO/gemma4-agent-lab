"""Exact-E0 native execution beside the independently audited synthetic path.

Native dependencies are imported only after the request, staged contract bytes,
Linux source environment and lock are admitted. No evaluator or dataset loader
is imported. Private contracts occur only inside the verifier entry point.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
from importlib.machinery import ModuleSpec
import inspect
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import stat
import sys
import sysconfig
import tarfile
import tempfile
import time
import traceback
from types import ModuleType, SimpleNamespace
import urllib.request
import zipfile

from ._contracts import ContractError, canonical_json, canonical_sha256, closed_dict
from .real_contracts import (ADAPTER_NAMES, E0_MEMBERS, E0_SHA256, E0_SIZE, FROZEN_BUDGET,
                             SERVED_MODEL, normalize_endpoint, validate_budget, validate_compaction,
                             validate_public_task_identity)
from .solver_task import SolverTask
from .staging import validate_staged_public_asset, validate_staged_solver_assets
from tools.common import tree_sha256
from tools.h23_v4.filesystem import anchor_directory, read_regular

SOURCE_WHEELS = {
    "adk_submission-0.2.12-py3-none-any.whl": "077c438c426e625b9f722081694e1d32856e6f7e932ef625002fc4a11aabdc10",
    "google_adk-1.36.1-py3-none-any.whl": "1a2f6868c509e3151fb0de3575a7d18b45c338be86f420924dad74e7193631a0",
    "google_genai-2.11.0-py3-none-any.whl": "5bc8186100e1d34d691fbe0cba392b7e04e98d286ca952323a6672d054accf95",
    "adk_eval_core-0.1.0-py3-none-any.whl": "194dd8f9aab154857bfa0db9d9ec382db856ee124529e072509634d3071d8643",
    "swegemma-0.2.7-py3-none-any.whl": "27a2f60f8db46c8fef5defc16df722dac0402446c9a6252e7e6b4c280e843c81",
}
SOURCE_VERSIONS = {"adk-submission": "0.2.12", "google-adk": "1.36.1", "google-genai": "2.11.0",
                   "adk-eval-core": "0.1.0", "swegemma": "0.2.7"}

# Frozen support pins from the committed acquisition plan; source wheels stay --no-deps.
SUPPORT_PINS = {
    'aiohappyeyeballs': '2.7.1',
    'aiohttp': '3.13.4',
    'aiosignal': '1.4.0',
    'annotated-doc': '0.0.5',
    'annotated-types': '0.8.0',
    'anyio': '4.15.1',
    'attrs': '26.1.0',
    'authlib': '1.6.6',
    'cachetools': '7.2.0',
    'certifi': '2026.7.22',
    'cffi': '2.1.1',
    'charset-normalizer': '3.5.2',
    'click': '8.1.8',
    'cryptography': '50.0.2',
    'distro': '1.9.0',
    'docker': '7.2.0',
    'fastapi': '0.141.1',
    'fastuuid': '0.14.0',
    'filelock': '4.0.9',
    'frozenlist': '1.8.0',
    'fsspec': '2026.9.0',
    'google-api-core': '2.34.0',
    'google-auth': '2.59.1',
    'google-cloud-core': '2.8.0',
    'google-cloud-storage': '3.16.0',
    'google-crc32c': '1.9.0',
    'google-resumable-media': '2.11.0',
    'googleapis-common-protos': '1.75.5',
    'h11': '0.16.0',
    'hf-xet': '1.6.0',
    'httpcore': '1.0.9',
    'httpx': '0.28.1',
    'huggingface-hub': '1.16.1',
    'idna': '3.20',
    'importlib-metadata': '8.5.0',
    'jinja2': '3.1.6',
    'jiter': '0.17.0',
    'jsonschema': '4.23.0',
    'jsonschema-specifications': '2025.9.1',
    'litellm': '1.83.14',
    'markdown-it-py': '4.2.0',
    'markupsafe': '3.0.4',
    'mdurl': '0.1.2',
    'multidict': '6.9.1',
    'networkx': '3.7',
    'numpy': '2.5.3',
    'openai': '2.24.0',
    'opentelemetry-api': '1.41.1',
    'opentelemetry-semantic-conventions': '0.62b1',
    'packaging': '26.3',
    'pandas': '3.0.6',
    'propcache': '0.5.4',
    'proto-plus': '1.29.0',
    'protobuf': '7.36.2',
    'pyasn1': '0.6.4',
    'pyasn1-modules': '0.4.2',
    'pycparser': '3.0',
    'pydantic': '2.12.5',
    'pydantic-core': '2.41.5',
    'pygments': '2.21.0',
    'python-dateutil': '2.9.0.post0',
    'python-dotenv': '1.2.2',
    'pyyaml': '6.0.3',
    'referencing': '0.37.0',
    'regex': '2026.9.29',
    'requests': '2.34.2',
    'rich': '15.0.0',
    'rpds-py': '2026.6.3',
    'shellingham': '1.5.4',
    'six': '1.17.0',
    'sniffio': '1.3.1',
    'starlette': '0.52.1',
    'tenacity': '9.1.4',
    'tiktoken': '0.12.0',
    'tokenizers': '0.22.2',
    'tqdm': '4.70.1',
    'typer': '0.27.2',
    'typing-extensions': '4.16.0',
    'typing-inspection': '0.4.4',
    'urllib3': '2.8.0',
    'websockets': '15.0.1',
    'yarl': '1.25.1',
    'zipp': '4.1.1',
}


class RealAdmissionError(ContractError):
    """Real execution failed a mandatory admission boundary."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, *, maximum: int = 64 * 1024 * 1024, private: bool = False) -> bytes:
    with anchor_directory(path.parent, private=private) as descriptor:
        return read_regular(descriptor, path.name, max_bytes=maximum, include=True,
                            reject_hardlinks=True, private=private).data


def _candidate_members(data: bytes) -> dict[str, bytes]:
    """Validate all entries before any extraction; duplicate paths also fail."""
    members = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (entry.filename != path.as_posix() or path.is_absolute() or ".." in path.parts
                        or "\\" in entry.filename or entry.filename in members or entry.is_dir()
                        or entry.filename not in E0_MEMBERS or entry.flag_bits & 1
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                        or mode & (stat.S_ISUID | stat.S_ISGID) or entry.file_size > 1024 * 1024):
                    raise RealAdmissionError("candidate has unsafe or unfrozen ZIP member")
                content = archive.read(entry)
                if _sha(content) != E0_MEMBERS[entry.filename]:
                    raise RealAdmissionError("candidate member identity differs from frozen E0")
                members[entry.filename] = content
    except (zipfile.BadZipFile, RuntimeError):
        raise RealAdmissionError("candidate is not an admitted regular ZIP") from None
    if set(members) != set(E0_MEMBERS):
        raise RealAdmissionError("candidate must contain exactly the 10 frozen E0 members")
    return members


def validate_candidate(path: Path) -> dict:
    data = _read(Path(path), maximum=E0_SIZE)
    if len(data) != E0_SIZE or _sha(data) != E0_SHA256:
        raise RealAdmissionError("candidate is not frozen E0; a later candidate-admission step is required")
    members = _candidate_members(data)
    return {"sha256": E0_SHA256, "size_bytes": E0_SIZE,
            "members": {name: {"sha256": _sha(value), "size_bytes": len(value)} for name, value in members.items()}}


def extract_candidate(path: Path, destination: Path) -> dict:
    """Create fresh private, single-link files only after exact ZIP admission."""
    data = _read(Path(path), maximum=E0_SIZE)
    if len(data) != E0_SIZE or _sha(data) != E0_SHA256:
        raise RealAdmissionError("candidate is not frozen E0; a later candidate-admission step is required")
    members = _candidate_members(data)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RealAdmissionError("candidate extraction root must be fresh")
    with anchor_directory(destination.parent):
        destination.mkdir(mode=0o700)
    for name, content in members.items():
        target = destination / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with anchor_directory(target.parent, private=True) as descriptor:
            fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if _read(target, private=True) != content:
                raise RealAdmissionError("extracted candidate identity changed")
    return {"sha256": E0_SHA256, "size_bytes": E0_SIZE, "member_sha256": dict(E0_MEMBERS)}


def parse_candidate_budget(candidate_dir: Path, expected: dict) -> dict:
    """Only the E0 evaluation schema is supported; no override or defaults."""
    validate_budget(expected)
    import yaml
    try:
        value = yaml.safe_load(_read(candidate_dir / "eval_config.yaml", maximum=1024 * 1024))
    except yaml.YAMLError:
        raise RealAdmissionError("candidate evaluation config is invalid YAML") from None
    closed_dict(value, allowed=("evaluation",), required=("evaluation",), name="candidate config")
    fields = ("max_time_minutes", "max_tool_calls", "max_turns", "timeout_seconds")
    evaluation = closed_dict(value["evaluation"], allowed=fields, required=fields, name="candidate evaluation")
    actual = {"time_minutes": evaluation["max_time_minutes"], "tool_calls": evaluation["max_tool_calls"],
              "turns": evaluation["max_turns"], "command_timeout_seconds": evaluation["timeout_seconds"]}
    validate_budget(actual)
    if actual != expected:
        raise RealAdmissionError("candidate budget differs from preregistration")
    return actual


def _tree(root: Path) -> dict[str, str]:
    files = {}
    with anchor_directory(root) as descriptor:
        for current, directories, names in os.walk(root, followlinks=False):
            for directory in directories:
                if not stat.S_ISDIR((Path(current) / directory).lstat().st_mode):
                    raise RealAdmissionError("runtime support tree contains a linked or special directory")
            for name in names:
                path = Path(current) / name
                relative = path.relative_to(root).as_posix()
                files[relative] = read_regular(descriptor, relative, max_bytes=256 * 1024 * 1024,
                                              include=False, reject_hardlinks=True).sha256
    if not files:
        raise RealAdmissionError("runtime support tree is empty")
    return files


def real_verification_config(public_root: Path, budget: dict) -> dict:
    validate_budget(budget)
    public_root = Path(public_root)
    return {"schema_version": 1, "sandbox": "subprocess", "timeout_seconds": budget["command_timeout_seconds"],
            "setup_py_sha256": _sha(_read(public_root / "sandbox/setup.py")),
            "wheels_tree_sha256": tree_sha256(_tree(public_root / "wheels"))}


def inspect_official_snapshot(path: Path, asset) -> dict:
    """Inventory faithful official Git metadata; contract bytes remain authority.

    No synthetic Git allowlist, index deletion or host-local Git injection occurs.
    Unsafe archive paths/types are refused before the unchanged native extractor.
    """
    data = _read(path, maximum=asset.size_bytes, private=True)
    if (len(data), _sha(data)) != (asset.size_bytes, asset.sha256):
        raise RealAdmissionError("official snapshot differs from contract authority")
    seen, inventory = set(), []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive:
                name = member.name.removeprefix("./").rstrip("/")
                if name in ("", ".") and member.isdir():
                    continue
                pure = PurePosixPath(name)
                if (not name or pure.is_absolute() or ".." in pure.parts or "\\" in name
                        or name != pure.as_posix() or name in seen
                        or not (member.isfile() or member.isdir() or member.issym() or member.islnk())
                        or member.mode & (stat.S_ISUID | stat.S_ISGID)):
                    raise RealAdmissionError("official snapshot contains an unsafe archive member")
                if member.issym() or member.islnk():
                    link = member.linkname
                    target = posixpath.normpath(posixpath.join(str(pure.parent), link) if member.issym() else link)
                    if (not link or "\\" in link or PurePosixPath(link).is_absolute()
                            or target == ".." or target.startswith("../")):
                        raise RealAdmissionError("official snapshot link escapes the native workspace")
                seen.add(name)
                if pure.parts[0] == ".git":
                    content = archive.extractfile(member).read() if member.isfile() else b""
                    inventory.append({"path": name, "type": "file" if member.isfile() else "directory" if member.isdir()
                                      else "symlink" if member.issym() else "hardlink", "link_target": member.linkname or None,
                                      "size_bytes": member.size, "sha256": _sha(content) if member.isfile() else None})
    except tarfile.TarError:
        raise RealAdmissionError("official snapshot is not an admitted archive") from None
    return {"snapshot_sha256": asset.sha256, "snapshot_size_bytes": asset.size_bytes,
            "git_index_present": ".git/index" in seen,
            "git_metadata": sorted(inventory, key=lambda entry: entry["path"]),
            "git_metadata_sha256": canonical_sha256(sorted(inventory, key=lambda entry: entry["path"]))}


def parse_harness_lock(data: bytes) -> dict[str, str]:
    """Admit a hash lock, never mutable ranges, editable paths or index options."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise RealAdmissionError("Linux harness lock is not UTF-8") from None
    pins, current, hashes = {}, None, 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash=sha256:"):
            if current is None or re.fullmatch(r"--hash=sha256:[0-9a-f]{64}(?: \\)?", line) is None:
                raise RealAdmissionError("invalid Linux harness lock hash")
            hashes += 1
            continue
        if current is not None and hashes == 0:
            raise RealAdmissionError("Linux harness lock pin lacks hashes")
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)(?: \\)?", line)
        if match is None:
            raise RealAdmissionError("Linux harness lock contains unsupported requirements")
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in pins:
            raise RealAdmissionError("Linux harness lock has duplicate distribution")
        pins[name], current, hashes = match.group(2), name, 0
    if current is None or hashes == 0:
        raise RealAdmissionError("Linux harness lock lacks hashed pins")
    if pins != SUPPORT_PINS:
        raise RealAdmissionError("Linux harness lock does not contain the committed 83 support pins")
    return pins


def validate_harness_environment(harness_root: Path, source_wheels_root: Path, *, expected_lock_sha256: str) -> dict:
    """Platform-neutral source check beside the immutable macOS control helper."""
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        raise RealAdmissionError("real native execution requires Linux x86_64 Python 3.12")
    harness_root = Path(harness_root)
    if harness_root != Path(sys.prefix).resolve():
        raise RealAdmissionError("real worker must use its explicitly admitted harness interpreter")
    prefixes = ("swegemma", "adk_submission", "adk_eval_core", "google.adk", "litellm", "openai", "httpx")
    if any(name == prefix or name.startswith(prefix + ".") for name in sys.modules for prefix in prefixes):
        raise RealAdmissionError("optional native/transport imports occurred before source admission")
    site = Path(sysconfig.get_path("purelib"))
    if site.resolve() != site or not site.is_relative_to(harness_root):
        raise RealAdmissionError("native site-packages is not within the admitted harness")
    lock_data = _read(Path(source_wheels_root) / "harness_linux_x86_64_py312.lock")
    if _sha(lock_data) != expected_lock_sha256:
        raise RealAdmissionError("Linux harness lock identity mismatch")
    pins = parse_harness_lock(lock_data)
    versions = {name: importlib.metadata.version(name) for name in (*pins, *SOURCE_VERSIONS)}
    if versions != {**pins, **SOURCE_VERSIONS}:
        raise RealAdmissionError("installed harness versions differ from admitted Linux lock")
    sources = []
    with anchor_directory(site) as descriptor:
        for name, expected in SOURCE_WHEELS.items():
            wheel = _read(Path(source_wheels_root) / name)
            if _sha(wheel) != expected:
                raise RealAdmissionError("native source wheel identity differs from pinned v28")
            count = 0
            with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
                for member in archive.namelist():
                    if member.endswith(".py"):
                        installed = read_regular(descriptor, member, max_bytes=16 * 1024 * 1024,
                                                 include=True, reject_hardlinks=True).data
                        if installed != archive.read(member):
                            raise RealAdmissionError("installed native Python source differs from v28 wheel")
                        count += 1
            sources.append({"wheel": name, "sha256": expected, "matching_python_files": count})
        tokenizer_root = "litellm/litellm_core_utils/tokenizers/"
        for filename, expected in {
            "9b5ad71b2ce5302211f9c61530b329a4922fc6a4": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
            "fb374d419588a4632f3f557e76b4b70aebbca790": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
        }.items():
            if read_regular(descriptor, tokenizer_root + filename, max_bytes=16 * 1024 * 1024,
                            include=False, reject_hardlinks=True).sha256 != expected:
                raise RealAdmissionError("native bundled tokenizer identity mismatch")
    return {"validator": "linux_source_wheel_and_hash_lock_v1", "versions": versions,
            "site_packages": str(site), "source_wheels": sources, "harness_lock_sha256": _sha(lock_data)}


def _native_namespaces(site: Path) -> None:
    # Bypass only broad eager re-exports. Native leaf source remains unchanged.
    for name, relative in (("swegemma", "swegemma"), ("swegemma.harness", "swegemma/harness")):
        if name in sys.modules:
            raise RealAdmissionError("native package imported before real admission")
        package = ModuleType(name)
        package.__package__, package.__path__ = name, [str(site / relative)]
        package.__spec__ = ModuleSpec(name, loader=None, is_package=True)
        package.__spec__.submodule_search_locations = package.__path__
        sys.modules[name] = package
    logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)


def _admit(request: dict, *, verifier: bool = False):
    from .worker_common import validate_request
    # This MUST precede optional imports even for direct entry-point callers.
    validate_request(request, verifier=verifier)
    if request["mode"] != "real_public":
        raise RealAdmissionError("real execution requires real_public mode")
    runtime = request["runtime"]
    paths = {name: Path(runtime[name]) for name in ("harness_root", "worker_root", "public_root", "evidence_root",
                                                 "submission_root", "wheels_root", "setup_root", "source_wheels_root")}
    validate_candidate(paths["submission_root"] / "submission.zip")
    if paths["wheels_root"] != paths["public_root"] / "wheels" or paths["setup_root"] != paths["public_root"] / "sandbox":
        raise RealAdmissionError("real support files must retain official staged public layout")
    if verifier:
        from .verifier_task import VerifierTask
        task = VerifierTask.from_dict(request["task"])
        validate_staged_public_asset(paths["public_root"], task.snapshot)
    else:
        task = SolverTask.from_dict(request["task"])
        validate_staged_solver_assets(task, paths["public_root"])
    validate_public_task_identity(task)
    snapshot_info = inspect_official_snapshot(paths["public_root"] / task.snapshot.source_relative_path, task.snapshot)
    pin = validate_harness_environment(paths["harness_root"], paths["source_wheels_root"],
                                       expected_lock_sha256=request["provenance"]["harness_lock_sha256"])
    return paths, task, pin, snapshot_info


def _prepare(paths: dict) -> None:
    # Replace inherited parent secrets rather than recording or forwarding them.
    for name in ("home", "tmp", "hf_cache"):
        (paths["worker_root"] / name).mkdir(mode=0o700, exist_ok=True)
    os.environ.clear()
    os.environ.update({"PATH": "/usr/bin:/bin", "HOME": str(paths["worker_root"] / "home"),
                       "TMPDIR": str(paths["worker_root"] / "tmp"), "HF_HOME": str(paths["worker_root"] / "hf_cache"),
                       "KAGGLE_SANDBOX_DIR": str(paths["setup_root"]), "HF_HUB_OFFLINE": "1",
                       "LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_MODE": "PRODUCTION",
                       "PYTHON_DOTENV_DISABLED": "1", "OTEL_SDK_DISABLED": "true", "GIT_CONFIG_NOSYSTEM": "1"})
    tempfile.tempdir = str(paths["worker_root"] / "tmp")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RealAdmissionError("model identity endpoint redirects are refused")


def check_model_identity(endpoint: str, *, fetch=None) -> tuple[dict, bytes]:
    """Explicit loopback GET; no proxy, credential, redirect or fallback."""
    endpoint = normalize_endpoint(endpoint)
    if fetch is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        def fetch(url):
            with opener.open(urllib.request.Request(url, method="GET"), timeout=15) as response:
                if response.status != 200:
                    raise RealAdmissionError("model identity endpoint must return HTTP 200")
                return response.read(1024 * 1024 + 1)
    raw = fetch(endpoint + "/models")
    if type(raw) is not bytes or len(raw) > 1024 * 1024:
        raise RealAdmissionError("model identity response exceeds admission limit")
    try:
        value = json.loads(raw)
        entries = value["data"]
        if type(entries) is not list or any(type(item) is not dict or type(item.get("id")) is not str for item in entries):
            raise ValueError
        identifiers = [item["id"] for item in entries]
    except (ValueError, KeyError, TypeError):
        raise RealAdmissionError("model identity response is malformed") from None
    if not {SERVED_MODEL, *ADAPTER_NAMES}.issubset(identifiers) or len(set(identifiers)) != len(identifiers):
        raise RealAdmissionError("model endpoint must expose exact served model and both frozen adapters")
    return {"endpoint": endpoint, "served_model": SERVED_MODEL, "adapter_names": list(ADAPTER_NAMES),
            "listed_model_ids": identifiers, "response_sha256": _sha(raw), "response_size_bytes": len(raw)}, raw


def _artifact(path: Path, data: bytes, *, sanitize: bool = True) -> dict:
    from .runtime_provenance import assert_secret_free, sanitize_evidence
    if path.suffix == ".json" and sanitize:
        data = canonical_json(sanitize_evidence(json.loads(data))).encode()
    else:
        if sanitize:
            data = sanitize_evidence(data.decode("utf-8")).encode()
        assert_secret_free(json.loads(data) if path.suffix == ".json" else data.decode("utf-8"))
    from .worker_common import seal_json
    if path.suffix == ".json" and sanitize:
        seal_json(path, json.loads(data))
        data = _read(path, private=True)
    else:
        with anchor_directory(path.parent, private=True) as descriptor:
            fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    return {"relative_path": path.name, "sha256": _sha(data), "size_bytes": len(data)}


def _compaction(value, site: Path):
    validate_compaction(value)
    if value is None:
        return None, None, {"compaction_applied": False, "reason": "explicit null compaction/cache configuration"}
    from google.adk.agents.context_cache_config import ContextCacheConfig
    from google.adk.apps._configs import EventsCompactionConfig
    events = EventsCompactionConfig(**{key: value[key] for key in value if key != "cache_min_tokens"})
    cache = ContextCacheConfig(min_tokens=value["cache_min_tokens"])
    source = _read(site / "swegemma/harness/agent_runner.py").decode()
    applied = all(f"app_kwargs['{name}'] = config.{name}" in source for name in
                  ("events_compaction_config", "context_cache_config"))
    return events, cache, {"compaction_applied": applied,
                           "reason": None if applied else "admitted native runner does not consume both configuration objects"}


def _config(paths, models, submission, budget, *, manifest=None, compaction=None, cache=None):
    from swegemma.config import EvalConfig
    return EvalConfig(tasks_path=paths["worker_root"] / "tasks/unopened.jsonl", snapshots_dir=paths["public_root"] / "snapshots",
                      results_dir=paths["evidence_root"], submission_dir=submission, models=models, sandbox="subprocess",
                      wheels_dir=paths["wheels_root"], image="SUBPROCESS_NO_IMAGE", graph_dir=str(paths["public_root"] / "graphs"),
                      embeddings_dir=str(paths["public_root"] / "embeddings"), timeout_seconds=budget["command_timeout_seconds"],
                      max_tool_calls=budget["tool_calls"], max_turns=budget["turns"], max_time_minutes=budget["time_minutes"],
                      display_mode="quiet", enable_sandbox_testing=True, events_compaction_config=compaction,
                      context_cache_config=cache, adapter_manifest=manifest)


def observe_manager(manager, paths, task, *, observing: bool = True) -> dict:
    """Passively observe native calls; no scripted behavior/command allowlist."""
    original_start, original_exec, original_copy, original_stop = manager.start, manager.exec, manager.copy_to, manager.stop
    result = {"commands": [], "copies": [], "sandbox_ids": [], "stopped_ids": [], "workspace_patch": None,
              "workspace": None, "junit_xml": None, "sandbox_started_perf": None, "protected_file_reset_commands": []}
    snapshot = paths["public_root"] / task.snapshot.source_relative_path

    def start():
        result["sandbox_started_perf"] = time.perf_counter()
        identifier = original_start()
        result["sandbox_ids"].append(identifier)
        return identifier

    def execute(identifier, command, timeout=None):
        began = time.perf_counter()
        observed = original_exec(identifier, command, timeout=timeout)
        if observing:
            result["commands"].append({"command": command, "exit_code": observed.exit_code,
                                       "stdout_size_bytes": len(observed.stdout.encode()), "stderr_size_bytes": len(observed.stderr.encode()),
                                       "stdout_sha256": _sha(observed.stdout.encode()), "stderr_sha256": _sha(observed.stderr.encode()),
                                       "elapsed_seconds": time.perf_counter() - began,
                                       "network_attempt": bool(re.search(r"\b(?:pip(?:3)?\s+install|curl|wget|git\s+clone)\b", command)),
                                       "patch_application": "Usage: apply_patch.py <workspace_dir> <patch_path>" in command,
                                       "purpose": "native"})
            if command.startswith("cat /tmp/_swegemma_junit_"):
                result["junit_xml"] = observed.stdout
            if "git checkout HEAD --" in command or "git clean -f --" in command:
                result["protected_file_reset_commands"].append(command)
        return observed

    def copy_to(identifier, source, destination):
        source = Path(source)
        if source == snapshot:
            # NEVER adopt the current on-disk hash as new snapshot authority.
            validate_staged_public_asset(paths["public_root"], task.snapshot)
        observed = original_copy(identifier, source, destination)
        if source == snapshot:
            validate_staged_public_asset(paths["public_root"], task.snapshot)
            copied = manager.sandboxes[identifier]["tmp"] / source.name
            copied_bytes = _read(copied, maximum=task.snapshot.size_bytes)
            if (_sha(copied_bytes), len(copied_bytes)) != (task.snapshot.sha256, task.snapshot.size_bytes):
                raise RealAdmissionError("native snapshot copy differs from authoritative SolverTask")
        if observing:
            result["copies"].append({"source_name": source.name, "destination": destination})
        return observed

    def stop(identifier):
        try:
            if observing and identifier in manager.sandboxes:
                workspace = manager.sandboxes[identifier]["workspace"]
                try:
                    diagnostic = original_exec(identifier, "cd /workspace && git diff --binary _swegemma_baseline", timeout=60)
                    if diagnostic.exit_code == 0:
                        result["workspace_patch"] = diagnostic.stdout
                    result["workspace"] = {"diagnostic_exit_code": diagnostic.exit_code,
                                           "patch_size_bytes": len(diagnostic.stdout.encode()) if diagnostic.exit_code == 0 else None,
                                           "reason": None if diagnostic.exit_code == 0 else "native baseline diff unavailable"}
                except Exception as exc:
                    result["workspace"] = {"reason": f"diagnostic failed: {type(exc).__name__}"}
        finally:
            original_stop(identifier)
            result["stopped_ids"].append(identifier)
    manager.start, manager.exec, manager.copy_to, manager.stop = start, execute, copy_to, stop
    direct = getattr(manager, "extract_archive_to_container", None)
    if callable(direct):
        def extract_archive(identifier, source, destination):
            if Path(source) != snapshot:
                raise RealAdmissionError("native direct extraction requires authoritative task snapshot")
            validate_staged_public_asset(paths["public_root"], task.snapshot)
            observed = direct(identifier, source, destination)
            validate_staged_public_asset(paths["public_root"], task.snapshot)
            return observed
        manager.extract_archive_to_container = extract_archive
    return result


class _RunnerEvidence(logging.Handler):
    def __init__(self, started):
        super().__init__()
        self.started, self.logs, self.exceptions = started, [], []

    def emit(self, record):
        self.logs.append({"elapsed_seconds": time.perf_counter() - self.started, "level": record.levelname,
                          "line": record.lineno, "message": record.getMessage()})
        if record.exc_info:
            self.exceptions.append("".join(traceback.format_exception(*record.exc_info)))


def trace_observations(trace_data: dict, *, session_epoch: float | None = None, trace_available: bool = True) -> dict:
    entries = trace_data.get("entries", [])
    edits, nudges, reasons = [], [], []
    for entry in entries:
        if entry.get("type") == "tool_response" and entry.get("tool") in ("edit_file", "write_file"):
            response = entry.get("result")
            try:
                response = json.loads(response) if type(response) is str else response
            except ValueError:
                response = None
            if type(response) is dict and (response.get("success") is True or response.get("status") in ("ok", "success")):
                timestamp = entry.get("timestamp")
                if session_epoch is not None and type(timestamp) in (int, float) and timestamp >= session_epoch:
                    edits.append(timestamp - session_epoch)
        if entry.get("type") == "continuation_nudge":
            nudges.append(entry)
        metadata = entry.get("metadata") or {}
        if type(metadata) is dict and metadata.get("finish_reason") is not None:
            reasons.append(metadata["finish_reason"])
    return {"elapsed_at_first_edit_seconds": min(edits) if edits else None,
            "elapsed_at_first_edit_reason": None if edits else "no timestamped successful edit response observed",
            "nudge_count": len(nudges) if nudges else None, "nudge_count_reason": None if nudges else "nudge events not present in native trace",
            "finish_reasons": reasons or None, "finish_reasons_reason": None if reasons else "finish reasons not exposed in native trace",
            "tool_attempts": sum(entry.get("type") == "tool_call" for entry in entries) if trace_available else None,
            "tool_attempts_reason": None if trace_available else "native trace unavailable"}


def solver_execute_real(request: dict) -> dict:
    paths, task, pin, snapshot_info = _admit(request)
    candidate = paths["worker_root"] / "candidate"
    candidate_identity = extract_candidate(paths["submission_root"] / "submission.zip", candidate)
    budget = parse_candidate_budget(candidate, request["runtime"]["budget"])
    _prepare(paths)
    endpoint_identity, response = check_model_identity(request["runtime"]["model_endpoint"])
    model_ref = _artifact(paths["evidence_root"] / "model_server_models.json", response, sanitize=False)
    _native_namespaces(Path(pin["site_packages"]))
    import litellm
    litellm.disable_hf_tokenizer_download, litellm.telemetry, litellm.cache = True, False, None
    from adk_submission import discover_adapters
    from swegemma.models.registry import setup_gemma_model_registry
    from swegemma.context import SwegemmaContext
    from swegemma.harness.agent_runner import run_agent_sandbox
    from swegemma.sandbox.subprocess import SubprocessManager
    manifest = discover_adapters(candidate)
    if set(manifest.adapters) != set(ADAPTER_NAMES):
        raise RealAdmissionError("native adapter discovery differs from frozen E0")
    models = setup_gemma_model_registry(api_base=request["runtime"]["model_endpoint"], api_key="EMPTY", num_retries=5,
                                        adapter_manifest=manifest, served_model=SERVED_MODEL, backend="vllm")
    compaction, cache, applied = _compaction(request["runtime"]["compaction"], Path(pin["site_packages"]))
    config = _config(paths, models, candidate, budget, manifest=manifest, compaction=compaction, cache=cache)
    public = SimpleNamespace(**{name: getattr(task, name) for name in
                              ("instance_id", "repo", "base_commit", "problem_statement", "hints_text")})
    manager = SubprocessManager(timeout_seconds=budget["command_timeout_seconds"],
                                base_dir=paths["worker_root"] / "sandboxes", system_site_packages=False)
    started, started_at = time.perf_counter(), datetime.now(timezone.utc).isoformat()
    observations = observe_manager(manager, paths, task, observing=request["observation_enabled"])
    context = SwegemmaContext(docker_manager=manager, task=public, problem_statement=public.problem_statement,
                              hints_text=public.hints_text, repo=public.repo, budget=config.budget, harness=config.harness,
                              graph_dir=config.graph_dir, embeddings_dir=config.embeddings_dir)
    session = {"epoch": None, "perf": None}
    original_session_start = context.start_agent_session
    def start_session():
        original_session_start()
        session["epoch"], session["perf"] = time.time(), time.perf_counter()
    context.start_agent_session = start_session
    logs = _RunnerEvidence(started)
    logger = logging.getLogger("swegemma.harness.agent_runner")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(logs)
    patch, error, trace, escaped = "", None, None, None
    try:
        patch, error, trace = asyncio.run(run_agent_sandbox(manager, config, public,
                                                           paths["public_root"] / task.snapshot.source_relative_path, context=context))
    except Exception as exc:
        escaped = f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}"
    finally:
        logger.removeHandler(logs)
        logger.setLevel(old_level)
        manager.cleanup_all()
    forbidden = ("eval.verifier_task", "eval.verifier_data", "swegemma.evaluate", "swegemma.harness.verification",
                 "swegemma.harness.sample_verification")
    if any(name in sys.modules for name in forbidden):
        raise RealAdmissionError("real solver imported trusted verifier/evaluator modules")
    if type(patch) is not str:
        raise RealAdmissionError("native runner did not return patch text")
    from .runtime_provenance import assert_secret_free, sanitize_evidence
    assert_secret_free(patch)
    if context.submitted_patch is not None:
        assert_secret_free(context.submitted_patch)
    error, escaped = sanitize_evidence(error), sanitize_evidence(escaped)
    elapsed = time.perf_counter() - started
    trace_data = trace.to_dict(format="legacy") if trace else {"entries": []}
    details = trace_observations(trace_data, session_epoch=session["epoch"], trace_available=trace is not None)
    trace_ref = _artifact(paths["evidence_root"] / "native_trace.json", canonical_json(trace_data).encode())
    observation_ref = _artifact(paths["evidence_root"] / "native_observations.json", canonical_json({
        "manager": observations, "runner_logs": logs.logs, "exceptions": logs.exceptions, "trace_observations": details,
        "snapshot": snapshot_info, "model_identity": endpoint_identity, "candidate_identity": candidate_identity,
        "setup_seconds": session["perf"] - observations["sandbox_started_perf"] if session["perf"] is not None
        and observations["sandbox_started_perf"] is not None else None,
        "setup_seconds_reason": None if session["perf"] is not None and observations["sandbox_started_perf"] is not None
        else "sandbox start or agent session start not observed",
        "session_seconds": context.agent_elapsed_seconds if session["perf"] is not None else None,
        "session_seconds_reason": None if session["perf"] is not None else "agent session did not start",
        "total_seconds": elapsed, **applied}).encode())
    prompt = next((entry.get("content") for entry in trace_data.get("entries", []) if entry.get("type") == "task_prompt"), None)
    prompt_ref = _artifact(paths["evidence_root"] / "agent_prompt.txt", prompt.encode()) if type(prompt) is str else None
    timeout = bool(error and error.startswith("Agent exceeded session timeout"))
    exhausted = bool(error and "budget" in error)
    fallback = any("Captured unsubmitted working tree modifications as fallback patch" in entry["message"] for entry in logs.logs)
    return {"schema_version": 1, "task_id": task.instance_id,
            "runtime_status": "worker_error" if escaped else "timeout" if timeout else "budget_exhausted" if exhausted else "error" if error else "completed",
            "started_at": started_at, "elapsed_seconds": elapsed, "returned_patch": patch, "returned_patch_sha256": _sha(patch.encode()),
            "submitted_patch": context.submitted_patch, "workspace_patch": sanitize_evidence(observations["workspace_patch"]), "agent_error": error,
            "escaped_exception": escaped, "llm_calls": context.llm_calls_used, "counted_tool_calls": context.tool_calls_used,
            "fallback_used": fallback, "timeout": timeout, "budget_exhausted": exhausted, "terminal_reason": error or "native_runner_returned",
            "native_trace_ref": trace_ref, "workspace_diagnostics_ref": observation_ref, "agent_prompt_ref": prompt_ref,
            "model_identity_ref": model_ref, "source_pin": pin, "model_server_identity": endpoint_identity,
            "native_result": {"patch_submitted": context.patch_submitted, "native_function": "swegemma.harness.agent_runner.run_agent_sandbox",
                              "forbidden_imports_absent": list(forbidden), "sandbox_ids": observations["sandbox_ids"],
                              "stopped_ids": observations["stopped_ids"]},
            "effective_configuration": {"budget": budget, "compaction": request["runtime"]["compaction"],
                                        "model_endpoint": request["runtime"]["model_endpoint"], "system_site_packages": False, **applied},
            "command_observations": sanitize_evidence(observations["commands"]), **details}


def verifier_native_kwargs(function, *, patch, error, started):
    """Match admitted Evaluator.evaluate_task; never force synthetic fast_path."""
    result = {"agent_patch": patch, "agent_error": error, "trace": None, "start_time": started}
    signature = inspect.signature(function)
    if "base_snapshot_path" in signature.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
        result.update(base_snapshot_path=None, patch_path=None)
    return result


def verification_observations(native_data: dict, observations: dict, patch: str) -> dict:
    applications = [item for item in observations["commands"] if item.get("patch_application")]
    expected = 2 if patch.strip() else 1
    failed = next((item for item in applications if item["exit_code"] != 0), None)
    complete = len(applications) == expected and all(item["exit_code"] == 0 for item in applications)
    return {"apply_status": "failed" if failed else ("applied" if patch.strip() else "not_needed") if complete else "UNKNOWN",
            "apply_error": f"native patch application exited {failed['exit_code']}" if failed else None,
            "apply_status_reason": None if complete or failed else "complete native agent/private application observations unavailable",
            "apply_pass_diagnostics": applications,
            "required_tests_passed": native_data.get("resolved") if native_data.get("test_exit_code") is not None else None,
            "test_exit_code": native_data.get("test_exit_code")}


def verifier_execute_real(request: dict) -> dict:
    paths, task, pin, snapshot_info = _admit(request, verifier=True)
    private, patch = request["test_patch"], request["returned_patch"]
    if type(private) is not str or (_sha(private.encode()), len(private.encode())) != (task.test_patch.sha256, task.test_patch.size_bytes):
        raise RealAdmissionError("private test patch identity differs from admitted verifier contract")
    if type(patch) is not str or _sha(patch.encode()) != request["returned_patch_sha256"]:
        raise RealAdmissionError("verifier returned patch differs from solver seal")
    if task.fail_to_pass is not None or task.pass_to_pass is not None:
        raise RealAdmissionError("real public verification uses extracted tests, no private node lists")
    configuration = real_verification_config(paths["public_root"], request["runtime"]["budget"])
    if request["verification_config"] != configuration or canonical_sha256(configuration) != task.verification_config_sha256:
        raise RealAdmissionError("verification config identity differs from trusted contract")
    _prepare(paths)
    endpoint_identity, response = check_model_identity(request["runtime"]["model_endpoint"])
    model_ref = _artifact(paths["evidence_root"] / "model_server_models.json", response, sanitize=False)
    _native_namespaces(Path(pin["site_packages"]))
    from adk_submission import ModelRegistry
    from swegemma.models.task import Task
    from swegemma.harness.verification import verify_task
    from swegemma.sandbox.subprocess import SubprocessManager
    budget = request["runtime"]["budget"]
    manager = SubprocessManager(timeout_seconds=budget["command_timeout_seconds"],
                                base_dir=paths["worker_root"] / "sandboxes", system_site_packages=False)
    observations = observe_manager(manager, paths, task, observing=request["observation_enabled"])
    native_task = Task(instance_id=task.instance_id, repo=task.repo, base_commit=task.base_commit,
                       problem_statement="", test_patch=private, FAIL_TO_PASS=(), PASS_TO_PASS=())
    config = _config(paths, ModelRegistry(), paths["submission_root"], budget)
    started, started_at = time.perf_counter(), datetime.now(timezone.utc).isoformat()
    kwargs = verifier_native_kwargs(verify_task, patch=patch, error=request["agent_error"], started=started)
    try:
        native = asyncio.run(verify_task(manager, config, native_task,
                                        paths["public_root"] / task.snapshot.source_relative_path, **kwargs))
    finally:
        manager.cleanup_all()
    from .runtime_provenance import sanitize_evidence
    native_data = sanitize_evidence(native.model_dump(mode="json"))
    verification = verification_observations(native_data, observations, patch)
    native_ref = _artifact(paths["evidence_root"] / "native_verification.json", canonical_json(native_data).encode())
    observation_ref = _artifact(paths["evidence_root"] / "native_observations.json", canonical_json({
        **observations, **verification, "snapshot": snapshot_info, "verification_config": configuration,
        "verify_keyword_names": sorted(kwargs), "fast_path_explicitly_passed": False,
        "synthetic_control_fast_path_explicit": True, "model_identity": endpoint_identity}).encode())
    junit = observations["junit_xml"]
    junit_ref = _artifact(paths["evidence_root"] / "junit.xml", junit.encode()) if junit else None
    return {"schema_version": 1, "task_id": task.instance_id, "runtime_status": "completed", "started_at": started_at,
            "elapsed_seconds": time.perf_counter() - started, "resolved": native.resolved,
            "returned_patch_sha256": _sha(patch.encode()), "test_exit_code": native.test_exit_code,
            "test_output": native_data.get("test_output"), "error": sanitize_evidence(native.error_message), "native_result": native_data,
            "native_result_ref": native_ref, "workspace_diagnostics_ref": observation_ref, "junit_ref": junit_ref,
            "source_pin": pin, "model_identity_ref": model_ref, "model_server_identity": endpoint_identity,
            "effective_configuration": {"budget": budget, "verification_config": configuration},
            "command_observations": sanitize_evidence(observations["commands"]), "lifecycle": {
                "sandbox_ids": observations["sandbox_ids"], "stopped_ids": observations["stopped_ids"]}, **verification}
