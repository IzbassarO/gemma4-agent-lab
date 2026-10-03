"""Validate a v4 observation archive and publish only to a trusted private root.

No target or payload code is imported. No subprocess, network, installation or
remote-origin promotion is part of validation. Capture observations are untrusted.
"""

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import site
import sys
import sysconfig

from .archive import parse_archive
from .filesystem import anchored_root, load_snapshot, publish_evidence
from .metadata_policy import validate_wheel_observation_identity
from .receipt import build_receipt, importer_program_sha256, local_revision
from .record_policy import derive_distribution_rows, validate_interpreter_roots, validate_unique_console_ownership
from .schema import PolicyError, absolute_path, canonical_json, validate_manifest


@dataclass(frozen=True)
class ImportResult:
    result: str
    reason_codes: tuple
    archive_sha256: str | None = None
    receipt: dict | None = None
    receipt_sha256: str | None = None
    evidence_path: str | None = None
    publication: str | None = None
    publication_status: str | None = None
    durability: str | None = None
    warning_codes: tuple = ()

    def as_dict(self):
        return {
            "result": self.result, "reason_codes": list(self.reason_codes),
            "archive_sha256": self.archive_sha256, "receipt": self.receipt,
            "receipt_sha256": self.receipt_sha256, "evidence_path": self.evidence_path,
            "publication": self.publication, "publication_status": self.publication_status,
            "durability": self.durability, "warning_codes": list(self.warning_codes),
        }


def _validate_wheels(manifest):
    installations = set()
    for wheel in manifest["wheels"]:
        source = wheel["dataset_filename"]
        installed = wheel["installation_filename"]
        validate_wheel_observation_identity(source, installed, wheel["metadata"])
        if installed.casefold() in installations:
            raise PolicyError("WHEEL_RENAME")
        installations.add(installed.casefold())
        if "cutlass" in source.lower():
            raise PolicyError("WHEEL_PROFILE")


def _validate_bootstrap(manifest):
    bootstrap = manifest["bootstrap"]
    if not bootstrap["executed"]:
        if (bootstrap["recipe_id"] != "NOT_RUN" or bootstrap["diagnostic"] != "NOT_RUN"
                or bootstrap["return_code"] is not None or bootstrap["wheel_references"] or bootstrap["argv"]
                or any(bootstrap[field] != 0 for field in ("stdout_byte_count", "stderr_byte_count"))
                or any(bootstrap[field] is not None for field in ("stdout_sha256", "stderr_sha256"))):
            raise PolicyError("BOOTSTRAP_CONTRADICTION")
        return
    if bootstrap["recipe_id"] != "OFFICIAL_NOTEBOOK_CELL_2":
        raise PolicyError("BOOTSTRAP_CONTRADICTION")
    code = bootstrap["return_code"]
    diagnostic = "EXECUTION_ERROR" if code is None else "EXIT_ZERO" if code == 0 else "EXIT_NONZERO"
    if bootstrap["diagnostic"] != diagnostic:
        raise PolicyError("BOOTSTRAP_CONTRADICTION")
    empty = hashlib.sha256(b"").hexdigest()
    for count, digest in (("stdout_byte_count", "stdout_sha256"), ("stderr_byte_count", "stderr_sha256")):
        if bootstrap[digest] is None or (bootstrap[count] == 0 and bootstrap[digest] != empty):
            raise PolicyError("BOOTSTRAP_LOG_COMMITMENT")
    ordered = sorted(manifest["wheels"], key=lambda item: item["installation_filename"])
    if bootstrap["wheel_references"] != [item["dataset_filename"] for item in ordered]:
        raise PolicyError("BOOTSTRAP_WHEEL_REFERENCES")
    argv = bootstrap["argv"]
    command = [manifest["interpreter"]["executable"], "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall"]
    if len(argv) != len(command) + len(ordered) or argv[:len(command)] != command:
        raise PolicyError("BOOTSTRAP_ARGV")
    parents = set()
    for argument, wheel in zip(argv[len(command):], ordered):
        absolute_path(argument)
        path = PurePosixPath(argument)
        if path.name != wheel["installation_filename"]:
            raise PolicyError("BOOTSTRAP_ARGV")
        parents.add(str(path.parent))
    if len(parents) > 1:
        raise PolicyError("BOOTSTRAP_ARGV")


def _validate_physical_ownership(manifest):
    """Cross-distribution ordinary paths and console names share one namespace."""
    roots = {item["name"]: item["installation_root"] for item in manifest["distributions"]}
    paths = set()
    for observation in manifest["file_observations"]:
        if observation["path"].startswith("../"):
            path = manifest["interpreter"]["scripts_directory"] + "/" + observation["path"].rsplit("/", 1)[-1]
        else:
            path = roots[observation["distribution"]] + "/" + observation["path"]
        folded = path.casefold()
        if folded in paths:
            raise PolicyError("OBSERVATION_OWNERSHIP")
        paths.add(folded)
    for path in paths:
        if any(str(parent) in paths for parent in PurePosixPath(path).parents):
            raise PolicyError("OBSERVATION_COLLISION")


