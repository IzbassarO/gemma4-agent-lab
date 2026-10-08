"""Linux process/account and evidence failure boundaries, all mocked on CPU."""
import json
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

from eval._contracts import ContractError
from eval import runtime, runtime_linux, runtime_provenance, worker_common
from eval.runtime_result import ArtifactRef
from tools.common import REPO_ROOT, sha256_bytes, tree_sha256


class ProcEntry:
    def __init__(self, name, value, accesses):
        self.name, self.value, self.accesses = name, value, accesses

    def __truediv__(self, component):
        assert component == "status", "process contents/environment must not be opened"
        return self

    def read_text(self):
        self.accesses.append(self.name)
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def proc_fixture(monkeypatch, entries, *, unavailable=False):
    accesses = []
    values = [ProcEntry(name, value, accesses) for name, value in entries]

    def iterdir():
        if unavailable:
            raise PermissionError("fixture")
        return iter(values)

    def path_factory(value):
        assert value == "/proc"
        return SimpleNamespace(iterdir=iterdir)

    monkeypatch.setattr(runtime_linux, "Path", path_factory)
    return accesses


def test_process_inventory_reads_only_uid_metadata_and_handles_vanished_pid(monkeypatch):
    accesses = proc_fixture(monkeypatch, [
        ("self", "NEVER_READ"), ("20", "Name: fixture\nUid:\t1001\t1001\t1001\t1001\n"),
        ("21", "Uid:\t0\t0\t0\t0\n"), ("22", FileNotFoundError()),
        ("23", "Uid:\t0\t1001\t0\t0\n"),
    ])
    assert runtime_linux.worker_pids(1001) == {20, 23}
    assert accesses == ["20", "21", "22", "23"]


@pytest.mark.parametrize("case", ["no_proc", "denied_status", "missing_uid"])
def test_process_inventory_unavailable_or_unidentifiable_pid_fails_closed(monkeypatch, case):
    value = PermissionError() if case == "denied_status" else "Name: missing identity\n"
    proc_fixture(monkeypatch, [("20", value)], unavailable=case == "no_proc")
    with pytest.raises(ContractError):
        runtime_linux.worker_pids(1001)


def test_dedicated_worker_account_with_existing_process_refuses_before_probe(monkeypatch):
    monkeypatch.setattr(runtime_linux, "linux_prefix", lambda user: (["/mock/runuser"], "runuser"))
    monkeypatch.setattr(runtime_linux, "worker_account", lambda user: SimpleNamespace(pw_uid=1001, pw_gid=1001))
    monkeypatch.setattr(runtime_linux, "worker_pids", lambda uid: {20})
    monkeypatch.setattr(runtime_linux.subprocess, "run", lambda *a, **k: pytest.fail("probe started"))
    with pytest.raises(ContractError, match="dedicated"):
        runtime_linux.verify_worker_confinement(SimpleNamespace(worker_user="gemma_worker"), {})


@pytest.mark.parametrize("case", ["pass", "set_failed", "get_failed", "not_enabled"])
def test_subreaper_enablement_is_verified_and_fail_closed(monkeypatch, case):
    calls = []

    def prctl(operation, value, *extras):
        calls.append(operation)
        if operation == 36:
            assert value == 1
            return -1 if case == "set_failed" else 0
        assert operation == 37
        value._obj.value = 0 if case == "not_enabled" else 1
        return -1 if case == "get_failed" else 0

    monkeypatch.setattr(runtime_linux.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(prctl=prctl))
    if case == "pass":
        runtime_linux.enable_subreaper()
        assert calls == [36, 37]
    else:
        with pytest.raises(ContractError):
            runtime_linux.enable_subreaper()


