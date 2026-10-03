"""Fabricated byte fixtures with literal expectations, unrelated to Kaggle."""

import base64
import csv
import hashlib
import io
import json
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture(distribution_names=("swegemma",), *, console=None, extra_files=None):
    interpreter = {"python_version": [3, 12, 1], "scheme": "posix_prefix",
                   "prefix": "/observed/python", "site_packages": ["/observed/python/lib/python3.12/site-packages"],
                   "scripts_directory": "/observed/python/bin", "executable": "/observed/python/bin/python"}
    blobs, distributions, records, observations = {}, [], [], []
    for name in distribution_names:
        package = {"swegemma": "swegemma", "adk-submission": "adk_submission", "adk-eval-core": "adk_eval_core"}[name]
        info = name.replace("-", "_") + "-0.2.7.dist-info"
        metadata = ("Metadata-Version: 2.1\nName: " + name + "\nVersion: 0.2.7\n\n").encode()
        source = package + "/__init__.py"
        files = {source: b"raise RuntimeError('MUST NEVER EXECUTE PAYLOAD')\n", info + "/METADATA": metadata}
        include = {source, info + "/RECORD"}
        declarations = (console or {}).get(name, [])
        if declarations:
            files[info + "/entry_points.txt"] = ("[console_scripts]\n" + "\n".join(item["name"] + " = " + item["target"] for item in declarations) + "\n").encode()
            for item in declarations:
                script = "../../../bin/" + item["name"]
                files[script] = b"#!/python\nraise RuntimeError('DO NOT RUN')\n"
                include.add(script)
        files.update((extra_files or {}).get(name, {}))
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        for path, data in sorted(files.items()):
            record_hash = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            writer.writerow((path, record_hash, str(len(data))))
            if path.startswith(package + "/") and path.endswith((".py", ".json", ".txt")):
                include.add(path)
        writer.writerow((info + "/RECORD", "", ""))
        raw_record = stream.getvalue().encode()
        files[info + "/RECORD"] = raw_record
        for path, data in sorted(files.items()):
            payload = digest(data) if path in include else None
            observations.append({"distribution": name, "path": path, "kind": "REGULAR", "size": len(data),
                                 "sha256": digest(data), "payload": payload, "links": 1})
            if payload:
                blobs[payload] = data
        distributions.append({"name": name, "version": "0.2.7", "installation_root": interpreter["site_packages"][0],
                              "dist_info": info, "metadata": {"raw_sha256": digest(metadata), "raw_size": len(metadata),
                              "projection": {"Metadata-Version": "2.1", "Name": name, "Version": "0.2.7"}},
                              "direct_url": {"present": False, "sha256": None, "size": None, "type": "ABSENT"},
                              "console_scripts": declarations})
        records.append({"distribution": name, "blob": digest(raw_record)})
    manifest = {"protocol": "H23_CAPTURE_PROTOCOL_V4", "captured_utc": "20261002T120000Z",
                "capture_program": {"sha256": "a" * 64, "notebook_sha256": None}, "interpreter": interpreter,
                "bootstrap": {"recipe_id": "NOT_RUN", "wheel_references": [], "argv": [], "executed": False,
                              "return_code": None, "stdout_byte_count": 0, "stderr_byte_count": 0,
                              "stdout_sha256": None, "stderr_sha256": None, "diagnostic": "NOT_RUN"},
                "wheels": [], "distributions": distributions, "record_evidence": records,
                "file_observations": observations, "payload_references": sorted(blobs)}
    return manifest, blobs


def archive_bytes(manifest, blobs):
    """Do not use the production archive builder to define accepted fixtures."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":"), allow_nan=False))
        for commitment, data in sorted(blobs.items()):
            archive.writestr("blobs/" + commitment, data)
    return stream.getvalue()


def reported_bootstrap(manifest, code):
    name = "swegemma-0.2.7-py3-none-any.whl"
    manifest["wheels"] = [{"dataset_filename": name, "installation_filename": name, "size": 100,
                           "sha256": "b" * 64, "metadata": {"Metadata-Version": "2.1", "Name": "swegemma", "Version": "0.2.7"}}]
    manifest["bootstrap"] = {"recipe_id": "OFFICIAL_NOTEBOOK_CELL_2", "wheel_references": [name],
                              "argv": ["/observed/python/bin/python", "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall", "/observed/temp/" + name],
                              "executed": True, "return_code": code, "stdout_byte_count": 0,
                              "stderr_byte_count": 17 if code else 0, "stdout_sha256": digest(b""),
                              "stderr_sha256": digest(b"observed failure\n\n") if code else digest(b""),
                              "diagnostic": "EXIT_NONZERO" if code else "EXIT_ZERO"}
    return manifest


def human_ledger():
    return {"schema": "H23_HUMAN_ATTESTATION_V1", "label": "HUMAN-ATTESTED", "git_commit_sha": "a" * 40,
            "executed_notebook_sha256": "b" * 64,
            "kaggle_notebook": {"owner": "a-person", "name": "capture-notebook", "immutable_version": 7,
                                "permalink": "https://www.kaggle.com/code/a-person/capture-notebook?scriptVersionId=7"},
            "wheelhouse_dataset": {"owner": "metric", "name": "wheelhouse", "immutable_version": 3},
            "capture_zip_sha256": "c" * 64, "validation_receipt_sha256": "d" * 64,
            "validation_receipt_reference": "external:evidence/capture.zip#receipt.json", "attester": "A Person",
            "attested_utc": "2026-10-02T12:00:00Z", "personally_checked": {
                "git_and_notebook_identity_checked": True, "immutable_notebook_version_and_permalink_checked": True,
                "immutable_wheelhouse_version_checked": True, "capture_zip_and_receipt_identity_checked": True,
                "receipt_scope_and_limitations_reviewed": True}}
