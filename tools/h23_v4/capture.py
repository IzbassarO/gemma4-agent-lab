"""Produce bounded, untrusted observations without claiming installation or origin.

The only subprocess is the intentional official pip bootstrap. No target module
is imported, and arbitrary metadata, direct URLs, environment values and pip
output are never archive payloads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from tools.h23_v4.archive import MAX_EXPANDED_BYTES, MAX_PAYLOADS, build_archive
from tools.h23_v4.filesystem import anchor_directory, read_regular
from tools.h23_v4.metadata_policy import (parse_console_scripts, parse_metadata,
                                         validate_wheel_dist_info, wheel_filename_identity,
                                         wheel_installation_filename)
from tools.h23_v4.record_policy import classify_record_rows, payload_required, validate_interpreter_roots
from tools.h23_v4.schema import PolicyError, canonical_json, validate_manifest


TARGETS = (
    "swegemma", "adk-submission", "adk-eval-core", "google-adk", "google-genai",
    "litellm", "vllm", "transformers",
)
RUNTIME_FILES = (
    "tools/__init__.py", "tools/h23_v4/__init__.py", "tools/h23_v4/schema.py",
    "tools/h23_v4/metadata_policy.py", "tools/h23_v4/record_policy.py",
    "tools/h23_v4/filesystem.py", "tools/h23_v4/archive.py", "tools/h23_v4/capture.py",
)
OFFICIAL_ENVIRONMENT = {
    "LITELLM_LOCAL_MODEL_COST_MAP": "True", "TRANSFORMERS_NO_TF": "1",
    "VLLM_WORKER_MULTIPROC_METHOD": "spawn", "VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS": "1",
    "VLLM_ENGINE_READY_TIMEOUT_S": "1200", "VLLM_NO_USAGE_STATS": "1",
    "OTEL_SDK_DISABLED": "true", "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
}
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
MAX_WHEEL_BYTES = 16 * 2**30


def _site_roots():
    return tuple(sorted({Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}))


def _installation_scheme():
    # Python names the default virtualenv scheme "venv" on POSIX. This sole
    # documented alias changes the label, never the observed roots or command.
    observed = sysconfig.get_default_scheme()
    return "posix_venv" if observed == "venv" else observed


@dataclass(frozen=True)
class CaptureConfig:
    wheelhouse: Path = Path("/kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse")
    output_directory: Path = Path("/kaggle/working")
    site_roots: tuple[Path, ...] = field(default_factory=_site_roots)
    prefix: Path = field(default_factory=lambda: Path(sys.prefix).resolve())
    scripts_directory: Path = field(default_factory=lambda: Path(sysconfig.get_path("scripts")).resolve())
    executable: Path = field(default_factory=lambda: Path(sys.executable).absolute())
    python_version: tuple[int, int, int] = field(default_factory=lambda: tuple(sys.version_info[:3]))
    scheme: str = field(default_factory=_installation_scheme)
    execute_bootstrap: bool = True
    executed_notebook: Path | None = None


def capture_program_sha256():
    project = Path(__file__).absolute().parents[2]
    commitments = {name: hashlib.sha256((project / name).read_bytes()).hexdigest() for name in RUNTIME_FILES}
    return hashlib.sha256(canonical_json(commitments)).hexdigest()


def _wheel_observation(root_fd, name):
    """Hash and inspect one wheel through the same no-follow opened inode."""
    restored = wheel_installation_filename(name)
    distribution_name, version = wheel_filename_identity(restored)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(name, flags, dir_fd=root_fd)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_WHEEL_BYTES:
            raise PolicyError("WHEEL_LIMIT")
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_WHEEL_BYTES:
                raise PolicyError("WHEEL_LIMIT")
            digest.update(chunk)
        stream.seek(0)
        with zipfile.ZipFile(stream) as wheel:
            members = wheel.infolist()
            for directory in {item.filename.split("/", 1)[0] for item in members
                              if item.filename.split("/", 1)[0].endswith(".dist-info")}:
                validate_wheel_dist_info(directory, distribution_name, version)
            candidates = [item for item in members
                          if item.filename.count("/") == 1 and item.filename.endswith(".dist-info/METADATA")]
            if len(candidates) > 1:
                raise PolicyError("WHEEL_METADATA")
            if candidates:
                validate_wheel_dist_info(candidates[0].filename.split("/", 1)[0],
                                         distribution_name, version)
                if candidates[0].file_size > 1024 * 1024:
                    raise PolicyError("WHEEL_METADATA")
                metadata = parse_metadata(wheel.read(candidates[0]), expected_name=distribution_name,
                                          expected_version=version)["projection"]
            else:
                metadata = None
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise PolicyError("WHEEL_CHANGED")
    return {"dataset_filename": name, "installation_filename": restored, "size": size,
            "sha256": digest.hexdigest(), "metadata": metadata}


def _stream_observation(stream):
    stream.seek(0)
    digest, size = hashlib.sha256(), 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _bootstrap(config, wheels, runner):
    if not config.execute_bootstrap:
        return {"recipe_id": "NOT_RUN", "wheel_references": [], "argv": [], "executed": False,
                "return_code": None, "stdout_byte_count": 0, "stderr_byte_count": 0,
                "stdout_sha256": None, "stderr_sha256": None, "diagnostic": "NOT_RUN"}
    with tempfile.TemporaryDirectory(prefix="h23-v4-wheelhouse-") as temporary:
        wheel_directory = Path(temporary)
        for wheel in wheels:
            (wheel_directory / wheel["installation_filename"]).symlink_to(
                config.wheelhouse / wheel["dataset_filename"]
            )
        ordered = sorted(wheels, key=lambda wheel: wheel["installation_filename"])
        argv = [str(config.executable), "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall",
                *(str(wheel_directory / wheel["installation_filename"]) for wheel in ordered)]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                completed = runner(argv, stdout=stdout, stderr=stderr, check=False,
                                   env={**os.environ, **OFFICIAL_ENVIRONMENT})
                return_code = completed.returncode
                diagnostic = "EXIT_ZERO" if return_code == 0 else "EXIT_NONZERO"
            except OSError:
                return_code, diagnostic = None, "EXECUTION_ERROR"
            stdout_size, stdout_hash = _stream_observation(stdout)
            stderr_size, stderr_hash = _stream_observation(stderr)
    return {"recipe_id": "OFFICIAL_NOTEBOOK_CELL_2",
            "wheel_references": [wheel["dataset_filename"] for wheel in ordered], "argv": argv,
            "executed": True, "return_code": return_code,
            "stdout_byte_count": stdout_size, "stderr_byte_count": stderr_size,
            "stdout_sha256": stdout_hash, "stderr_sha256": stderr_hash, "diagnostic": diagnostic}


def _optional_read(root_fd, path, max_bytes=MAX_PAYLOAD_BYTES):
    directory, basename = path.split("/")
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        os.stat(basename, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    finally:
        os.close(parent)
    return read_regular(root_fd, path, max_bytes=max_bytes)


def _retain_blob(blobs, observed):
    if observed.data is None or observed.size > MAX_PAYLOAD_BYTES:
        raise PolicyError("PAYLOAD_LIMIT")
    if observed.sha256 not in blobs:
        if len(blobs) >= MAX_PAYLOADS or sum(map(len, blobs.values())) + observed.size > MAX_EXPANDED_BYTES:
            raise PolicyError("PAYLOAD_LIMIT")
        blobs[observed.sha256] = observed.data


def _distribution_observations(config, interpreter, blobs):
    distributions, records, observations, seen = [], [], [], set()
    with anchor_directory(config.scripts_directory) as scripts_fd:
        for root in config.site_roots:
            with anchor_directory(root) as root_fd:
                directories = os.listdir(root_fd)
                if len(directories) > 8192:
                    raise PolicyError("DIRECTORY_LIMIT")
                for directory in sorted(directories):
                    if not directory.endswith(".dist-info"):
                        continue
                    matched = [name for name in TARGETS if directory.startswith(name.replace("-", "_") + "-")]
                    if not matched:
                        continue
                    name = matched[0]
                    directory_version = directory[len(name.replace("-", "_")) + 1:-10]
                    if name in seen:
                        raise PolicyError("DISTRIBUTION_DUPLICATE")
                    seen.add(name)
                    metadata_file = read_regular(root_fd, f"{directory}/METADATA", max_bytes=1024 * 1024)
                    metadata = parse_metadata(metadata_file.data, expected_name=name, expected_version=directory_version)
                    direct = _optional_read(root_fd, f"{directory}/direct_url.json", 512 * 1024)
                    entry_points = _optional_read(root_fd, f"{directory}/entry_points.txt", 512 * 1024)
                    distribution = {
                        "name": name, "version": metadata["projection"]["Version"],
                        "installation_root": str(root), "dist_info": directory, "metadata": metadata,
                        "direct_url": {"present": direct is not None, "sha256": direct.sha256 if direct else None,
                                       "size": direct.size if direct else None, "type": "REGULAR" if direct else "ABSENT"},
                        "console_scripts": parse_console_scripts(entry_points.data) if entry_points else [],
                    }
                    record = read_regular(root_fd, f"{directory}/RECORD", max_bytes=MAX_PAYLOAD_BYTES)
                    classified = classify_record_rows(distribution, interpreter, record.data)
                    records.append({"distribution": name, "blob": record.sha256})
                    _retain_blob(blobs, record)
                    for row, category in classified:
                        include = payload_required(distribution, row.path, category)
                        if category == "verified_console_script":
                            observed = read_regular(scripts_fd, row.path.rsplit("/", 1)[-1],
                                                    max_bytes=MAX_PAYLOAD_BYTES, reject_hardlinks=True)
                        elif row.path == f"{directory}/RECORD":
                            observed = record
                        elif row.path == f"{directory}/METADATA":
                            observed = metadata_file
                        elif row.path == f"{directory}/direct_url.json" and direct is not None:
                            observed = direct
                        elif row.path == f"{directory}/entry_points.txt" and entry_points is not None:
                            observed = entry_points
                        else:
                            observed = read_regular(root_fd, row.path,
                                                    max_bytes=MAX_PAYLOAD_BYTES if include else 2 * 2**30,
                                                    include=include)
                        payload = observed.sha256 if include else None
                        if include:
                            _retain_blob(blobs, observed)
                        observations.append({"distribution": name, "path": row.path, "kind": "REGULAR",
                                             "size": observed.size, "sha256": observed.sha256,
                                             "links": observed.nlink, "payload": payload})
                        if len(observations) > 100_000:
                            raise PolicyError("OBSERVATION_LIMIT")
                    distributions.append(distribution)
    return distributions, records, observations


def run_capture(config: CaptureConfig, *, pip_runner=None):
    """Return a new archive path; observations remain untrusted even after import."""
    interpreter = {"python_version": list(config.python_version), "scheme": config.scheme,
                   "prefix": str(config.prefix), "site_packages": [str(root) for root in config.site_roots],
                   "scripts_directory": str(config.scripts_directory), "executable": str(config.executable)}
    validate_interpreter_roots(interpreter)
    wheels = []
    with anchor_directory(config.wheelhouse) as wheel_fd:
        names = os.listdir(wheel_fd)
        if len(names) > 8192:
            raise PolicyError("DIRECTORY_LIMIT")
        for name in sorted(names):
            if name.endswith(".whl") and "cutlass" not in name.lower():
                wheels.append(_wheel_observation(wheel_fd, name))
                if len(wheels) > 256:
                    raise PolicyError("WHEEL_LIMIT")
    if len({wheel["installation_filename"] for wheel in wheels}) != len(wheels):
        raise PolicyError("WHEEL_FILENAME_COLLISION")
    bootstrap = _bootstrap(config, wheels, pip_runner or subprocess.run)
    blobs = {}
    distributions, records, observations = _distribution_observations(config, interpreter, blobs)
    notebook_hash = hashlib.sha256(config.executed_notebook.read_bytes()).hexdigest() if config.executed_notebook else None
    utc = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "protocol": "H23_CAPTURE_PROTOCOL_V4", "captured_utc": utc,
        "capture_program": {"sha256": capture_program_sha256(), "notebook_sha256": notebook_hash},
        "interpreter": interpreter, "bootstrap": bootstrap, "wheels": wheels,
        "distributions": distributions, "record_evidence": records, "file_observations": observations,
        "payload_references": sorted(blobs),
    }
    validate_manifest(manifest)
    archive_bytes = build_archive(manifest, blobs)
    # Fixed member names and content addressing; no remote source-shaped tree.
    with anchor_directory(config.output_directory) as output_fd:
        name = f"h23_capture_v4_{utc}.zip"
        archive_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=output_fd)
        try:
            with os.fdopen(archive_fd, "wb") as stream:
                stream.write(archive_bytes)
        except BaseException:
            os.unlink(name, dir_fd=output_fd)
            raise
    return config.output_directory / name