def test_descendant_reaping_uses_dedicated_uid_inventory_and_waits_even_after_setsid(monkeypatch):
    monkeypatch.setattr(runtime_linux, "worker_account", lambda user: SimpleNamespace(pw_uid=1001, pw_gid=1001))
    scans = iter(({20, 21}, set()))
    identities, kills, waits = [], [], []

    def inventory(uid):
        identities.append(uid)
        return next(scans)

    monkeypatch.setattr(runtime_linux, "worker_pids", inventory)
    monkeypatch.setattr(runtime_linux.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(runtime_linux.os, "waitpid", lambda pid, options: waits.append((pid, options)))
    monkeypatch.setattr(runtime_linux.time, "sleep", lambda duration: None)
    runtime_linux.terminate_worker_descendants("gemma_worker")
    assert identities == [1001, 1001]
    assert set(kills) == {(20, signal.SIGKILL), (21, signal.SIGKILL)}
    assert {pid for pid, _ in waits} == {20, 21}


@pytest.mark.parametrize("case", ["remain", "kill_denied"])
def test_descendant_failure_refuses_private_evidence_progress(monkeypatch, case):
    monkeypatch.setattr(runtime_linux, "worker_account", lambda user: SimpleNamespace(pw_uid=1001, pw_gid=1001))
    monkeypatch.setattr(runtime_linux, "worker_pids", lambda uid: {20})
    monkeypatch.setattr(runtime_linux.time, "monotonic", lambda: 0.0)

    def kill(pid, sig):
        if case == "kill_denied":
            raise PermissionError()

    monkeypatch.setattr(runtime_linux.os, "kill", kill)
    monkeypatch.setattr(runtime_linux.os, "waitpid", lambda *a: None)
    with pytest.raises(ContractError):
        runtime_linux.terminate_worker_descendants("gemma_worker", timeout=0)


def evidence(tmp_path):
    root = tmp_path / "worker"
    root.mkdir(mode=0o700)
    (root / "evidence").mkdir(mode=0o700)
    return root


def private_text(path, value):
    path.write_text(value)
    path.chmod(0o600)


def test_process_log_sanitizer_removes_credentials_and_preserves_private_mode(tmp_path):
    root = evidence(tmp_path)
    log = root / "evidence/process.log"
    private_text(log, 'Authorization: "Bearer LOG_SECRET_SENTINEL"\napi_key=KEY_SENTINEL\npublic diagnostic\n')
    runtime._sanitize_worker_log(log)
    assert "SENTINEL" not in log.read_text()
    assert "public diagnostic" in log.read_text()
    assert log.stat().st_mode & 0o777 == 0o600 and log.stat().st_nlink == 1


def test_failed_real_worker_archive_exports_only_sanitized_log(tmp_path):
    root = evidence(tmp_path)
    private_text(root / "evidence/process.log", "Bearer FAILED_LOG_SENTINEL\npublic diagnostic\n")
    private_text(root / "evidence/partial_trace.json", '{"api_key":"UNSEALED_TRACE_SENTINEL"}')
    private_text(root / "evidence/partial.txt", "UNSEALED_BYTES_SENTINEL")
    destination = tmp_path / "archive"
    runtime._archive_phase(root, destination, real=True)
    assert (destination / "process.log").is_file()
    assert "SENTINEL" not in (destination / "process.log").read_text()
    assert "public diagnostic" in (destination / "process.log").read_text()
    assert not (destination / "partial_trace.json").exists() and not (destination / "partial.txt").exists()


@pytest.mark.parametrize("case", ["unsealed", "credential", "changed_seal"])
def test_real_archive_refuses_extra_secret_or_changed_sealed_artifact(tmp_path, case):
    root = evidence(tmp_path)
    path = root / "evidence/result.json"
    private_text(path, '{"status":"public"}')
    data = path.read_bytes()
    ref = ArtifactRef("result", "result.json", sha256_bytes(data), len(data))
    if case == "unsealed":
        private_text(root / "evidence/zz-extra.json", '{"private":"unsealed"}')
    elif case == "credential":
        private_text(path, '{"api_key":"SECRET_SENTINEL"}')
        data = path.read_bytes()
        ref = ArtifactRef("result", "result.json", sha256_bytes(data), len(data))
    else:
        private_text(path, '{"status":"mutated"}')
    with pytest.raises(ContractError):
        runtime._archive_phase(root, tmp_path / "archive", (ref,), real=True)
    if case == "credential":
        assert not (tmp_path / "archive/result.json").exists()


def test_real_archive_accepts_private_sealed_bytes_and_scrubs_unsealed_process_log(tmp_path):
    root = evidence(tmp_path)
    private_text(root / "evidence/result.json", '{"status":"public"}')
    private_text(root / "evidence/process.log", "password=LOG_SENTINEL\n")
    data = (root / "evidence/result.json").read_bytes()
    ref = ArtifactRef("result", "result.json", sha256_bytes(data), len(data))
    destination = tmp_path / "archive"
    runtime._archive_phase(root, destination, (ref,), real=True)
    assert (destination / "result.json").read_bytes() == data
    assert "LOG_SENTINEL" not in (destination / "process.log").read_text()
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in destination.iterdir())


def staged_code_request(tmp_path, *, verifier=False):
    root = evidence(tmp_path)
    records = runtime_provenance.runtime_source_records(REPO_ROOT)
    runtime._stage_code(root, verifier=verifier, real=True, source_records=records)
    return {"runtime": {"worker_root": str(root)}, "provenance": {"runtime_sources": records,
        "staged_source_paths": list(runtime._phase_code_paths(verifier=verifier, real=True)),
        "eval_infra_source_identity": {"runtime_source_sha256": tree_sha256({path: value["sha256"]
                                                        for path, value in records.items()})}},
        "mode": "real_public", "task": {}, **({"returned_patch": ""} if verifier else {})}, root


@pytest.mark.parametrize("verifier", [False, True])
def test_real_staged_source_inventory_validates_actual_private_code_namespace(tmp_path, verifier):
    request, root = staged_code_request(tmp_path, verifier=verifier)
    assert root.joinpath("code").stat().st_mode & 0o777 == 0o700
    worker_common.validate_worker_sources(request)
    assert root.joinpath("code/eval/verifier_data.py").exists() is verifier


@pytest.mark.parametrize("case", ["remove_record", "forged_record", "extra_code", "removed_code", "subset_mutation"])
def test_real_source_validation_retains_coordinator_authority_over_mutable_inventory(tmp_path, case):
    request, root = staged_code_request(tmp_path)
    inventory_path = root / "evidence/runtime_sources.json"
    inventory = json.loads(inventory_path.read_text())
    if case == "remove_record":
        inventory["sources"].pop()
        private_text(inventory_path, json.dumps(inventory))
    elif case == "forged_record":
        inventory["sources"][0]["sha256"] = "1" * 64
        private_text(inventory_path, json.dumps(inventory))
    elif case == "extra_code":
        private_text(root / "code/eval/unadmitted.py", "unadmitted code")
    elif case == "removed_code":
        (root / "code" / inventory["sources"][0]["relative_path"]).unlink()
    else:
        request["provenance"]["staged_source_paths"].pop()
    with pytest.raises(ContractError):
        worker_common.validate_worker_sources(request)
