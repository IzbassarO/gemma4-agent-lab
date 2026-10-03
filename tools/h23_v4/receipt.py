"""Technical receipts describe validation scope, never remote provenance."""

import hashlib
from pathlib import Path
import re

from .schema import canonical_json

POLICY_REVISION = "h23-v4.1"
LIMITATIONS = (
    "Does not establish Kaggle origin or truth of reported command execution.",
    "Does not establish successful installation or package installation causality.",
    "Does not establish hidden scorer identity or version.",
    "Does not establish runtime functionality, importability, or code safety.",
    "Does not independently verify omitted file or wheel bytes.",
    "Does not establish an exhaustive installation or arbitrary source secret-freedom.",
)
IMPORTER_FILES = (
    "tools/__init__.py", "tools/h23_v4/__init__.py", "tools/h23_v4/schema.py",
    "tools/h23_v4/metadata_policy.py", "tools/h23_v4/record_policy.py",
    "tools/h23_v4/archive.py", "tools/h23_v4/filesystem.py",
    "tools/h23_v4/importer.py", "tools/h23_v4/receipt.py",
)


def local_revision(project=None):
    """Read an available local HEAD; never run Git or invent a revision."""
    project = Path(project or Path(__file__).resolve().parents[2])
    try:
        git = project / ".git"
        head = (git / "HEAD").read_text(encoding="ascii").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            if (not re.fullmatch(r"refs/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", ref)
                    or any(component in (".", "..") for component in ref.split("/"))):
                return None
            try:
                head = (git / ref).read_text(encoding="ascii").strip()
            except FileNotFoundError:
                head = next((line.split(" ", 1)[0] for line in (git / "packed-refs").read_text(encoding="ascii").splitlines()
                             if line.endswith(" " + ref)), "")
        return head if re.fullmatch(r"[0-9a-f]{40}", head) else None
    except (OSError, UnicodeError):
        return None


def importer_program_sha256():
    project = Path(__file__).resolve().parents[2]
    commitments = {name: hashlib.sha256((project / name).read_bytes()).hexdigest() for name in IMPORTER_FILES}
    return hashlib.sha256(canonical_json(commitments)).hexdigest()


def build_receipt(contents, facts, *, git_revision=None, program_sha256=None):
    """No timestamp: the same snapshot and validator identity reimport exactly."""
    manifest = contents.manifest
    bootstrap = manifest["bootstrap"]
    return {
        "receipt_schema": "H23_VALIDATION_RECEIPT_V4",
        "protocol": "H23_CAPTURE_PROTOCOL_V4",
        "archive_sha256": contents.archive_sha256,
        "policy_revision": POLICY_REVISION,
        "importer_git_revision": git_revision,
        "importer_program_sha256": program_sha256,
        "validation_result": "CAPTURE_VALIDATED",
        "reason_codes": [],
        "publication_semantics": {
            "commit_point": "ATOMIC_NO_REPLACE_PUBLICATION",
            "validated_requires_committed_publication": True,
            "durability_reported_separately": True,
        },
        "bootstrap_observation": {
            "recipe_id": bootstrap["recipe_id"], "executed": bootstrap["executed"],
            "return_code": bootstrap["return_code"], "diagnostic": bootstrap["diagnostic"],
            "stdout_byte_count": bootstrap["stdout_byte_count"],
            "stderr_byte_count": bootstrap["stderr_byte_count"],
            "stdout_sha256": bootstrap["stdout_sha256"], "stderr_sha256": bootstrap["stderr_sha256"],
        },
        "distribution_identities": [
            {"name": item["name"], "version": item["version"]}
            for item in sorted(manifest["distributions"], key=lambda item: item["name"])
        ],
        "reported_wheel_commitment_count": len(manifest["wheels"]),
        "payload_set_sha256": facts["payload_set_sha256"],
        "payload_count": facts["payload_count"],
        "record_coverage_complete": facts["record_coverage_complete"],
        "record_row_count": facts["record_row_count"],
        "byte_verified_observation_count": facts["byte_verified_observation_count"],
        "digest_only_observation_count": facts["digest_only_observation_count"],
        "row_category_counts": facts["row_category_counts"],
        "technical_limitations": list(LIMITATIONS),
    }
