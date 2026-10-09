"""Real-native admission and fidelity using CPU fixtures, no model or network."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
from types import ModuleType, SimpleNamespace
import zipfile

import pytest

from eval._contracts import ContractError, canonical_sha256
from eval.real_contracts import (ADAPTER_NAMES, E0_MEMBERS, E0_SHA256, E0_SIZE, FROZEN_BUDGET,
                                 FROZEN_COMPACTION, SERVED_MODEL, normalize_endpoint, validate_budget,
                                 validate_compaction, validate_public_task_identity, validate_real_runtime)
from eval import runtime_real as real
from eval.solver_task import PublicAssetRef, SolverTask
from eval.verifier_task import PrivateTestPatchRef, VerifierTask
from tools.common import REPO_ROOT, tree_sha256

CANDIDATE = REPO_ROOT / "artifacts/submissions/e0_official_control/submission.zip"
LOCK = REPO_ROOT / "harness_cert/locks/harness_linux_x86_64_py312.lock"


@pytest.fixture(autouse=True)
def no_test_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real-native CPU tests must never make network calls")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.mark.parametrize("endpoint", [
    "http://localhost:8000/v1", "https://127.0.0.1:8000/v1", "http://127.0.0.2:8000/v1",
    "http://example.invalid:8000/v1", "http://[::1]:8000/v1", "http://127.0.0.1/v1",
    "http://127.0.0.1:8000/v1/", "http://127.0.0.1:8000/v1?token=sentinel",
    "http://user:password@127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1#secret",
    "http://127.0.0.1:0/v1", "http://127.0.0.1:65536/v1", None,
])
def test_exact_loopback_admission(endpoint):
    with pytest.raises(ContractError):
        normalize_endpoint(endpoint)


def test_closed_real_values_accept_only_frozen_science():
    assert normalize_endpoint("http://127.0.0.1:8123/v1") == "http://127.0.0.1:8123/v1"
    assert validate_budget(dict(FROZEN_BUDGET)) == FROZEN_BUDGET
    assert validate_compaction(dict(FROZEN_COMPACTION)) == FROZEN_COMPACTION
    assert validate_compaction(None) is None
    runtime = {"sandbox": "subprocess", "model_endpoint": "http://127.0.0.1:8123/v1",
               "budget": dict(FROZEN_BUDGET), "compaction": None, "worker_user": "gemma_worker",
               "preregistration_sha256": "a" * 64}
    assert validate_real_runtime(runtime) is runtime


@pytest.mark.parametrize("key,value", [("time_minutes", True), ("time_minutes", float("nan")),
    ("time_minutes", 2.0), ("tool_calls", 11), ("tool_calls", 10.0), ("turns", True),
    ("command_timeout_seconds", 59), ("extra", 1)])
def test_budget_refuses_unknown_keys_types_or_change(key, value):
    with pytest.raises(ContractError):
        validate_budget({**FROZEN_BUDGET, key: value})


@pytest.mark.parametrize("change", [{"extra": 1}, {"token_threshold": 32768}, {"compaction_interval": True},
                                    {"cache_min_tokens": 0}])
def test_compaction_refuses_unknown_or_non_documented_values(change):
    with pytest.raises(ContractError):
        validate_compaction({**FROZEN_COMPACTION, **change})


@pytest.mark.parametrize("repo,task_id", [("synthetic/probe", "synthetic_1"), ("evil/repo", "fastapi_1"),
    ("fastapi/fastapi", "rich_1"), ("encode/httpx", "httpx_1/../private")])
def test_public_identity_is_repository_specific(repo, task_id):
    with pytest.raises(ContractError):
        validate_public_task_identity(SimpleNamespace(repo=repo, instance_id=task_id))


def test_exact_e0_members_and_private_fresh_extraction(tmp_path):
    identity = real.validate_candidate(CANDIDATE)
    assert identity["sha256"] == E0_SHA256 and identity["size_bytes"] == E0_SIZE
    assert len(identity["members"]) == 10
    target = tmp_path.resolve() / "candidate"
    real.extract_candidate(CANDIDATE, target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()} == set(E0_MEMBERS)
    for member, expected in E0_MEMBERS.items():
        path = target / member
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        assert path.stat().st_nlink == 1 and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert real.parse_candidate_budget(target, dict(FROZEN_BUDGET)) == FROZEN_BUDGET
    with pytest.raises(real.RealAdmissionError, match="fresh"):
        real.extract_candidate(CANDIDATE, target)


def test_candidate_outer_identity_refuses_any_change(tmp_path):
    changed = tmp_path.resolve() / "submission.zip"
    data = CANDIDATE.read_bytes()
    changed.write_bytes(data[:-1] + bytes((data[-1] ^ 1,)))
    with pytest.raises(real.RealAdmissionError, match="later candidate-admission"):
        real.validate_candidate(changed)
    with pytest.raises(real.RealAdmissionError):
        real.extract_candidate(changed, tmp_path.resolve() / "candidate")
    assert not (tmp_path / "candidate").exists()


def zip_entries(extra=None, *, omit=None, corrupt=None):
    data = io.BytesIO()
    with zipfile.ZipFile(CANDIDATE) as original, zipfile.ZipFile(data, "w") as archive:
        for item in original.infolist():
            if item.filename == omit:
                continue
            value = b"changed" if item.filename == corrupt else original.read(item)
            archive.writestr(item, value)
        if extra:
            entry, value = extra
            archive.writestr(entry, value)
    return data.getvalue()


@pytest.mark.parametrize("path", ["/tmp/escape", "../escape", "prompts/../../escape", "evil.py",
                                   "prompts\\escape", "prompts//escape", "./agent.yaml"])
def test_safe_zip_rejects_path_or_extra_member_before_extract(path):
    with pytest.raises(real.RealAdmissionError):
        real._candidate_members(zip_entries((path, b"sentinel")))


def test_safe_zip_refuses_link_duplicate_missing_corrupt():
    linked = zipfile.ZipInfo("prompts/system.md")
    linked.create_system = 3
    linked.external_attr = (stat.S_IFLNK | 0o777) << 16
    for data in (zip_entries((linked, b"../../private"), omit=linked.filename),
                 zip_entries(omit="agent.yaml"), zip_entries(corrupt="agent.yaml")):
        with pytest.raises(real.RealAdmissionError):
            real._candidate_members(data)
    with pytest.warns(UserWarning, match="Duplicate"):
        duplicate = zip_entries(("agent.yaml", b"changed"))
    with pytest.raises(real.RealAdmissionError):
        real._candidate_members(duplicate)


@pytest.mark.parametrize("content", ["evaluation:\n  extra: 1\n", "unknown: 1\n", "evaluation:\n  timeout_seconds: 61\n"
    "  max_tool_calls: 10\n  max_time_minutes: 1\n  max_turns: 50\n", "evaluation: [bad]\n"])
def test_budget_config_refuses_unknown_missing_or_mismatched_keys(tmp_path, content):
    (tmp_path / "eval_config.yaml").write_text(content)
    with pytest.raises(ContractError):
        real.parse_candidate_budget(tmp_path.resolve(), dict(FROZEN_BUDGET))


def make_snapshot(root, entries=None):
    root.mkdir(mode=0o700, exist_ok=True)
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w:gz") as archive:
        for name, value in (entries or {"./": None, ".git/index": b"official index bytes",
                                      ".git/hooks/pre-commit.sample": b"official sample", ".git/config": b"official config",
                                      "app.py": b"public source"}).items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if value is None else 0o644
            if value is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
    raw = content.getvalue()
    path = root / "snapshots/fastapi_1.tgz"
    path.parent.mkdir(mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    asset = PublicAssetRef("snapshot", "fastapi_1", "snapshots/fastapi_1.tgz", hashlib.sha256(raw).hexdigest(), len(raw))
    task = SolverTask(1, "fastapi_1", "fastapi/fastapi", "a" * 40, "Public issue", "", asset)
    return path, task


def test_official_git_fidelity_preserves_index_and_metadata(tmp_path):
    path, task = make_snapshot(tmp_path.resolve() / "public")
    data = real.inspect_official_snapshot(path, task.snapshot)
    assert data["git_index_present"] is True
    assert {entry["path"] for entry in data["git_metadata"]} == {".git/index", ".git/hooks/pre-commit.sample", ".git/config"}
    assert data["snapshot_sha256"] == task.snapshot.sha256
    assert data["git_metadata_sha256"] == canonical_sha256(data["git_metadata"])


def test_official_git_absent_index_is_recorded(tmp_path):
    path, task = make_snapshot(tmp_path.resolve() / "public", {"app.py": b"source", ".git/config": b"official"})
    assert real.inspect_official_snapshot(path, task.snapshot)["git_index_present"] is False


@pytest.mark.parametrize("member", ["../escape", "/tmp/escape", "dir/../../escape"])
def test_official_archive_unsafe_paths_refused(tmp_path, member):
    path, task = make_snapshot(tmp_path.resolve() / "public", {member: b"unsafe"})
    with pytest.raises(real.RealAdmissionError):
        real.inspect_official_snapshot(path, task.snapshot)


def test_snapshot_contract_is_authority_at_native_use(tmp_path):
    path, task = make_snapshot(tmp_path.resolve() / "public")
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    with pytest.raises(real.RealAdmissionError, match="contract authority"):
        real.inspect_official_snapshot(path, task.snapshot)


def test_real_verification_config_hashes_exact_public_support(tmp_path):
    public = tmp_path.resolve() / "public"
    (public / "sandbox").mkdir(parents=True)
    (public / "wheels").mkdir()
    (public / "sandbox/setup.py").write_bytes(b"public setup\n")
    (public / "wheels/local.whl").write_bytes(b"wheel fixture")
    config = real.real_verification_config(public, dict(FROZEN_BUDGET))
    assert set(config) == {"schema_version", "sandbox", "timeout_seconds", "setup_py_sha256", "wheels_tree_sha256"}
    assert config["setup_py_sha256"] == hashlib.sha256(b"public setup\n").hexdigest()
    assert config["wheels_tree_sha256"] == tree_sha256({"local.whl": hashlib.sha256(b"wheel fixture").hexdigest()})
    first = canonical_sha256(config)
    (public / "wheels/local.whl").write_bytes(b"changed")
    assert canonical_sha256(real.real_verification_config(public, dict(FROZEN_BUDGET))) != first


def test_support_config_refuses_symlink_or_hardlink(tmp_path):
    public = tmp_path.resolve() / "public"
    (public / "sandbox").mkdir(parents=True)
    (public / "wheels").mkdir()
    (public / "sandbox/setup.py").write_text("public")
    secret = tmp_path / "private"
    secret.write_text("SECRET FIXTURE")
    (public / "wheels/local.whl").symlink_to(secret)
    with pytest.raises(Exception):
        real.real_verification_config(public, dict(FROZEN_BUDGET))
    (public / "wheels/local.whl").unlink()
    os.link(secret, public / "wheels/local.whl")
    with pytest.raises(Exception):
        real.real_verification_config(public, dict(FROZEN_BUDGET))


@pytest.mark.parametrize("verifier", [False, True], ids=["solver", "verifier"])
@pytest.mark.parametrize("changed", ["sandbox/setup.py", "wheels/local.whl"])
def test_staging_cannot_rebase_admitted_public_support(tmp_path, monkeypatch, verifier, changed):
    from eval import runtime, worker_common
    public = tmp_path.resolve() / "source"
    (public / "sandbox").mkdir(parents=True)
    (public / "wheels").mkdir()
    (public / "sandbox/setup.py").write_text("original public setup")
    (public / "wheels/local.whl").write_bytes(b"original public wheel")
    config = real.real_verification_config(public, dict(FROZEN_BUDGET))
    authority = {key: config[key] for key in ("setup_py_sha256", "wheels_tree_sha256")}
    (public / changed).write_bytes(b"changed after coordinator admission")
    worker = tmp_path.resolve() / "worker"
    staged = worker / "public"
    staged.mkdir(parents=True)
    runtime._stage_public_support(public, staged)
    submission = worker / "submission"
    submission.mkdir()
    shutil.copyfile(CANDIDATE, submission / "submission.zip")
    paths = {"worker_root": worker, "public_root": staged, "wheels_root": staged / "wheels",
             "setup_root": staged / "sandbox", "submission_root": submission,
             "evidence_root": worker / "evidence", "harness_root": worker / "harness",
             "source_wheels_root": worker / "source_wheels"}
    request = {"mode": "real_public", "runtime": {key: str(value) for key, value in paths.items()},
               "provenance": {"public_identities": {"public_support": authority}}}
    # Isolate the support boundary; the closed-request gate has separate tests.
    monkeypatch.setattr(worker_common, "validate_request", lambda value, **kwargs: value)
    monkeypatch.setattr(real, "validate_harness_environment", lambda *a, **kw: pytest.fail("optional native admission reached"))
    with pytest.raises(real.RealAdmissionError, match="coordinator authority"):
        real._admit(request, verifier=verifier)


def model_response(ids=None, **extra):
    return json.dumps({"object": "list", "data": [{"id": name} for name in
                      (ids if ids is not None else [SERVED_MODEL, *ADAPTER_NAMES])], **extra}).encode()


def test_model_identity_uses_mocked_loopback_get_and_both_adapters():
    urls = []
    raw = model_response()
    def fetch(url):
        urls.append(url)
        return raw
    identity, response = real.check_model_identity("http://127.0.0.1:8123/v1", fetch=fetch)
    assert urls == ["http://127.0.0.1:8123/v1/models"]
    assert response is raw
    assert identity["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert identity["adapter_names"] == list(ADAPTER_NAMES)


@pytest.mark.parametrize("raw", [b"bad JSON", b"{}", b'{"data": {}}', model_response([SERVED_MODEL]),
                                 model_response([SERVED_MODEL, "other_adapter"]), model_response([SERVED_MODEL, *ADAPTER_NAMES, SERVED_MODEL])])
def test_model_identity_refuses_missing_models_malformed_or_duplicate(raw):
    with pytest.raises(real.RealAdmissionError):
        real.check_model_identity("http://127.0.0.1:8123/v1", fetch=lambda url: raw)


def test_model_redirect_is_refused_without_fetching():
    with pytest.raises(real.RealAdmissionError):
        real._NoRedirect().redirect_request(None, None, 302, None, None, "http://external.invalid/")


def test_hash_lock_matches_all_83_frozen_pins():
    assert len(real.parse_harness_lock(LOCK.read_bytes())) == 83
    assert real.parse_harness_lock(LOCK.read_bytes()) == real.SUPPORT_PINS


@pytest.mark.parametrize("mutation", [lambda value: value.replace(b"litellm==1.83.14", b"litellm==1.83.13"),
    lambda value: value + b"\n--index-url https://external.invalid\n", lambda value: b"editable @ /tmp/pkg\n",
    lambda value: b"litellm==1.83.14\n", lambda value: value + b"\nlitellm==1.83.14 \\\n --hash=sha256:" + b"a" * 64 + b"\n"])
def test_linux_lock_refuses_unhashed_changed_unknown_or_duplicate(mutation):
    with pytest.raises(real.RealAdmissionError):
        real.parse_harness_lock(mutation(LOCK.read_bytes()))


def test_native_admission_refuses_darwin_before_optional_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(real.sys, "platform", "darwin")
    with pytest.raises(real.RealAdmissionError, match="Linux x86_64 Python 3.12"):
        real.validate_harness_environment(tmp_path.resolve(), tmp_path.resolve(), expected_lock_sha256="a" * 64)


@pytest.mark.parametrize("mismatch", [None, "source", "wheel", "version", "lock"])
def test_linux_source_and_lock_validator_using_cpu_platform_fixture(tmp_path, monkeypatch, mismatch):
    """Emulate the Linux gate and metadata; exercise actual wheel/source hashes."""
    harness, wheels = tmp_path.resolve() / "harness", tmp_path.resolve() / "source_wheels"
    site = harness / "lib/python3.12/site-packages"
    site.mkdir(parents=True)
    wheels.mkdir()
    shutil.copyfile(LOCK, wheels / LOCK.name)
    wheel_hashes = {}
    first_source = None
    for index, name in enumerate(real.SOURCE_WHEELS):
        source = f"# controlled CPU source fixture {index}\n".encode()
        relative = f"fixture_package_{index}/native.py"
        path = site / relative
        path.parent.mkdir()
        path.write_bytes(source)
        first_source = first_source or path
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(relative, source)
        (wheels / name).write_bytes(data.getvalue())
        wheel_hashes[name] = hashlib.sha256(data.getvalue()).hexdigest()
    monkeypatch.setattr(real, "SOURCE_WHEELS", wheel_hashes)
    monkeypatch.setattr(real.sys, "platform", "linux")
    monkeypatch.setattr(real.sys, "prefix", str(harness))
    monkeypatch.setattr(real.sys, "version_info", (3, 12, 14))
    monkeypatch.setattr(real.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(real.sysconfig, "get_path", lambda name: str(site))
    versions = {**real.SUPPORT_PINS, **real.SOURCE_VERSIONS}
    monkeypatch.setattr(real.importlib.metadata, "version", lambda name: versions[name])
    # Tokenizer payload identities are checked independently in the real
    # environment. This CPU source fixture substitutes their read-only metadata.
    original = real.read_regular
    tokenizer_hashes = {"9b5ad71b2ce5302211f9c61530b329a4922fc6a4": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
                        "fb374d419588a4632f3f557e76b4b70aebbca790": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"}
    def read(descriptor, relative, **kwargs):
        if str(relative).startswith("litellm/litellm_core_utils/tokenizers/"):
            return SimpleNamespace(sha256=tokenizer_hashes[Path(relative).name])
        return original(descriptor, relative, **kwargs)
    monkeypatch.setattr(real, "read_regular", read)
    if mismatch == "source":
        first_source.write_bytes(b"modified installed source")
    elif mismatch == "wheel":
        (wheels / next(iter(wheel_hashes))).write_bytes(b"changed source wheel")
    elif mismatch == "version":
        versions["swegemma"] = "changed"
    lock_identity = hashlib.sha256(LOCK.read_bytes()).hexdigest() if mismatch != "lock" else "f" * 64
    if mismatch:
        with pytest.raises(real.RealAdmissionError):
            real.validate_harness_environment(harness, wheels, expected_lock_sha256=lock_identity)
    else:
        result = real.validate_harness_environment(harness, wheels, expected_lock_sha256=lock_identity)
        assert len(result["source_wheels"]) == 5
        assert all(entry["matching_python_files"] == 1 for entry in result["source_wheels"])
        assert result["versions"] == versions and result["harness_lock_sha256"] == lock_identity


def test_real_entry_request_validation_precedes_native_imports(monkeypatch):
    import eval.worker_common as worker
    seen = []
    def refuse(request, *, verifier=False):
        seen.append(verifier)
        raise ContractError("closed schema failed")
    monkeypatch.setattr(worker, "validate_request", refuse)
    monkeypatch.setattr(real, "validate_harness_environment", lambda *args, **kwargs: pytest.fail("native source reached before request gate"))
    for verifier in (False, True):
        with pytest.raises(ContractError, match="closed schema"):
            real._admit({}, verifier=verifier)
    assert seen == [False, True]


def test_native_verifier_exact_keyword_semantics():
    def native(manager, config, task, snapshot, *, base_snapshot_path=None, patch_path=None, fast_path=True,
               agent_patch="", agent_error=None, trace=None, start_time=0):
        pass
    kwargs = real.verifier_native_kwargs(native, patch="SEALED PATCH", error=None, started=1.5)
    assert kwargs == {"agent_patch": "SEALED PATCH", "agent_error": None, "trace": None,
                      "start_time": 1.5, "base_snapshot_path": None, "patch_path": None}
    assert "fast_path" not in kwargs


def test_native_verifier_no_extras_for_older_signature():
    def native(manager, config, task, snapshot, *, agent_patch="", agent_error=None, trace=None, start_time=0):
        pass
    assert set(real.verifier_native_kwargs(native, patch="", error=None, started=0)) == {
        "agent_patch", "agent_error", "trace", "start_time"}


def test_trace_first_successful_edit_nudges_and_unknowns():
    entries = [{"type": "tool_response", "tool": "edit_file", "result": '{"success": false}', "timestamp": 101},
               {"type": "tool_response", "tool": "write_file", "result": '{"status": "ok"}', "timestamp": 103},
               {"type": "continuation_nudge"}, {"type": "text", "metadata": {"finish_reason": "MAX_TOKENS"}},
               {"type": "tool_call", "tool": "write_file"}]
    observed = real.trace_observations({"entries": entries}, session_epoch=100)
    assert observed["elapsed_at_first_edit_seconds"] == 3
    assert observed["nudge_count"] == 1 and observed["finish_reasons"] == ["MAX_TOKENS"]
    missing = real.trace_observations({"entries": []})
    assert all(missing[name] is None for name in ("elapsed_at_first_edit_seconds", "nudge_count", "finish_reasons"))
    assert all(missing[name] for name in ("elapsed_at_first_edit_reason", "nudge_count_reason", "finish_reasons_reason"))
    unavailable = real.trace_observations({"entries": []}, trace_available=False)
    assert unavailable["tool_attempts"] is None and unavailable["tool_attempts_reason"]


class ManagerFixture:
    def __init__(self, workspace, copied):
        self.sandboxes = {"one": {"workspace": workspace, "tmp": copied}}
        self.calls = []

    def start(self):
        return "one"

    def exec(self, identifier, command, timeout=None):
        self.calls.append(command)
        return SimpleNamespace(exit_code=0, stdout="WORKSPACE DIAGNOSTIC PATCH", stderr="")

    def copy_to(self, identifier, source, destination):
        shutil.copyfile(source, self.sandboxes[identifier]["tmp"] / Path(source).name)

    def stop(self, identifier):
        self.sandboxes.pop(identifier)


def test_passive_observer_preserves_response_and_native_snapshot_contract(tmp_path):
    path, task = make_snapshot(tmp_path.resolve() / "public")
    workspace, copied = tmp_path.resolve() / "workspace", tmp_path.resolve() / "copied"
    workspace.mkdir(); copied.mkdir()
    manager = ManagerFixture(workspace, copied)
    observed = real.observe_manager(manager, {"public_root": path.parents[1]}, task)
    assert manager.start() == "one"
    response = manager.exec("one", "curl http://public.example.invalid", timeout=60)
    assert response.stdout == "WORKSPACE DIAGNOSTIC PATCH"
    assert observed["commands"][0]["network_attempt"] is True
    manager.copy_to("one", path, "/tmp")
    assert (copied / path.name).read_bytes() == path.read_bytes()
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    with pytest.raises(ContractError):
        manager.copy_to("one", path, "/tmp")
    manager.stop("one")
    assert observed["workspace_patch"] == "WORKSPACE DIAGNOSTIC PATCH"
    assert observed["sandbox_ids"] == observed["stopped_ids"] == ["one"]


def test_observer_records_actual_junit_and_reset_commands(tmp_path):
    path, task = make_snapshot(tmp_path.resolve() / "public")
    manager = ManagerFixture(tmp_path, tmp_path)
    observed = real.observe_manager(manager, {"public_root": path.parents[1]}, task)
    manager.exec("one", "cat /tmp/_swegemma_junit_abc.xml 2>/dev/null || true")
    manager.exec("one", "cd /workspace && git checkout HEAD -- conftest.py 2>/dev/null || true")
    assert observed["junit_xml"] == "WORKSPACE DIAGNOSTIC PATCH"
    assert len(observed["protected_file_reset_commands"]) == 1


def test_native_evidence_redacts_diagnostics_without_hydrating_env(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    root.chmod(0o700)
    monkeypatch.setenv("PARENT_PRIVATE_TOKEN", "do-not-copy")
    ref = real._artifact(root / "trace.json", json.dumps({"authorization": "Bearer fixture-value",
                                                        "message": "password=fixture-value"}).encode())
    value = (root / "trace.json").read_text()
    assert "fixture-value" not in value and "do-not-copy" not in value
    assert hashlib.sha256((root / "trace.json").read_bytes()).hexdigest() == ref["sha256"]
    with pytest.raises(ContractError):
        real._artifact(root / "model_response.json", model_response(api_key="must-not-seal"), sanitize=False)
    assert not (root / "model_response.json").exists()


@pytest.mark.parametrize("patch,applications,expected", [
    ("", [{"patch_application": True, "exit_code": 0}], "not_needed"),
    ("nonempty native patch", [{"patch_application": True, "exit_code": 0}] * 2, "applied"),
    ("nonempty native patch", [{"patch_application": True, "exit_code": 1}], "failed"),
    ("", [], "UNKNOWN"),
])
def test_apply_observations_never_invent_unobserved_pass(patch, applications, expected):
    observed = real.verification_observations({"resolved": False, "test_exit_code": 1},
                                             {"commands": applications}, patch)
    assert observed["apply_status"] == expected
    assert observed["required_tests_passed"] is False
    assert bool(observed["apply_error"]) is (expected == "failed")


def fake_native_runtime(tmp_path, monkeypatch, *, native_patch="", agent_error=None):
    paths = {}
    worker = tmp_path.resolve() / "worker"
    worker.mkdir(mode=0o700)
    paths["worker_root"] = worker
    for name, relative in (("public_root", "public"), ("evidence_root", "evidence"),
                           ("submission_root", "submission"), ("source_wheels_root", "source_wheels")):
        paths[name] = worker / relative
        paths[name].mkdir(mode=0o700)
    paths["harness_root"] = worker / "harness"
    paths["harness_root"].mkdir()
    paths["wheels_root"] = paths["public_root"] / "wheels"
    paths["setup_root"] = paths["public_root"] / "sandbox"
    paths["wheels_root"].mkdir(mode=0o700)
    paths["setup_root"].mkdir(mode=0o700)
    (paths["wheels_root"] / "public.whl").write_bytes(b"public wheel fixture")
    (paths["setup_root"] / "setup.py").write_text("# public setup fixture\n")
    shutil.copyfile(CANDIDATE, paths["submission_root"] / "submission.zip")
    _, task = make_snapshot(paths["public_root"])
    pin = {"site_packages": str(paths["harness_root"]), "versions": {"swegemma": "0.2.7"}}
    configuration = real.real_verification_config(paths["public_root"], dict(FROZEN_BUDGET))
    private = "private test patch fixture\n"
    verifier = VerifierTask(1, task.instance_id, task.repo, task.base_commit, task.snapshot,
                            PrivateTestPatchRef(hashlib.sha256(private.encode()).hexdigest(), len(private.encode())),
                            canonical_sha256(configuration), None, None)
    request = {"mode": "real_public", "task": task.to_dict(), "observation_enabled": True,
               "runtime": {"budget": dict(FROZEN_BUDGET), "compaction": None, "model_endpoint": "http://127.0.0.1:8123/v1"},
               "returned_patch": native_patch, "returned_patch_sha256": hashlib.sha256(native_patch.encode()).hexdigest(),
               "test_patch": private, "agent_error": agent_error, "verification_config": configuration}
    monkeypatch.setattr(real, "_admit", lambda value, verifier=False: (paths, verifier_task if verifier else task, pin, {"git_index_present": True}))
    verifier_task = verifier
    monkeypatch.setattr(real, "_prepare", lambda value: None)
    monkeypatch.setattr(real, "_native_namespaces", lambda value: None)
    monkeypatch.setattr(real, "check_model_identity", lambda endpoint: (
        {"endpoint": endpoint, "served_model": SERVED_MODEL, "adapter_names": list(ADAPTER_NAMES)}, model_response()))
    calls = {"config": [], "registry": [], "solver": [], "verifier": []}
    def module(name, **values):
        result = ModuleType(name)
        result.__path__ = []
        result.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, result)
    class FakeManager(ManagerFixture):
        def __init__(self, timeout_seconds, base_dir, system_site_packages):
            assert timeout_seconds == 60 and system_site_packages is False
            base_dir.mkdir(mode=0o700)
            copied, workspace = base_dir / "tmp", base_dir / "workspace"
            copied.mkdir(mode=0o700); workspace.mkdir(mode=0o700)
            super().__init__(workspace, copied)
        def cleanup_all(self):
            for identifier in tuple(self.sandboxes):
                self.stop(identifier)
    class FakeContext:
        def __init__(self, **kwargs):
            self.llm_calls_used, self.tool_calls_used, self.patch_submitted = 2, 1, False
            self.submitted_patch, self.agent_elapsed_seconds = None, 0.25
        def start_agent_session(self):
            pass
    class FakeTrace:
        def to_dict(self, *, format):
            assert format == "legacy"
            return {"entries": [{"type": "task_prompt", "content": "ACTUAL PUBLIC PROMPT"},
                                {"type": "tool_call", "tool": "write_file"}]}
    def config(**kwargs):
        calls["config"].append(kwargs)
        return SimpleNamespace(**kwargs, budget=SimpleNamespace(time_minutes=kwargs["max_time_minutes"]),
                               harness=SimpleNamespace(command_timeout_seconds=kwargs["timeout_seconds"]))
    def registry(**kwargs):
        calls["registry"].append(kwargs)
        return object()
    async def solver(manager, config, public, snapshot, *, context):
        calls["solver"].append((public, snapshot))
        manager.start()
        context.start_agent_session()
        assert not hasattr(public, "test_patch") and not hasattr(public, "patch")
        return native_patch, agent_error, FakeTrace()
    async def verification(manager, config, task, snapshot, *, base_snapshot_path=None, patch_path=None,
                           fast_path=True, agent_patch="", agent_error=None, trace=None, start_time=0):
        calls["verifier"].append({"agent_patch": agent_patch, "agent_error": agent_error, "trace": trace,
                                  "base_snapshot_path": base_snapshot_path, "patch_path": patch_path})
        manager.start()
        assert task.test_patch == private and task.FAIL_TO_PASS == task.PASS_TO_PASS == ()
        data = {"instance_id": task.instance_id, "agent_patch": agent_patch, "resolved": False,
                "test_exit_code": 1, "test_output": "actual native output", "error": None}
        return SimpleNamespace(resolved=False, test_exit_code=1, error_message=None, model_dump=lambda **kwargs: data)
    module("adk_submission", discover_adapters=lambda path: SimpleNamespace(adapters={name: object() for name in ADAPTER_NAMES}),
           ModelRegistry=lambda: object())
    module("swegemma")
    module("swegemma.models")
    module("swegemma.models.registry", setup_gemma_model_registry=registry)
    module("swegemma.models.task", Task=lambda **kwargs: SimpleNamespace(**kwargs))
    module("swegemma.config", EvalConfig=config)
    module("swegemma.context", SwegemmaContext=FakeContext)
    module("swegemma.harness")
    module("swegemma.harness.agent_runner", run_agent_sandbox=solver)
    module("swegemma.harness.verification", verify_task=verification)
    module("swegemma.sandbox")
    module("swegemma.sandbox.subprocess", SubprocessManager=FakeManager)
    module("litellm")
    return request, paths, calls


@pytest.mark.parametrize("patch,error", [("", None), ("native fallback patch\n", "Agent exceeded session timeout (1 min)"),
                                       ("native returned patch\n", "Agent exceeded tool call budget (10 calls)")])
def test_real_solver_preserves_native_returned_patch_not_workspace_diff(tmp_path, monkeypatch, patch, error):
    request, paths, calls = fake_native_runtime(tmp_path, monkeypatch, native_patch=patch, agent_error=error)
    # A fresh worker has none of these trusted imports; emulate only that CPU
    # process state while exercising mocked native leaves in this test process.
    for name in ("eval.verifier_task", "eval.verifier_data", "swegemma.evaluate", "swegemma.harness.verification",
                 "swegemma.harness.sample_verification"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    result = real.solver_execute_real(request)
    assert result["returned_patch"] == patch
    assert result["workspace_patch"] == "WORKSPACE DIAGNOSTIC PATCH"
    assert result["returned_patch_sha256"] == hashlib.sha256(patch.encode()).hexdigest()
    assert calls["registry"][0]["num_retries"] == 5
    assert calls["registry"][0]["backend"] == "vllm" and calls["registry"][0]["served_model"] == SERVED_MODEL
    assert calls["config"][0]["max_time_minutes"] == 1 and calls["config"][0]["max_tool_calls"] == 10
    assert calls["config"][0]["timeout_seconds"] == 60 and calls["config"][0]["max_turns"] == 50
    assert not calls["config"][0]["tasks_path"].exists()
    assert (paths["evidence_root"] / "agent_prompt.txt").read_text() == "ACTUAL PUBLIC PROMPT"
    assert (paths["evidence_root"] / "model_server_models.json").read_bytes() == model_response()
    assert result["native_result"]["sandbox_ids"] == result["native_result"]["stopped_ids"]
    assert result["timeout"] is bool(error and "session timeout" in error)
    assert result["budget_exhausted"] is bool(error and "budget" in error)


def test_real_solver_rejects_secret_returned_patch_without_modification(tmp_path, monkeypatch):
    request, paths, calls = fake_native_runtime(tmp_path, monkeypatch, native_patch="password=credential-value")
    for name in ("eval.verifier_task", "eval.verifier_data", "swegemma.evaluate", "swegemma.harness.verification",
                 "swegemma.harness.sample_verification"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with pytest.raises(ContractError, match="authentication material"):
        real.solver_execute_real(request)
    assert not (paths["evidence_root"] / "native_trace.json").exists()


def test_real_verifier_uses_sealed_patch_fresh_native_config_and_keywords(tmp_path, monkeypatch):
    request, paths, calls = fake_native_runtime(tmp_path, monkeypatch, native_patch="SEALED NATIVE PATCH\n")
    result = real.verifier_execute_real(request)
    assert calls["verifier"][0]["agent_patch"] == "SEALED NATIVE PATCH\n"
    assert calls["verifier"][0]["trace"] is None
    assert result["native_result"]["agent_patch"] == "SEALED NATIVE PATCH\n"
    assert result["test_exit_code"] == 1 and result["test_output"] == "actual native output"
    observations = json.loads((paths["evidence_root"] / "native_observations.json").read_text())
    assert observations["fast_path_explicitly_passed"] is False
    assert observations["apply_status"] == "UNKNOWN"
    assert calls["config"][0]["adapter_manifest"] is None


@pytest.mark.parametrize("mutation", [lambda request: request.update(returned_patch_sha256="f" * 64),
    lambda request: request.update(test_patch="changed private bytes"),
    lambda request: request["verification_config"].update(timeout_seconds=59)])
def test_real_verifier_refuses_private_patch_seal_or_config_mismatch_before_native(tmp_path, monkeypatch, mutation):
    request, paths, calls = fake_native_runtime(tmp_path, monkeypatch)
    mutation(request)
    with pytest.raises(real.RealAdmissionError):
        real.verifier_execute_real(request)
    assert not calls["verifier"] and not calls["config"]


@pytest.mark.parametrize("real_mode", [False, True], ids=["synthetic", "real"])
@pytest.mark.parametrize("verifier", [False, True], ids=["solver", "verifier"])
def test_actual_staged_phase_imports_with_isolated_interpreter(tmp_path, real_mode, verifier):
    """Exercise the packaged imports only; no worker main, native call or data."""
    from eval.runtime import _COMMON_CODE, _REAL_COMMON_CODE, _VERIFIER_CODE, _stage_code
    from eval.runtime_provenance import runtime_source_records
    root = tmp_path.resolve() / "phase-worker"
    root.mkdir(mode=0o700)
    if not real_mode:
        (root / "code").mkdir(mode=0o700)
    (root / "evidence").mkdir(mode=0o700)
    sources = runtime_source_records(REPO_ROOT) if real_mode else None
    _stage_code(root, verifier=verifier, real=real_mode, source_records=sources)
    expected = {*_COMMON_CODE, *(_REAL_COMMON_CODE if real_mode else ()),
                *(_VERIFIER_CODE if verifier else ("eval/solver_worker.py",))}
    assert {path.relative_to(root / "code").as_posix() for path in (root / "code").rglob("*") if path.is_file()} == expected
    for relative in expected:
        staged = root / "code" / relative
        assert staged.stat().st_nlink == 1 and stat.S_IMODE(staged.stat().st_mode) == 0o600
    bootstrap = """
