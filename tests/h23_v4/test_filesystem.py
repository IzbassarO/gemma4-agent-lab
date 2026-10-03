"""Descriptor and publication invariants against temporary protected trees."""

import hashlib
import io
import os
from pathlib import Path
import stat
import zipfile

import pytest

from tools.h23_v4.filesystem import anchor_directory, anchored_root, load_snapshot, publish_evidence, read_regular
from tools.h23_v4.schema import PolicyError


@pytest.fixture
def roots(tmp_path):
    base = tmp_path.resolve()
    evidence = base / "evidence"
    evidence.mkdir(mode=0o700)
    protected = base / "protected"
    protected.mkdir()
    return evidence, protected


def test_missing_root_creates_nothing(roots):
    evidence, protected = roots
    missing = evidence / "missing" / "nested"
    before = sorted(evidence.iterdir())
    with pytest.raises(PolicyError) as raised:
        with anchored_root(missing, [protected]):
            pytest.fail("missing root was accepted")
    assert raised.value.code == "LOCAL_ROOT_UNAVAILABLE"
    assert sorted(evidence.iterdir()) == before


def test_relative_root_rejected(roots):
    _, protected = roots
    with pytest.raises(PolicyError) as raised:
        with anchored_root(Path("relative"), [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_PATH"


@pytest.mark.parametrize("suffix", ["/", "/./", "/../evidence", "\n", "\\alias"])
def test_raw_root_syntax_rejected_before_path_normalization(roots, suffix):
    evidence, protected = roots
    with pytest.raises(PolicyError) as raised:
        with anchored_root(str(evidence) + suffix, [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_PATH"


def test_double_slash_root_rejected(roots):
    evidence, protected = roots
    with pytest.raises(PolicyError) as raised:
        with anchored_root("/" + str(evidence), [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_PATH"


@pytest.mark.parametrize("permission", [0o755, 0o770, 0o707])
def test_root_must_be_private(roots, permission):
    evidence, protected = roots
    evidence.chmod(permission)
    with pytest.raises(PolicyError) as raised:
        with anchored_root(evidence, [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_PERMISSIONS"


def test_root_ownership_rejected(roots, monkeypatch):
    import tools.h23_v4.filesystem as module
    evidence, protected = roots
    monkeypatch.setattr(module.os, "getuid", lambda: os.stat(evidence).st_uid + 1)
    with pytest.raises(PolicyError) as raised:
        with anchored_root(evidence, [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_PERMISSIONS"


def test_root_inside_protected_tree(roots):
    _, protected = roots
    nested = protected / "evidence"
    nested.mkdir(mode=0o700)
    with pytest.raises(PolicyError) as raised:
        with anchored_root(nested, [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_PROTECTED"
    assert not list(nested.iterdir())


def test_root_containing_protected_tree(roots):
    evidence, _ = roots
    nested = evidence / "repo"
    nested.mkdir()
    with pytest.raises(PolicyError) as raised:
        with anchored_root(evidence, [nested]):
            pass
    assert raised.value.code == "LOCAL_ROOT_PROTECTED"
    assert list(evidence.iterdir()) == [nested]


@pytest.mark.parametrize("marker", ["directory", "worktree_file", "dangling_symlink"])
def test_root_inside_unrelated_git_repository_rejected_before_writes(roots, marker):
    evidence, protected = roots
    repository = evidence.parent / "unrelated-repository"
    repository.mkdir(mode=0o700)
    if marker == "directory":
        (repository / ".git").mkdir()
    elif marker == "worktree_file":
        (repository / ".git").write_text("gitdir: /elsewhere\n")
    else:
        (repository / ".git").symlink_to(repository / "missing")
    nested = repository / "private-evidence"
    nested.mkdir(mode=0o700)
    with pytest.raises(PolicyError) as raised:
        with anchored_root(nested, [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_GIT"
    assert not list(nested.iterdir())


@pytest.mark.parametrize("alias", [None, "HEAD", "objects", "refs"])
def test_root_inside_unrelated_bare_repository_rejected_before_writes(roots, alias):
    evidence, protected = roots
    repository = evidence.parent / "unrelated-bare.git"
    repository.mkdir(mode=0o700)
    for name in ("HEAD", "objects", "refs"):
        if name == alias:
            (repository / name).symlink_to(repository / "missing-marker")
        elif name == "HEAD":
            (repository / name).write_text("ref: refs/heads/main\n")
        else:
            (repository / name).mkdir()
    nested = repository / "private-evidence"
    nested.mkdir(mode=0o700)
    with pytest.raises(PolicyError) as raised:
        with anchored_root(nested, [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_GIT"
    assert not list(nested.iterdir())


@pytest.mark.parametrize("names", [("HEAD",), ("objects",), ("HEAD", "objects"), ("HEAD", "refs"), ("objects", "refs")])
def test_incomplete_bare_markers_are_not_git_repositories(roots, names):
    evidence, protected = roots
    parent = evidence.parent / "ordinary-parent"
    parent.mkdir(mode=0o700)
    for name in names:
        if name == "HEAD":
            (parent / name).write_text("ordinary text\n")
        else:
            (parent / name).mkdir()
    nested = parent / "private-evidence"
    nested.mkdir(mode=0o700)
    with anchored_root(nested, [protected]) as handle:
        assert handle.path == nested
    assert not list(nested.iterdir())


def test_root_containing_unrelated_git_repository_rejected_before_writes(roots):
    evidence, protected = roots
    repository = evidence / "unrelated-repository"
    repository.mkdir(mode=0o700)
    (repository / ".git").mkdir()
    with pytest.raises(PolicyError) as raised:
        with anchored_root(evidence, [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_NOT_DEDICATED"
    assert sorted(entry.name for entry in evidence.iterdir()) == ["unrelated-repository"]
    assert sorted(entry.name for entry in repository.iterdir()) == [".git"]


def test_staging_name_cannot_hide_nested_git_repository(roots):
    evidence, protected = roots
    stage = evidence / (".h23v4-staging-" + "e" * 32)
    stage.mkdir(mode=0o700)
    (stage / ".git").mkdir()
    with pytest.raises(PolicyError) as raised:
        with anchored_root(evidence, [protected]):
            pass
    assert raised.value.code == "LOCAL_STAGING"
    assert sorted(entry.name for entry in evidence.iterdir()) == [stage.name]
    assert sorted(entry.name for entry in stage.iterdir()) == [".git"]


def test_foreign_regular_files_preserved_in_dedicated_root(roots):
    evidence, protected = roots
    (evidence / "operator-notes.txt").write_bytes(b"owned local notes")
    with anchored_root(evidence, [protected]) as handle:
        publish_evidence(handle, b"archive", b"receipt")
    assert (evidence / "operator-notes.txt").read_bytes() == b"owned local notes"


def test_general_read_anchor_remains_usable_inside_git_tree(roots):
    evidence, _ = roots
    (evidence / ".git").mkdir()
    (evidence / "source.py").write_bytes(b"source = 1")
    with anchor_directory(evidence) as descriptor:
        assert read_regular(descriptor, "source.py").data == b"source = 1"


def test_sibling_prefix_not_containment(roots):
    evidence, _ = roots
    sibling = evidence.with_name(evidence.name + "-repository")
    sibling.mkdir()
    with anchored_root(evidence, [sibling]) as handle:
        assert handle.path == evidence


def test_symlink_ancestor_rejected(roots):
    evidence, protected = roots
    alias = evidence.parent / "alias"
    alias.symlink_to(evidence.parent, target_is_directory=True)
    with pytest.raises(PolicyError) as raised:
        with anchored_root(alias / "evidence", [protected]):
            pass
    assert raised.value.code == "LOCAL_ROOT_UNAVAILABLE"
    assert not list(protected.iterdir())


def test_snapshot_is_single_regular_byte_stream(roots):
    evidence, _ = roots
    archive = evidence / "input.zip"
    archive.write_bytes(b"first snapshot")
    snapshot = load_snapshot(archive)
    archive.write_bytes(b"changed after snapshot")
    assert snapshot == b"first snapshot"


@pytest.mark.parametrize("relative", ["../escape", "/absolute", "dir/../escape", "dir//file", "C:/escape", "dir\\file",
                                     "dir/./file", "file\x00", "file\n"])
def test_read_rejects_unsafe_paths(roots, relative):
    evidence, _ = roots
    with anchor_directory(evidence) as descriptor:
        with pytest.raises(PolicyError) as raised:
            read_regular(descriptor, relative)
    assert raised.value.code == "LOCAL_PATH"


def test_regular_read_hash_size_and_optional_bytes(roots):
    evidence, _ = roots
    data = b"source = 1\n"
    (evidence / "source.py").write_bytes(data)
    with anchor_directory(evidence) as descriptor:
        result = read_regular(descriptor, "source.py", include=False)
    assert result.size == 11
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.data is None


def test_symlink_file_and_intermediate_directory_rejected(roots):
    evidence, protected = roots
    (protected / "secret").write_bytes(b"private")
    (evidence / "file").symlink_to(protected / "secret")
    (evidence / "directory").symlink_to(protected, target_is_directory=True)
    with anchor_directory(evidence) as descriptor:
        for name in ("file", "directory/secret"):
            with pytest.raises(PolicyError) as raised:
                read_regular(descriptor, name)
            assert raised.value.code == "LOCAL_FILE_UNAVAILABLE"


def test_console_script_hardlink_rejected(roots):
    evidence, _ = roots
    (evidence / "script").write_bytes(b"#!/bin/python\n")
    os.link(evidence / "script", evidence / "alias")
    with anchor_directory(evidence) as descriptor:
        with pytest.raises(PolicyError) as raised:
            read_regular(descriptor, "script", reject_hardlinks=True)
    assert raised.value.code == "LOCAL_FILE_TYPE"


def test_publish_complete_atomic_bundle_and_exact_reimport(roots):
    evidence, protected = roots
    snapshot, receipt = b"exact archive bytes", b'{"validation_result":"CAPTURE_VALIDATED"}'
    with anchored_root(evidence, [protected]) as handle:
        first = publish_evidence(handle, snapshot, receipt)
        second = publish_evidence(handle, snapshot, receipt)
    assert first.status == "CREATED"
    assert second.status == "IDEMPOTENT"
    assert first.durability == second.durability == "CONFIRMED"
    assert first.warning_codes == second.warning_codes == ()
    assert first.path.name == hashlib.sha256(snapshot).hexdigest() + ".evidence.zip"
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o600
    with zipfile.ZipFile(first.path) as archive:
        assert archive.namelist() == ["capture.zip", "receipt.json"]
        assert archive.read("capture.zip") == snapshot
        assert archive.read("receipt.json") == receipt
    assert not [entry for entry in evidence.iterdir() if entry.name.startswith(".h23v4-staging-")]
    assert not list(protected.iterdir())


def test_existing_evidence_conflict_never_overwrites(roots):
    evidence, protected = roots
    snapshot = b"archive bytes"
    destination = evidence / (hashlib.sha256(snapshot).hexdigest() + ".evidence.zip")
    destination.write_bytes(b"existing evidence")
    with anchored_root(evidence, [protected]) as handle:
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, snapshot, b"receipt")
    assert raised.value.code == "EVIDENCE_CONFLICT"
    assert destination.read_bytes() == b"existing evidence"


def test_changed_receipt_conflicts(roots):
    evidence, protected = roots
    with anchored_root(evidence, [protected]) as handle:
        first = publish_evidence(handle, b"archive", b"receipt A")
        original = first.path.read_bytes()
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"archive", b"receipt B")
    assert raised.value.code == "EVIDENCE_CONFLICT"
    assert first.path.read_bytes() == original


def test_exact_existing_evidence_hardlink_is_rejected(roots):
    evidence, protected = roots
    with anchored_root(evidence, [protected]) as handle:
        first = publish_evidence(handle, b"archive", b"receipt")
        os.link(first.path, evidence / "alias")
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"archive", b"receipt")
    assert raised.value.code == "EVIDENCE_CONFLICT"


def test_exact_existing_evidence_changed_permissions_rejected(roots):
    evidence, protected = roots
    with anchored_root(evidence, [protected]) as handle:
        first = publish_evidence(handle, b"archive", b"receipt")
        first.path.chmod(0o644)
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"archive", b"receipt")
    assert raised.value.code == "EVIDENCE_CONFLICT"


@pytest.mark.parametrize("failure_point", ["write", "rename", "link"])
def test_failure_cleanup_covers_write_rename_and_publication(roots, monkeypatch, failure_point):
    import tools.h23_v4.filesystem as module
    evidence, protected = roots
    def fail(*args, **kwargs):
        raise OSError("test injection contains no serialized diagnostic")
    monkeypatch.setattr(module.os, failure_point, fail)
    with anchored_root(evidence, [protected]) as handle:
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"archive", b"receipt")
    assert raised.value.code == "LOCAL_STORAGE"
    assert sorted(entry.name for entry in evidence.iterdir()) == [".h23v4.lock"]
    assert not list(protected.iterdir())


def test_stale_staging_recovery_keeps_committed_evidence(roots):
    evidence, protected = roots
    stale = evidence / (".h23v4-staging-" + "a" * 32)
    stale.mkdir(mode=0o700)
    (stale / "bundle.tmp").write_bytes(b"partial")
    committed = evidence / ("b" * 64 + ".evidence.zip")
    committed.write_bytes(b"old committed evidence")
    with anchored_root(evidence, [protected]) as handle:
        publish_evidence(handle, b"new", b"receipt")
    assert not stale.exists()
    assert committed.read_bytes() == b"old committed evidence"


def test_stale_alias_never_followed_or_deleted(roots):
    evidence, protected = roots
    (protected / "bundle.tmp").write_bytes(b"must remain")
    alias = evidence / (".h23v4-staging-" + "c" * 32)
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(PolicyError) as raised:
        with anchored_root(evidence, [protected]) as handle:
            publish_evidence(handle, b"new", b"receipt")
    assert raised.value.code == "LOCAL_ROOT_NOT_DEDICATED"
    assert alias.is_symlink()
    assert (protected / "bundle.tmp").read_bytes() == b"must remain"
    assert not (evidence / ".h23v4.lock").exists()


def test_stale_recovery_refuses_unknown_file_without_deleting_anything(roots):
    evidence, protected = roots
    stale = evidence / (".h23v4-staging-" + "d" * 32)
    stale.mkdir(mode=0o700)
    (stale / "bundle.tmp").write_bytes(b"partial")
    (stale / "unknown").write_bytes(b"unrecognized")
    with pytest.raises(PolicyError) as raised:
        with anchored_root(evidence, [protected]) as handle:
            publish_evidence(handle, b"archive", b"receipt")
    assert raised.value.code == "LOCAL_STAGING"
    assert sorted(entry.name for entry in stale.iterdir()) == ["bundle.tmp", "unknown"]


def test_stale_recovery_bound_checked_before_deletion(roots):
    evidence, protected = roots
    for number in range(17):
        (evidence / (".h23v4-staging-" + f"{number:032x}")).mkdir(mode=0o700)
    with anchored_root(evidence, [protected]) as handle:
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"archive", b"receipt")
    assert raised.value.code == "LOCAL_STAGING_LIMIT"
    assert len(list(evidence.glob(".h23v4-staging-*"))) == 17


def test_race_before_anchor_acquisition_rejects_without_protected_writes(roots, monkeypatch):
    import tools.h23_v4.filesystem as module
    evidence, protected = roots
    original_open = module.os.open
    swapped = False
    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "evidence" and kwargs.get("dir_fd") is not None and not swapped:
            evidence.rename(evidence.with_name("original-evidence"))
            evidence.symlink_to(protected, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", racing_open)
    with pytest.raises(PolicyError):
        with anchored_root(evidence, [protected]):
            pass
    assert swapped
    assert not list(protected.iterdir())


def test_stage_child_symlink_swap_cannot_write_protected_tree(roots, monkeypatch):
    import tools.h23_v4.filesystem as module
    evidence, protected = roots
    original_open = module.os.open
    swapped = False
    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if isinstance(path, str) and path.startswith(".h23v4-staging-") and not swapped:
            stage = evidence / path
            stage.rename(evidence / "moved-stage")
            stage.symlink_to(protected, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", racing_open)
    with anchored_root(evidence, [protected]) as handle:
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"archive", b"receipt")
    assert swapped
    assert raised.value.code == "LOCAL_STORAGE"
    assert not list(protected.iterdir())


@pytest.mark.parametrize("point", ["after_anchor", "before_write", "before_publish"])
def test_root_path_swaps_remain_bound_to_original_inode(roots, monkeypatch, point):
    import tools.h23_v4.filesystem as module
    evidence, protected = roots
    original = evidence.with_name("original-evidence")
    swapped = False
    def swap():
        nonlocal swapped
        if not swapped:
            evidence.rename(original)
            evidence.symlink_to(protected, target_is_directory=True)
            swapped = True
    with anchored_root(evidence, [protected]) as handle:
        if point == "after_anchor":
            swap()
        elif point == "before_write":
            write = module._write_new
            def racing_write(*args, **kwargs):
                swap()
                return write(*args, **kwargs)
            monkeypatch.setattr(module, "_write_new", racing_write)
        else:
            link = module.os.link
            def racing_link(*args, **kwargs):
                swap()
                return link(*args, **kwargs)
            monkeypatch.setattr(module.os, "link", racing_link)
        result = publish_evidence(handle, b"archive", b"receipt")
    assert result.status == "CREATED"
    assert not list(protected.iterdir())
    assert len(list(original.glob("*.evidence.zip"))) == 1
    assert not list(original.glob(".h23v4-staging-*"))


def test_no_replace_even_if_conflict_created_at_publish(roots, monkeypatch):
    import tools.h23_v4.filesystem as module
    evidence, protected = roots
    original_link = module.os.link
    def racing_link(source, target, **kwargs):
        (evidence / target).write_bytes(b"other committed evidence")
        return original_link(source, target, **kwargs)
    monkeypatch.setattr(module.os, "link", racing_link)
    with anchored_root(evidence, [protected]) as handle:
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"archive", b"receipt")
    assert raised.value.code == "EVIDENCE_CONFLICT"
    assert (evidence / (hashlib.sha256(b"archive").hexdigest() + ".evidence.zip")).read_bytes() == b"other committed evidence"
    assert not list(evidence.glob(".h23v4-staging-*"))


@pytest.mark.parametrize("failure_call", [1, 2])
def test_precommit_fsync_failure_rejects_without_committed_evidence(roots, monkeypatch, failure_call):
    evidence, protected = roots
    original_fsync = os.fsync
    calls = 0

    def fail_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("secret failure text must not become evidence")
        return original_fsync(descriptor)

    with anchored_root(evidence, [protected]) as handle:
        monkeypatch.setattr(os, "fsync", fail_fsync)
        with pytest.raises(PolicyError) as raised:
            publish_evidence(handle, b"snapshot", b'{"validation_result":"CAPTURE_VALIDATED"}')
    assert raised.value.code == "LOCAL_STORAGE"
    assert sorted(entry.name for entry in evidence.iterdir()) == [".h23v4.lock"]
    assert not list(protected.iterdir())


@pytest.mark.parametrize("failure_call", [3, 4])
def test_postcommit_fsync_failure_preserves_publication_with_warning(roots, monkeypatch, failure_call):
    evidence, protected = roots
    snapshot = b"exact capture snapshot"
    receipt = b'{"validation_result":"CAPTURE_VALIDATED"}'
    original_fsync = os.fsync
    calls = 0

    def fail_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("secret failure text must not become evidence")
        return original_fsync(descriptor)

    with anchored_root(evidence, [protected]) as handle:
        monkeypatch.setattr(os, "fsync", fail_fsync)
        result = publish_evidence(handle, snapshot, receipt)
        assert result.status == "CREATED"
        assert result.durability == "UNCONFIRMED"
        assert result.warning_codes == ("PUBLICATION_DURABILITY_UNCONFIRMED",)
        assert result.path == evidence / (hashlib.sha256(snapshot).hexdigest() + ".evidence.zip")
        original_bytes = result.path.read_bytes()
        with zipfile.ZipFile(result.path) as archive:
            assert archive.read("capture.zip") == snapshot
            assert archive.read("receipt.json") == receipt
        assert not list(evidence.glob(".h23v4-staging-*"))
        monkeypatch.setattr(os, "fsync", original_fsync)
        retry = publish_evidence(handle, snapshot, receipt)
    assert retry.status == "IDEMPOTENT"
    assert retry.path == result.path
    assert retry.durability == "CONFIRMED"
    assert retry.warning_codes == ()
    assert retry.path.read_bytes() == original_bytes


@pytest.mark.parametrize("failure_call", [1, 2])
def test_existing_publication_fsync_failure_is_idempotent_warning(roots, monkeypatch, failure_call):
    evidence, protected = roots
    original_fsync = os.fsync
    calls = 0

    def fail_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("unconfirmed durability")
        return original_fsync(descriptor)

    with anchored_root(evidence, [protected]) as handle:
        first = publish_evidence(handle, b"snapshot", b"receipt")
        committed_bytes = first.path.read_bytes()
        monkeypatch.setattr(os, "fsync", fail_fsync)
        retry = publish_evidence(handle, b"snapshot", b"receipt")
    assert retry.status == "IDEMPOTENT"
    assert retry.path == first.path
    assert retry.durability == "UNCONFIRMED"
    assert retry.warning_codes == ("PUBLICATION_DURABILITY_UNCONFIRMED",)
    assert first.path.read_bytes() == committed_bytes
    assert not list(evidence.glob(".h23v4-staging-*"))


@pytest.mark.parametrize("error_kind", ["os", "policy"])
def test_postcommit_cleanup_failure_is_bounded_warning_and_retry_recovers(roots, monkeypatch, error_kind):
    import tools.h23_v4.filesystem as module
    evidence, protected = roots
    original_remove = module._remove_staging
    receipt = b'{"validation_result":"CAPTURE_VALIDATED"}'

    def fail_cleanup(*args, **kwargs):
        if error_kind == "policy":
            raise PolicyError("LOCAL_STAGING")
        raise OSError("secret cleanup failure text")

    with anchored_root(evidence, [protected]) as handle:
        monkeypatch.setattr(module, "_remove_staging", fail_cleanup)
        result = publish_evidence(handle, b"snapshot", receipt)
        assert result.status == "CREATED"
        assert result.durability == "CONFIRMED"
        assert result.warning_codes == ("PUBLICATION_CLEANUP_UNCONFIRMED",)
        assert len(list(evidence.glob(".h23v4-staging-*"))) == 1
        assert result.path.stat().st_nlink == 2
        committed_bytes = result.path.read_bytes()
        monkeypatch.setattr(module, "_remove_staging", original_remove)
        retry = publish_evidence(handle, b"snapshot", receipt)
    assert retry.status == "IDEMPOTENT"
    assert retry.durability == "CONFIRMED"
    assert retry.warning_codes == ()
    assert retry.path.read_bytes() == committed_bytes
    assert retry.path.stat().st_nlink == 1
    assert not list(evidence.glob(".h23v4-staging-*"))


@pytest.mark.parametrize("close_target", ["stage", "lock"])
def test_postcommit_descriptor_close_failure_never_rejects(roots, monkeypatch, close_target):
    evidence, protected = roots
    original_open, original_close = os.open, os.close
    watched = None
    failed = False

    def observe_open(path, *args, **kwargs):
        nonlocal watched
        descriptor = original_open(path, *args, **kwargs)
        if watched is None and ((close_target == "stage" and str(path).startswith(".h23v4-staging-"))
                                or (close_target == "lock" and path == ".h23v4.lock")):
            watched = descriptor
        return descriptor

    def fail_close(descriptor):
        nonlocal failed
        original_close(descriptor)
        if descriptor == watched and not failed:
            failed = True
            raise OSError("descriptor close failed after publication")

    with anchored_root(evidence, [protected]) as handle:
        monkeypatch.setattr(os, "open", observe_open)
        monkeypatch.setattr(os, "close", fail_close)
        result = publish_evidence(handle, b"snapshot", b"receipt")
    assert failed
    assert result.status == "CREATED"
    assert result.durability == "CONFIRMED"
    assert result.warning_codes == ("PUBLICATION_CLEANUP_UNCONFIRMED",)
    assert result.path.is_file()
    assert not list(evidence.glob(".h23v4-staging-*"))


@pytest.mark.parametrize("operation", ["unlink", "rmdir"])
def test_postcommit_cleanup_filesystem_failure_preserves_committed_bytes(roots, monkeypatch, operation):
    evidence, protected = roots
    original_operation = getattr(os, operation)

    def fail_cleanup(*args, **kwargs):
        raise OSError("cleanup filesystem operation failed")

    with anchored_root(evidence, [protected]) as handle:
        monkeypatch.setattr(os, operation, fail_cleanup)
        result = publish_evidence(handle, b"snapshot", b"receipt")
        assert result.status == "CREATED"
        assert result.durability == "CONFIRMED"
        assert result.warning_codes == ("PUBLICATION_CLEANUP_UNCONFIRMED",)
        committed_bytes = result.path.read_bytes()
        assert len(list(evidence.glob(".h23v4-staging-*"))) == 1
        monkeypatch.setattr(os, operation, original_operation)
        retry = publish_evidence(handle, b"snapshot", b"receipt")
    assert retry.status == "IDEMPOTENT"
    assert retry.warning_codes == ()
    assert retry.path.read_bytes() == committed_bytes
    assert retry.path.stat().st_nlink == 1
    assert not list(evidence.glob(".h23v4-staging-*"))


def test_postcommit_durability_and_cleanup_warnings_remain_distinct(roots, monkeypatch):
    evidence, protected = roots
    original_fsync = os.fsync
    calls = 0

    def fail_first_root_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("root durability unconfirmed")
        return original_fsync(descriptor)

    def fail_unlink(*args, **kwargs):
        raise OSError("cleanup unconfirmed")

    with anchored_root(evidence, [protected]) as handle:
        monkeypatch.setattr(os, "fsync", fail_first_root_fsync)
        monkeypatch.setattr(os, "unlink", fail_unlink)
        result = publish_evidence(handle, b"snapshot", b"receipt")
    assert result.status == "CREATED"
    assert result.durability == "UNCONFIRMED"
    assert result.warning_codes == ("PUBLICATION_CLEANUP_UNCONFIRMED", "PUBLICATION_DURABILITY_UNCONFIRMED")
    assert result.path.is_file()


def _operator_files(evidence, count):
    for number in range(count):
        (evidence / (f"operator-{number:05d}.txt")).touch(mode=0o600, exist_ok=False)


@pytest.mark.parametrize("initial_count,accepted", [(8188, True), (8189, True), (8190, False)])
def test_new_publication_reserves_missing_lock_stage_and_final_before_mutation(roots, monkeypatch,
                                                                           initial_count, accepted):
    evidence, protected = roots
    _operator_files(evidence, initial_count)
    before = {entry.name for entry in evidence.iterdir()}
    original_link = os.link
    peak = []

    def observe_link(*args, **kwargs):
        value = original_link(*args, **kwargs)
        peak.append(len(list(evidence.iterdir())))
        return value

    with anchored_root(evidence, [protected]) as handle:
        monkeypatch.setattr(os, "link", observe_link)
        if accepted:
            first = publish_evidence(handle, b"snapshot", b"receipt")
            assert first.status == "CREATED"
            assert peak == [initial_count + 3]
            assert peak[0] <= 8192
            assert len(list(evidence.iterdir())) == initial_count + 2
            retry = publish_evidence(handle, b"snapshot", b"receipt")
            assert retry.status == "IDEMPOTENT"
            assert retry.path == first.path
        else:
            with pytest.raises(PolicyError) as raised:
                publish_evidence(handle, b"snapshot", b"receipt")
            assert raised.value.code == "LOCAL_ROOT_LIMIT"
            assert {entry.name for entry in evidence.iterdir()} == before
            assert peak == []


@pytest.mark.parametrize("initial_count,accepted", [(8189, True), (8190, True), (8191, False)])
def test_new_publication_reserves_two_slots_when_lock_already_exists(roots, initial_count, accepted):
    evidence, protected = roots
    (evidence / ".h23v4.lock").touch(mode=0o600, exist_ok=False)
    _operator_files(evidence, initial_count - 1)
    before = {entry.name for entry in evidence.iterdir()}
    with anchored_root(evidence, [protected]) as handle:
        if accepted:
            first = publish_evidence(handle, b"snapshot", b"receipt")
            assert first.status == "CREATED"
            assert len(list(evidence.iterdir())) == initial_count + 1
        else:
            with pytest.raises(PolicyError) as raised:
                publish_evidence(handle, b"snapshot", b"receipt")
            assert raised.value.code == "LOCAL_ROOT_LIMIT"
            assert {entry.name for entry in evidence.iterdir()} == before


def test_exact_reimport_at_full_root_needs_no_transient_or_commit_slots(roots):
    evidence, protected = roots
    _operator_files(evidence, 8189)
    with anchored_root(evidence, [protected]) as handle:
        first = publish_evidence(handle, b"snapshot", b"receipt")
    (evidence / "last-operator-slot.txt").touch(mode=0o600, exist_ok=False)
    assert len(list(evidence.iterdir())) == 8192
    before = {entry.name for entry in evidence.iterdir()}
    with anchored_root(evidence, [protected]) as handle:
        retry = publish_evidence(handle, b"snapshot", b"receipt")
    assert retry.status == "IDEMPOTENT"
    assert retry.path == first.path
    assert retry.durability == "CONFIRMED"
    assert retry.warning_codes == ()
    assert {entry.name for entry in evidence.iterdir()} == before


@pytest.mark.parametrize("initial_count,accepted", [(8191, True), (8192, False)])
def test_exact_reimport_without_lock_reserves_only_missing_lock_slot(roots, initial_count, accepted):
    evidence, protected = roots
    with anchored_root(evidence, [protected]) as handle:
        first = publish_evidence(handle, b"snapshot", b"receipt")
    (evidence / ".h23v4.lock").unlink()
    _operator_files(evidence, initial_count - 1)
    before = {entry.name for entry in evidence.iterdir()}
    with anchored_root(evidence, [protected]) as handle:
        if accepted:
            retry = publish_evidence(handle, b"snapshot", b"receipt")
            assert retry.status == "IDEMPOTENT"
            assert retry.path == first.path
            assert len(list(evidence.iterdir())) == 8192
        else:
            with pytest.raises(PolicyError) as raised:
                publish_evidence(handle, b"snapshot", b"receipt")
            assert raised.value.code == "LOCAL_ROOT_LIMIT"
            assert {entry.name for entry in evidence.iterdir()} == before