def validate_observations(contents):
    """Independently derive relationships; do not trust producer classifications."""
    manifest = validate_manifest(contents.manifest)
    validate_interpreter_roots(manifest["interpreter"])
    _validate_wheels(manifest)
    _validate_bootstrap(manifest)
    validate_unique_console_ownership(manifest["distributions"], manifest["interpreter"])
    records = {item["distribution"]: item["blob"] for item in manifest["record_evidence"]}
    grouped = {item["name"]: [] for item in manifest["distributions"]}
    references = set(records.values())
    for observation in manifest["file_observations"]:
        grouped[observation["distribution"]].append(observation)
        if observation["payload"] is not None:
            references.add(observation["payload"])
    if references != set(manifest["payload_references"]) or references != contents.blobs.keys():
        raise PolicyError("PAYLOAD_GRAPH")
    counts = {"owned_regular": 0, "excluded_volatile": 0, "verified_console_script": 0}
    byte_verified, digest_only = 0, 0
    for distribution in manifest["distributions"]:
        record = contents.blobs[records[distribution["name"]]]
        facts = derive_distribution_rows(distribution, manifest["interpreter"], record,
                                         grouped[distribution["name"]], contents.blobs)
        for category in facts["categories"].values():
            counts[category] += 1
        byte_verified += facts["byte_verified_observation_count"]
        digest_only += facts["digest_only_observation_count"]
    _validate_physical_ownership(manifest)
    commitment = "".join(digest + "\n" for digest in sorted(references)).encode("ascii")
    return {
        "payload_set_sha256": hashlib.sha256(commitment).hexdigest(),
        "payload_count": len(references), "record_coverage_complete": True,
        "record_row_count": sum(counts.values()), "row_category_counts": counts,
        "byte_verified_observation_count": byte_verified,
        "digest_only_observation_count": digest_only,
    }


def validate_snapshot(snapshot):
    contents = parse_archive(snapshot)
    return contents, validate_observations(contents)


def trusted_protected_roots(dataset_root, extra=()):
    """Only local configuration supplies exclusions; never read them from a capture."""
    if not dataset_root:
        raise PolicyError("LOCAL_DATASET_ROOT_REQUIRED")
    dataset = Path(dataset_root)
    if not dataset.is_absolute() or not dataset.is_dir():
        raise PolicyError("LOCAL_DATASET_ROOT_REQUIRED")
    roots = [Path(__file__).resolve().parents[2], dataset]
    env_dataset = os.environ.get("GEMMA4_DATASET_ROOT")
    if env_dataset:
        if not Path(env_dataset).is_absolute() or not Path(env_dataset).is_dir():
            raise PolicyError("LOCAL_DATASET_ROOT_REQUIRED")
        roots.append(Path(env_dataset))
    roots.extend(Path(value) for value in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix))
    roots.extend(Path(value) for value in site.getsitepackages())
    user = site.getusersitepackages()
    roots.extend(Path(value) for value in ([user] if isinstance(user, str) else user))
    roots.extend(Path(sysconfig.get_path(key)) for key in ("purelib", "platlib"))
    roots.extend(Path(value) for value in extra)
    if any(not root.is_absolute() for root in roots):
        raise PolicyError("LOCAL_PROTECTED_ROOT")
    return tuple(roots)


def import_capture(archive_path, *, harness_root, dataset_root, extra_protected_roots=()):
    """Validate and commit once; post-commit errors cannot become rejection."""
    digest = None
    publication = None
    teardown_warning = False
    try:
        protected = trusted_protected_roots(dataset_root, extra_protected_roots)
        with anchored_root(harness_root, protected) as root:
            snapshot = load_snapshot(Path(archive_path).absolute())
            digest = hashlib.sha256(snapshot).hexdigest()
            contents, facts = validate_snapshot(snapshot)
            receipt = build_receipt(contents, facts, git_revision=local_revision(),
                                    program_sha256=importer_program_sha256())
            raw_receipt = canonical_json(receipt)
            receipt_digest = hashlib.sha256(raw_receipt).hexdigest()
            publication = publish_evidence(root, snapshot, raw_receipt)
    except PolicyError as error:
        if publication is None:
            return ImportResult("CAPTURE_REJECTED", (error.code,), digest)
        teardown_warning = True
    except OSError:
        if publication is None:
            return ImportResult("CAPTURE_REJECTED", ("LOCAL_STORAGE",), digest)
        teardown_warning = True
    # Publication survived even a root-descriptor context teardown failure.
    warnings = set(publication.warning_codes)
    if teardown_warning:
        warnings.add("PUBLICATION_CLEANUP_UNCONFIRMED")
    path = str(publication.path)
    return ImportResult(
        "CAPTURE_VALIDATED", (), digest, receipt, receipt_digest,
        evidence_path=path, publication=path, publication_status=publication.status,
        durability=publication.durability, warning_codes=tuple(sorted(warnings)),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--harness-root", default=os.environ.get("GEMMA4_HARNESS_ROOT"))
    parser.add_argument("--dataset-root", default=os.environ.get("GEMMA4_DATASET_ROOT"))
    args = parser.parse_args(argv)
    if not args.harness_root:
        result = ImportResult("CAPTURE_REJECTED", ("LOCAL_ROOT_REQUIRED",))
    else:
        result = import_capture(args.archive, harness_root=args.harness_root, dataset_root=args.dataset_root)
    print(canonical_json(result.as_dict()).decode("utf-8"), end="")
    return 0 if result.result == "CAPTURE_VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
