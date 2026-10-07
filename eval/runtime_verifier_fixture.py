"""Trusted synthetic fixture construction and verifier-only test material."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tarfile

from ._contracts import canonical_sha256
from .solver_task import PublicAssetRef, SolverTask
from .runtime_fixture import INITIAL

TEST_NAME = "test_marker.py"
TEST_CONTENT = 'from app import MARKER\n\ndef test_marker():\n    assert MARKER == "after"\n'
SUPPORT_MANIFEST_SHA256 = "3694df089cbd36a34d0d79ae5cb5ac7bbb67963aef1250661377400a10263b76"


def test_patch() -> str:
    return (f"diff --git a/{TEST_NAME} b/{TEST_NAME}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{TEST_NAME}\n@@ -0,0 +1,4 @@\n"
            + "".join("+" + line for line in TEST_CONTENT.splitlines(keepends=True)))


def verification_config() -> dict:
    return {"schema_version": 1, "sandbox": "subprocess", "timeout_seconds": 20,
            "fast_path": True, "pytest_support_manifest_sha256": SUPPORT_MANIFEST_SHA256}


def create_synthetic_fixture(public_root: Path):
    """Return public SolverTask, separate VerifierTask and private test text.

    This is trusted test fixture construction. Its verifier contract import is
    local and is never used by the solver worker.
    """
    from .verifier_task import PrivateTestPatchRef, VerifierTask

    public_root = public_root.resolve()
    public_root.mkdir(parents=True, exist_ok=True)
    snapshots = public_root / "snapshots"
    snapshots.mkdir()
    work = public_root / ".fixture_build"
    work.mkdir()
    template = work / "empty_template"
    template.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
           "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000"}

    def git(*args):
        return subprocess.run(["/usr/bin/git", "-C", str(work), *args], env=env,
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "--template=" + str(template), "--initial-branch=main", "--object-format=sha1")
    for name, value in {"user.name": "Synthetic runtime fixture", "user.email": "synthetic@example.invalid",
                        "core.hooksPath": ".git/hooks", "core.abbrev": "7", "core.autocrlf": "false",
                        "core.fsmonitor": "false", "commit.gpgsign": "false", "tag.gpgsign": "false"}.items():
        git("config", "--local", name, value)
    (work / ".git/hooks").mkdir()
    (work / "app.py").write_text(INITIAL)
    git("add", "app.py")
    git("commit", "-m", "synthetic initial", "--quiet")
    commit = git("rev-parse", "HEAD")
    archive_path = snapshots / "synthetic.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(work.rglob("*")):
            relative = path.relative_to(work)
            # Native add -A must build its index solely from admitted public
            # files and trusted native-created configs. A stale index can keep
            # hidden assume-unchanged entries that checkout later materializes.
            if relative.parts[0] == "empty_template" or relative.as_posix() == ".git/index":
                continue
            archive.add(path, arcname=relative.as_posix(), recursive=False)
    import shutil
    shutil.rmtree(work)
    data = archive_path.read_bytes()
    snapshot = PublicAssetRef("snapshot", "synthetic", "snapshots/synthetic.tgz",
                              hashlib.sha256(data).hexdigest(), len(data))
    task = SolverTask(1, "synthetic_runtime", "synthetic/probe", commit,
                      'Change app.py MARKER from "before" to "after". Synthetic fixture only.', "", snapshot)
    private = test_patch().encode()
    verifier = VerifierTask(1, task.instance_id, task.repo, commit, snapshot,
                            PrivateTestPatchRef(hashlib.sha256(private).hexdigest(), len(private)),
                            canonical_sha256(verification_config()), (f"{TEST_NAME}::test_marker",), ())
    return task, verifier, private.decode()
