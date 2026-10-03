"""Read-only format checking for a separate, manually authored project ledger.

Passing this checker establishes only ledger syntax. It neither authenticates a
person nor confirms a run, and it does not alter an importer receipt or H23 status.
"""

import argparse
import datetime as dt
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .schema import PolicyError, canonical_json, exact_fields, integer, sha256, strict_json, string

CHECKLIST = (
    "git_and_notebook_identity_checked",
    "immutable_notebook_version_and_permalink_checked",
    "immutable_wheelhouse_version_checked",
    "capture_zip_and_receipt_identity_checked",
    "receipt_scope_and_limitations_reviewed",
)


def validate_attestation(value):
    exact_fields(value, ("schema", "label", "git_commit_sha", "executed_notebook_sha256",
                         "kaggle_notebook", "wheelhouse_dataset", "capture_zip_sha256",
                         "validation_receipt_sha256", "validation_receipt_reference", "attester",
                         "attested_utc", "personally_checked"))
    if value["schema"] != "H23_HUMAN_ATTESTATION_V1" or value["label"] != "HUMAN-ATTESTED":
        raise PolicyError("ATTESTATION_SCHEMA")
    string(value["git_commit_sha"], pattern=r"[0-9a-f]{40}")
    for field in ("executed_notebook_sha256", "capture_zip_sha256", "validation_receipt_sha256"):
        sha256(value[field])
    notebook = value["kaggle_notebook"]
    exact_fields(notebook, ("owner", "name", "immutable_version", "permalink"))
    dataset = value["wheelhouse_dataset"]
    exact_fields(dataset, ("owner", "name", "immutable_version"))
    for identity in (notebook, dataset):
        for field in ("owner", "name"):
            string(identity[field], pattern=r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
        integer(identity["immutable_version"], 1, 2**31 - 1)
    string(notebook["permalink"])
    try:
        url = urlsplit(notebook["permalink"])
    except ValueError:
        raise PolicyError("ATTESTATION_PERMALINK") from None
    expected_path = "/code/" + notebook["owner"] + "/" + notebook["name"]
    if (url.scheme != "https" or url.netloc != "www.kaggle.com" or url.path != expected_path
            or url.fragment or parse_qs(url.query, keep_blank_values=True) != {
                "scriptVersionId": [str(notebook["immutable_version"])]}):
        raise PolicyError("ATTESTATION_PERMALINK")
    string(value["validation_receipt_reference"])
    string(value["attester"])
    if len(value["attester"]) > 128 or len(value["validation_receipt_reference"]) > 2048:
        raise PolicyError("ATTESTATION_LIMIT")
    string(value["attested_utc"], pattern=r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    try:
        dt.datetime.strptime(value["attested_utc"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise PolicyError("ATTESTATION_TIMESTAMP") from None
    exact_fields(value["personally_checked"], CHECKLIST)
    if any(value["personally_checked"][field] is not True for field in CHECKLIST):
        raise PolicyError("ATTESTATION_CHECKLIST")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.ledger.open("rb") as stream:
            value = strict_json(stream.read(8 * 1024 * 1024 + 1))
        validate_attestation(value)
        result = {"result": "HUMAN-ATTESTED", "format_only": True, "reason_codes": []}
    except PolicyError as error:
        result = {"result": "ATTESTATION_REJECTED", "reason_codes": [error.code]}
    except OSError:
        result = {"result": "ATTESTATION_REJECTED", "reason_codes": ["LOCAL_LEDGER_UNAVAILABLE"]}
    print(canonical_json(result).decode("utf-8"), end="")
    return 0 if result["result"] == "HUMAN-ATTESTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