import importlib, importlib.util, json, pathlib, sys
code, repository, real_mode, verifier = sys.argv[1:]
sys.path.insert(0, code)
phase = 'verifier' if verifier == '1' else 'solver'
entry = importlib.import_module('eval.' + phase + '_worker')
execution = importlib.import_module('eval.runtime_real' if real_mode == '1' else 'eval.runtime_adapter')
assert callable(entry.main)
assert callable(getattr(execution, phase + ('_execute_real' if real_mode == '1' else '_execute')))
assert repository not in sys.path
local = {name: value.__file__ for name, value in sys.modules.items()
         if (name == 'eval' or name.startswith('eval.') or name == 'tools' or name.startswith('tools.'))
         and getattr(value, '__file__', None)}
assert all(pathlib.Path(path).is_relative_to(pathlib.Path(code)) for path in local.values())
private = ('eval.verifier_task', 'eval.verifier_data', 'eval.public_data', 'eval.runtime_verifier_fixture')
if verifier == '0':
    assert not any(name in sys.modules for name in private)
    assert all(importlib.util.find_spec(name) is None for name in private)
assert 'eval.public_data' not in sys.modules
assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules
               for prefix in ('swegemma', 'adk_submission', 'google.adk', 'litellm'))
print(json.dumps({'phase': phase, 'local_modules': sorted(local), 'private_modules_absent': verifier == '0'}))
"""
    process = subprocess.run([sys.executable, "-I", "-B", "-c", bootstrap, str(root / "code"), str(REPO_ROOT),
                              str(int(real_mode)), str(int(verifier))], cwd=root, close_fds=True, timeout=20,
                             env={"PATH": "/usr/bin:/bin", "HOME": str(root), "PYTHON_DOTENV_DISABLED": "1"},
                             capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    output = json.loads(process.stdout)
    assert output["phase"] == ("verifier" if verifier else "solver")
    assert output["private_modules_absent"] is (not verifier)
    sealed_sources = json.loads((root / "evidence/runtime_sources.json").read_text())
    assert {record["relative_path"] for record in sealed_sources["sources"]} == expected
    if real_mode:
        assert {"eval/real_contracts.py", "eval/runtime_real.py", "eval/runtime_provenance.py"}.issubset(expected)
