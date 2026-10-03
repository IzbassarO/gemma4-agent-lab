import ast
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from tools.h23_v4 import importer
from tools.h23_v4 import filesystem
from tools.h23_v4.schema import PolicyError
from .fixtures import archive_bytes, fixture, reported_bootstrap


def local_import(tmp_path, monkeypatch, raw):
    monkeypatch.delenv("GEMMA4_DATASET_ROOT", raising=False)
    tmp_path = tmp_path.resolve()
    dataset = tmp_path / "dataset"
    dataset.mkdir(exist_ok=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700, exist_ok=True)
    archive = tmp_path / "input.zip"
    archive.write_bytes(raw)
    return importer.import_capture(archive, harness_root=str(evidence), dataset_root=str(dataset))


def test_fabricated_local_capture_validates_without_origin_or_execution(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Importer invoked a subprocess")
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = local_import(tmp_path, monkeypatch, archive_bytes(*fixture()))
    assert result.result == "CAPTURE_VALIDATED"
    assert result.reason_codes == ()
    serialized = json.dumps(result.as_dict())
    for prohibited in ("VERSION_SPECIFIC", "VERSION-SPECIFIC", "source_status", "evidence_strength", "OFFICIAL_FULL_BOOTSTRAP", "BASE_IMAGE"):
        assert prohibited not in serialized
    assert result.receipt["byte_verified_observation_count"] == 2
    assert result.receipt["digest_only_observation_count"] == 1
    assert result.receipt["record_row_count"] == 3
    assert result.receipt["record_coverage_complete"] is True
    assert any("Kaggle origin" in value for value in result.receipt["technical_limitations"])


@pytest.mark.parametrize("code,diagnostic", [(0, "EXIT_ZERO"), (1, "EXIT_NONZERO")])
def test_success_and_failed_bootstrap_are_observations(tmp_path, monkeypatch, code, diagnostic):
    manifest, blobs = fixture()
    reported_bootstrap(manifest, code)
    result = local_import(tmp_path, monkeypatch, archive_bytes(manifest, blobs))
    assert result.result == "CAPTURE_VALIDATED"
    assert result.receipt["bootstrap_observation"]["return_code"] == code
    assert result.receipt["bootstrap_observation"]["diagnostic"] == diagnostic
    assert result.receipt["reported_wheel_commitment_count"] == 1


def test_execution_error_is_bounded_observation(tmp_path, monkeypatch):
    manifest, blobs = fixture()
    reported_bootstrap(manifest, 0)
    manifest["bootstrap"].update(return_code=None, diagnostic="EXECUTION_ERROR")
    result = local_import(tmp_path, monkeypatch, archive_bytes(manifest, blobs))
    assert result.result == "CAPTURE_VALIDATED"
    assert result.receipt["bootstrap_observation"]["diagnostic"] == "EXECUTION_ERROR"


@pytest.mark.parametrize("change,code", [
    (lambda m: m.update(protocol="H23_CAPTURE_PROTOCOL_V3"), "PROTOCOL_UNSUPPORTED"),
    (lambda m: m.update(source_status="VERSION-SPECIFIC"), "SCHEMA_FIELDS"),
    (lambda m: m["bootstrap"].update(return_code=0), "BOOTSTRAP_CONTRADICTION"),
    (lambda m: m["bootstrap"].update(diagnostic="EXIT_ZERO"), "BOOTSTRAP_CONTRADICTION"),
    (lambda m: m["distributions"][0]["metadata"].update(raw_sha256="f" * 64), "METADATA_OBSERVATION"),
    (lambda m: m["file_observations"][0].update(size=1), "RECORD_SIZE_MISMATCH"),
    (lambda m: m["distributions"][0].update(source_status="VERSION-SPECIFIC"), "SCHEMA_FIELDS"),
])
def test_contradictions_reject_without_publication(tmp_path, monkeypatch, change, code):
    manifest, blobs = fixture()
    change(manifest)
    result = local_import(tmp_path, monkeypatch, archive_bytes(manifest, blobs))
    assert result.result == "CAPTURE_REJECTED"
    assert result.reason_codes == (code,)
    assert result.evidence_path is None
    assert list((tmp_path / "evidence").iterdir()) == []


@pytest.mark.parametrize("field,value,code", [
    ("diagnostic", "EXIT_NONZERO", "BOOTSTRAP_CONTRADICTION"),
    ("wheel_references", [], "BOOTSTRAP_WHEEL_REFERENCES"),
    ("argv", ["pip", "install"], "BOOTSTRAP_ARGV"),
    ("stdout_sha256", None, "BOOTSTRAP_LOG_COMMITMENT"),
    ("stdout_sha256", "f" * 64, "BOOTSTRAP_LOG_COMMITMENT"),
])
def test_structured_command_relationships_recomputed(field, value, code):
    manifest, blobs = fixture()
    reported_bootstrap(manifest, 0)
    manifest["bootstrap"][field] = value
    with pytest.raises(PolicyError) as raised:
        importer.validate_snapshot(archive_bytes(manifest, blobs))
    assert raised.value.code == code


def test_payload_graph_cannot_hide_unused_referenced_blob():
    manifest, blobs = fixture()
    extra = b"unreferenced evidence"
    commitment = hashlib.sha256(extra).hexdigest()
    blobs[commitment] = extra
    manifest["payload_references"] = sorted(blobs)
    with pytest.raises(PolicyError) as raised:
        importer.validate_snapshot(archive_bytes(manifest, blobs))
    assert raised.value.code == "PAYLOAD_GRAPH"


def test_cross_distribution_console_ownership_rejected():
    declarations = [{"name": "UniqueTool", "target": "some_package.cli:main"}]
    manifest, blobs = fixture(("swegemma", "adk-submission"), console={"swegemma": declarations, "adk-submission": declarations})
    with pytest.raises(PolicyError) as raised:
        importer.validate_snapshot(archive_bytes(manifest, blobs))
    assert raised.value.code == "CONSOLE_OWNERSHIP"


def test_valid_console_payload_counts_independently():
    manifest, blobs = fixture(console={"swegemma": [{"name": "ExactTool", "target": "swegemma.cli:main"}]})
    _contents, facts = importer.validate_snapshot(archive_bytes(manifest, blobs))
    assert facts["row_category_counts"] == {"owned_regular": 3, "excluded_volatile": 1, "verified_console_script": 1}
    assert facts["byte_verified_observation_count"] == 3
    assert facts["digest_only_observation_count"] == 2


def test_omitted_payload_cannot_claim_independent_byte_verification():
    manifest, blobs = fixture(extra_files={"swegemma": {"swegemma/native.so": b"opaque binary"}})
    _contents, facts = importer.validate_snapshot(archive_bytes(manifest, blobs))
    assert facts["byte_verified_observation_count"] == 2
    assert facts["digest_only_observation_count"] == 2


def test_exact_reimport_and_complete_atomic_wrapper(tmp_path, monkeypatch):
    raw = archive_bytes(*fixture())
    first = local_import(tmp_path, monkeypatch, raw)
    second = local_import(tmp_path, monkeypatch, raw)
    assert first.result == second.result == "CAPTURE_VALIDATED"
    assert first.publication_status == "CREATED"
    assert second.publication_status == "IDEMPOTENT"
    assert first.publication == second.publication == first.evidence_path
    assert first.durability == second.durability == "CONFIRMED"
    assert first.warning_codes == second.warning_codes == ()
    path = Path(first.evidence_path)
    assert path.name == hashlib.sha256(raw).hexdigest() + ".evidence.zip"
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["capture.zip", "receipt.json"]
        assert archive.read("capture.zip") == raw
        receipt = archive.read("receipt.json")
        assert hashlib.sha256(receipt).hexdigest() == first.receipt_sha256
        assert json.loads(receipt)["validation_result"] == "CAPTURE_VALIDATED"
    assert not list(path.parent.glob(".h23v4-staging-*"))


@pytest.mark.parametrize("failure_call", [1, 2])
def test_prepublication_fsync_failure_rejects_without_committed_evidence(tmp_path, monkeypatch, failure_call):
    original = filesystem.os.fsync
    calls = 0
    def fail_before_commit(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("PRIVATE_FSYNC_ERROR_MUST_NOT_ESCAPE")
        original(descriptor)
    monkeypatch.setattr(filesystem.os, "fsync", fail_before_commit)
    result = local_import(tmp_path, monkeypatch, archive_bytes(*fixture()))
    evidence = tmp_path / "evidence"
    assert result.result == "CAPTURE_REJECTED"
    assert result.publication is result.evidence_path is result.durability is None
    assert not list(evidence.glob("*.evidence.zip"))
    assert not list(evidence.glob(".h23v4-staging-*"))
    assert "PRIVATE_FSYNC_ERROR" not in json.dumps(result.as_dict())


@pytest.mark.parametrize("failure_call", [3, 4])
def test_postpublication_fsync_failure_is_validated_with_explicit_durability(tmp_path, monkeypatch, failure_call):
    original = filesystem.os.fsync
    calls = 0
    def fail_after_commit(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("PRIVATE_FSYNC_ERROR_MUST_NOT_ESCAPE")
        original(descriptor)
    raw = archive_bytes(*fixture())
    with monkeypatch.context() as patch:
        patch.setattr(filesystem.os, "fsync", fail_after_commit)
        result = local_import(tmp_path, patch, raw)
    assert result.result == "CAPTURE_VALIDATED"
    assert result.reason_codes == ()
    assert result.publication == result.evidence_path
    assert result.publication_status == "CREATED"
    assert result.durability == "UNCONFIRMED"
    assert result.warning_codes == ("PUBLICATION_DURABILITY_UNCONFIRMED",)
    path = Path(result.publication)
    before_retry = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        assert archive.read("capture.zip") == raw
        receipt = json.loads(archive.read("receipt.json"))
    assert receipt["validation_result"] == result.result
    assert receipt["publication_semantics"] == {
        "commit_point": "ATOMIC_NO_REPLACE_PUBLICATION",
        "validated_requires_committed_publication": True,
        "durability_reported_separately": True,
    }
    assert "durability" not in receipt
    assert "PRIVATE_FSYNC_ERROR" not in json.dumps(result.as_dict())
    retry = local_import(tmp_path, monkeypatch, raw)
    assert retry.result == "CAPTURE_VALIDATED"
    assert retry.publication_status == "IDEMPOTENT"
    assert retry.publication == result.publication
    assert retry.durability == "CONFIRMED"
    assert retry.warning_codes == ()
    assert path.read_bytes() == before_retry


def test_publication_operation_failure_stays_rejected(tmp_path, monkeypatch):
    def failed_link(*args, **kwargs):
        raise OSError("PRIVATE_PUBLICATION_ERROR")
    monkeypatch.setattr(filesystem.os, "link", failed_link)
    result = local_import(tmp_path, monkeypatch, archive_bytes(*fixture()))
    assert result.result == "CAPTURE_REJECTED"
    assert result.reason_codes == ("LOCAL_STORAGE",)
    assert result.publication is result.evidence_path is None
    assert not list((tmp_path / "evidence").glob("*.evidence.zip"))
    assert not list((tmp_path / "evidence").glob(".h23v4-staging-*"))


@pytest.mark.parametrize("error", [OSError("PRIVATE_ROOT_CLOSE_ERROR"), PolicyError("LOCAL_ROOT_UNAVAILABLE")])
def test_root_context_teardown_cannot_reject_committed_import(tmp_path, monkeypatch, error):
    original = importer.anchored_root
    @contextmanager
    def failed_teardown(*args, **kwargs):
        with original(*args, **kwargs) as handle:
            yield handle
        raise error
    monkeypatch.setattr(importer, "anchored_root", failed_teardown)
    result = local_import(tmp_path, monkeypatch, archive_bytes(*fixture()))
    assert result.result == "CAPTURE_VALIDATED"
    assert result.publication == result.evidence_path
    assert result.durability == "CONFIRMED"
    assert result.warning_codes == ("PUBLICATION_CLEANUP_UNCONFIRMED",)
    with zipfile.ZipFile(result.publication) as archive:
        assert json.loads(archive.read("receipt.json"))["validation_result"] == result.result


def test_postcommit_staging_cleanup_failure_cannot_reject_import(tmp_path, monkeypatch):
    def failed_cleanup(*args, **kwargs):
        raise OSError("PRIVATE_CLEANUP_ERROR")
    raw = archive_bytes(*fixture())
    with monkeypatch.context() as patch:
        patch.setattr(filesystem, "_remove_staging", failed_cleanup)
        result = local_import(tmp_path, patch, raw)
    assert result.result == "CAPTURE_VALIDATED"
    assert result.publication_status == "CREATED"
    assert result.publication == result.evidence_path
    assert result.warning_codes == ("PUBLICATION_CLEANUP_UNCONFIRMED",)
    assert result.durability == "CONFIRMED"
    path = Path(result.publication)
    before_retry = path.read_bytes()
    assert list(path.parent.glob(".h23v4-staging-*"))
    retry = local_import(tmp_path, monkeypatch, raw)
    assert retry.result == "CAPTURE_VALIDATED"
    assert retry.publication_status == "IDEMPOTENT"
    assert path.read_bytes() == before_retry
    assert not list(path.parent.glob(".h23v4-staging-*"))


def test_one_snapshot_even_when_input_changes(tmp_path, monkeypatch):
    raw = archive_bytes(*fixture())
    original = importer.load_snapshot
    def read_then_change(path):
        snapshot = original(path)
        path.write_bytes(b"different input after read")
        return snapshot
    monkeypatch.setattr(importer, "load_snapshot", read_then_change)
    result = local_import(tmp_path, monkeypatch, raw)
    assert result.result == "CAPTURE_VALIDATED"
    assert result.archive_sha256 == hashlib.sha256(raw).hexdigest()
    with zipfile.ZipFile(result.evidence_path) as archive:
        assert archive.read("capture.zip") == raw


def test_missing_root_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMMA4_DATASET_ROOT", raising=False)
    dataset = (tmp_path / "dataset").resolve()
    dataset.mkdir()
    missing = tmp_path.resolve() / "not-created" / "evidence"
    result = importer.import_capture(tmp_path / "missing-input.zip", harness_root=str(missing), dataset_root=str(dataset))
    assert result.result == "CAPTURE_REJECTED"
    assert result.reason_codes == ("LOCAL_ROOT_UNAVAILABLE",)
    assert not missing.parent.exists()


def test_trusted_dataset_root_required(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMMA4_DATASET_ROOT", raising=False)
    result = importer.import_capture(tmp_path / "input.zip", harness_root=str(tmp_path / "missing"), dataset_root=None)
    assert result.result == "CAPTURE_REJECTED"
    assert result.reason_codes == ("LOCAL_DATASET_ROOT_REQUIRED",)


def test_cli_only_two_technical_terminal_results(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMMA4_HARNESS_ROOT", raising=False)
    assert importer.main([str(tmp_path / "missing.zip")]) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "CAPTURE_REJECTED"


def test_importer_and_validation_dependencies_have_no_execution_calls():
    root = Path(importer.__file__).parent
    for name in ("importer.py", "archive.py", "filesystem.py", "schema.py", "metadata_policy.py", "record_policy.py", "receipt.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name not in ("subprocess", "runpy", "importlib", "socket", "requests") for alias in node.names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec", "__import__")
