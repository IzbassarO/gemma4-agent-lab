"""Exact offline support and command admission for synthetic official Phase 2.

No harness package is imported here. The five pure-Python distributions are
read and staged before the parent hook; subsequent reads/writes use only the
existing synthetic root. This never changes library roots or wheel discovery.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import stat
import sysconfig
from email.parser import BytesParser
from pathlib import Path

from ._probe_safety import ProbeRefused, check_wheels, confined
from .run_h04_h05_h18 import INITIAL, digest, expected_patch, sandbox_commands

TEST_NAME = "test_marker.py"
TEST_CONTENT = 'from app import MARKER\n\ndef test_marker():\n    assert MARKER == "after"\n'
SUPPORT_VERSIONS = {
    "pytest": "9.1.1", "iniconfig": "2.3.0", "packaging": "26.3",
    "pluggy": "1.6.0", "pygments": "2.21.0",
}
SUPPORT_MANIFEST_SHA256 = "3694df089cbd36a34d0d79ae5cb5ac7bbb67963aef1250661377400a10263b76"
SUPPORT_ROOTS = (
    "_pytest", "pytest", "py.py", "iniconfig", "packaging", "pluggy", "pygments",
    *(f"{name}-{version}.dist-info" for name, version in SUPPORT_VERSIONS.items()),
)
_DIST_TEXT = frozenset({
    "METADATA", "RECORD", "WHEEL", "INSTALLER", "REQUESTED", "entry_points.txt", "top_level.txt",
    "licenses/LICENSE", "licenses/LICENSE.APACHE", "licenses/LICENSE.BSD", "licenses/AUTHORS",
})
_CACHE_NAME = re.compile(r"[A-Za-z0-9_.-]+\.cpython-[0-9]+(?:\.opt-[0-9]+)?\.pyc\Z")
_PATCH_NAME = re.compile(r"tmp[a-z0-9_]{8}\.patch\Z")
_JUNIT = re.compile(r"/tmp/_swegemma_junit_[0-9a-f]{12}\.xml\Z")
_DIFF = "cd /workspace && (git diff --binary _swegemma_baseline 2>/dev/null || git diff --binary HEAD)"


def _manifest_hash(manifest):
    return digest(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())


def _regular(path: Path, root: Path) -> None:
    if path.absolute() != path.resolve() or not path.resolve().is_relative_to(root.resolve()):
        raise ProbeRefused(f"support path is an alias or escape: {path}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ProbeRefused(f"support file is not regular: {path}")


def _collect(base: Path, *, caches: bool) -> list[dict]:
    """Enumerate only the pinned closure, inspecting aliases even in caches."""
    if base.absolute() != base.resolve() or not stat.S_ISDIR(base.lstat().st_mode):
        raise ProbeRefused(f"support root is not a canonical directory: {base}")
    manifest = []
    for name in SUPPORT_ROOTS:
        top = base / name
        if top.is_symlink() or not top.exists():
            raise ProbeRefused(f"support root missing or symlinked: {top}")
        entries = [top, *sorted(top.rglob("*"))] if top.is_dir() else [top]
        for path in entries:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ProbeRefused(f"special support entry refused: {path}")
            relative = path.relative_to(base)
            if "__pycache__" in relative.parts:
                if not caches or (path.is_file() and not _CACHE_NAME.fullmatch(path.name)):
                    raise ProbeRefused(f"unexpected support cache entry: {path}")
                continue
            if path.is_dir():
                continue
            if name.endswith(".dist-info"):
                if path.relative_to(top).as_posix() not in _DIST_TEXT:
                    raise ProbeRefused(f"unexpected distribution support file: {path}")
            elif path.suffix != ".py" and path.name != "py.typed":
                raise ProbeRefused(f"non-Python support file refused: {path}")
            _regular(path, base)
            manifest.append({"path": relative.as_posix(), "sha256": digest(path.read_bytes())})
    return sorted(manifest, key=lambda item: item["path"])


def prepare_support(repo: Path, root: Path) -> dict:
    """Pre-hook metadata/hash verification; never import the support packages."""
    source = repo / ".venv/lib/python3.14/site-packages"
    manifest = _collect(source, caches=True)
    if _manifest_hash(manifest) != SUPPORT_MANIFEST_SHA256:
        raise ProbeRefused("pytest support differs from the reviewed pure-Python closure")
    for name, version in SUPPORT_VERSIONS.items():
        metadata = BytesParser().parsebytes((source / f"{name}-{version}.dist-info/METADATA").read_bytes())
        if metadata["Name"].lower() != name or metadata["Version"] != version:
            raise ProbeRefused(f"pytest support version mismatch: {name}")
        wheel = (source / f"{name}-{version}.dist-info/WHEEL").read_text()
        if "Root-Is-Purelib: true\n" not in wheel or "Tag: py3-none-any\n" not in wheel:
            raise ProbeRefused(f"pytest support is not a pure-Python wheel: {name}")
    target = confined(root / "pytest_support", root)
    target.mkdir(exist_ok=False)
    for item in manifest:
        destination = target / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = (source / item["path"]).read_bytes()
        if digest(data) != item["sha256"]:
            raise ProbeRefused("pytest support changed during staging")
        destination.write_bytes(data)
    return {"root": str(target), "manifest": manifest,
            "manifest_sha256": SUPPORT_MANIFEST_SHA256, "versions": dict(SUPPORT_VERSIONS)}


def stage_support(manager, sandbox_id, support: dict, guard) -> list[dict]:
    """Copy only rehashed pinned files into the isolated evaluation venv."""
    if manager.system_site_packages is not False:
        guard.refuse("verification requires system_site_packages=False")
    source = Path(support["root"])
    confined(source, guard.run_root)
    manifest = _collect(source, caches=False)
    if (set(path.name for path in source.iterdir()) != set(SUPPORT_ROOTS)
            or manifest != support["manifest"] or _manifest_hash(manifest) != SUPPORT_MANIFEST_SHA256
            or support["manifest_sha256"] != SUPPORT_MANIFEST_SHA256
            or support["versions"] != SUPPORT_VERSIONS):
        guard.refuse("staged pytest support differs from the reviewed manifest")
    target = Path(manager.sandboxes[sandbox_id]["venv"]) / "lib/python3.12/site-packages"
    confined(target, guard.run_root)
    if target.absolute() != target.resolve():
        guard.refuse("verification site-packages contains an alias")
    target.mkdir(parents=True, exist_ok=True)
    existing = {entry.name for entry in target.iterdir()}
    for entry in target.iterdir():
        if entry.is_symlink() or not entry.is_dir() or not (
                entry.name == "pip" or re.fullmatch(r"pip-[0-9][A-Za-z0-9_.-]*\.dist-info", entry.name)):
            guard.refuse(f"unexpected isolated venv module: {entry}")
    if any(path.suffix == ".pth" or path.is_symlink() for path in target.rglob("*")):
        guard.refuse("isolated venv contains a .pth or symlink")
    for item in manifest:
        data = (source / item["path"]).read_bytes()
        if digest(data) != item["sha256"]:
            guard.refuse("pytest support changed during sandbox staging")
        destination = target / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    installed = _collect(target, caches=False)
    if installed != manifest or set(path.name for path in target.iterdir()) != existing | set(SUPPORT_ROOTS):
        guard.refuse("installed synthetic pytest support hash mismatch")
    return installed


def _source_script(site: Path, name: str) -> str:
    tree = ast.parse((site / "swegemma/harness/container_setup.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return next(ast.literal_eval(node.value) for node in ast.walk(function)
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "script" for t in node.targets))


def verification_commands(site: Path, snapshot_name: str) -> set[str]:
    # This tranche uses FAIL_TO_PASS only: no test_patch and no test_files.
    # Official Phase 2 therefore resets the configuration files and discovers '.'.
    reset = "conftest.py pytest.ini sitecustomize.py usercustomize.py _swegemma_stubs.py"
    overwrite = f"python3 -S -c {shlex.quote(_source_script(site, 'setup_workspace_test_config'))} /workspace synthetic/probe 1"
    return sandbox_commands(site, snapshot_name) | {
        'cd /workspace && git add -A && git commit -m "eval_baseline" --allow-empty -q && git tag -f _swegemma_baseline',
        overwrite,
        "cd /workspace && (git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null) || true",
        f"cd /workspace && git checkout HEAD -- {reset} 2>/dev/null || true",
        f"cd /workspace && git clean -f -- {reset} 2>/dev/null || true",
    }


def instrument_verifier(manager, snapshot: Path, wheel_paths, guard, support: dict) -> dict:
    """Observe one official verification sandbox under exact copy/command gates."""
    site = Path(sysconfig.get_path("purelib"))
    commands = verification_commands(site, snapshot.name)
    patch_script = _source_script(site, "apply_patch_in_container")
    observation = {"commands": [], "copies": [], "workspace": None, "observer_errors": [],
                   "baseline_before_patch": None, "junit_xml": None, "support_file_hashes": []}
    original_start, original_exec = manager.start, manager.exec
    original_copy, original_stop = manager.copy_to, manager.stop
    snapshot_sha = digest(snapshot.read_bytes())
    active = None
    patch_path = None
    junit_path = None

    def wheels():
        try:
            check_wheels(wheel_paths)
        except ProbeRefused as exc:
            guard.refuse(str(exc))

    def valid(identifier):
        if identifier != active or identifier not in manager.sandboxes:
            guard.refuse("unapproved verification sandbox")
        if any(manager.sandboxes[identifier]["wheels"].iterdir()):
            guard.refuse("verification sandbox wheel staging detected")

    def start():
        nonlocal active
        wheels()
        if active is not None:
            guard.refuse("verification manager admits exactly one sandbox")
        with guard.processes_allowed("venv"):
            active = original_start()
        valid(active)
        try:
            observation["support_file_hashes"] = stage_support(manager, active, support, guard)
        except ProbeRefused as exc:
            guard.refuse(str(exc))
        return active

    def execute(identifier, command, timeout=None):
        nonlocal junit_path
        wheels()
        valid(identifier)
        accepted = command in commands
        if patch_path is not None:
            accepted |= command in {
                f"python3 -S -c {shlex.quote(patch_script)} /workspace {shlex.quote(patch_path)}",
                f"rm -f {shlex.quote(patch_path)}*",
            }
        if command.startswith("rm -f ") and _JUNIT.fullmatch(command[6:]):
            proposed = command[6:]
            if junit_path is None:
                junit_path = proposed
            accepted |= proposed == junit_path
        if junit_path is not None:
            accepted |= command in {
                "cd /workspace && PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 python3 -s -m pytest . "
                f'--junitxml={junit_path} -p no:anyio -o timeout=0 -o python_classes="Test* *Test" -q',
                f"cat {junit_path} 2>/dev/null || true",
            }
        if not accepted:
            guard.refuse(f"unapproved verification command: {command!r}")
        if "python3 -s -m pytest " in command and {
                key: value for key, value in os.environ.items() if key.startswith("PYTEST_")
        } != {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}:
            guard.refuse("verification pytest environment is not the exact isolated policy")
        if "python3 -s -m pytest " in command and patch_path is None:
            workspace = manager.sandboxes[identifier]["workspace"]
            observation["baseline_before_patch"] = {
                "app_content": (workspace / "app.py").read_text(),
                "test_content": (workspace / TEST_NAME).read_text(),
            }
            if observation["baseline_before_patch"] != {"app_content": INITIAL, "test_content": TEST_CONTENT}:
                guard.refuse("negative verification control was not the pristine synthetic fixture")
        # SubprocessManager sanitizes inherited PYTEST_* variables. Configure
        # only this already-admitted official command, recording both forms.
        executed_command = command
        if "python3 -s -m pytest " in command:
            executed_command = command.replace(
                "cd /workspace && PYTHONSAFEPATH=1 ",
                "cd /workspace && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONSAFEPATH=1 ", 1,
            )
        with guard.processes_allowed("shell"):
            result = original_exec(identifier, executed_command, timeout=timeout)
        observation["commands"].append({"command": command, "executed_command": executed_command,
                                        "exit_code": result.exit_code,
                                        "stdout": result.stdout, "stderr": result.stderr})
        if command == "ls /wheels/*.whl 2>/dev/null | wc -l" and result.stdout.strip() != "0":
            guard.refuse("verification wheel check did not confirm zero wheels")
        if command.startswith("cat ") and junit_path is not None:
            observation["junit_xml"] = result.stdout
        return result

    def copy_to(identifier, source, destination):
        nonlocal patch_path
        wheels()
        valid(identifier)
        source = Path(source)
        try:
            _regular(source, guard.run_root)
        except ProbeRefused as exc:
            guard.refuse(str(exc))
        data = source.read_bytes()
        accepted = source == snapshot and destination == "/tmp" and digest(data) == snapshot_sha
        if (source.parent == guard.run_root / "tmp" and _PATCH_NAME.fullmatch(source.name)
                and destination == "/tmp/" and data == expected_patch().encode() and patch_path is None):
            workspace = manager.sandboxes[identifier]["workspace"]
            observation["baseline_before_patch"] = {
                "app_content": (workspace / "app.py").read_text(),
                "test_content": (workspace / TEST_NAME).read_text(),
            }
            if observation["baseline_before_patch"] != {"app_content": INITIAL, "test_content": TEST_CONTENT}:
                guard.refuse("verification patch was not applied to the pristine synthetic fixture")
            patch_path = f"/tmp/{source.name}"
            accepted = True
        if not accepted:
            guard.refuse(f"unapproved verification copy: {source} -> {destination}")
        observation["copies"].append({"source": str(source), "destination": destination, "sha256": digest(data)})
        return original_copy(identifier, source, destination)

    def stop(identifier):
        try:
            wheels()
            valid(identifier)
            workspace = manager.sandboxes[identifier]["workspace"]
            diff = execute(identifier, _DIFF)
            config = (workspace / ".git/config").read_text()
            if '[remote ' in config or list((workspace / ".git/hooks").iterdir()):
                guard.refuse("remote or hooks appeared in synthetic verification workspace")
            if (workspace / TEST_NAME).read_text() != TEST_CONTENT:
                guard.refuse("synthetic verification test changed")
            if junit_path is not None:
                junit = Path(manager.sandboxes[identifier]["tmp"]) / Path(junit_path).name
                if junit.exists():
                    _regular(junit, guard.run_root)
                    xml = junit.read_text()
                    if observation["junit_xml"] is not None and observation["junit_xml"] != xml:
                        guard.refuse("verification JUnit observer disagrees with the recorded cat output")
                    observation["junit_xml"] = xml
            observation["workspace"] = {
                "app_content": (workspace / "app.py").read_text(),
                "app_sha256": digest((workspace / "app.py").read_bytes()),
                "test_content": (workspace / TEST_NAME).read_text(),
                "test_sha256": digest((workspace / TEST_NAME).read_bytes()),
                "diff": diff.stdout, "diff_sha256": digest(diff.stdout.encode()),
                "diff_exit_code": diff.exit_code, "git_remotes": [], "git_hooks": [], "path": str(workspace),
            }
        except Exception as exc:
            observation["observer_errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            original_stop(identifier)

    manager.start, manager.exec, manager.copy_to, manager.stop = start, execute, copy_to, stop
    return observation
