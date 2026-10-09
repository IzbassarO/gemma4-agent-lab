"""Separate, hash-admitted public wheels; no acquisition or network access."""
from __future__ import annotations

import io
import os
import re
import zipfile
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ._contracts import ContractError, closed_dict, hex_digest, identifier, load_json_object
from tools.common import tree_sha256
from tools.h23_v4.filesystem import anchor_directory, read_regular

_FIELDS = ("schema_version", "supplement_id", "target", "original_wheels_tree_sha256",
           "wheels_tree_sha256", "wheels", "install_requirements")
_WHEEL_FIELDS = ("filename", "distribution", "version", "sha256", "requires_python",
                 "requires_dist", "source_url", "reason", "role")


def _manifest(root, expected):
    hex_digest(expected, "supplement_manifest_sha256")
    with anchor_directory(root, private=True) as descriptor:
        if set(os.listdir(descriptor)) != {"manifest.json", "wheels"}:
            raise ContractError("supplement requires only manifest.json and wheels")
        snapshot = read_regular(descriptor, "manifest.json", max_bytes=4 * 1024 * 1024,
                                include=True, reject_hardlinks=True)
    if snapshot.sha256 != expected:
        raise ContractError("supplement manifest differs from operator authority")
    value = load_json_object(snapshot.data, "dependency supplement")
    closed_dict(value, allowed=_FIELDS, required=_FIELDS, name="dependency supplement")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("supplement schema must be 1")
    identifier(value["supplement_id"], "supplement_id")
    if value["target"] != {"python": "3.12", "platform": "linux_x86_64"}:
        raise ContractError("supplement must target Linux x86_64 CPython 3.12")
    for field in ("original_wheels_tree_sha256", "wheels_tree_sha256"):
        hex_digest(value[field], field)
    if type(value["wheels"]) is not list or not 1 <= len(value["wheels"]) <= 256:
        raise ContractError("supplement requires a bounded wheel inventory")
    if type(value["install_requirements"]) is not list:
        raise ContractError("supplement install requirements must be a list")
    return value


