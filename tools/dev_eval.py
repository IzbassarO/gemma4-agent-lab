"""Closed, operator-invoked E0/S1 driver; importing this module performs no IO.

Disk admission is an engineering duplication envelope, not a measured peak:
one spare admitted copy for model/public/venvs/private, two sequential worker
copy/extraction envelopes, and two evidence/export envelopes. Reservations are
summed on shared filesystems. Every input size is measured; no guessed GB floor.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from eval._contracts import ContractError, canonical_json
from eval.failure_taxonomy import Failure
from eval.manifests import SCREEN_PATH, SPLIT_PATH, TASKS_SHA256
from tools.common import REPO_ROOT, WriteGuard, iter_files, scan_tree

EXPERIMENT = "EXP-20261007-001"
S1_IDS = (
    "fastapi_14186", "fastapi_14372", "fastapi_14794", "fastapi_14978",
    "fastapi_15763", "fastapi_9555", "httpx_3672", "requests_7502",
    "rich_3468", "rich_3676", "rich_3777", "rich_4079",
)
CONTROL_IDS = ("httpx_3672", "requests_7502", "rich_3468", "fastapi_14186")
PACKAGES = ("swegemma", "adk-submission", "adk-eval-core", "google-adk", "google-genai", "litellm", "vllm")
PINNED = {"swegemma": "0.2.7", "adk-submission": "0.2.12",
          "adk-eval-core": "0.1.0", "google-adk": "1.36.1"}
RETRY_DIAGNOSES = ("MODEL_SERVER_START_FAILURE", "PLATFORM_SCORER_EXCEPTION",
                   "ENVIRONMENT_VERIFICATION_ARTIFACT")
MIN_VRAM_BYTES = 40_000_000_000  # GB as specified; nvidia-smi reports MiB.


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _jsonl_row(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"


def _read(path):
    """Reject links and special evidence; do not follow any ancestor symlink."""
    path = Path(path).absolute()
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ContractError("symlink in evidence path")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ContractError("single-link regular evidence required")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ContractError("evidence changed during open")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read()
        after = os.fstat(descriptor)
        if (after.st_size, after.st_mtime_ns, after.st_nlink) != (
                info.st_size, info.st_mtime_ns, 1):
            raise ContractError("evidence changed during read")
        return data
    finally:
        os.close(descriptor)


def _load(path):
    try:
        value = json.loads(_read(path))
    except (json.JSONDecodeError, UnicodeError):
        raise ContractError("invalid driver evidence JSON") from None
    if type(value) is not dict:
        raise ContractError("driver evidence must be an object")
    from eval.runtime_provenance import assert_secret_free
    assert_secret_free(value)
    return value


def _write(path, value, guard):
    from eval.runtime_provenance import assert_secret_free
    assert_secret_free(value)
    target = guard.check(Path(path))
    guard.write_text(target, canonical_json(value) + "\n")
    target.chmod(0o600)
    target.parent.chmod(0o700)


def _command(argv):
    """No inherited secrets, shell expansion, stdin or implicit environment."""
    return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30,
                          stdin=subprocess.DEVNULL, close_fds=True,
                          env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                               "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1",
                               "PYTHON_DOTENV_DISABLED": "1"})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ContractError("model endpoint redirects are forbidden")


def observe_model_server(endpoint, *, fetch=None):
    """Only unauthenticated admitted loopback GETs, with no proxy or redirects."""
    from eval.real_contracts import normalize_endpoint
    from eval.runtime_provenance import assert_secret_free
    endpoint = normalize_endpoint(endpoint)
    if fetch is None:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        def fetch(url):
            with opener.open(Request(url, method="GET"), timeout=10) as response:
                return response.read(1024 * 1024 + 1)
    observed = {}
    for name, url in (("version", endpoint[:-3] + "/version"),
                      ("models", endpoint + "/models")):
        raw = fetch(url)
        if len(raw) > 1024 * 1024:
            raise ContractError("model identity response exceeds admission bound")
        body = json.loads(raw)
        assert_secret_free(body)
        observed[name] = {"response_sha256": hashlib.sha256(raw).hexdigest(), "body": body}
    model_ids = {row.get("id") for row in observed["models"]["body"].get("data", [])
                 if type(row) is dict}
    if not {"gemma-4-31b-it-qat-w4a16-ct", "main_lora", "tool_lora"} <= model_ids:
        raise ContractError("served model or E0 adapter identity missing")
    return observed


def observe_harness(python_executable, *, command=_command):
    script = ("import importlib.metadata as m,json,platform; "
              "p={n:m.version(n) for n in " + repr(PACKAGES[:-1]) + "};p['vllm']=None;"
              "print(json.dumps({'python_version':platform.python_version(),'packages':p}))")
    value = json.loads(command([str(python_executable), "-I", "-B", "-c", script]).stdout)
    if not str(value.get("python_version", "")).startswith("3.12."):
        raise ContractError("real harness requires Python 3.12")
    if any(value.get("packages", {}).get(name) != pin for name, pin in PINNED.items()):
        raise ContractError("v28 harness package pin mismatch")
    return value


def observe_gpu(*, command=_command):
    rows = command(["/usr/bin/nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits"]).stdout.splitlines()
    gpus = []
    for row in rows:
        fields = [value.strip() for value in row.split(",")]
        if len(fields) != 3:
            raise ContractError("invalid GPU observation")
        gpus.append({"name": fields[0], "memory_mib": int(fields[1]), "driver": fields[2]})
    if len(gpus) != 1 or gpus[0]["memory_mib"] * 1024 * 1024 < MIN_VRAM_BYTES:
        raise ContractError("TP=1 requires one assigned GPU with >=40 GB VRAM")
    return gpus


def _file_identity(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ContractError("identity requires a single-link regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            raise ContractError("file identity changed during open")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = os.fstat(descriptor)
        if (after.st_size, after.st_mtime_ns, after.st_nlink) != (info.st_size, info.st_mtime_ns, 1):
            raise ContractError("file identity changed during read")
        return {"sha256": digest.hexdigest(), "size_bytes": info.st_size}
    finally:
        os.close(descriptor)


def capture_fingerprint(*, repo_root, candidate, prereg, public_root, endpoint,
                        python_executable, harness_lock, model_path, allow_dirty=False,
                        server_observer=observe_model_server, harness_observer=observe_harness,
                        gpu_observer=observe_gpu, vllm_version=None, now=_now):
    from eval.runtime_provenance import build_real_fingerprint
    from eval.runtime_real import validate_candidate
    from eval.real_contracts import normalize_endpoint
    normalize_endpoint(endpoint)
    validate_candidate(candidate)
    identities = {"tasks": _file_identity(public_root / "tasks.jsonl"),
                  "screen": _file_identity(repo_root / "eval/splits/screens_v1.json"),
                  "split": _file_identity(repo_root / "eval/splits/v1.json")}
    if identities["tasks"]["sha256"] != TASKS_SHA256:
        raise ContractError("frozen public dataset identity mismatch")
    if (identities["screen"]["sha256"] != "a6bdb7e9f99c3cbfd59be4130884c3dadcd99d4cfb6a91260bbfcdb1f71832f7"
            or identities["split"]["sha256"] != "420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12"
            or _file_identity(prereg) != _file_identity(repo_root / "docs/experiments/DEV_E0_FORENSICS_V1_S1_PREREG.md")):
        raise ContractError("frozen screen/split/preregistration identity mismatch")
    harness = harness_observer(python_executable)
    harness["packages"]["vllm"] = vllm_version
    fingerprint = build_real_fingerprint(
        repo_root, candidate, prereg, endpoint, phase="driver",
        package_versions=harness["packages"], model_server_identity=server_observer(endpoint),
        public_identities=identities, harness_lock_sha256=_file_identity(harness_lock)["sha256"])
    if fingerprint["eval_infra_source_identity"]["source_dirty"] and not allow_dirty:
        raise ContractError("dirty source requires explicit --allow-dirty fingerprint")
    weights = {p.relative_to(model_path).as_posix(): _file_identity(p)
               for p in iter_files(model_path) if p.suffix == ".safetensors"}
    if not weights:
        raise ContractError("model safetensor identity is unobserved")
    fingerprint.update({"captured_at": now(), "harness_python": harness["python_version"],
                        "gpu": gpu_observer(), "model_weights": weights,
                        "seed_forwarding": "unsupported", "tp": 1, "max_model_len": 32768})
    return fingerprint


def validate_fingerprint(fingerprint, expected, *, now=None):
    from eval.runtime_provenance import FINGERPRINT_FIELDS, validate_real_fingerprint
    driver_fields = {"captured_at", "harness_python", "gpu", "model_weights", "seed_forwarding", "tp", "max_model_len"}
    if set(fingerprint) != set(FINGERPRINT_FIELDS) | driver_fields:
        raise ContractError("driver fingerprint schema is incomplete or unknown")
    validate_real_fingerprint({k: fingerprint[k] for k in FINGERPRINT_FIELDS}, phase="driver")
    for key in ("eval_infra_source_identity", "candidate_identity", "preregistration_identity",
                "model_endpoint_identity", "public_identities", "harness_lock_sha256",
                "model_weights", "gpu", "harness_python", "package_versions", "tp", "max_model_len"):
        if fingerprint.get(key) != expected.get(key):
            raise ContractError("stale or mismatched driver fingerprint: " + key)
    saved_server = fingerprint.get("model_server_identity", {})
    current_server = expected.get("model_server_identity", {})
    if saved_server.get("version", {}).get("body") != current_server.get("version", {}).get("body"):
        raise ContractError("model server version changed after fingerprint")
    if fingerprint["eval_infra_source_identity"]["source_dirty"]:
        raise ContractError("real execution requires clean admitted source")
    try:
        captured = datetime.fromisoformat(fingerprint["captured_at"])
        age = ((now or datetime.now(timezone.utc)) - captured).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise ContractError("fingerprint timestamp missing or invalid") from None
    if not 0 <= age <= 12 * 3600:
        raise ContractError("fingerprint is stale (>12 hours) or future dated")
    if (fingerprint.get("tp") != 1 or fingerprint.get("max_model_len") != 32768
            or not str(fingerprint.get("harness_python", "")).startswith("3.12.")
            or len(fingerprint.get("gpu", [])) != 1
            or fingerprint["gpu"][0].get("memory_mib", 0) * 1024 * 1024 < MIN_VRAM_BYTES
            or not fingerprint.get("model_weights") or not fingerprint.get("harness_lock_sha256")):
        raise ContractError("fingerprint lacks admitted real hardware/model/runtime identity")
    return fingerprint


def _size(root):
    # Venv interpreter links are normal; this is a disk measurement, not an
    # admission of bytes through those links. Count their own directory-entry
    # sizes and never traverse targets.
    files, links = scan_tree(root)
    return sum(path.lstat().st_size for path in (*files, *links))


def _nearest_existing(path):
    path = Path(path).absolute()
    while not path.exists():
        path = path.parent
    if path.is_symlink():
        raise ContractError("disk root is a symlink")
    return path


def admit_disk_space(roots, footprint, *, disk_usage=shutil.disk_usage, device=None):
    """Actual-size reservations; aggregate shared filesystems, inspect every role.

    `footprint` records model/public/private/harness/vllm plus worker peak and
    evidence export bytes. Evidence reserve equals admitted public+candidate
    bytes; it is a conservative engineering envelope, not an observed peak.
    """
    required = {"model", "public", "private", "harness", "vllm", "worker", "export"}
    if set(roots) not in (required, required | {"artifact"}) or set(footprint) != set(roots):
        raise ContractError("disk admission requires every runtime storage role")
    if any(type(v) is not int or v < 0 for v in footprint.values()):
        raise ContractError("invalid admitted disk footprint")
    devices, rows = {}, []
    for role in sorted(roots):
        root = _nearest_existing(roots[role])
        key = device(root) if device else root.stat().st_dev
        reserve = footprint[role] * (2 if role in ("worker", "export") else 1)
        devices[key] = devices.get(key, 0) + reserve
        rows.append({"role": role, "root": str(roots[role]), "filesystem": str(key),
                     "admitted_bytes": footprint[role], "reserve_bytes": reserve,
                     "free_bytes": disk_usage(root).free})
    for row in rows:
        row["minimum_free_bytes"] = next(total for key, total in devices.items() if str(key) == row["filesystem"])
        if row["free_bytes"] < row["minimum_free_bytes"]:
            raise ContractError("disk admission below derived minimum for " + row["role"])
    return {"basis": "admitted footprint duplication envelope; not measured runtime peak",
            "filesystems": rows}


def measure_footprint(*, model, public, private, harness, vllm, tasks, candidate):
    expanded = []
    for task in tasks:
        with tarfile.open(public / task.snapshot.source_relative_path, "r:*") as archive:
            expanded.append(sum(member.size for member in archive if member.isfile()))
    public_bytes = _size(public)
    private_bound = (public / "tasks.jsonl").stat().st_size + sum(
        len(canonical_json(task.to_dict()).encode()) for task in tasks)
    return {"model": _size(model), "public": public_bytes,
            "private": max(_size(private) if private.exists() else 0, private_bound),
            "harness": _size(harness), "vllm": _size(vllm),
            "worker": max(expanded, default=0) + public_bytes + candidate.stat().st_size,
            "export": public_bytes + candidate.stat().st_size}


def build_serve_argv(*, candidate, model_path, vllm_root, out, guard,
                    tp=1, gpu_mem=0.80, native=None, harness_root=None,
                    source_wheels_root=None, harness_lock=None):
    from eval.runtime_real import extract_candidate, validate_candidate
    if tp != 1 or gpu_mem != 0.80:
        raise ContractError("frozen server admission requires TP=1 and gpu-mem=0.80")
    validate_candidate(candidate)
    executable = Path(vllm_root) / "bin/python"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ContractError("explicit vLLM venv Python executable unavailable")
    # Keep the extracted adapters alive for the later operator launch. Native
    # argv records cannot point into a temporary directory deleted on exit.
    candidate_dir = guard.check(Path(out) / "server_candidate")
    if candidate_dir.exists():
        raise ContractError("server candidate extraction refuses overwrite")
    candidate_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    extract_candidate(candidate, candidate_dir)
    if native is None:
        if harness_root is None or source_wheels_root is None or harness_lock is None:
            raise ContractError("native argv generation requires admitted harness/root/source wheels/lock")
        if Path(sys.prefix).absolute() != Path(harness_root).absolute():
            raise ContractError("serve-argv must run under the admitted harness interpreter")
        from eval.runtime_real import validate_harness_environment
        validate_harness_environment(harness_root, source_wheels_root,
                                     expected_lock_sha256=_file_identity(harness_lock)["sha256"])
        observe_harness(Path(sys.executable))
        from adk_submission import discover_adapters
        from adk_submission.server import VllmConfig, VllmServer
    else:
        discover_adapters, VllmConfig, VllmServer = native
    config = VllmConfig(model=str(model_path), max_model_len=32768,
                        tensor_parallel_size=1, gpu_memory_utilization=0.80,
                        tool_call_parser="gemma4", reasoning_parser="gemma4",
                        default_chat_template_kwargs={"enable_thinking": True}, enable_lora=True,
                        extra_args=["--served-model-name", "gemma-4-31b-it-qat-w4a16-ct"])
    server = VllmServer(config, adapter_manifest=discover_adapters(candidate_dir))
    original = server.build_cmd()
    if not (type(original) is list and len(original) >= 3 and original[1:3] == ["-m", "vllm.entrypoints.openai.api_server"]):
        raise ContractError("unexpected native vLLM argv executable contract")
    executed = [str(executable.absolute()), *original[1:]]
    value = {"schema_version": 1, "original_argv": original, "executed_argv": executed,
             "executable_substitution": {"index": 0, "original": original[0], "executed": executed[0]},
             "candidate_identity": _file_identity(candidate), "candidate_dir": str(candidate_dir),
             "model_path": str(model_path), "max_model_len": 32768, "tp": 1,
             "server_started": False}
    _write(Path(out) / "serve_argv.json", value, guard)
    return value


def control_passes(control, result):
    """Exact preregistered criteria; unknown evidence never passes no-patch."""
    if control == "known_patch":
        return result.get("resolved") is True
    if control != "no_patch" or result.get("resolved") is not False:
        return False
    verifier = result.get("verifier_result", {})
    native = json.loads(verifier["native_result_json"]) if verifier.get("native_result_json") else {}
    observations = result.get("verification_observations", {})
    apply_status = observations.get("apply_status", native.get("patch_applied"))
    apply_error = observations.get("apply_error", native.get("apply_error"))
    # Native public TaskResult uses patch_applied and test_results; both must
    # have actual observations. Exit code is optional if required-test failure
    # itself was observed by the verifier.
    required = observations.get("required_tests_passed", native.get("tests_passed"))
    exit_code = native.get("test_exit_code", native.get("exit_code"))
    no_apply_error = apply_error in (None, "") and apply_status in (True, "not_needed", "applied")
    tests_failed = (type(exit_code) is int and exit_code != 0) or required is False
    return no_apply_error and tests_failed


def _control_observations(result, expected=None):
    root = Path(result["run_root"])
    from eval.runtime_result import VerifierRunResult
    verifier = VerifierRunResult.from_dict(result["verifier_result"])
    if verifier.fingerprint is None:
        raise ContractError("verifier control fingerprint missing")
    from eval.runtime_provenance import validate_real_fingerprint
    real_fingerprint = root / "verifier/real_fingerprint.json"
    if "real_fingerprint.json" not in {ref.relative_path for ref in verifier.artifacts}:
        raise ContractError("verifier control rich fingerprint has no seal")
    phase_fingerprint = validate_real_fingerprint(_load(real_fingerprint), phase="verifier")
    if expected is not None:
        for key in ("eval_infra_source_identity", "candidate_identity", "preregistration_identity", "model_endpoint_identity"):
            if phase_fingerprint[key] != expected[key]:
                raise ContractError("control fingerprint disagrees with driver")
    if (root / "solver").exists() or result.get("solver_started") is not False:
        raise ContractError("verifier control cannot contain solver execution evidence")
    for ref in verifier.artifacts:
        info = _file_identity(root / "verifier" / ref.relative_path)
        if info != {"sha256": ref.sha256, "size_bytes": ref.size_bytes}:
            raise ContractError("control verifier artifact seal mismatch")
    observations = root / "verifier/native_observations.json"
    if observations.exists() and "native_observations.json" not in {ref.relative_path for ref in verifier.artifacts}:
        raise ContractError("control observations are missing their artifact seal")
    return {**result, "verification_observations": _load(observations) if observations.exists() else {}}


def run_controls(tasks, verifier_tasks, *, control, public_root, private_root,
                 config, guard, fingerprint, run_control=None, reference_reader=None):
    if control not in ("no_patch", "known_patch") or tuple(t.instance_id for t in tasks) != CONTROL_IDS:
        raise ContractError("only frozen four verifier-control tasks are admitted")
    if run_control is None:
        from eval.runtime import run_verifier_control as run_control
    if reference_reader is None:
        from eval.verifier_data import read_reference_patch as reference_reader
    root = guard.check(config.artifact_root / "controls" / control)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    marker = {"schema_version": 1, "verifier_only": True, "solver_started": False,
              "gold_assisted": control == "known_patch", "excluded_from_normal_reports": True,
              "excluded_from_candidate_comparison": True, "excluded_from_designer_inspection": True,
              "excluded_from_solver_inspection": True,
              "gold_assisted_diagnostics": list(CONTROL_IDS) if control == "known_patch" else [],
              "fingerprint_sha256": _digest(fingerprint)}
    _write(root / "control_manifest.json", marker, guard)
    if control == "known_patch":
        _write(root / "GOLD_ASSISTED_VERIFIER_ONLY.json", marker, guard)
    results = []
    for task in tasks:
        patch = "" if control == "no_patch" else reference_reader(public_root, task.instance_id)
        observed = run_control(task, verifier_tasks[task.instance_id], public_root=public_root,
                               private_root=private_root, test_patch_relative_path=task.instance_id + ".test.patch",
                               config=replace(config, artifact_root=root), returned_patch=patch, control=control)
        if observed.get("solver_started") is not False:
            raise ContractError("control attempted solver execution")
        results.append({"task_id": task.instance_id, "control": control,
                        "passed": control_passes(control, _control_observations(observed, fingerprint)), "run_root": observed["run_root"],
                        "control_result_sha256": _file_identity(Path(observed["run_root"]) / "control_result.json")["sha256"]})
    value = {**marker, "control": control, "results": results,
             "passed": all(row["passed"] for row in results)}
    _write(root / "controls.json", value, guard)
    controls_path = config.artifact_root / "controls.json"
    previous = _load(controls_path) if controls_path.exists() else {"schema_version": 1, "controls": {}}
    previous["controls"][control] = value
    _write(controls_path, previous, guard)
    return value


def validate_controls(root, fingerprint):
    controls = _load(root / "controls.json").get("controls", {})
    if set(controls) != {"no_patch", "known_patch"}:
        raise ContractError("all eight preregistered controls are required")
    for control, value in controls.items():
        if value.get("passed") is not True or value.get("fingerprint_sha256") != _digest(fingerprint):
            raise ContractError("controls failed or have mismatched fingerprint")
        if tuple(row["task_id"] for row in value["results"]) != CONTROL_IDS:
            raise ContractError("control membership mismatch")
        for row in value["results"]:
            if row.get("passed") is not True:
                raise ContractError("failed verifier control")
            sealed = Path(row["run_root"]) / "control_result.json"
            if not Path(row["run_root"]).is_relative_to(root / "controls" / control):
                raise ContractError("control root escapes verifier-only evidence")
            if _file_identity(sealed)["sha256"] != row["control_result_sha256"]:
                raise ContractError("control seal changed")
            if not control_passes(control, _control_observations(_load(sealed), fingerprint)):
                raise ContractError("sealed control does not satisfy preregistered criterion")


def _private_publication(public_root, ids, private_root, guard, verification, solver_tasks):
    """Resume admitted refs without reopening private bytes before solver exit."""
    from eval.verifier_data import publish_private_test_patches
    from eval.verifier_task import VerifierTask
    manifest = private_root / "publication.json"
    expected = {"schema_version": 1, "task_ids": list(ids),
                "source_identity": _file_identity(public_root / "tasks.jsonl"),
                "verification_config_sha256": _digest(verification)}
    by_id = {task.instance_id: task for task in solver_tasks}
    if manifest.exists():
        saved = _load(manifest)
        if any(saved.get(key) != value for key, value in expected.items()):
            raise ContractError("private publication belongs to a different source/config")
        refs = saved.get("verifier_tasks")
        if type(refs) is not dict or set(refs) != set(ids):
            raise ContractError("private publication task membership mismatch")
        admitted = {task_id: VerifierTask.from_dict(refs[task_id]) for task_id in ids}
        for task_id, task in admitted.items():
            solver = by_id[task_id]
            if ((task.instance_id, task.repo, task.base_commit, task.snapshot)
                    != (solver.instance_id, solver.repo, solver.base_commit, solver.snapshot)
                    or task.verification_config_sha256 != expected["verification_config_sha256"]):
                raise ContractError("resumed verifier/public contract mismatch")
            info = (private_root / (task_id + ".test.patch")).lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != task.test_patch.size_bytes):
                raise ContractError("resumed private file metadata mismatch")
        return admitted
    admitted = publish_private_test_patches(public_root, ids, private_root,
                                            guard=guard, verification_config=verification)
    _write(manifest, {**expected, "verifier_tasks": {k: v.to_dict() for k, v in admitted.items()}}, guard)
    return admitted


def _ledger(root):
    path = root / "ledger.jsonl"
    if not path.exists():
        return []
    from eval.runtime_provenance import assert_secret_free
    rows = [json.loads(line) for line in _read(path).splitlines() if line]
    assert_secret_free(rows)
    return rows


def _append_ledger(root, row, guard):
    from eval.runtime_provenance import assert_secret_free
    assert_secret_free(row)
    path = guard.check(root / "ledger.jsonl")
    existing = _read(path) if path.exists() else b""
    guard.write_text(path, existing.decode() + _jsonl_row(row))
    path.chmod(0o600)


def _result_root(result, root):
    prefixes = {ref.relative_path.split("/")[0] for ref in result.artifacts}
    if len(prefixes) != 1:
        raise ContractError("task result must identify one sealed run root")
    run_root = root / prefixes.pop()
    if not run_root.is_relative_to(root):
        raise ContractError("task run root escapes artifact root")
    return run_root


def validate_task_seal(seal, artifact_root, fingerprint):
    from eval.runtime_result import TaskRuntimeResult
    from eval.runtime_provenance import crosscheck_real_fingerprints
    if seal.get("fingerprint_sha256") != _digest(fingerprint):
        raise ContractError("attempt belongs to a different fingerprint")
    relative = seal.get("run_root")
    if type(relative) is not str or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ContractError("invalid sealed run root")
    run_root = artifact_root / relative
    raw = _read(run_root / "task_result.json")
    if hashlib.sha256(raw).hexdigest() != seal.get("task_result_sha256"):
        raise ContractError("task result seal mismatch")
    result = TaskRuntimeResult.from_json(raw)
    if result.task_id != seal.get("task_id"):
        raise ContractError("sealed task identity mismatch")
    if result.solver_result.fingerprint is None or result.verifier_result is None or result.verifier_result.fingerprint is None:
        raise ContractError("both phase fingerprints are mandatory")
    phase_fingerprints = []
    for ref in result.artifacts:
        info = _file_identity(artifact_root / ref.relative_path)
        if info != {"sha256": ref.sha256, "size_bytes": ref.size_bytes}:
            raise ContractError("task phase result seal mismatch")
    for phase, observed in (("solver", result.solver_result), ("verifier", result.verifier_result)):
        phase_root = run_root / phase
        fp = _load(phase_root / "real_fingerprint.json")
        if "real_fingerprint.json" not in {ref.relative_path for ref in observed.artifacts}:
            raise ContractError("phase fingerprint has no artifact seal")
        phase_fingerprints.append(fp)
        for ref in observed.artifacts:
            info = _file_identity(phase_root / ref.relative_path)
            if info != {"sha256": ref.sha256, "size_bytes": ref.size_bytes}:
                raise ContractError("phase artifact seal mismatch")
    crosscheck_real_fingerprints(*phase_fingerprints)
    for fp in phase_fingerprints:
        for key in ("eval_infra_source_identity", "candidate_identity", "preregistration_identity", "model_endpoint_identity"):
            if fp.get(key) != fingerprint.get(key):
                raise ContractError("task fingerprint disagrees with driver")
    return result


def _seal_path(root, task_id, attempt):
    return root / "attempts" / task_id / (str(attempt) + ".json")


def run_s1(tasks, verifier_tasks, *, public_root, private_root, config, guard,
           fingerprint, attempt=1, retry_ids=(), retry_diagnosis=None,
           task_runner=None, scanner=None, controls_validator=validate_controls):
    """Sequential closed S1 only. Injected callables permit offline CPU fixtures."""
    if tuple(t.instance_id for t in tasks) != S1_IDS:
        raise ContractError("driver accepts frozen S1 task order only; no holdout mode")
    validate_fingerprint(fingerprint, fingerprint)
    if type(attempt) is not int or attempt not in (1, 2):
        raise ContractError("preregistration admits initial attempt or diagnosed attempt 2")
    if attempt == 2 and (not retry_ids or retry_diagnosis not in RETRY_DIAGNOSES):
        raise ContractError("attempt 2 requires selected task IDs and diagnosed platform/environment failure")
    if attempt == 1 and (retry_ids or retry_diagnosis is not None):
        raise ContractError("initial attempt cannot carry retry diagnosis")
    if not set(retry_ids) <= set(S1_IDS):
        raise ContractError("retry task is outside S1")
    root = guard.check(config.artifact_root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    controls_validator(root, fingerprint)
    from eval.solver_task import SolverTask
    solver_contracts = {task.instance_id: task.to_dict() for task in tasks if type(task) is SolverTask}
    if solver_contracts and len(solver_contracts) != len(tasks):
        raise ContractError("mixed admitted and fixture solver task contracts")
    manifest = {"schema_version": 1, "experiment": EXPERIMENT, "screen": "S1", "concurrency": 1,
                "task_ids": list(S1_IDS), "fingerprint_sha256": _digest(fingerprint),
                "gold_assisted": False, "gold_assisted_diagnostics": list(CONTROL_IDS),
                "solver_contracts": solver_contracts}
    manifest_path = root / "run_manifest.json"
    if manifest_path.exists() and _load(manifest_path) != manifest:
        raise ContractError("existing run manifest has a different frozen identity")
    if not manifest_path.exists():
        _write(manifest_path, manifest, guard)
    if task_runner is None:
        from eval.runtime import run_task as task_runner
    if scanner is None:
        scanner = lambda: leak_scan(root, public_root, guard=guard)
    previous = _ledger(root)
    if any(row.get("status") == "p0_stopped" for row in previous):
        raise ContractError("P0 invalidated this run; resume is forbidden")
    scan_path = root / "leak_scan.json"
    if scan_path.exists() and _load(scan_path).get("p0") is True:
        raise ContractError("P0 leak scan invalidated this run; resume is forbidden")
    for task in tasks:
        if attempt == 2 and task.instance_id not in retry_ids:
            continue
        seal_path = _seal_path(root, task.instance_id, attempt)
        if seal_path.exists():
            validate_task_seal(_load(seal_path), root, fingerprint)
            continue
        old = [row for row in previous if row.get("task_id") == task.instance_id]
        if attempt == 1:
            later_path = _seal_path(root, task.instance_id, 2)
            if later_path.exists():
                later = _load(later_path)
                if later.get("task_id") != task.instance_id or later.get("attempt") != 2:
                    raise ContractError("later attempt seal identity mismatch")
                validate_task_seal(later, root, fingerprint)
                continue
        same_attempt = [row for row in old if row.get("attempt") == attempt]
        if same_attempt:
            if (same_attempt[-1].get("status") == "failed"
                    and any(row.get("status") == "started" for row in same_attempt)):
                # A recorded terminal failure is retained, never rerun under
                # the same attempt number. Later never-started tasks may run.
                continue
            raise ContractError("unsealed earlier start cannot be silently resumed")
        if attempt == 2:
            first_path = _seal_path(root, task.instance_id, 1)
            if first_path.exists():
                first = validate_task_seal(_load(first_path), root, fingerprint)
                if first.resolved is True:
                    raise ContractError("resolved task cannot be rerun as environment repair")
            elif not old:
                raise ContractError("attempt 2 requires retained initial attempt evidence")
        started = _now()
        row = {"task_id": task.instance_id, "attempt": attempt, "phase": "task", "status": "started",
               "started_at": started, "finished_at": None, "run_root": None,
               "retry_diagnosis": retry_diagnosis, "fingerprint_sha256": _digest(fingerprint)}
        _append_ledger(root, row, guard)
        before_roots = {path.name for path in root.iterdir() if path.is_dir()}
        try:
            result = task_runner(task, verifier_tasks[task.instance_id], public_root=public_root,
                                 private_root=private_root, test_patch_relative_path=task.instance_id + ".test.patch", config=config)
            run_root = _result_root(result, root)
            seal = {"schema_version": 1, "task_id": task.instance_id, "attempt": attempt,
                    "fingerprint_sha256": _digest(fingerprint), "run_root": run_root.relative_to(root).as_posix(),
                    "task_result_sha256": _file_identity(run_root / "task_result.json")["sha256"]}
            validate_task_seal(seal, root, fingerprint)
            _write(seal_path, seal, guard)
            finding = scanner()
            status = "p0_stopped" if finding.get("p0") else "completed"
            _append_ledger(root, {**row, "status": status, "finished_at": _now(), "run_root": seal["run_root"]}, guard)
            if finding.get("p0"):
                break
        except Exception:
            # Do not serialize arbitrary exception text (may contain credentials
            # or private bytes); phase evidence remains at its original root.
            partial = [path.name for path in root.iterdir() if path.is_dir()
                       and path.name not in before_roots and (path / "solver").exists()]
            _append_ledger(root, {**row, "status": "failed", "finished_at": _now(),
                                 "error": "UNSEALED_RUNTIME_FAILURE", "unsealed_run_roots": sorted(partial)}, guard)
            break
    scanner()
    summary = write_report(root, guard=guard, fingerprint=fingerprint)
    latest = {row["task_id"]: row["status"] for row in _ledger(root)}
    summary["stopped"] = any(status in ("failed", "p0_stopped") for status in latest.values())
    _write(root / "summary.json", summary, guard)
    return summary


def _added_lines(patch):
    return [line[1:].encode() for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
            and len("".join(line[1:].split())) >= 40]


def _trusted_patch_lines(public_root, task_id, solver_task):
    # No public-view widening: this is an explicitly trusted post-solver scan.
    from eval.solver_task import SolverTask
    from tools.h23_v4.filesystem import anchor_directory, read_regular
    from tools.h23_v4.schema import PolicyError
    if type(solver_task) is not SolverTask or solver_task.instance_id != task_id:
        raise ContractError("private scan requires original admitted solver contract")
    with anchor_directory(public_root) as fd:
        raw = read_regular(fd, "tasks.jsonl", max_bytes=32 * 1024 * 1024,
                           include=True, reject_hardlinks=True)
    if raw.sha256 != TASKS_SHA256:
        raise ContractError("leak scan dataset identity mismatch")
    row = next((r for r in (json.loads(line) for line in raw.data.splitlines() if line)
                if r.get("instance_id") == task_id), None)
    if row is None or type(row.get("test_patch")) is not str:
        raise ContractError("private scan task identity missing")
    if type(row.get("patch")) is not str:
        raise ContractError("reference scan task identity missing")
    candidates = set(_added_lines(row["test_patch"]))
    snapshot = solver_task.snapshot
    try:
        with anchor_directory(public_root) as fd:
            observed = read_regular(fd, snapshot.source_relative_path, max_bytes=snapshot.size_bytes,
                                    include=True, reject_hardlinks=False)
    except PolicyError:
        raise ContractError("public snapshot differs from admitted contract authority") from None
    if (observed.sha256, observed.size) != (snapshot.sha256, snapshot.size_bytes):
        raise ContractError("public snapshot differs from admitted contract authority")
    remaining = set(candidates)
    try:
        with tarfile.open(fileobj=io.BytesIO(observed.data), mode="r:gz") as archive:
            for member in archive:
                if not remaining:
                    break
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise ContractError("public snapshot file cannot be read")
                overlap = max(map(len, remaining)) - 1
                tail = b""
                with stream:
                    while chunk := stream.read(1024 * 1024):
                        data = tail + chunk
                        remaining.difference_update(needle for needle in tuple(remaining) if needle in data)
                        if not remaining:
                            break
                        tail = data[-overlap:] if overlap else b""
    except (tarfile.TarError, OSError, EOFError):
        raise ContractError("public snapshot cannot support trusted private-line exclusion") from None
    return {"test_patch": sorted(remaining), "patch": _added_lines(row["patch"]),
            "private_needle_diagnostics": {
                "total_candidate_private_needle_count": len(candidates),
                "excluded_public_needle_count": len(candidates) - len(remaining),
                "effective_private_needle_count": len(remaining),
                "scan_sensitivity": "exact_private_lines" if remaining else "none",
                "snapshot_identity": snapshot.to_dict()}}


def _is_control(root):
    return (root / "control_manifest.json").exists() or (root / "GOLD_ASSISTED_VERIFIER_ONLY.json").exists()


def _json_string_views(data):
    """Decode JSON/JSONL string payloads without duplicating encoded parents.

    Native result fields can themselves contain serialized JSON. Decode those
    containers recursively, retaining each leaf string/key once. Non-JSON log
    lines remain covered by the scanner's independent raw-byte view.
    """
    def decode(value):
        # Keep duplicate object keys: dropping an earlier value could conceal
        # a private line in an unsealed structured process log.
        return json.loads(value, object_pairs_hook=lambda pairs: [[key, child] for key, child in pairs])
    def strings(value, depth=0):
        if depth > 128:
            raise ContractError("solver JSON nesting exceeds leakage scan bound")
        if type(value) is dict:
            for key, child in value.items():
                yield from strings(key, depth + 1)
                yield from strings(child, depth + 1)
        elif type(value) is list:
            for child in value:
                yield from strings(child, depth + 1)
        elif type(value) is str:
            if depth < 32:
                try:
                    decoded = decode(value)
                except (ValueError, RecursionError):
                    decoded = None
                if type(decoded) in (dict, list, str) and decoded != value:
                    yield from strings(decoded, depth + 1)
                    return
            yield value.encode("utf-8")
    try:
        decoded = decode(data)
    except (ValueError, UnicodeError, RecursionError):
        for line in data.splitlines():
            try:
                decoded = decode(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            yield from strings(decoded)
    else:
        yield from strings(decoded)


def leak_scan(root, public_root, *, guard, patch_lines=None):
    if _is_control(root):
        raise ContractError("gold-assisted/verifier-only roots excluded from experiment scan")
    manifest = _load(root / "run_manifest.json")
    if manifest.get("screen") != "S1" or tuple(manifest.get("task_ids", ())) != S1_IDS:
        raise ContractError("leak scan requires frozen S1 experiment manifest")
    hits, overlaps, assigned_roots = [], [], {}
    for seal_path in sorted((root / "attempts").glob("*/*.json")):
        seal = _load(seal_path)
        task_id = seal["task_id"]
        if task_id not in S1_IDS:
            raise ContractError("scan task outside S1")
        relative = seal["run_root"]
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ContractError("scan root escapes experiment")
        assigned_roots[relative] = (task_id, seal["attempt"])
    ledger = _ledger(root)
    for row in ledger:
        if row.get("status") != "failed":
            continue
        task_id = row.get("task_id")
        if task_id not in S1_IDS:
            raise ContractError("partial scan task outside S1")
        for relative in row.get("unsealed_run_roots", []):
            if type(relative) is not str or Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ContractError("partial scan root escapes experiment")
            assigned_roots[relative] = (task_id, row["attempt"])
    actual_roots = {path.name for path in root.iterdir() if path.is_dir() and (path / "solver").exists()}
    for relative in actual_roots - set(assigned_roots):
        identity_path = root / relative / "run_identity.json"
        fingerprint_path = root / "fingerprint.json"
        if not identity_path.exists() or not fingerprint_path.exists():
            continue
        identity = _load(identity_path)
        fingerprint = _load(fingerprint_path)
        if (identity.get("task_id") not in S1_IDS
                or identity.get("candidate_sha256") != fingerprint["candidate_identity"]["sha256"]
                or identity.get("preregistration_sha256") != fingerprint["preregistration_identity"]["sha256"]
                or _digest(fingerprint) != manifest.get("fingerprint_sha256")):
            raise ContractError("partial run identity differs from admitted experiment")
        starts = [row for row in ledger if row.get("task_id") == identity["task_id"]
                  and row.get("status") == "started" and row.get("fingerprint_sha256") == _digest(fingerprint)]
        if starts:
            assigned_roots[relative] = (identity["task_id"], starts[-1]["attempt"])
    if not actual_roots <= set(assigned_roots):
        raise ContractError("unattributed solver evidence prevents exhaustive leak scan")
    admitted = {}
    if patch_lines is None:
        from eval.solver_task import SolverTask
        contracts = manifest.get("solver_contracts")
        if type(contracts) is not dict:
            raise ContractError("leak scan requires original admitted solver contracts")
        for task_id in {task_id for task_id, _ in assigned_roots.values()}:
            if task_id not in contracts:
                raise ContractError("scan task lacks original admitted solver contract")
            task = SolverTask.from_dict(contracts[task_id])
            if task.instance_id != task_id:
                raise ContractError("solver contract differs from task admission")
            admitted[task_id] = task
    task_lines, diagnostics = {}, {}
    for relative, (task_id, attempt) in sorted(assigned_roots.items()):
        solver_root = root / relative / "solver"
        if patch_lines is None:
            task = admitted[task_id]
            identity_path = root / relative / "run_identity.json"
            if identity_path.exists() and _load(identity_path).get("solver_contract_sha256") != task.sha256():
                raise ContractError("solver contract differs from original run admission")
            result_path = root / relative / "task_result.json"
            if result_path.exists():
                from eval.runtime_result import TaskRuntimeResult
                result = TaskRuntimeResult.from_json(_read(result_path))
                if result.solver_result.fingerprint.solver_contract_sha256 != task.sha256():
                    raise ContractError("solver contract differs from sealed phase admission")
                phase_snapshot = _load(solver_root / "real_fingerprint.json")["public_identities"].get("snapshot")
                if phase_snapshot != task.snapshot.to_dict():
                    raise ContractError("snapshot contract differs from sealed phase admission")
            if task_id not in task_lines:
                task_lines[task_id] = _trusted_patch_lines(public_root, task_id, task)
        elif task_id not in task_lines:
            task_lines[task_id] = patch_lines(public_root, task_id)
        lines = task_lines[task_id]
        effective_count = len(set(lines.get("test_patch", ())))
        diagnostics[task_id] = {"task_id": task_id, **lines.get("private_needle_diagnostics", {
            "total_candidate_private_needle_count": effective_count,
            "excluded_public_needle_count": 0, "effective_private_needle_count": effective_count,
            "scan_sensitivity": "exact_private_lines" if effective_count else "none"})}
        for path in iter_files(solver_root):
            data = _read(path)
            decoded_views = tuple(_json_string_views(data))
            for kind in ("test_patch", "patch"):
                needles = lines.get(kind, ())
                for needle in sorted(set(needles)):
                    count = max(data.count(needle), sum(view.count(needle) for view in decoded_views))
                    if count:
                        finding = {"task_id": task_id, "attempt": attempt,
                                   "artifact": path.relative_to(root).as_posix(),
                                   "line_sha256": hashlib.sha256(needle).hexdigest(), "count": count}
                        (hits if kind == "test_patch" else overlaps).append(finding)
    value = {"schema_version": 1, "p0": bool(hits), "test_patch_hits": hits,
             "private_needle_diagnostics": [diagnostics[task_id] for task_id in sorted(diagnostics)],
             "patch_overlap": overlaps, "patch_overlap_is_leakage": False,
             "count_basis": "maximum raw-byte or decoded JSON string occurrences per artifact; views are not added",
             "private_reference_openings": {"task_ids": sorted({task_id for task_id, _ in assigned_roots.values()}),
                                             "purpose": "trusted post-solver boundary scan; no reference bytes exported"},
             "observation_limit": "reference overlap is diagnostic; exact-line scanning does not prove absence of leakage, especially when scan_sensitivity is none"}
    _write(root / "leak_scan.json", value, guard)
    return value


def forensic_record(task_id, *, index, result=None, repo=None, base_commit=None, attempt=None, started=False):
    """Observed process outcomes only; manual quality judgments stay UNKNOWN."""
    solver = result.solver_result if result else None
    verifier = result.verifier_result if result else None
    resolved = result.resolved if result else None
    primary = None if resolved is True else Failure.UNKNOWN.value
    if solver is not None and resolved is not True:
        if solver.escaped_exception:
            primary = Failure.UNKNOWN.value
        elif solver.timeout is True:
            primary = Failure.AGENT_TIMEOUT.value
        elif solver.budget_exhausted is True:
            primary = Failure.TOOL_BUDGET_EXHAUSTED.value
        elif solver.returned_patch == "":
            primary = Failure.NO_PATCH.value
    reason = ("started without trustworthy sealed result" if started else "not started") if result is None else "not exposed by sealed native observation; manual review required"
    unknown = {"value": None, "reason": reason}
    return {"identity": {"experiment": EXPERIMENT, "instance_id": task_id, "repo": repo,
                         "base_commit": base_commit, "screen": "S1", "task_order_index": index, "attempt": attempt},
            "outcome": {"resolved": resolved, "verifier_status": verifier.runtime_status if verifier else None,
                        "verifier_error": verifier.error if verifier else None,
                        "patch_nonempty": bool(solver.returned_patch) if solver else None,
                        "returned_patch_sha256": solver.returned_patch_sha256 if solver else None,
                        "returned_patch_bytes": len(solver.returned_patch.encode()) if solver else None,
                        "terminal_reason": solver.terminal_reason if solver else None,
                        "fallback_used": solver.fallback_used if solver else None,
                        "timeout": solver.timeout if solver else None,
                        "budget_exhausted": solver.budget_exhausted if solver else None,
                        "explicit_submit_patch_observed": True if solver and solver.submitted_patch is not None else None,
                        "changed_paths": unknown, "apply_status": unknown, "required_tests": unknown},
            "time": {"session": unknown, "setup": unknown,
                     "solver_total_seconds": solver.elapsed_seconds if solver else None,
                     "verification_seconds": verifier.elapsed_seconds if verifier else None,
                     "elapsed_at_first_edit": unknown, "start_utc": solver.started_at if solver else None,
                     "end_utc": unknown},
            "model": {"llm_calls": solver.llm_calls if solver else None, "completed_turns": unknown,
                      "partial_turns": unknown, "finish_reasons": unknown, "nudges": unknown, "tokens": unknown},
            "tools": {"attempted": solver.tool_attempts if solver else None,
                      "counted": solver.counted_tool_calls if solver else None, "ordered_names": unknown,
                      "executed": unknown, "free_calls": unknown, "undeclared_attempts": unknown},
            "tests": {"commands": unknown, "exit_codes": unknown, "before_after_edit": unknown},
            "graph": {"calls": unknown, "result_sizes": unknown, "no_match": unknown, "before_first_edit": unknown},
            "delegation": {"analyst_invocations": unknown, "turns": unknown, "tools": unknown,
                           "time": unknown, "recommendations_used": unknown, "duplicate_reads": unknown},
            "forensics": {"primary": primary, "secondary": [], "confidence": None,
                          "gold_assisted": False, "observation_limits": reason}}


def _enrich_record(record, result, run_root):
    """Only fields directly exposed by sealed native observations are copied."""
    def observed(phase, filename):
        value = result.solver_result if phase == "solver" else result.verifier_result
        if value is None or filename not in {ref.relative_path for ref in value.artifacts}:
            return {}
        return _load(run_root / phase / filename)
    solver = observed("solver", "native_observations.json")
    verifier = observed("verifier", "native_observations.json")
    details = solver.get("trace_observations", {})
    # Exact native error-class/prefix observations only. General process errors
    # stay UNKNOWN; error prose is not interpreted as agent quality.
    error = result.solver_result.agent_error or ""
    escaped = result.solver_result.escaped_exception or ""
    if result.resolved is not True:
        category = None
        if "ServerStartupError:" in escaped:
            category = Failure.MODEL_SERVER_START_FAILURE.value
        elif any(name + ":" in escaped for name in ("SubmissionValidationError", "SubmissionCompilationError", "ToolNotFoundError")):
            category = Failure.HARNESS_COMPILE_VALIDATION.value
        elif re.search(r"Tool '[^'\n]+' not found\.\nAvailable tools:", error + "\n" + escaped):
            category = Failure.INVALID_TOOL_FATAL.value
        if category:
            record["forensics"]["primary"] = category
    for destination, source in (("setup", "setup_seconds"), ("session", "session_seconds")):
        if solver.get(source) is not None:
            record["time"][destination] = {"value": solver[source], "reason": None}
    for group, destination, source, reason in (
        ("time", "elapsed_at_first_edit", "elapsed_at_first_edit_seconds", "elapsed_at_first_edit_reason"),
        ("model", "finish_reasons", "finish_reasons", "finish_reasons_reason"),
        ("model", "nudges", "nudge_count", "nudge_count_reason"),
    ):
        if source in details:
            record[group][destination] = {"value": details[source], "reason": details.get(reason)}
    if "apply_status" in verifier:
        status = verifier["apply_status"]
        record["outcome"]["apply_status"] = {"value": status, "reason": verifier.get("apply_status_reason")}
        if status == "failed" and result.resolved is not True and record["forensics"]["primary"] == Failure.UNKNOWN.value:
            record["forensics"]["primary"] = Failure.PATCH_MALFORMED_OR_APPLY_FAIL.value
    commands = solver.get("manager", {}).get("commands")
    if type(commands) is list:
        record["tests"]["commands"] = {"value": [{"artifact": (run_root / "solver/native_observations.json").name,
                                                    "command_index": index} for index, _ in enumerate(commands)],
                                          "reason": "command text retained in sealed public solver evidence"}
        record["tests"]["exit_codes"] = {"value": [entry.get("exit_code") for entry in commands], "reason": None}
    trace = observed("solver", "native_trace.json")
    entries = trace.get("entries")
    if type(entries) is list and entries:
        tools = [entry.get("tool") for entry in entries if entry.get("type") == "tool_call"]
        record["tools"]["ordered_names"] = {"value": tools, "reason": None}
    verifier_error = result.verifier_result.error if result.verifier_result else None
    if result.resolved is not True and type(verifier_error) is str and verifier_error.startswith((
            "Evaluation error:", "Failed to apply test_patch:", "Missing test specification (",
            "ENVIRONMENT_VERIFICATION_ARTIFACT")):
        record["forensics"]["primary"] = Failure.ENVIRONMENT_VERIFICATION_ARTIFACT.value
    return record


def write_report(root, *, guard, fingerprint=None):
    if _is_control(root):
        raise ContractError("gold-assisted/verifier-only controls excluded from normal reports")
    manifest = _load(root / "run_manifest.json")
    if manifest.get("gold_assisted") is not False or tuple(manifest.get("task_ids", ())) != S1_IDS:
        raise ContractError("normal report requires frozen S1 non-gold manifest")
    fingerprint = fingerprint or _load(root / "fingerprint.json")
    if manifest.get("fingerprint_sha256") != _digest(fingerprint):
        raise ContractError("report fingerprint mismatch")
    from eval.manifests import _PLAN_IDS
    from eval.runtime_provenance import assert_secret_free
    split = json.loads(_read(SPLIT_PATH))
    metadata = {t["instance_id"]: t for t in split["tasks"] if t["instance_id"] in _PLAN_IDS["S1"]}
    ledger = _ledger(root)
    scan_path = root / "leak_scan.json"
    invalidated = scan_path.exists() and _load(scan_path).get("p0") is True
    records, totals = [], Counter(assigned=12, started=0, verified=0, resolved=0, unresolved=0, indeterminate=0)
    by_repo, terminal = {}, Counter()
    for index, task_id in enumerate(S1_IDS):
        seals = sorted((root / "attempts" / task_id).glob("*.json"), key=lambda p: int(p.stem))
        seal = _load(seals[-1]) if seals else None
        result = validate_task_seal(seal, root, fingerprint) if seal else None
        started = any(row.get("task_id") == task_id and row.get("status") == "started" for row in ledger)
        verified = result is not None and result.verifier_result is not None
        record = forensic_record(task_id, index=index, result=result, repo=metadata[task_id]["repo"],
                                 base_commit=metadata[task_id]["base_commit"], attempt=seal["attempt"] if seal else None, started=started)
        if result is not None and not invalidated:
            record = _enrich_record(record, result, root / seal["run_root"])
        outcome = "resolved" if result and result.resolved is True else "unresolved" if result and result.resolved is False else "indeterminate"
        if invalidated or record["forensics"]["primary"] in (Failure.ENVIRONMENT_VERIFICATION_ARTIFACT.value,
                Failure.MODEL_SERVER_START_FAILURE.value, Failure.PLATFORM_SCORER_EXCEPTION.value):
            outcome = "indeterminate"
        totals["started"] += int(started)
        totals["verified"] += int(verified)
        totals[outcome] += 1
        info = metadata[task_id]
        repo_counts = by_repo.setdefault(info["repo"], Counter(assigned=0, started=0, verified=0, resolved=0, unresolved=0, indeterminate=0))
        repo_counts["assigned"] += 1
        repo_counts["started"] += int(started)
        repo_counts["verified"] += int(verified)
        repo_counts[outcome] += 1
        finished = [row.get("finished_at") for row in ledger if row.get("task_id") == task_id
                    and row.get("attempt") == (seal["attempt"] if seal else None) and row.get("finished_at")]
        if finished:
            record["time"]["end_utc"] = {"value": finished[-1], "reason": None}
        if result and result.solver_result.terminal_reason:
            terminal[result.solver_result.terminal_reason] += 1
        records.append(record)
    assert_secret_free(records)
    guard.write_text(root / "records.jsonl", "".join(_jsonl_row(row) for row in records))
    edits = [row["time"]["elapsed_at_first_edit"]["value"] for row in records
             if row["time"]["elapsed_at_first_edit"]["value"] is not None]
    summary = {**dict(totals), "by_repo": {k: dict(v) for k, v in by_repo.items()},
               "terminal_reason_histogram": dict(terminal), "elapsed_at_first_edit_distribution": None,
               "analyst_share_of_session": None, "unobserved_reason": "requires native trace review",
               "candidate_comparison": None, "agent_quality_assessment": "UNKNOWN", "invalidated_by_p0": bool(invalidated)}
    if edits:
        summary["elapsed_at_first_edit_distribution"] = edits
    _write(root / "summary.json", summary, guard)
    body = ["# E0 S1 forensic observations", "", "Agent-quality assessment: UNKNOWN; manual review pending.", "",
            "Assigned: {assigned}; started: {started}; verified: {verified}; resolved: {resolved}; unresolved: {unresolved}; indeterminate: {indeterminate}.".format(**summary),
            "", "| Task | Resolved | Primary |", "|---|---|---|"]
    body.extend("| {} | {} | {} |".format(row["identity"]["instance_id"],
                                         row["outcome"]["resolved"], row["forensics"]["primary"]) for row in records)
    body.extend(["", "Repository accounting:", "", "```json", canonical_json(summary["by_repo"]), "```",
                 "", "Terminal reasons:", "", "```json", canonical_json(dict(terminal)), "```",
                 "", "Elapsed at first edit (seconds): " + str(summary["elapsed_at_first_edit_distribution"]),
                 "Analyst share and dominant-mode excerpts: UNKNOWN pending manual trace review."])
    guard.write_text(root / "report.md", "\n".join(body) + "\n")
    return summary


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fingerprint", "serve-argv", "preflight", "run", "leak-scan", "report"):
        command = sub.add_parser(name)
        command.add_argument("--public-root", type=Path, required=True)
        if name in ("fingerprint", "preflight", "run"):
            for flag in ("candidate", "prereg", "harness-python", "harness-root", "harness-lock", "source-wheels-root", "model-path", "vllm-root"):
                command.add_argument("--" + flag, type=Path, required=True)
            command.add_argument("--endpoint", required=True)
            command.add_argument("--allow-dirty", action="store_true")
        if name == "fingerprint":
            command.add_argument("--out", type=Path, required=True)
        elif name == "serve-argv":
            for flag in ("candidate", "model-path", "vllm-root", "out", "harness-root", "harness-lock", "source-wheels-root"):
                command.add_argument("--" + flag, type=Path, required=True)
            command.add_argument("--tp", type=int, default=1)
            command.add_argument("--gpu-mem", type=float, default=0.80)
        elif name in ("preflight", "run"):
            for flag in ("private-root", "artifact-root", "worker-root", "export-root"):
                command.add_argument("--" + flag, type=Path, required=True)
            command.add_argument("--worker-user", required=True)
            command.add_argument("--compaction", type=Path, required=True, help="explicit closed compaction JSON or null")
            if name == "run":
                command.add_argument("--worker-timeout-seconds", type=int, choices=(3600,), required=True)
            else:
                command.add_argument("--worker-timeout-seconds", type=int, default=3600)
            if name == "preflight":
                command.add_argument("--control", choices=("no_patch", "known_patch"), required=True)
                command.add_argument("--task-ids", nargs="+", required=True)
            else:
                command.add_argument("--screen", choices=("S1",), required=True)
                command.add_argument("--attempt", type=int, default=1)
                command.add_argument("--retry-task-ids", nargs="*", default=[])
                command.add_argument("--retry-diagnosis", choices=RETRY_DIAGNOSES)
        else:
            command.add_argument("--run-root", type=Path, required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    guard = WriteGuard(args.public_root)
    for name in ("out", "artifact_root", "run_root"):
        path = getattr(args, name, None)
        if path is not None:
            lexical = path.absolute()
            if (not lexical.is_relative_to(REPO_ROOT / "artifacts/dev_runtime")
                    or ".." in lexical.parts or guard.check(lexical) != lexical):
                raise ContractError("driver evidence requires an unaliased ignored artifacts/dev_runtime root")
    if args.command == "serve-argv":
        value = build_serve_argv(candidate=args.candidate, model_path=args.model_path,
                                vllm_root=args.vllm_root, out=args.out, guard=guard,
                                tp=args.tp, gpu_mem=args.gpu_mem, harness_root=args.harness_root,
                                harness_lock=args.harness_lock, source_wheels_root=args.source_wheels_root)
        print(canonical_json(value))
        return 0
    if args.command == "leak-scan":
        return int(leak_scan(args.run_root, args.public_root, guard=guard)["p0"])
    if args.command == "report":
        write_report(args.run_root, guard=guard)
        return 0
    vllm_version = _command([str(args.vllm_root / "bin/python"), "-I", "-B", "-c",
                             "import importlib.metadata as m;print(m.version('vllm'))"]).stdout.strip()
    current = capture_fingerprint(repo_root=REPO_ROOT, candidate=args.candidate, prereg=args.prereg,
                                  public_root=args.public_root, endpoint=args.endpoint,
                                  python_executable=args.harness_python, harness_lock=args.harness_lock,
                                  model_path=args.model_path, allow_dirty=args.allow_dirty, vllm_version=vllm_version)
    if args.command == "fingerprint":
        _write(args.out / "fingerprint.json", current, guard)
        return 0
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise ContractError("real execution requires operator-controlled Linux x86_64 host")
    fingerprint = validate_fingerprint(_load(args.artifact_root / "fingerprint.json"), current)
    from eval.manifests import load_screen_manifest, verify_frozen_split
    from eval.public_data import load_public_metadata, load_solver_tasks
    from eval.runtime import RuntimeConfig
    from eval.real_contracts import FROZEN_BUDGET
    from eval.runtime_real import real_verification_config
    from eval.verifier_data import publish_private_test_patches
    metadata = load_public_metadata(args.public_root)
    split = verify_frozen_split(metadata, tasks_sha256=TASKS_SHA256)
    screening = load_screen_manifest(split)
    tasks = load_solver_tasks(args.public_root, screening, "S1")
    private_parent = guard.check(args.private_root)
    private_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_parent.chmod(0o700)
    private_root = private_parent / ("control_" + args.control if args.command == "preflight" else "S1")
    footprint = measure_footprint(model=args.model_path, public=args.public_root, private=private_root,
                                  harness=args.harness_root, vllm=args.vllm_root, tasks=tasks, candidate=args.candidate)
    footprint["artifact"] = footprint["export"] * 2
    disk = admit_disk_space({"model": args.model_path, "public": args.public_root, "private": args.private_root,
                            "harness": args.harness_root, "vllm": args.vllm_root, "worker": args.worker_root,
                            "export": args.export_root, "artifact": args.artifact_root}, footprint)
    _write(args.artifact_root / "disk_admission.json", disk, guard)
    compaction = json.loads(_read(args.compaction))
    config = RuntimeConfig(python_executable=args.harness_python, harness_root=args.harness_root,
                           candidate_path=args.candidate, artifact_root=args.artifact_root,
                           source_wheels_root=args.source_wheels_root, mode="real_public", synthetic_case=None,
                           model_endpoint=args.endpoint, budget=dict(FROZEN_BUDGET), compaction=compaction,
                           worker_user=args.worker_user, preregistration_sha256=current["preregistration_identity"]["sha256"],
                           task_manifest_sha256=screening.sha256, harness_lock_path=args.harness_lock,
                           worker_parent_root=args.worker_root,
                           worker_timeout_seconds=args.worker_timeout_seconds)
    ids = CONTROL_IDS if args.command == "preflight" else S1_IDS
    if args.command == "preflight" and tuple(args.task_ids) != CONTROL_IDS:
        raise ContractError("preflight task IDs must match frozen preregistration order")
    verification = real_verification_config(args.public_root, dict(FROZEN_BUDGET))
    verifier_tasks = _private_publication(args.public_root, ids, private_root, guard, verification, tasks)
    if args.command == "preflight":
        by_id = {task.instance_id: task for task in tasks}
        value = run_controls(tuple(by_id[i] for i in CONTROL_IDS), verifier_tasks,
                             control=args.control, public_root=args.public_root, private_root=private_root,
                             config=config, guard=guard, fingerprint=fingerprint)
        return 0 if value["passed"] else 1
    summary = run_s1(tasks, verifier_tasks, public_root=args.public_root, private_root=private_root,
           config=config, guard=guard, fingerprint=fingerprint, attempt=args.attempt,
           retry_ids=tuple(args.retry_task_ids), retry_diagnosis=args.retry_diagnosis)
    return int(summary["stopped"])


if __name__ == "__main__":
    raise SystemExit(main())
