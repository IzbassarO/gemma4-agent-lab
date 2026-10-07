"""Fail-closed public stream staging, using only synthetic admitted task bytes."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from eval._contracts import ContractError
from eval.public_data import solver_task_from_public_row
from eval.solver_task import PublicAssetRef
from eval.staging import (
    MAX_PUBLIC_ASSET_BYTES, StagingError, stage_public_asset, stage_solver_assets,
    validate_staged_public_asset, validate_staged_solver_assets,
)
import eval.staging as staging
from eval.verifier_data import load_verifier_material
from eval.verifier_task import PrivateTestPatchRef, VerifierTask


@pytest.fixture
def admitted(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    row = {"instance_id": "fastapi_1", "repo": "fastapi/fastapi", "base_commit": "a" * 40,
           "problem_statement": "Synthetic issue", "hints_text": "",
           "patch": "REFERENCE_PATCH_SENTINEL", "test_patch": "PRIVATE_PATCH_SENTINEL"}
    (public / "tasks.jsonl").write_text(json.dumps(row) + "\n")
    contents = {
        "snapshots/fastapi_1.tgz": b"admitted snapshot bytes\n" * 12000,
        f"graphs/fastapi_{'a' * 40}.json": b"public graph data\n" * 100,
        f"embeddings/fastapi_{'a' * 40}.npz": b"public embedding data\n" * 100,
    }
    for relative, data in contents.items():
        path = public / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    task = solver_task_from_public_row(public, row)
    destination = tmp_path / "stage"
    destination.mkdir(mode=0o700)
    return public, task, destination, contents


def _no_published_asset(destination, asset):
    assert not (destination / asset.source_relative_path).exists()
    assert not list(destination.rglob(".runtime-staging-*"))


def test_only_explicit_public_assets_are_copied_into_fresh_private_inodes(admitted):
    public, task, destination, contents = admitted
    source_before = {relative: ((public / relative).read_bytes(), (public / relative).stat().st_mtime_ns)
                     for relative in contents}
    staged = stage_solver_assets(task, public, destination)
    assert staged == validate_staged_solver_assets(task, destination)
    assert {str(item.path.relative_to(destination)) for item in staged} == set(contents)
    assert {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()} == set(contents)
    assert not (destination / "tasks.jsonl").exists()
    for item in staged:
        assert item.path.read_bytes() == contents[item.asset.source_relative_path]
        source = (public / item.asset.source_relative_path).stat()
        copied = item.path.stat()
        assert (source.st_dev, source.st_ino) != (copied.st_dev, copied.st_ino)
        assert copied.st_nlink == 1 and stat.S_IMODE(copied.st_mode) == 0o600
        assert stat.S_IMODE(item.path.parent.stat().st_mode) == 0o700
    assert source_before == {relative: ((public / relative).read_bytes(), (public / relative).stat().st_mtime_ns)
                             for relative in contents}


@pytest.mark.parametrize("mutation", ["same_size", "truncate", "append", "replace"])
def test_mutation_after_admission_never_publishes(admitted, mutation):
    public, task, destination, _ = admitted
    source = public / task.snapshot.source_relative_path
    original = source.read_bytes()
    if mutation == "same_size":
        source.write_bytes(b"X" * len(original))
    elif mutation == "truncate":
        source.write_bytes(original[:-1])
    elif mutation == "append":
        source.write_bytes(original + b"X")
    else:
        replacement = source.with_name("new.tgz")
        replacement.write_bytes(b"X" * len(original))
        os.replace(replacement, source)
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot, destination)
    _no_published_asset(destination, task.snapshot)


@pytest.mark.parametrize("mutation", ["same_size", "copied_prefix", "truncate", "append", "replace", "parent_replace", "root_replace"])
def test_mutation_during_exact_descriptor_copy_never_publishes(admitted, monkeypatch, mutation):
    public, task, destination, _ = admitted
    source = public / task.snapshot.source_relative_path
    original = source.read_bytes()
    source_identity = (source.stat().st_dev, source.stat().st_ino)
    real_read = os.read
    mutated = False

    def mutate_on_read(descriptor, size):
        nonlocal mutated
        chunk = real_read(descriptor, size)
        current = os.fstat(descriptor)
        if chunk and not mutated and (current.st_dev, current.st_ino) == source_identity:
            mutated = True
            if mutation == "same_size":
                with source.open("r+b") as stream:
                    stream.seek(len(original) - 1)
                    stream.write(b"X")
            elif mutation == "copied_prefix":
                # Stream SHA can still match because this prefix was already
                # read; fstat identity catches the now changed original inode.
                with source.open("r+b") as stream:
                    stream.write(b"X")
            elif mutation == "truncate":
                with source.open("r+b") as stream:
                    stream.truncate(len(chunk) // 2)
            elif mutation == "append":
                with source.open("ab") as stream:
                    stream.write(b"X")
            elif mutation == "replace":
                replacement = source.with_name("replacement.tgz")
                replacement.write_bytes(original)
                os.replace(replacement, source)
            elif mutation == "parent_replace":
                old_parent = source.parent.with_name("old_snapshots")
                source.parent.rename(old_parent)
                source.parent.mkdir()
                source.write_bytes(original)
            else:
                public.rename(public.with_name("old_public"))
                source.parent.mkdir(parents=True)
                source.write_bytes(original)
        return chunk

    monkeypatch.setattr(staging.os, "read", mutate_on_read)
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot, destination)
    assert mutated
    _no_published_asset(destination, task.snapshot)


@pytest.mark.parametrize("wrong", ["sha", "size_small", "size_large"])
def test_wrong_admitted_identity_never_publishes(admitted, wrong):
    public, task, destination, _ = admitted
    asset = replace(task.snapshot, sha256="0" * 64) if wrong == "sha" else replace(
        task.snapshot, size_bytes=task.snapshot.size_bytes + (-1 if wrong == "size_small" else 1))
    with pytest.raises(StagingError):
        stage_public_asset(public, asset, destination)
    _no_published_asset(destination, asset)


def test_staged_stream_is_rehashed_before_publication(admitted, monkeypatch):
    public, task, destination, _ = admitted
    source = (public / task.snapshot.source_relative_path).stat()
    source_identity = (source.st_dev, source.st_ino)
    real_read = os.read
    mutated = False

    def corrupt_staged_copy(descriptor, size):
        nonlocal mutated
        current = os.fstat(descriptor)
        if not mutated and stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) != source_identity:
            mutated = True
            os.pwrite(descriptor, b"X", 0)
        return real_read(descriptor, size)

    monkeypatch.setattr(staging.os, "read", corrupt_staged_copy)
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot, destination)
    assert mutated
    _no_published_asset(destination, task.snapshot)


@pytest.mark.parametrize("mutation", ["root_replace", "namespace_replace"])
def test_destination_hierarchy_replacement_never_publishes(admitted, tmp_path, monkeypatch, mutation):
    public, task, destination, _ = admitted
    source = (public / task.snapshot.source_relative_path).stat()
    source_identity = (source.st_dev, source.st_ino)
    real_read = os.read
    mutated = False
    moved = tmp_path / "old_stage"

    def replace_target_on_read(descriptor, size):
        nonlocal mutated
        chunk = real_read(descriptor, size)
        info = os.fstat(descriptor)
        if chunk and not mutated and (info.st_dev, info.st_ino) == source_identity:
            mutated = True
            target = destination if mutation == "root_replace" else destination / "snapshots"
            target.rename(moved)
            target.mkdir(mode=0o700)
        return chunk

    monkeypatch.setattr(staging.os, "read", replace_target_on_read)
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot, destination)
    assert mutated
    _no_published_asset(destination, task.snapshot)
    assert not list(moved.rglob(".runtime-staging-*"))
    assert not list(moved.rglob("*.tgz"))


def test_legitimate_public_source_hardlinks_are_admitted_and_byte_copied(admitted, tmp_path):
    public, task, destination, _ = admitted
    source = public / task.snapshot.source_relative_path
    alias = tmp_path / "deduplicated-public.tgz"
    os.link(source, alias)
    assert source.stat().st_nlink == 2
    row = {"instance_id": task.instance_id, "repo": task.repo, "base_commit": task.base_commit,
           "problem_statement": task.problem_statement, "hints_text": task.hints_text}
    assert solver_task_from_public_row(public, row).snapshot == task.snapshot
    staged = stage_public_asset(public, task.snapshot, destination)
    assert staged.path.stat().st_nlink == 1
    assert staged.path.stat().st_ino != source.stat().st_ino
    alias.write_bytes(b"X" * task.snapshot.size_bytes)
    assert staged.path.read_bytes() != alias.read_bytes()
    assert validate_staged_public_asset(destination, task.snapshot) == staged


@pytest.mark.parametrize("bad", ["symlink", "hardlink", "mutation", "mode"])
def test_worker_revalidation_rejects_changed_or_aliased_staged_copy(admitted, tmp_path, bad):
    public, task, destination, _ = admitted
    staged = stage_public_asset(public, task.snapshot, destination)
    path = staged.path
    if bad == "symlink":
        path.unlink()
        path.symlink_to(public / task.snapshot.source_relative_path)
    elif bad == "hardlink":
        os.link(path, tmp_path / "alias")
    elif bad == "mutation":
        path.write_bytes(b"X" * task.snapshot.size_bytes)
    else:
        path.chmod(0o644)
    with pytest.raises(StagingError):
        validate_staged_public_asset(destination, task.snapshot)


def test_private_verifier_evidence_still_rejects_hardlinks(admitted, tmp_path):
    _, task, _, _ = admitted
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    content = b"PRIVATE_TEST_PATCH_SENTINEL"
    patch = root / "test.patch"
    patch.write_bytes(content)
    patch.chmod(0o600)
    verifier = VerifierTask(1, task.instance_id, task.repo, task.base_commit, task.snapshot,
                            PrivateTestPatchRef(hashlib.sha256(content).hexdigest(), len(content)), "a" * 64)
    assert load_verifier_material(verifier, private_root=root, test_patch_relative_path=patch.name).test_patch == content.decode()
    os.link(patch, tmp_path / "private_alias")
    with pytest.raises(ContractError):
        load_verifier_material(verifier, private_root=root, test_patch_relative_path=patch.name)


@pytest.mark.parametrize("bad", ["source_file_symlink", "source_parent_symlink", "source_root_symlink", "fifo",
                                "destination_root_symlink", "destination_parent_symlink", "destination_mode", "destination_parent_mode"])
def test_unsafe_path_or_special_file_fails_closed(admitted, tmp_path, bad):
    public, task, destination, _ = admitted
    source = public / task.snapshot.source_relative_path
    if bad == "source_file_symlink":
        real = tmp_path / "source.tgz"
        source.rename(real)
        source.symlink_to(real)
    elif bad == "source_parent_symlink":
        real = tmp_path / "real_snapshots"
        source.parent.rename(real)
        source.parent.symlink_to(real, target_is_directory=True)
    elif bad == "source_root_symlink":
        alias = tmp_path / "public_alias"
        alias.symlink_to(public, target_is_directory=True)
        public = alias
    elif bad == "fifo":
        source.unlink()
        os.mkfifo(source)
    elif bad == "destination_root_symlink":
        alias = tmp_path / "destination_alias"
        alias.symlink_to(destination, target_is_directory=True)
        destination = alias
    elif bad == "destination_parent_symlink":
        (destination / "snapshots").symlink_to(source.parent, target_is_directory=True)
    elif bad == "destination_mode":
        destination.chmod(0o755)
    else:
        (destination / "snapshots").mkdir(mode=0o755)
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot, destination)
    assert not list(destination.rglob(".runtime-staging-*"))


def test_existing_destination_is_never_replaced(admitted):
    public, task, destination, _ = admitted
    staged = stage_public_asset(public, task.snapshot, destination)
    before = staged.path.stat()
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot, destination)
    assert staged.path.stat().st_ino == before.st_ino
    assert validate_staged_public_asset(destination, task.snapshot) == staged
    assert not list(destination.rglob(".runtime-staging-*"))


@pytest.mark.parametrize("bad", [None, "root", Path("relative")])
def test_roots_are_explicit_absolute_paths(admitted, bad):
    public, task, destination, _ = admitted
    with pytest.raises(StagingError):
        stage_public_asset(bad, task.snapshot, destination)
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot, bad)


def test_source_and_staging_roots_cannot_overlap(admitted):
    public, task, destination, _ = admitted
    nested = public / "runtime"
    nested.mkdir(mode=0o700)
    for source_root, stage_root in ((public, public), (public, nested), (destination, destination.parent)):
        with pytest.raises(StagingError):
            stage_public_asset(source_root, task.snapshot, stage_root)


@pytest.mark.parametrize("relationship", ["same_root", "child"])
def test_case_alias_source_overlap_is_rejected_before_source_mutation(admitted, relationship):
    public, task, _, _ = admitted
    public.chmod(0o700)
    alias = public.with_name(public.name.swapcase())
    if not alias.exists() or not alias.samefile(public):
        pytest.skip("filesystem does not provide case-insensitive directory aliases")
    if relationship == "child":
        (public / "runtime").mkdir(mode=0o700)
        destination = alias / "runtime"
    else:
        destination = alias
    assert public != destination and public not in destination.parents

    def source_state():
        paths = (public, *sorted(public.rglob("*")))
        return {path.relative_to(public).as_posix(): (
            path.stat().st_ino, path.stat().st_mode, path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None) for path in paths}

    before = source_state()
    with pytest.raises(StagingError, match="roots must be separate"):
        stage_public_asset(public, task.snapshot, destination)
    assert source_state() == before
    assert not list(public.rglob(".runtime-staging-*"))


def test_staging_contract_and_size_limit_are_closed(admitted):
    public, task, destination, _ = admitted
    with pytest.raises(StagingError):
        stage_public_asset(public, task.snapshot.to_dict(), destination)
    with pytest.raises(StagingError):
        stage_solver_assets(task.to_dict(), public, destination)
    oversized = replace(task.snapshot, size_bytes=MAX_PUBLIC_ASSET_BYTES + 1)
    with pytest.raises(StagingError):
        stage_public_asset(public, oversized, destination)


def test_optional_assets_are_never_discovered(admitted):
    public, task, destination, _ = admitted
    snapshot_only = replace(task, graph=None, embedding=None)
    assert len(stage_solver_assets(snapshot_only, public, destination)) == 1
    assert not (destination / "graphs").exists() and not (destination / "embeddings").exists()