def validate_supplement(root: Path, expected_manifest_sha256: str, *, original_wheels_root: Path) -> dict:
    """Verify actual archive bytes, metadata and target tags against a reviewed manifest.

    Compatibility here checks target tags and Requires-Python. Actual Linux
    imports, plugin loading and offline task resolution remain operator checks.
    """
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.tags import compatible_tags, cpython_tags
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.version import Version
    from .runtime_real import _tree

    root = Path(root)
    original_wheels_root = Path(original_wheels_root)
    if root == original_wheels_root or root in original_wheels_root.parents or original_wheels_root in root.parents:
        raise ContractError("supplement and original wheelhouse must be separate")
    value = _manifest(root, expected_manifest_sha256)
    original = _tree(original_wheels_root)
    if tree_sha256(original) != value["original_wheels_tree_sha256"]:
        raise ContractError("supplement original wheelhouse binding differs")
    originals = {parse_wheel_filename(name)[0] for name in original}
    # Manylinux tags encode their minimum glibc. The actual worker's sys_tags
    # is checked again by the offline resolver; this is not host admission.
    platforms = ["manylinux_2_" + str(n) + "_x86_64" for n in range(5, 41)]
    platforms += ["manylinux1_x86_64", "manylinux2010_x86_64", "manylinux2014_x86_64"]
    target_tags = set(cpython_tags((3, 12), abis=["cp312"], platforms=platforms))
    target_tags.update(compatible_tags((3, 12), interpreter="cp312", platforms=platforms))
    expected_files, projects = {}, set()
    with anchor_directory(root / "wheels", private=True) as descriptor:
        for row in value["wheels"]:
            closed_dict(row, allowed=_WHEEL_FIELDS, required=_WHEEL_FIELDS, name="supplement wheel")
            name = row["filename"]
            if type(name) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+\-]*\.whl", name) is None:
                raise ContractError("supplement wheel filename is not a flat wheel path")
            project, version, _, tags = parse_wheel_filename(name)
            if (project != row["distribution"] or str(version) != row["version"]
                    or project in projects or project in originals or name in expected_files):
                raise ContractError("supplement cannot replace an original or duplicate distribution")
            if not tags & target_tags:
                raise ContractError("supplement wheel is incompatible with Linux CPython 3.12")
            hex_digest(row["sha256"], "supplement wheel sha256")
            if (row["role"] not in ("foundation", "native_plugin", "test_fixture", "transitive")
                    or type(row["reason"]) is not str or not row["reason"].strip()):
                raise ContractError("supplement wheel needs a public inclusion reason and role")
            source = urlsplit(row["source_url"])
            if (source.scheme != "https" or source.netloc != "files.pythonhosted.org"
                    or source.query or source.fragment or Path(unquote(source.path)).name != name):
                raise ContractError("supplement acquisition source must be an exact public PyPI wheel")
            observed = read_regular(descriptor, name, max_bytes=20 * 1024 * 1024,
                                    include=True, reject_hardlinks=True)
            if observed.sha256 != row["sha256"]:
                raise ContractError("supplement wheel hash differs from manifest")
            with zipfile.ZipFile(io.BytesIO(observed.data)) as archive:
                entries = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
                           and len(Path(n).parts) == 2]
                if len(entries) != 1:
                    raise ContractError("supplement wheel metadata is ambiguous")
                metadata = BytesParser().parsebytes(archive.read(entries[0]))
            dependencies = metadata.get_all("Requires-Dist", [])
            if (canonicalize_name(metadata["Name"] or "") != project
                    or Version(metadata["Version"] or "0") != version
                    or metadata.get("Requires-Python", "") != row["requires_python"]
                    or dependencies != row["requires_dist"]):
                raise ContractError("supplement wheel metadata differs from reviewed manifest")
            if type(row["requires_python"]) is not str or not SpecifierSet(row["requires_python"]).contains("3.12"):
                raise ContractError("supplement Requires-Python excludes 3.12")
            for dependency in dependencies:
                if Requirement(dependency).url is not None:
                    raise ContractError("supplement direct URL dependencies are not admitted")
            projects.add(project)
            expected_files[name] = observed.sha256
        if set(os.listdir(descriptor)) != set(expected_files):
            raise ContractError("supplement wheel inventory differs from manifest")
    if (tree_sha256(expected_files) != value["wheels_tree_sha256"]
            or _tree(root / "wheels") != expected_files
            or _tree(original_wheels_root) != original):
        raise ContractError("supplement or original bytes changed during admission")
    requirements = set()
    for text in value["install_requirements"]:
        parsed = Requirement(text)
        pin = next(row["version"] for row in value["wheels"] if row["distribution"] == parsed.name) if parsed.name in projects else None
        if (parsed.name not in projects or parsed.url or parsed.marker or parsed.extras
                or str(parsed.specifier) != "==" + str(pin) or text in requirements):
            raise ContractError("supplement installation must pin every reviewed distribution exactly")
        requirements.add(text)
    if requirements != {row["distribution"] + "==" + row["version"] for row in value["wheels"]}:
        raise ContractError("supplement install requirements are incomplete")
    # Recheck the manifest after archive reads and before returning authority.
    if _manifest(root, expected_manifest_sha256) != value:
        raise ContractError("supplement manifest changed during admission")
    return {"manifest_sha256": expected_manifest_sha256, "wheels_tree_sha256": value["wheels_tree_sha256"],
            "supplement_id": value["supplement_id"]}


def stage_supplement(source_root: Path, destination: Path, expected_manifest_sha256: str,
                     *, original_wheels_root: Path) -> dict:
    """Create fresh, phase-local copies; never merge into original assets."""
    identity = validate_supplement(source_root, expected_manifest_sha256, original_wheels_root=original_wheels_root)
    destination.mkdir(mode=0o700)
    (destination / "wheels").mkdir(mode=0o700)
    value = _manifest(source_root, expected_manifest_sha256)
    for name in ("manifest.json", *("wheels/" + row["filename"] for row in value["wheels"])):
        with anchor_directory(source_root, private=True) as descriptor:
            content = read_regular(descriptor, name, max_bytes=20 * 1024 * 1024,
                                   include=True, reject_hardlinks=True).data
        target = destination / name
        with anchor_directory(target.parent, private=True) as descriptor:
            fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
    if (validate_supplement(destination, expected_manifest_sha256, original_wheels_root=original_wheels_root) != identity
            or validate_supplement(source_root, expected_manifest_sha256, original_wheels_root=original_wheels_root) != identity):
        raise ContractError("supplement changed during staging")
    return identity
