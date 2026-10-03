import json

import pytest

from tools.h23_v4.attestation import main, validate_attestation
from tools.h23_v4.schema import PolicyError
from .fixtures import human_ledger


def test_human_attestation_is_separate_format_only():
    ledger = human_ledger()
    assert validate_attestation(ledger)["label"] == "HUMAN-ATTESTED"
    assert "validation_result" not in ledger
    assert "source_status" not in ledger


@pytest.mark.parametrize("mutate,code", [
    (lambda v: v.update(label="INTERACTIVE_KAGGLE_VERSION_SPECIFIC"), "ATTESTATION_SCHEMA"),
    (lambda v: v.update(evidence_strength="official"), "SCHEMA_FIELDS"),
    (lambda v: v.update(git_commit_sha="bad"), "SCHEMA_STRING"),
    (lambda v: v["kaggle_notebook"].update(immutable_version=8), "ATTESTATION_PERMALINK"),
    (lambda v: v["kaggle_notebook"].update(permalink="https://[invalid-host/code/a-person/capture-notebook"), "ATTESTATION_PERMALINK"),
    (lambda v: v["wheelhouse_dataset"].update(immutable_version=True), "SCHEMA_INTEGER"),
    (lambda v: v["personally_checked"].update(receipt_scope_and_limitations_reviewed=False), "ATTESTATION_CHECKLIST"),
    (lambda v: v.update(attested_utc="2026-02-31T12:00:00Z"), "ATTESTATION_TIMESTAMP"),
])
def test_ledger_requires_literal_personal_checks_and_immutable_ids(mutate, code):
    ledger = human_ledger()
    mutate(ledger)
    with pytest.raises(PolicyError) as raised:
        validate_attestation(ledger)
    assert raised.value.code == code


def test_readonly_cli_writes_no_ledger_or_receipt(tmp_path, capsys):
    path = tmp_path / "ledger.json"
    raw = json.dumps(human_ledger()).encode()
    path.write_bytes(raw)
    assert main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"result": "HUMAN-ATTESTED", "format_only": True, "reason_codes": []}
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]
