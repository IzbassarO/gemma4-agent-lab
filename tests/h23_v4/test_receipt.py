import hashlib

from tools.h23_v4.importer import validate_snapshot
from tools.h23_v4.receipt import build_receipt, importer_program_sha256, local_revision
from tools.h23_v4.schema import canonical_json
from .fixtures import archive_bytes, fixture, reported_bootstrap


def test_receipt_has_bounded_technical_scope_and_precise_counts():
    raw = archive_bytes(*fixture())
    contents, facts = validate_snapshot(raw)
    receipt = build_receipt(contents, facts, git_revision="a" * 40, program_sha256="b" * 64)
    assert receipt["receipt_schema"] == "H23_VALIDATION_RECEIPT_V4"
    assert receipt["protocol"] == "H23_CAPTURE_PROTOCOL_V4"
    assert receipt["policy_revision"] == "h23-v4.1"
    assert receipt["validation_result"] == "CAPTURE_VALIDATED"
    assert receipt["reason_codes"] == []
    assert receipt["publication_semantics"] == {
        "commit_point": "ATOMIC_NO_REPLACE_PUBLICATION",
        "validated_requires_committed_publication": True,
        "durability_reported_separately": True,
    }
    assert "durability" not in receipt
    assert "warning_codes" not in receipt
    assert receipt["archive_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["distribution_identities"] == [{"name": "swegemma", "version": "0.2.7"}]
    assert receipt["row_category_counts"] == {"owned_regular": 2, "excluded_volatile": 1, "verified_console_script": 0}
    expected_commitment = "".join(digest + "\n" for digest in sorted(contents.blobs)).encode()
    assert receipt["payload_set_sha256"] == hashlib.sha256(expected_commitment).hexdigest()
    assert receipt["payload_count"] == 2
    assert receipt["technical_limitations"] == [
        "Does not establish Kaggle origin or truth of reported command execution.",
        "Does not establish successful installation or package installation causality.",
        "Does not establish hidden scorer identity or version.",
        "Does not establish runtime functionality, importability, or code safety.",
        "Does not independently verify omitted file or wheel bytes.",
        "Does not establish an exhaustive installation or arbitrary source secret-freedom.",
    ]


def test_receipt_deterministic_and_failed_pip_stays_validated():
    manifest, blobs = fixture()
    reported_bootstrap(manifest, 1)
    contents, facts = validate_snapshot(archive_bytes(manifest, blobs))
    first = build_receipt(contents, facts)
    assert canonical_json(first) == canonical_json(build_receipt(contents, facts))
    assert first["validation_result"] == "CAPTURE_VALIDATED"
    assert first["bootstrap_observation"]["return_code"] == 1
    assert "installation_success" not in first
    assert "complete" not in first


def test_local_revision_reads_without_git_execution(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/branch\n")
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "branch").write_text("a" * 40 + "\n")
    assert local_revision(tmp_path) == "a" * 40
    (git / "HEAD").write_text("ref: ../../outside\n")
    assert local_revision(tmp_path) is None
    assert len(importer_program_sha256()) == 64


def test_packed_or_unavailable_git_revision(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/branch\n")
    (git / "packed-refs").write_text("b" * 40 + " refs/heads/branch\n")
    assert local_revision(tmp_path) == "b" * 40
    (git / "packed-refs").unlink()
    assert local_revision(tmp_path) is None


def test_malformed_local_ref_cannot_escape_git_directory(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/../../../outside\n")
    (tmp_path / "outside").write_text("f" * 40)
    assert local_revision(tmp_path) is None
