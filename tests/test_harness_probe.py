"""Admission checks only: no harness imports, audit-hook installation, or probe runs."""

import os
import platform
import asyncio
import copy
from dataclasses import FrozenInstanceError
import hashlib
from importlib.machinery import ModuleSpec
import logging
import json
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import threading
import sys
from types import CodeType, ModuleType, SimpleNamespace

import pytest

from tools.harness_cert import _probe_safety as safety


def _fake_git(monkeypatch, repo, *, head=safety.BASELINE, status="", root=None):
    responses = {
        ("rev-parse", "--show-toplevel"): str(root or repo) + "\n",
        ("rev-parse", "HEAD"): head + "\n",
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): status,
    }
    calls = []

    def fake_git(candidate, *args):
        assert candidate == repo
        calls.append(args)
        return responses[args]

    monkeypatch.setattr(safety, "git", fake_git)
    return calls


@pytest.fixture
def reviewed_support_document(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    name = next(iter(safety.REVIEWED_SUPPORT_DOCUMENTS))
    path = repo / name
    path.parent.mkdir(parents=True)
    path.write_text("Reviewed synthetic support-pin correction\n", encoding="utf-8")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(safety, "REVIEWED_SUPPORT_DOCUMENTS", {name: expected})
    return SimpleNamespace(repo=repo, name=name, path=path)


@pytest.mark.parametrize("status", [" M", "M ", "MM"])
def test_repository_admits_only_reviewed_tracked_support_document(monkeypatch, reviewed_support_document, status):
    fixture = reviewed_support_document
    _fake_git(monkeypatch, fixture.repo, status=f"{status} {fixture.name}\0")
    assert safety.check_repository(fixture.repo) == [fixture.name]
    assert fixture.name not in safety.PROBE_FILES


@pytest.mark.parametrize("status", ["??", "A ", "AM"])
def test_repository_refuses_untracked_or_added_support_document(monkeypatch, reviewed_support_document, status):
    fixture = reviewed_support_document
    _fake_git(monkeypatch, fixture.repo, status=f"{status} {fixture.name}\0")
    with pytest.raises(safety.ProbeRefused, match="support document must be a tracked modification"):
        safety.check_repository(fixture.repo)


def test_repository_refuses_support_document_edits_beyond_reviewed_digest(monkeypatch, reviewed_support_document):
    fixture = reviewed_support_document
    fixture.path.write_text("Unreviewed change\n", encoding="utf-8")
    _fake_git(monkeypatch, fixture.repo, status=f" M {fixture.name}\0")
    with pytest.raises(safety.ProbeRefused, match="differs from reviewed pin correction"):
        safety.check_repository(fixture.repo)


@pytest.mark.parametrize("kind", ["file_symlink", "directory_symlink", "directory"])
def test_repository_refuses_support_document_aliases_or_nonfiles(monkeypatch, reviewed_support_document, tmp_path, kind):
    fixture = reviewed_support_document
    content = fixture.path.read_bytes()
    fixture.path.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "directory_symlink":
        (outside / fixture.path.name).write_bytes(content)
        fixture.path.parent.rmdir()
        fixture.path.parent.symlink_to(outside, target_is_directory=True)
    elif kind == "file_symlink":
        target = outside / "support.md"
        target.write_bytes(content)
        fixture.path.symlink_to(target)
    else:
        fixture.path.mkdir()
    _fake_git(monkeypatch, fixture.repo, status=f" M {fixture.name}\0")
    with pytest.raises(safety.ProbeRefused, match="differs from reviewed pin correction"):
        safety.check_repository(fixture.repo)


def test_repository_still_refuses_unrelated_report_changes(monkeypatch, reviewed_support_document):
    fixture = reviewed_support_document
    other = "harness_cert/reports/H23_KAGGLE_CAPTURE_2026-10-03.md"
    _fake_git(monkeypatch, fixture.repo, status=f" M {fixture.name}\0 M {other}\0")
    with pytest.raises(safety.ProbeRefused, match="repository is dirty outside the probe"):
        safety.check_repository(fixture.repo)


@pytest.fixture
def fake_auth_imports(monkeypatch):
    from tools.harness_cert import run_h04_h05_h18 as probe

    class ImportOnlyType:
        def __new__(cls, *args, **kwargs):
            pytest.fail("Auth import smoke must not construct OAuth sessions/tokens")

    modules = {name: ModuleType(name) for name in (
        "authlib", "authlib.integrations", "authlib.integrations.requests_client",
        "authlib.oauth2", "authlib.oauth2.rfc6749", "google", "google.adk", "google.adk.auth",
        "google.adk.auth.oauth2_credential_util",
        "google.adk.flows", "google.adk.flows.llm_flows", "google.adk.flows.llm_flows.auto_flow",
    )}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(modules[parent], child, module)
    util = modules["google.adk.auth.oauth2_credential_util"]
    util.OAuth2Session = modules["authlib.integrations.requests_client"].OAuth2Session = ImportOnlyType
    util.OAuth2Token = modules["authlib.oauth2.rfc6749"].OAuth2Token = ImportOnlyType
    constructed = []

    class FakeAutoFlow:
        def __init__(self):
            constructed.append(self)

    modules["google.adk.flows.llm_flows.auto_flow"].AutoFlow = FakeAutoFlow
    versions = {"google-adk": "1.36.1", "authlib": "1.6.6"}
    monkeypatch.setattr(probe.importlib.metadata, "version", lambda name: versions[name])
    return SimpleNamespace(probe=probe, modules=modules, util=util, versions=versions,
                           constructed=constructed, auto_flow=FakeAutoFlow)


def test_auth_smoke_constructs_previously_failing_autoflow_without_auth_execution(fake_auth_imports):
    fixture = fake_auth_imports
    assert fixture.probe.smoke_auth_import() == {
        "module": "google.adk.auth.oauth2_credential_util", "google-adk": "1.36.1",
        "authlib": "1.6.6", "result": "AUTHLIB_IMPORT_OK",
    }
    assert len(fixture.constructed) == 1
    assert isinstance(fixture.constructed[0], fixture.auto_flow)


def test_auth_smoke_does_not_treat_leaf_import_as_successful_autoflow_construction(monkeypatch, fake_auth_imports):
    fixture = fake_auth_imports

    class BrokenAutoFlow:
        def __init__(self):
            raise RuntimeError("AutoFlow construction failed")

    monkeypatch.setattr(fixture.modules["google.adk.flows.llm_flows.auto_flow"], "AutoFlow", BrokenAutoFlow)
    with pytest.raises(RuntimeError, match="AutoFlow construction failed"):
        fixture.probe.smoke_auth_import()


@pytest.mark.parametrize("name,version", [("authlib", "1.6.5"), ("authlib", "2.0.0"), ("google-adk", "1.37.0")])
def test_auth_smoke_refuses_wrong_exact_distribution_versions(fake_auth_imports, name, version):
    fixture = fake_auth_imports
    fixture.versions[name] = version
    with pytest.raises(safety.ProbeRefused, match="auth import smoke requires"):
        fixture.probe.smoke_auth_import()
    assert fixture.constructed == []


def test_auth_smoke_refuses_missing_distribution_metadata(monkeypatch, fake_auth_imports):
    fixture = fake_auth_imports
    def version(name):
        if name == "authlib":
            raise fixture.probe.importlib.metadata.PackageNotFoundError(name)
        return fixture.versions[name]
    monkeypatch.setattr(fixture.probe.importlib.metadata, "version", version)
    with pytest.raises(fixture.probe.importlib.metadata.PackageNotFoundError):
        fixture.probe.smoke_auth_import()


def test_auth_smoke_does_not_treat_metadata_as_successful_module_import(monkeypatch, fake_auth_imports):
    fixture = fake_auth_imports
    monkeypatch.setitem(sys.modules, "authlib.integrations.requests_client", None)
    with pytest.raises(ModuleNotFoundError):
        fixture.probe.smoke_auth_import()


@pytest.mark.parametrize("binding", ["OAuth2Session", "OAuth2Token"])
def test_auth_smoke_refuses_unexpected_adk_authlib_bindings(fake_auth_imports, binding):
    fixture = fake_auth_imports
    setattr(fixture.util, binding, object())
    with pytest.raises(safety.ProbeRefused, match="unexpected Authlib bindings"):
        fixture.probe.smoke_auth_import()
    assert fixture.constructed == []


def test_wheel_candidates_cover_both_resolvers_and_config_siblings(tmp_path):
    cwd = tmp_path / "launch"
    tasks = tmp_path / "synthetic_tasks" / "tasks.jsonl"
    snapshots = tmp_path / "synthetic_snapshots" / "snapshots"
    candidates = safety.wheel_candidates(cwd, tasks, snapshots)
    assert set(candidates) == {
        tasks.parent / "wheels",
        tasks.parent / "sandbox/wheels",
        snapshots.parent / "wheels",
        Path("/kaggle/input/datasets/metric/gemma-4-developer-agent-metric-data/sandbox/wheels"),
        Path("/kaggle/input/gemma-4-developer-agent-metric-data/sandbox/wheels"),
        Path("/kaggle/input/competition-data/wheels"),
        cwd / "data/competition_data/secret/sandbox/wheels",
        cwd / "wheels",
        Path("/wheels"),
        Path("/tmp/wheels"),
        Path("/kaggle/input/datasets/ryanholbrook/gemma4swe-wheelhouses"),
        cwd / "build/wheelhouse",
        cwd / "data/wheels",
        Path("/tmp/wheelhouse"),
    }
    assert len(candidates) == len(set(candidates))


def test_wheel_candidates_preserve_original_cwd_after_chdir(monkeypatch, tmp_path):
    original, fresh = tmp_path / "original", tmp_path / "fresh"
    original.mkdir()
    fresh.mkdir()
    tasks, snapshots = fresh / "tasks.jsonl", fresh / "snapshots"
    monkeypatch.chdir(original)
    original_cwd = Path.cwd()
    monkeypatch.chdir(fresh)
    original_candidates = safety.wheel_candidates(original_cwd, tasks, snapshots)
    fresh_candidates = safety.wheel_candidates(Path.cwd(), tasks, snapshots)
    assert original / "build/wheelhouse" in original_candidates
    assert original / "data/wheels" in original_candidates
    assert fresh / "build/wheelhouse" in fresh_candidates
    assert fresh / "data/wheels" in fresh_candidates
    assert original / "wheels" not in fresh_candidates
    assert fresh / "build/wheelhouse" not in original_candidates
    # Config sibling discovery is independent of the captured launch directory.
    assert fresh / "wheels" in original_candidates


@pytest.mark.parametrize("launch", ["original", "fresh"])
def test_original_and_fresh_cwd_candidates_both_refuse_wheels(tmp_path, launch):
    original, fresh = tmp_path / "original", tmp_path / "fresh"
    wheel_dir = tmp_path / launch / "build/wheelhouse"
    wheel_dir.mkdir(parents=True)
    (wheel_dir / "unreviewed.whl").write_bytes(b"not package code")
    tasks, snapshots = fresh / "tasks.jsonl", fresh / "snapshots"
    # Check only temporary candidates; never inspect real global wheel locations.
    candidates = [
        p for cwd in (original, fresh)
        for p in safety.wheel_candidates(cwd, tasks, snapshots)
        if p.is_relative_to(tmp_path)
    ]
    with pytest.raises(safety.ProbeRefused) as exc:
        safety.check_wheels(candidates)
    assert str(exc.value) == (
        f"discoverable wheelhouse: {wheel_dir} (first wheel: unreviewed.whl)"
    )


def test_check_wheels_refuses_top_level_entry_without_opening_wheel(monkeypatch, tmp_path):
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    for name in ("z.whl", "a.whl"):
        (wheel_dir / name).write_bytes(b"never opened")

    def forbid_read(*args, **kwargs):
        pytest.fail("wheel contents must not be opened during discovery")

    monkeypatch.setattr(Path, "read_bytes", forbid_read)
    with pytest.raises(safety.ProbeRefused) as exc:
        safety.check_wheels([wheel_dir])
    assert str(exc.value) == f"discoverable wheelhouse: {wheel_dir} (first wheel: a.whl)"


@pytest.mark.parametrize("broken", [False, True])
def test_check_wheels_refuses_matching_symlink_even_when_broken(tmp_path, broken):
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    target = tmp_path / "target"
    if not broken:
        target.write_bytes(b"not a wheel archive")
    (wheel_dir / "linked.whl").symlink_to(target)
    with pytest.raises(safety.ProbeRefused) as exc:
        safety.check_wheels([wheel_dir])
    assert str(exc.value) == f"discoverable wheelhouse: {wheel_dir} (first wheel: linked.whl)"


def test_check_wheels_matches_only_top_level_wheels_and_deduplicates(tmp_path):
    wheel_dir = tmp_path / "wheels"
    nested = wheel_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "nested.whl").write_bytes(b"not discovered by the real resolvers")
    (wheel_dir / "README.txt").write_text("synthetic", encoding="utf-8")
    missing = tmp_path / "missing"
    assert safety.check_wheels([wheel_dir, missing, wheel_dir]) == [
        {"path": str(wheel_dir), "top_level_wheels": []},
        {"path": str(missing), "top_level_wheels": []},
    ]


def test_check_wheels_refuses_candidate_that_is_a_file(tmp_path):
    candidate = tmp_path / "wheels"
    candidate.write_bytes(b"synthetic")
    with pytest.raises(safety.ProbeRefused) as exc:
        safety.check_wheels([candidate])
    assert str(exc.value) == f"wheel candidate is not a directory: {candidate}"


def test_check_wheels_refuses_uninspectable_candidate(monkeypatch, tmp_path):
    candidate = tmp_path / "wheels"
    candidate.mkdir()

    def deny_scandir(path):
        assert path == candidate
        raise PermissionError("synthetic permission failure")

    monkeypatch.setattr(safety.os, "scandir", deny_scandir)
    with pytest.raises(safety.ProbeRefused) as exc:
        safety.check_wheels([candidate])
    assert str(exc.value) == (
        f"cannot inspect wheel candidate {candidate}: synthetic permission failure"
    )


def test_repository_admission_accepts_clean_exact_baseline(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    calls = _fake_git(monkeypatch, repo)
    assert safety.check_repository(repo) == []
    assert calls == [
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    ]


def test_git_identity_check_ignores_inherited_repository_and_config_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other_git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "other_worktree"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "alias.status")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "!false")
    repo = tmp_path / "repo"

    def fake_run(argv, **kwargs):
        assert argv == ["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"]
        assert {key: value for key, value in kwargs["env"].items() if key.startswith("GIT_")} == {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
        }
        assert kwargs["check"] and kwargs["capture_output"]
        assert kwargs["timeout"] == 15
        return SimpleNamespace(stdout=(safety.BASELINE + "\n").encode())

    monkeypatch.setattr(safety.subprocess, "run", fake_run)
    assert safety.git(repo, "rev-parse", "HEAD") == safety.BASELINE + "\n"


@pytest.mark.parametrize("status_code", ["??", " M", "M ", "MM", "A ", "AM"])
def test_repository_admission_allows_only_reviewed_probe_edits(monkeypatch, tmp_path, status_code):
    repo = tmp_path / "repo"
    paths = sorted(safety.PROBE_FILES)
    status = "".join(f"{status_code} {path}\0" for path in paths)
    _fake_git(monkeypatch, repo, status=status)
    assert safety.check_repository(repo) == paths


@pytest.mark.parametrize("path", ["README.md", "agents/baseline/agent.yaml", "tests/unrelated.py"])
def test_repository_admission_refuses_unrelated_changes(monkeypatch, tmp_path, path):
    repo = tmp_path / "repo"
    _fake_git(monkeypatch, repo, status=f" M {path}\0")
    with pytest.raises(safety.ProbeRefused) as exc:
        safety.check_repository(repo)
    assert str(exc.value) == f"repository is dirty outside the probe: {path}"


@pytest.mark.parametrize("record", [
    "R  tools/harness_cert/_probe_safety.py\0tools/old_probe.py\0",
    " D tools/harness_cert/_probe_safety.py\0",
    "UU tools/harness_cert/_probe_safety.py\0",
    "X\0",
])
def test_repository_admission_refuses_rename_delete_conflict_and_malformed_status(
    monkeypatch, tmp_path, record,
):
    repo = tmp_path / "repo"
    _fake_git(monkeypatch, repo, status=record)
    with pytest.raises(safety.ProbeRefused, match="unreviewed Git change"):
        safety.check_repository(repo)


def test_repository_admission_refuses_wrong_commit(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _fake_git(monkeypatch, repo, head="0" * 40)
    with pytest.raises(safety.ProbeRefused, match=f"HEAD must equal reviewed baseline {safety.BASELINE}"):
        safety.check_repository(repo)


def test_repository_admission_requires_actual_repository_root(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _fake_git(monkeypatch, repo, root=repo.parent)
    with pytest.raises(safety.ProbeRefused, match="not the repository root"):
        safety.check_repository(repo)


def test_confinement_allows_descendant_and_resolved_in_root_symlink(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "inside").mkdir()
    (root / "link").symlink_to(root / "inside", target_is_directory=True)
    assert safety.confined(root / "inside/file.txt", root) == root / "inside/file.txt"
    assert safety.confined(root / "link/file.txt", root) == root / "inside/file.txt"


@pytest.mark.parametrize("escape", ["parent", "prefix_sibling", "symlink"])
def test_confinement_refuses_parent_prefix_and_symlink_escapes(tmp_path, escape):
    root = tmp_path / "allowed"
    root.mkdir()
    if escape == "parent":
        path = root / "../outside.txt"
    elif escape == "prefix_sibling":
        path = tmp_path / "allowed_other/file.txt"
    else:
        (root / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
        path = root / "link/file.txt"
    with pytest.raises(safety.ProbeRefused, match="path escapes permitted root"):
        safety.confined(path, root)


@pytest.fixture
def guard(tmp_path):
    repo = tmp_path / "repo"
    run_root = tmp_path / "run"
    outputs = repo / "harness_cert/results/H04"
    protected = [repo / "data", tmp_path / "competition"]
    instance = safety.ParentGuard(repo, run_root, [outputs], protected)
    instance.port = 45123
    return instance


@pytest.mark.parametrize("host", ["127.0.0.1", b"127.0.0.1"])
def test_parent_guard_allows_literal_ipv4_loopback_resolution(guard, host):
    guard.audit("socket.getaddrinfo", (host, guard.port, 0, 0, 0))
    assert guard.violations == []


@pytest.mark.parametrize("host", ["localhost", "example.com", "::1", "proxy.invalid", b"10.0.0.2"])
def test_parent_guard_refuses_nonliteral_dns_and_proxy_resolution(guard, host):
    with pytest.raises(safety.ProbeRefused, match="non-loopback DNS refused"):
        guard.audit("socket.getaddrinfo", (host, guard.port, 0, 0, 0))
    assert len(guard.violations) == 1


def test_parent_guard_allows_only_exact_probe_connection_and_ephemeral_bind(guard):
    sock = SimpleNamespace(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0)
    guard.audit("socket.bind", (sock, ("127.0.0.1", 0)))
    guard.audit("socket.connect", (sock, ("127.0.0.1", guard.port)))
    assert len(guard.bindings) == 1
    assert {key: guard.bindings[0][key] for key in ("host", "port", "purpose")} == {
        "host": "127.0.0.1", "port": 0, "purpose": "scripted_listener",
    }
    assert guard.connections == [{"host": "127.0.0.1", "port": guard.port}]


@pytest.mark.parametrize("address", [
    ("203.0.113.1", 443), ("127.0.0.1", 8080), ("10.0.0.2", 3128),
    ("localhost", 45123), ("::1", 45123), "/tmp/proxy.sock",
])
def test_parent_guard_refuses_external_proxy_and_wrong_port_connections(guard, address):
    with pytest.raises(safety.ProbeRefused, match="non-probe connection refused"):
        guard.audit("socket.connect", (object(), address))
    assert guard.connections == []


@pytest.mark.parametrize("address", [("0.0.0.0", 0), ("127.0.0.1", 45123), ("::1", 0)])
def test_parent_guard_refuses_non_ephemeral_or_nonliteral_loopback_bind(guard, address):
    with pytest.raises(safety.ProbeRefused, match="non-loopback/ephemeral bind refused"):
        guard.audit("socket.bind", (object(), address))


def test_parent_guard_refuses_connections_before_port_is_registered(guard):
    guard.port = None
    with pytest.raises(safety.ProbeRefused, match="non-probe connection refused"):
        guard.audit("socket.connect", (object(), ("127.0.0.1", None)))


@pytest.mark.parametrize("host, expected", [
    ("127.0.0.1", safety.IPV4_SERVER), (b"127.0.0.1", safety.IPV4_SERVER),
    ("::1", safety.IPV6_FEATURE_LOOPBACK),
    ("0:0:0:0:0:0:0:1", safety.IPV6_FEATURE_LOOPBACK),
])
def test_ip_literal_uses_semantic_numeric_addresses(host, expected):
    assert safety.ip_literal(host) == expected


@pytest.mark.parametrize("host", [
    "localhost", "example.com", "127.1", "2130706433", "0177.0.0.1",
    "127.000.000.001", "::1%lo0", "fe80::1%eth0", "[::1]", " ::1",
    "127.0.0.1\n", "", b"\xff", None, 2130706433,
])
def test_ip_literal_rejects_hostnames_scopes_and_nonliteral_spellings(host):
    assert safety.ip_literal(host) is None


@pytest.mark.parametrize("proto", [0, socket.IPPROTO_TCP])
def test_ipv4_listener_and_connection_allow_only_stream_socket_protocols(guard, proto):
    sock = SimpleNamespace(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=proto)
    guard.audit("socket.bind", (sock, ("127.0.0.1", 0)))
    guard.audit("socket.connect", (sock, ("127.0.0.1", guard.port)))
    assert guard.violations == []


@pytest.mark.parametrize("event", ["socket.bind", "socket.connect"])
@pytest.mark.parametrize("fields", [
    {"family": socket.AF_INET6}, {"family": socket.AF_UNIX},
    {"type": socket.SOCK_DGRAM}, {"proto": socket.IPPROTO_UDP},
    {"family": None}, {"type": None}, {"proto": None},
])
def test_network_guard_rejects_socket_family_type_and_protocol_mismatch(guard, event, fields):
    sock = SimpleNamespace(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0)
    sock.__dict__.update(fields)
    address = ("127.0.0.1", 0 if event == "socket.bind" else guard.port)
    with pytest.raises(safety.ProbeRefused):
        guard.audit(event, (sock, address))
    assert guard.bindings == [] and guard.connections == []
    assert len(guard.violations) == 1


@pytest.mark.parametrize("address", [
    ("127.0.0.2", 0), ("127.1", 0), ("localhost", 0), ("0.0.0.0", 0),
    ("10.0.0.2", 0), ("203.0.113.1", 0), ("8.8.8.8", 0),
    ("127.0.0.1.evil.example", 0), ("::", 0), ("::1", 0),
    ("::1%lo0", 0), ("::ffff:127.0.0.1", 0),
    ("0:0:0:0:0:ffff:7f00:1", 0), ("::ffff:8.8.8.8", 0),
    ("fe80::1", 0), ("fc00::1", 0), ("2001:4860:4860::8888", 0),
    ("127.0.0.1", False), ("127.0.0.1", 0.0), ("127.0.0.1", "0"),
    ("127.0.0.1", -1), ("127.0.0.1", 1), ("127.0.0.1", 0, 0, 0),
    ["127.0.0.1", 0], "/tmp/synthetic.sock",
])
def test_ipv4_listener_rejects_other_hosts_ports_and_address_shapes(guard, address):
    host = address[0] if isinstance(address, (tuple, list)) else ""
    family = socket.AF_INET6 if isinstance(host, str) and ":" in host else socket.AF_INET
    sock = SimpleNamespace(family=family, type=socket.SOCK_STREAM, proto=0)
    with pytest.raises(safety.ProbeRefused, match="non-loopback/ephemeral bind refused"):
        guard.audit("socket.bind", (sock, address))
    assert guard.bindings == []


@pytest.mark.parametrize("address", [
    ("127.0.0.2", 45123), ("0.0.0.0", 45123), ("10.0.0.2", 45123),
    ("203.0.113.1", 45123), ("8.8.8.8", 45123),
    ("127.0.0.1.evil.example", 45123), ("localhost", 45123), ("::1", 45123),
    ("::1", 0), ("::1%lo0", 45123), ("::ffff:127.0.0.1", 45123),
    ("0:0:0:0:0:ffff:7f00:1", 45123), ("::ffff:8.8.8.8", 45123),
    ("fe80::1", 45123), ("fc00::1", 45123), ("2001:4860:4860::8888", 45123),
    (b"127.0.0.1", 45123), ("127.0.0.1", 0), ("127.0.0.1", True),
    ("127.0.0.1", 45123.0), ("127.0.0.1", "45123"),
    ("127.0.0.1", 45123, 0, 0), ["127.0.0.1", 45123],
])
def test_outbound_guard_requires_exact_ipv4_endpoint_and_integer_port(guard, address):
    host = address[0] if isinstance(address, (tuple, list)) else ""
    family = socket.AF_INET6 if isinstance(host, str) and ":" in host else socket.AF_INET
    sock = SimpleNamespace(family=family, type=socket.SOCK_STREAM, proto=0)
    with pytest.raises(safety.ProbeRefused, match="non-probe connection refused"):
        guard.audit("socket.connect", (sock, address))
    assert guard.connections == []


_SYNTHETIC_FEATURE_SOURCE = '''def _has_ipv6(host):
    sock = make_socket()
    sock.bind((host, 0))
    return True

HAS_IPV6 = _has_ipv6("::1")
'''


def _synthetic_ipv6_feature(monkeypatch, tmp_path, *, source_text=_SYNTHETIC_FEATURE_SOURCE,
                            execution_text=None, execution_filename=None, before_bind=None):
    """Compile only a synthetic module and use a fake socket, never urllib3 code."""
    source = tmp_path / "reviewed_urllib3/util/connection.py"
    module_code = compile(source_text, str(source), "exec", dont_inherit=True)
    function_code = next(code for code in module_code.co_consts
                         if isinstance(code, CodeType) and code.co_name == "_has_ipv6")
    policy = safety.IPv6FeatureProbe(source, module_code, function_code)
    module = ModuleType("urllib3.util.connection")
    module.__file__ = str(source)
    module.__spec__ = ModuleSpec(module.__name__, loader=None, origin=str(source))
    module.__spec__._initializing = True
    monkeypatch.setitem(sys.modules, module.__name__, module)
    sock = SimpleNamespace(family=socket.AF_INET6, type=socket.SOCK_STREAM, proto=0)
    fixture = SimpleNamespace(policy=policy, module=module, sock=sock, decisions=[],
                              checked_socket=sock, address_override=None, guard=None)
    original_getframe = sys._getframe

    def bind(address):
        caller = original_getframe(1)
        if before_bind is not None:
            before_bind(fixture, caller)
        checked_address = address if fixture.address_override is None else fixture.address_override
        accepted = policy.permits_bind(fixture.checked_socket, checked_address, caller)
        fixture.decisions.append(accepted)
        if fixture.guard is not None:
            # The real C socket audit event sees _has_ipv6 directly. Reproduce
            # that frame here without a socket, audit hook, or harness import.
            with monkeypatch.context() as patch:
                patch.setattr(safety.sys, "_getframe", lambda depth: caller)
                fixture.guard.audit("socket.bind", (fixture.checked_socket, checked_address))

    sock.bind = bind
    module.make_socket = lambda: sock
    execution_code = compile(execution_text or source_text,
                             str(execution_filename or source), "exec", dont_inherit=True)
    fixture.run = lambda: exec(execution_code, module.__dict__)
    return fixture


def test_ipv6_feature_bind_requires_verified_module_initialization_context(monkeypatch, tmp_path, guard):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path)
    guard.ipv6_feature_probe = fixture.policy
    fixture.guard = guard
    fixture.run()
    assert fixture.decisions == [True]
    assert len(guard.bindings) == 1
    assert {key: guard.bindings[0][key] for key in ("host", "port", "purpose")} == {
        "host": "::1", "port": 0, "purpose": "urllib3_ipv6_detection",
    }
    assert guard.bindings[0]["source"] == str(fixture.policy.source)
    assert guard.bindings[0]["function"] == "_has_ipv6"
    assert guard.connections == [] and guard.violations == []
    with pytest.raises(FrozenInstanceError):
        fixture.policy.source = tmp_path / "other.py"


def test_ipv6_feature_bind_accepts_semantically_equal_expanded_loopback(monkeypatch, tmp_path):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path,
                                     source_text=_SYNTHETIC_FEATURE_SOURCE.replace('"::1"', '"0:0:0:0:0:0:0:1"'))
    fixture.run()
    assert fixture.decisions == [True]


@pytest.mark.parametrize("change", [
    "initialization_finished", "initialization_truthy", "spec_missing", "origin_changed",
    "file_changed", "name_changed", "module_replaced", "module_missing", "function_replaced", "socket_replaced",
    "host_local_changed",
])
def test_ipv6_feature_bind_rejects_changed_module_function_and_socket_identity(
    monkeypatch, tmp_path, change,
):
    def before_bind(fixture, caller):
        if change == "initialization_finished":
            fixture.module.__spec__._initializing = False
        elif change == "initialization_truthy":
            fixture.module.__spec__._initializing = 1
        elif change == "spec_missing":
            fixture.module.__spec__ = None
        elif change == "origin_changed":
            fixture.module.__spec__.origin = str(tmp_path / "other.py")
        elif change == "file_changed":
            fixture.module.__file__ = str(tmp_path / "other.py")
        elif change == "name_changed":
            fixture.module.__name__ = "unreviewed.util.connection"
        elif change == "module_replaced":
            monkeypatch.setitem(sys.modules, fixture.module.__name__, ModuleType(fixture.module.__name__))
        elif change == "module_missing":
            monkeypatch.delitem(sys.modules, fixture.module.__name__)
        elif change == "function_replaced":
            fixture.module._has_ipv6 = lambda host: True
        elif change == "socket_replaced":
            fixture.checked_socket = SimpleNamespace(family=socket.AF_INET6, type=socket.SOCK_STREAM, proto=0)
        elif change == "host_local_changed":
            # The address alone must not authorize a different local host.
            fixture.address_override = ("::1", 0)

    text = _SYNTHETIC_FEATURE_SOURCE
    if change == "host_local_changed":
        text = text.replace('"::1"', '"203.0.113.1"')
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path, source_text=text, before_bind=before_bind)
    fixture.run()
    assert fixture.decisions == [False]


@pytest.mark.parametrize("execution_change", ["filename", "module_code", "function_code", "callback"])
def test_ipv6_feature_bind_rejects_unreviewed_code_or_intermediate_callback(
    monkeypatch, tmp_path, execution_change,
):
    expected_text = execution_text = _SYNTHETIC_FEATURE_SOURCE
    filename = None
    if execution_change == "filename":
        filename = tmp_path / "spoofed/connection.py"
    elif execution_change == "module_code":
        execution_text += "UNREVIEWED_MODULE_VALUE = 1\n"
    elif execution_change == "function_code":
        execution_text = execution_text.replace("    sock = make_socket()", "    unexpected = 1\n    sock = make_socket()")
    elif execution_change == "callback":
        expected_text = execution_text = '''def _has_ipv6(host):
    sock = make_socket()
    def callback():
        sock.bind((host, 0))
    callback()
    return True

HAS_IPV6 = _has_ipv6("::1")
'''
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path, source_text=expected_text,
                                     execution_text=execution_text, execution_filename=filename)
    fixture.run()
    assert fixture.decisions == [False]


@pytest.mark.parametrize("initializing", [True, False])
def test_ipv6_feature_function_later_call_is_refused_even_with_initialization_flag(
    monkeypatch, tmp_path, initializing,
):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path)
    fixture.run()
    fixture.module.__spec__._initializing = initializing
    fixture.module._has_ipv6("::1")
    assert fixture.decisions == [True, False]


@pytest.mark.parametrize("fields", [
    {"family": socket.AF_INET}, {"family": socket.AF_UNIX},
    {"type": socket.SOCK_DGRAM}, {"proto": socket.IPPROTO_TCP},
    {"family": None}, {"type": None}, {"proto": None},
])
def test_ipv6_feature_bind_rejects_other_socket_families_types_and_protocols(
    monkeypatch, tmp_path, fields,
):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path)
    fixture.sock.__dict__.update(fields)
    fixture.run()
    assert fixture.decisions == [False]


@pytest.mark.parametrize("address", [
    ("::1", 1), ("::1", -1), ("::1", False), ("::1", 0.0), ("::1", "0"),
    ("::1", 0, 0, 0), ["::1", 0], ("localhost", 0), ("::1%lo0", 0),
    ("::ffff:127.0.0.1", 0), ("127.0.0.1", 0), ("127.0.0.2", 0),
    ("0:0:0:0:0:ffff:7f00:1", 0), ("::ffff:8.8.8.8", 0),
    ("::", 0), ("0.0.0.0", 0), ("8.8.8.8", 0), ("127.0.0.1.evil.example", 0),
    ("fe80::1", 0), ("fc00::1", 0), ("2001:db8::1", 0), ("2001:4860:4860::8888", 0),
])
def test_ipv6_feature_bind_rejects_other_addresses_and_nonzero_or_noninteger_ports(
    monkeypatch, tmp_path, address,
):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path)
    fixture.address_override = address
    fixture.run()
    assert fixture.decisions == [False]


@pytest.mark.parametrize("host", [
    "8.8.8.8", "2001:4860:4860::8888", "::ffff:8.8.8.8",
    "0:0:0:0:0:ffff:7f00:1", "fe80::1", "fc00::1", "127.0.0.1.evil.example",
])
def test_ipv6_guard_refuses_other_hosts_even_in_verified_feature_context(monkeypatch, tmp_path, guard, host):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path)
    guard.ipv6_feature_probe = fixture.policy
    fixture.guard = guard
    fixture.address_override = (host, 0)
    with pytest.raises(safety.ProbeRefused, match="non-loopback/ephemeral bind refused"):
        fixture.run()
    assert fixture.decisions == [False]
    assert guard.bindings == [] and len(guard.violations) == 1


def test_ipv6_import_exception_does_not_authorize_outbound_connections(monkeypatch, tmp_path, guard):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path)
    guard.ipv6_feature_probe = fixture.policy
    with pytest.raises(safety.ProbeRefused, match="non-probe connection refused"):
        guard.audit("socket.connect", (fixture.sock, ("::1", 0)))
    assert guard.connections == []


def test_ipv6_bind_outside_import_context_is_refused_with_sticky_violation(monkeypatch, tmp_path, guard):
    fixture = _synthetic_ipv6_feature(monkeypatch, tmp_path)
    guard.ipv6_feature_probe = fixture.policy
    with pytest.raises(safety.ProbeRefused, match="non-loopback/ephemeral bind refused"):
        guard.audit("socket.bind", (fixture.sock, ("::1", 0)))
    assert guard.bindings == []
    assert len(guard.violations) == 1


def test_ipv6_feature_from_site_hash_verifies_and_compiles_without_execution(monkeypatch, tmp_path):
    site = tmp_path / "site-packages"
    source = site / "urllib3/util/connection.py"
    source.parent.mkdir(parents=True)
    data = (_SYNTHETIC_FEATURE_SOURCE + '\nraise AssertionError("package code must not execute")\n').encode()
    source.write_bytes(data)
    monkeypatch.setattr(safety, "URLLIB3_CONNECTION_SHA256", hashlib.sha256(data).hexdigest())
    sentinel = ModuleType("urllib3.util.connection")
    monkeypatch.setitem(sys.modules, sentinel.__name__, sentinel)
    policy = safety.IPv6FeatureProbe.from_site(site)
    assert policy.source == source
    assert policy.module_code.co_filename == str(source)
    assert policy.function_code.co_name == "_has_ipv6"
    assert policy.function_code.co_filename == str(source)
    assert policy.function_code in policy.module_code.co_consts
    assert sys.modules[sentinel.__name__] is sentinel
    assert not hasattr(sentinel, "_has_ipv6")


def test_ipv6_feature_from_site_rejects_changed_source_before_compiling(monkeypatch, tmp_path):
    site = tmp_path / "site-packages"
    source = site / "urllib3/util/connection.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"raise AssertionError('changed source')\n")

    def refuse_compile(*args, **kwargs):
        pytest.fail("Source with an unreviewed hash must not be compiled")

    monkeypatch.setattr(safety, "compile", refuse_compile, raising=False)
    with pytest.raises(safety.ProbeRefused, match="differs from reviewed 2.8.0"):
        safety.IPv6FeatureProbe.from_site(site)


def test_ipv6_feature_from_site_rejects_symlink_outside_installed_site(tmp_path):
    site = tmp_path / "site-packages"
    source = site / "urllib3/util/connection.py"
    source.parent.mkdir(parents=True)
    outside = tmp_path / "external.py"
    outside.write_text(_SYNTHETIC_FEATURE_SOURCE, encoding="utf-8")
    source.symlink_to(outside)
    with pytest.raises(safety.ProbeRefused, match="path escapes permitted root"):
        safety.IPv6FeatureProbe.from_site(site)


def _popen_audit(guard, argv):
    guard.audit("subprocess.Popen", (argv[0], argv, guard.run_root, {}))


def test_parent_guard_refuses_subprocess_without_explicit_scope(guard):
    with pytest.raises(safety.ProbeRefused, match="unapproved subprocess refused"):
        _popen_audit(guard, ["/bin/bash", "-c", "echo synthetic"])


@pytest.mark.parametrize("argv", ["/bin/bash -c true", None, 123])
def test_parent_guard_refuses_unstructured_subprocess(guard, argv):
    with guard.processes_allowed("shell"):
        with pytest.raises(safety.ProbeRefused, match="unstructured subprocess refused"):
            guard.audit("subprocess.Popen", ("/bin/bash", argv, guard.run_root, {}))


def test_parent_guard_allows_structured_shell_only_in_shell_scope(guard):
    argv = ["/bin/bash", "-c", "echo synthetic"]
    with guard.processes_allowed("shell"):
        _popen_audit(guard, argv)
    assert guard.processes == [{"kind": "shell", "argv": argv, "cwd": str(guard.run_root)}]
    with pytest.raises(safety.ProbeRefused, match="unapproved subprocess refused"):
        _popen_audit(guard, argv)


@pytest.mark.parametrize("argv", [
    ["/bin/sh", "-c", "true"],
    ["/bin/bash", "-c", "true", "extra"],
    ["/usr/bin/python3", "-c", "pass"],
])
def test_parent_guard_refuses_shell_shape_or_executable_mismatch(guard, argv):
    with guard.processes_allowed("shell"):
        with pytest.raises(safety.ProbeRefused, match="unapproved subprocess refused"):
            _popen_audit(guard, argv)


@pytest.mark.parametrize("arguments", [
    ["-Im", "ensurepip", "--upgrade", "--default-pip"],
    ["-m", "ensurepip", "--upgrade", "--default-pip"],
    ["-m", "pip", "--version"],
])
def test_parent_guard_allows_exact_venv_bootstrap_commands(guard, arguments):
    argv = [str(guard.run_root / "sandbox/venv/bin/python3"), *arguments]
    with guard.processes_allowed("venv"):
        _popen_audit(guard, argv)
    assert guard.processes[-1]["kind"] == "venv"


def test_parent_guard_venv_executable_check_is_lexical_for_trusted_symlink(guard, tmp_path):
    executable = guard.run_root / "venv/bin/python3"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(tmp_path / "trusted_base_python")
    with guard.processes_allowed("venv"):
        _popen_audit(guard, [str(executable), "-m", "pip", "--version"])


@pytest.mark.parametrize("arguments", [
    ["-m", "pip", "install", "unreviewed"],
    ["-c", "pass"],
    ["-Im", "ensurepip", "--upgrade", "--default-pip", "--user"],
])
def test_parent_guard_refuses_unapproved_venv_commands(guard, arguments):
    argv = [str(guard.run_root / "venv/bin/python3"), *arguments]
    with guard.processes_allowed("venv"):
        with pytest.raises(safety.ProbeRefused, match="unapproved subprocess refused"):
            _popen_audit(guard, argv)


def test_parent_guard_refuses_venv_command_using_external_executable(guard):
    with guard.processes_allowed("venv"):
        with pytest.raises(safety.ProbeRefused, match="unapproved subprocess refused"):
            _popen_audit(guard, ["/usr/bin/python3", "-m", "pip", "--version"])


def test_parent_guard_restores_nested_process_scope_even_after_exception(guard):
    with guard.processes_allowed("shell"):
        with pytest.raises(RuntimeError, match="synthetic"):
            with guard.processes_allowed("venv"):
                raise RuntimeError("synthetic")
        _popen_audit(guard, ["/bin/bash", "-c", "true"])
    with pytest.raises(safety.ProbeRefused):
        _popen_audit(guard, ["/bin/bash", "-c", "true"])


def test_parent_guard_process_scope_does_not_leak_into_another_thread(guard):
    failures = []

    def inspect_from_other_thread():
        try:
            _popen_audit(guard, ["/bin/bash", "-c", "true"])
        except safety.ProbeRefused as exc:
            failures.append(str(exc))

    with guard.processes_allowed("shell"):
        thread = threading.Thread(target=inspect_from_other_thread)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(failures) == 1
    assert guard.processes == []


@pytest.mark.parametrize("relative", ["data/tasks.jsonl", "README.md", "agents/baseline/agent.yaml"])
def test_parent_guard_refuses_protected_and_unapproved_repository_reads(guard, relative):
    with pytest.raises(safety.ProbeRefused):
        guard.audit("open", (str(guard.repo / relative), "r", 0))


def test_parent_guard_refuses_external_protected_dataset_read(guard):
    with pytest.raises(safety.ProbeRefused, match="dataset/competition read refused"):
        guard.audit("open", (guard.protected[1] / "synthetic.jsonl", "rb", 0))


def test_parent_guard_allows_probe_source_runtime_and_output_reads(guard, tmp_path):
    for path in (
        guard.repo / "tools/harness_cert/run_h04_h05_h18.py",
        guard.outputs[0] / "outcome.json",
        guard.run_root / "fixture/marker.txt",
        tmp_path / "trusted_runtime/module.py",
    ):
        guard.audit("open", (path, "rb", 0))
    assert guard.violations == []


@pytest.mark.parametrize("flags", [os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_APPEND])
def test_parent_guard_refuses_all_write_modes_outside_outputs(guard, flags):
    with pytest.raises(safety.ProbeRefused, match="parent write outside probe outputs refused"):
        guard.audit("open", (str(guard.repo / "README.md"), "w", flags))


def test_parent_guard_allows_writes_and_directories_only_under_run_or_outputs(guard):
    for root in (guard.run_root, guard.outputs[0]):
        guard.audit("open", (os.fsencode(root / "outcome.json"), "w", os.O_CREAT | os.O_WRONLY))
        guard.audit("os.mkdir", (str(root / "nested"), 0o700, -1))
    assert guard.violations == []


def test_parent_guard_refuses_directory_outside_outputs(guard):
    with pytest.raises(safety.ProbeRefused, match="parent mutation outside probe outputs refused"):
        guard.audit("os.mkdir", (str(guard.repo / "unapproved"), 0o700, -1))


def test_parent_guard_resolves_symlinks_before_read_or_write_admission(guard, tmp_path):
    guard.run_root.mkdir()
    (guard.run_root / "escape").symlink_to(guard.repo / "data", target_is_directory=True)
    path = guard.run_root / "escape/synthetic.txt"
    with pytest.raises(safety.ProbeRefused, match="dataset/competition read refused"):
        guard.audit("open", (str(path), "r", 0))
    with pytest.raises(safety.ProbeRefused, match="parent write outside probe outputs refused"):
        guard.audit("open", (str(path), "w", os.O_WRONLY))


def test_parent_guard_does_not_allow_output_prefix_siblings(guard):
    path = guard.outputs[0].with_name("H04_other") / "outcome.json"
    with pytest.raises(safety.ProbeRefused, match="parent write outside probe outputs refused"):
        guard.audit("open", (str(path), "w", os.O_WRONLY))


@pytest.mark.parametrize("event", ["os.remove", "os.rmdir"])
def test_parent_guard_allows_removal_only_inside_outputs(guard, event):
    guard.audit(event, (str(guard.run_root / "temporary"), -1))
    with pytest.raises(safety.ProbeRefused, match="parent mutation outside probe outputs refused"):
        guard.audit(event, (str(guard.repo / "README.md"), -1))


def test_parent_guard_rename_checks_both_source_and_destination(guard):
    inside = guard.run_root / "old.txt"
    moved = guard.outputs[0] / "new.txt"
    outside = guard.repo / "README.md"
    guard.audit("os.rename", (str(inside), str(moved), -1, -1))
    for source, destination in ((outside, moved), (inside, outside)):
        with pytest.raises(safety.ProbeRefused, match="parent mutation outside probe outputs refused"):
            guard.audit("os.rename", (str(source), str(destination), -1, -1))


def test_parent_guard_allows_removing_an_in_root_symlink_itself(guard):
    guard.run_root.mkdir()
    link = guard.run_root / "outside_link"
    link.symlink_to(guard.repo / "README.md")
    guard.audit("os.remove", (str(link), -1))
    assert link.is_symlink()  # Manual audit never performs the mutation.


def test_parent_guard_renames_link_itself_but_refuses_hardlink_to_external_target(guard):
    guard.run_root.mkdir()
    link = guard.run_root / "outside_link"
    link.symlink_to(guard.repo / "README.md")
    destination = guard.run_root / "moved_link"
    guard.audit("os.rename", (str(link), str(destination), -1, -1))
    with pytest.raises(safety.ProbeRefused, match="parent mutation outside probe outputs refused"):
        guard.audit("os.link", (str(link), str(destination), -1, -1))
    assert link.is_symlink()  # Neither audit call performs a real mutation.


@pytest.mark.parametrize("event,arguments", [
    ("os.chmod", (0o600, -1)),
    ("os.chown", (1000, 1000, -1)),
    ("os.utime", (None, None, -1)),
])
def test_parent_guard_metadata_mutation_refuses_final_symlink_escape(guard, event, arguments):
    guard.run_root.mkdir()
    link = guard.run_root / "outside_link"
    link.symlink_to(guard.repo / "README.md")
    with pytest.raises(safety.ProbeRefused, match="parent mutation outside probe outputs refused"):
        guard.audit(event, (str(link), *arguments))


def test_parent_guard_fixture_git_requires_scoped_exact_argv(guard):
    argv = ["/usr/bin/git", "init", "-q", str(guard.run_root / "fixture")]
    with guard.processes_allowed("fixture_git", argv):
        _popen_audit(guard, argv)
        with pytest.raises(safety.ProbeRefused, match="unapproved subprocess refused"):
            _popen_audit(guard, ["/usr/bin/git", "clean", "-fdx"])
    assert guard.processes[-1]["argv"] == argv


@pytest.mark.parametrize("event,arguments", [
    ("socket.gethostbyname", ("proxy.invalid",)),
    ("socket.gethostbyaddr", ("203.0.113.1",)),
    ("socket.getnameinfo", (("127.0.0.1", 443), 0)),
    ("socket.sendto", (object(), b"synthetic", ("203.0.113.1", 53))),
])
def test_parent_guard_refuses_alternate_dns_and_datagram_paths(guard, event, arguments):
    with pytest.raises(safety.ProbeRefused, match="DNS/datagram operation refused"):
        guard.audit(event, arguments)


def _valid_control(probe):
    patch = probe.expected_patch()
    return {
        "infrastructure_ok": True, "agent_error": None,
        "write_file_succeeded": True, "http_request_count": 3, "agent_patch": patch,
        "context": {"patch_submitted": True, "submitted_patch": patch},
    }


@pytest.mark.parametrize("fault", [
    "infrastructure", "agent_error", "tool_failure", "missing_turn", "extra_retry",
    "wrong_patch", "not_submitted", "wrong_submitted_patch",
])
def test_h05_control_requires_successful_exact_patch_and_complete_protocol(fault):
    from tools.harness_cert import run_h04_h05_h18 as probe

    valid = _valid_control(probe)
    assert probe.control_ok(valid)
    broken = copy.deepcopy(valid)
    if fault == "infrastructure":
        broken["infrastructure_ok"] = False
    elif fault == "agent_error":
        broken["agent_error"] = "Sandbox execution error: synthetic transport failure"
    elif fault == "tool_failure":
        broken["write_file_succeeded"] = False
    elif fault == "missing_turn":
        broken["http_request_count"] = 2
    elif fault == "extra_retry":
        broken["http_request_count"] = 4
    elif fault == "wrong_patch":
        broken["agent_patch"] = ""
    elif fault == "not_submitted":
        broken["context"]["patch_submitted"] = False
    else:
        broken["context"]["submitted_patch"] = "different patch"
    assert not probe.control_ok(broken)


@pytest.mark.parametrize("failure", ["control", "infrastructure"])
def test_failed_h05_prevents_running_or_interpreting_h04_and_h18(monkeypatch, failure):
    from tools.harness_cert import run_h04_h05_h18 as probe

    calls = []

    async def fake_case(case, *args):
        calls.append(case)
        assert case == "H05", "missing-tool probes must not run after a failed control"
        return {"infrastructure_ok": failure != "infrastructure", "control_pass": failure != "control"}

    monkeypatch.setattr(probe, "run_case", fake_case)
    code = asyncio.run(probe.run_cases(None, {"H05": object()}, None, None, None, [], None, None))
    assert code == 2
    assert calls == ["H05"]


def test_runner_order_is_h05_then_h04_then_h18_with_valid_control(monkeypatch):
    from tools.harness_cert import run_h04_h05_h18 as probe

    calls = []

    async def fake_case(case, *args):
        calls.append(case)
        return {"infrastructure_ok": True, "control_pass": case == "H05"}

    monkeypatch.setattr(probe, "run_case", fake_case)
    outputs = {case: object() for case in ("H05", "H04", "H18")}
    assert asyncio.run(probe.run_cases(None, outputs, None, None, None, [], None, None)) == 0
    assert calls == ["H05", "H04", "H18"]


def test_h04_infrastructure_failure_prevents_h18(monkeypatch):
    from tools.harness_cert import run_h04_h05_h18 as probe

    calls = []

    async def fake_case(case, *args):
        calls.append(case)
        return {"infrastructure_ok": case == "H05", "control_pass": case == "H05"}

    monkeypatch.setattr(probe, "run_case", fake_case)
    outputs = {case: object() for case in ("H05", "H04", "H18")}
    assert asyncio.run(probe.run_cases(None, outputs, None, None, None, [], None, None)) == 2
    assert calls == ["H05", "H04"]


def test_missing_tool_scripts_leave_recovery_observable():
    from tools.harness_cert import run_h04_h05_h18 as probe

    h04 = probe.scripts("H04")
    assert h04 == probe.scripts("H05")  # Declaration is the changed variable.
    assert h04[0]["tool_calls"][0]["function"]["name"] == "write_file"
    h18 = probe.scripts("H18")
    assert [message["tool_calls"][0]["function"]["name"] for message in h18[:-1]] == [
        "write_file", "bash", "submit_patch",
    ]
    assert h18[-1]["content"]


def test_exception_observer_preserves_original_exception_and_lookup_traceback():
    from tools.harness_cert import run_h04_h05_h18 as probe

    handler = probe.Exceptions()

    def _get_tool():
        raise ValueError("Tool 'synthetic_missing' not found.")

    try:
        _get_tool()
    except ValueError:
        record = logging.LogRecord(
            "swegemma.harness.agent_runner", logging.ERROR,
            "synthetic_runner.py", 1, "Synthetic exception observation", (), sys.exc_info(),
        )
        handler.emit(record)
    assert len(handler.records) == 1
    observed = handler.records[0]
    assert observed["class"] == "builtins.ValueError"
    assert observed["message"] == "Tool 'synthetic_missing' not found."
    assert observed["adk_get_tool_in_traceback"] is True
    assert "ValueError: Tool 'synthetic_missing' not found." in observed["traceback"]
    assert observed["logger"] == "swegemma.harness.agent_runner"


def test_write_file_results_reads_actual_legacy_trace_fields():
    from tools.harness_cert import run_h04_h05_h18 as probe

    response = {"status": "ok", "filepath": "app.py", "size": len(probe.CHANGED)}
    trace = {"entries": [
        {"type": "tool_call", "tool": "write_file", "args": probe.WRITE_ARGS},
        {"type": "tool_response", "tool": "write_file", "result": json.dumps(response)},
        {"type": "tool_response", "tool": "submit_patch", "result": '{"status":"ok"}'},
        {"type": "final", "content": "Synthetic completion"},
    ]}
    assert probe.write_file_results(trace) == [response]


def test_write_file_results_ignores_calls_other_tools_and_missing_response():
    from tools.harness_cert import run_h04_h05_h18 as probe

    assert probe.write_file_results({"entries": []}) == []
    assert probe.write_file_results({"entries": [
        {"type": "tool_call", "tool": "write_file", "args": probe.WRITE_ARGS},
        {"type": "tool_response", "tool": "read_file", "result": '{"status":"ok"}'},
        {"type": "usage", "usage": {"total_tokens": 1}},
        {"event_type": "tool_response", "tool_name": "write_file", "tool_result": "unused"},
    ]}) == []


def test_write_file_results_preserves_tool_error_instead_of_claiming_success():
    from tools.harness_cert import run_h04_h05_h18 as probe

    failure = {"status": "error", "error_type": "FileWriteError", "error_message": "synthetic"}
    assert probe.write_file_results({"entries": [
        {"type": "tool_response", "tool": "write_file", "result": json.dumps(failure)},
    ]}) == [failure]


@pytest.fixture
def fake_instrumented_manager(guard):
    """A same-name stdlib fake; no installed sandbox is imported or executed."""
    from tools.harness_cert import run_h04_h05_h18 as probe

    guard.run_root.mkdir()
    snapshot = guard.run_root / "synthetic.tar.gz"
    snapshot.write_bytes(b"synthetic fixture identity, not an executable archive")
    diff_command = "cd /workspace && (git diff --binary _swegemma_baseline 2>/dev/null || git diff --binary HEAD)"
    zero_wheels = "ls /wheels/*.whl 2>/dev/null | wc -l"
    commands = {diff_command, zero_wheels}

    class SubprocessManager:
        def __init__(self):
            self._sandboxes = {}
            self.calls = []
            self.wheel_stdout = "0\n"
            self.on_stop = lambda: None

        @property
        def sandboxes(self):
            return {key: dict(paths) for key, paths in self._sandboxes.items()}

        def start(self):
            self.calls.append(("start", getattr(guard._scope, "kind", None)))
            root = guard.run_root / "sandbox"
            workspace = root / "workspace"
            wheels = root / "wheels"
            (workspace / ".git/hooks").mkdir(parents=True)
            wheels.mkdir()
            (workspace / ".git/config").write_text("[core]\n", encoding="utf-8")
            (workspace / "app.py").write_text(probe.INITIAL, encoding="utf-8")
            self._sandboxes["fake_id"] = {"root": root, "workspace": workspace, "wheels": wheels}
            return "fake_id"

        def exec(self, sandbox_id, command, timeout=None):
            self.calls.append(("exec", sandbox_id, command, timeout, getattr(guard._scope, "kind", None)))
            stdout = self.wheel_stdout if command == zero_wheels else probe.expected_patch()
            return SimpleNamespace(exit_code=0, stdout=stdout, stderr="", timed_out=False)

        def copy_to(self, sandbox_id, source, destination):
            self.calls.append(("copy", sandbox_id, source, destination))

        def stop(self, sandbox_id):
            root = self._sandboxes[sandbox_id]["root"]
            self.calls.append(("stop", sandbox_id, root.exists()))
            self.on_stop()
            shutil.rmtree(root)
            self._sandboxes.pop(sandbox_id)

    manager = SubprocessManager()
    observation = probe.instrument_manager(manager, snapshot, commands, [], guard)
    return SimpleNamespace(
        probe=probe, manager=manager, observation=observation, snapshot=snapshot,
        diff_command=diff_command, zero_wheels=zero_wheels, guard=guard,
    )


def test_manager_observers_preserve_class_and_delegate_admitted_commands(fake_instrumented_manager):
    fixture = fake_instrumented_manager
    manager = fixture.manager
    assert type(manager).__name__ == "SubprocessManager"
    identifier = manager.start()
    result = manager.exec(identifier, fixture.zero_wheels, timeout=7)
    assert result.stdout == "0\n"
    assert manager.calls == [
        ("start", "venv"), ("exec", identifier, fixture.zero_wheels, 7, "shell"),
    ]
    assert fixture.observation["commands"][0]["command"] == fixture.zero_wheels


def test_manager_command_refusal_is_sticky_and_never_reaches_original(fake_instrumented_manager):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()
    with pytest.raises(safety.ProbeRefused, match="unapproved sandbox command"):
        fixture.manager.exec(identifier, "curl https://example.invalid")
    assert not any(call[0] == "exec" for call in fixture.manager.calls)
    violations = list(fixture.guard.violations)
    fixture.manager.exec(identifier, fixture.zero_wheels)
    assert fixture.guard.violations == violations
    assert violations


def test_manager_unknown_sandbox_refusal_never_dispatches(fake_instrumented_manager):
    fixture = fake_instrumented_manager
    fixture.manager.start()
    with pytest.raises(safety.ProbeRefused, match="unapproved sandbox command"):
        fixture.manager.exec("unknown", fixture.zero_wheels)
    assert fixture.guard.violations
    assert not any(call[0] == "exec" for call in fixture.manager.calls)


def test_manager_refuses_newly_discoverable_wheelhouse_before_command(fake_instrumented_manager, monkeypatch):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()

    def refuse_wheels(paths):
        raise safety.ProbeRefused("discoverable wheelhouse: synthetic test location")

    monkeypatch.setattr(fixture.probe, "check_wheels", refuse_wheels)
    with pytest.raises(safety.ProbeRefused, match="discoverable wheelhouse"):
        fixture.manager.exec(identifier, fixture.zero_wheels)
    assert fixture.guard.violations == ["discoverable wheelhouse: synthetic test location"]
    assert not any(call[0] == "exec" for call in fixture.manager.calls)


def test_manager_wheel_refusal_precedes_original_start(fake_instrumented_manager, monkeypatch):
    fixture = fake_instrumented_manager

    def refuse_wheels(paths):
        raise safety.ProbeRefused("discoverable wheelhouse: synthetic startup refusal")

    monkeypatch.setattr(fixture.probe, "check_wheels", refuse_wheels)
    with pytest.raises(safety.ProbeRefused, match="discoverable wheelhouse"):
        fixture.manager.start()
    assert fixture.manager.calls == []
    assert not fixture.manager.sandboxes
    assert fixture.guard.violations == ["discoverable wheelhouse: synthetic startup refusal"]


def test_manager_refuses_staged_sandbox_wheels_before_command(fake_instrumented_manager):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()
    (fixture.manager.sandboxes[identifier]["wheels"] / "unapproved.whl").write_bytes(b"synthetic")
    with pytest.raises(safety.ProbeRefused, match="sandbox wheel staging detected"):
        fixture.manager.exec(identifier, fixture.zero_wheels)
    assert fixture.guard.violations
    assert not any(call[0] == "exec" for call in fixture.manager.calls)


def test_manager_nonzero_wheel_discovery_is_sticky_infrastructure_failure(fake_instrumented_manager):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()
    fixture.manager.wheel_stdout = "1\n"
    with pytest.raises(safety.ProbeRefused, match="wheel check did not confirm zero wheels"):
        fixture.manager.exec(identifier, fixture.zero_wheels)
    assert fixture.guard.violations == ["wheel check did not confirm zero wheels"]


def test_manager_allows_only_immutable_snapshot_and_exact_changed_app_copy(fake_instrumented_manager):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()
    app = fixture.guard.run_root / "app.py"
    app.write_text(fixture.probe.CHANGED, encoding="utf-8")
    fixture.manager.copy_to(identifier, fixture.snapshot, "/tmp")
    fixture.manager.copy_to(identifier, app, "/workspace/")
    assert [item["sha256"] for item in fixture.observation["copies"]] == [
        fixture.probe.digest(fixture.snapshot.read_bytes()), fixture.probe.digest(fixture.probe.CHANGED.encode()),
    ]
    assert [call[0] for call in fixture.manager.calls] == ["start", "copy", "copy"]


@pytest.mark.parametrize("fault", ["app_hash", "snapshot_hash", "destination", "outside", "unknown_sandbox"])
def test_manager_rejects_unsafe_copy_with_sticky_refusal(fake_instrumented_manager, tmp_path, fault):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()
    app = fixture.guard.run_root / "app.py"
    app.write_text(fixture.probe.CHANGED, encoding="utf-8")
    source, destination = app, "/workspace/"
    if fault == "app_hash":
        app.write_text("different bytes", encoding="utf-8")
    elif fault == "snapshot_hash":
        fixture.snapshot.write_bytes(b"changed after admission")
        source, destination = fixture.snapshot, "/tmp"
    elif fault == "destination":
        destination = "/wheels"
    elif fault == "outside":
        source = tmp_path / "app.py"
        source.write_text(fixture.probe.CHANGED, encoding="utf-8")
    else:
        identifier = "unknown"
    with pytest.raises(safety.ProbeRefused):
        fixture.manager.copy_to(identifier, source, destination)
    assert fixture.guard.violations
    assert fixture.observation["copies"] == []
    assert not any(call[0] == "copy" for call in fixture.manager.calls)


def test_manager_observes_workspace_and_diff_before_original_cleanup(fake_instrumented_manager):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()
    workspace = fixture.manager.sandboxes[identifier]["workspace"]
    (workspace / "app.py").write_text(fixture.probe.CHANGED, encoding="utf-8")
    before_cleanup = []
    fixture.manager.on_stop = lambda: before_cleanup.append(fixture.observation["workspace"])
    fixture.manager.stop(identifier)
    observed = fixture.observation["workspace"]
    assert before_cleanup == [observed]
    assert observed["edited"] is True
    assert observed["app_content"] == fixture.probe.CHANGED
    assert observed["app_sha256"] == fixture.probe.digest(fixture.probe.CHANGED.encode())
    assert observed["diff"] == fixture.probe.expected_patch()
    assert fixture.observation["observer_errors"] == []
    assert [call[0] for call in fixture.manager.calls] == ["start", "exec", "stop"]
    assert not workspace.exists()
    assert not fixture.manager.sandboxes


@pytest.mark.parametrize("fault", ["missing_app", "remote", "hook"])
def test_manager_always_cleans_up_after_observation_failure(fake_instrumented_manager, fault):
    fixture = fake_instrumented_manager
    identifier = fixture.manager.start()
    workspace = fixture.manager.sandboxes[identifier]["workspace"]
    if fault == "missing_app":
        (workspace / "app.py").unlink()
    elif fault == "remote":
        (workspace / ".git/config").write_text('[remote "unapproved"]\n', encoding="utf-8")
    else:
        (workspace / ".git/hooks/unapproved").write_text("synthetic", encoding="utf-8")
    fixture.manager.stop(identifier)
    assert fixture.observation["workspace"] is None
    assert fixture.observation["observer_errors"]
    assert fixture.manager.calls[-1] == ("stop", identifier, True)
    assert not workspace.exists()
    assert not fixture.manager.sandboxes
    if fault != "missing_app":
        assert fixture.guard.violations == ["remote or hooks appeared in synthetic workspace"]


@pytest.fixture
def strict_guard(tmp_path):
    repo, run, library = tmp_path / "repo", tmp_path / "run", tmp_path / "runtime"
    metadata = tmp_path / "discoverable_wheels"
    return safety.ParentGuard(
        repo, run, [repo / "harness_cert/results/H05"], [repo / "data"],
        read_roots=[library], metadata_dirs=[metadata],
    )


def test_strict_parent_guard_allows_only_libraries_synthetic_files_and_outputs(strict_guard):
    for path in (
        strict_guard.read_roots[0] / "module.py",
        strict_guard.run_root / "fixture/app.py",
        strict_guard.outputs[0] / "result.json",
        strict_guard.repo / "tools/harness_cert/run_h04_h05_h18.py",
    ):
        strict_guard.audit("open", (path, "r", 0))
    assert strict_guard.violations == []


def test_strict_parent_guard_refuses_external_file_reads(strict_guard, tmp_path):
    path = tmp_path / "unapproved_external_cache/model.bin"
    with pytest.raises(safety.ProbeRefused, match="read outside synthetic/library roots refused"):
        strict_guard.audit("open", (path, "rb", 0))
    assert strict_guard.violations


@pytest.mark.parametrize("raw", ["/dev/null", b"/dev/null", Path("/dev/null")])
@pytest.mark.parametrize("flags", [
    os.O_WRONLY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC,
    os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
])
def test_exact_null_write_and_append_sink_is_admitted(strict_guard, raw, flags):
    strict_guard.audit("open", (raw, None, flags))
    assert strict_guard.violations == []
    # A real sink write is harmless; neither the device nor its directory is a
    # read/write root. stat/lstat are ordinary, nonmutating metadata inspection.
    descriptor = os.open(raw, flags)
    try:
        assert os.write(descriptor, b"synthetic null sink regression\n") == 31
        assert stat.S_ISCHR(os.fstat(descriptor).st_mode)
    finally:
        os.close(descriptor)
    assert stat.S_ISCHR(os.stat("/dev/null").st_mode)
    assert Path("/dev") not in strict_guard.read_roots


@pytest.mark.parametrize("raw", [
    "/dev/null/anything", "/dev/null.evil", "/dev/zero", "/dev/random",
    "/dev/../dev/null", "/dev/./null", "//dev/null", "/dev//null",
    "/dev/null/", "/etc/unrelated-probe-write",
])
@pytest.mark.parametrize("flags", [os.O_WRONLY, os.O_WRONLY | os.O_APPEND])
def test_null_sink_does_not_admit_prefixes_alias_spellings_or_other_system_writes(strict_guard, raw, flags):
    with pytest.raises(safety.ProbeRefused):
        strict_guard.audit("open", (raw, None, flags))
    assert len(strict_guard.violations) == 1


@pytest.mark.parametrize("flags", [
    os.O_RDONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_APPEND,
    os.O_WRONLY | os.O_EXCL, os.O_RDWR | os.O_CREAT | os.O_TRUNC,
    os.O_WRONLY | os.O_NONBLOCK,
])
def test_null_reads_general_rdwr_and_nonsink_flags_are_refused(strict_guard, flags):
    with strict_guard.processes_allowed("shell"):
        with pytest.raises(safety.ProbeRefused, match="exact sink policy"):
            strict_guard.audit("open", ("/dev/null", None, flags))


@pytest.mark.parametrize("root_kind", ["run", "output", "outside"])
@pytest.mark.parametrize("flags", [os.O_RDONLY, os.O_WRONLY, os.O_RDWR])
def test_symlink_to_null_never_inherits_sink_open_permission(strict_guard, tmp_path, root_kind, flags):
    root = {"run": strict_guard.run_root, "output": strict_guard.outputs[0],
            "outside": tmp_path / "outside"}[root_kind]
    root.mkdir(parents=True)
    link = root / "null_alias"
    link.symlink_to("/dev/null")
    with pytest.raises(safety.ProbeRefused, match="exact sink policy"):
        strict_guard.audit("open", (link, None, flags))


@pytest.mark.parametrize("event", [
    "os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.chown", "os.utime",
    "os.rename", "os.link", "os.symlink", "os.truncate",
])
def test_null_sink_never_becomes_mutation_root(strict_guard, event):
    path, inside = "/dev/null", str(strict_guard.run_root / "synthetic")
    arguments = {
        "os.mkdir": (path, 0o700, -1), "os.remove": (path, -1), "os.rmdir": (path, -1),
        "os.chmod": (path, 0o600, -1), "os.chown": (path, 1000, 1000, -1),
        "os.utime": (path, None, None, -1), "os.rename": (inside, path, -1, -1),
        "os.link": (path, inside, -1, -1), "os.symlink": (inside, path, -1),
        "os.truncate": (path, 0),
    }
    with pytest.raises(safety.ProbeRefused, match="mutation outside probe outputs"):
        strict_guard.audit(event, arguments[event])
    if event in {"os.rename", "os.link"}:
        # os.replace emits os.rename too; check both ends of each operation.
        with pytest.raises(safety.ProbeRefused, match="mutation outside probe outputs"):
            strict_guard.audit(event, (path, inside, -1, -1))


@pytest.mark.parametrize("defect", ["regular", "symlink", "wrong_device", "nonroot_device",
                                       "writable_parent", "nonroot_parent", "metadata_error",
                                       "resolved_alias", "unsupported_platform"])
def test_null_device_validation_fails_closed(monkeypatch, strict_guard, defect):
    original_lstat, original_resolve = Path.lstat, Path.resolve

    def lstat(path):
        info = original_lstat(path)
        fields = {name: getattr(info, name) for name in ("st_mode", "st_uid", "st_rdev")}
        if path == safety.NULL_SINK:
            if defect == "metadata_error":
                raise PermissionError("synthetic null metadata refusal")
            if defect in {"regular", "symlink"}:
                fields["st_mode"] = (stat.S_IFREG if defect == "regular" else stat.S_IFLNK) | 0o666
            if defect == "wrong_device":
                fields["st_rdev"] = os.makedev(99, 99)
            if defect == "nonroot_device":
                fields["st_uid"] = 1000
        elif path == Path("/dev"):
            if defect == "writable_parent":
                fields["st_mode"] |= stat.S_IWOTH
            if defect == "nonroot_parent":
                fields["st_uid"] = 1000
        return SimpleNamespace(**fields)

    def resolve(path, **kwargs):
        if defect == "resolved_alias" and path == safety.NULL_SINK:
            return Path("/dev/zero")
        return original_resolve(path, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "resolve", resolve)
    if defect == "unsupported_platform":
        monkeypatch.setattr(safety, "sys", SimpleNamespace(platform="unsupported", _getframe=sys._getframe))
    with pytest.raises(safety.ProbeRefused, match="exact sink policy"):
        strict_guard.audit("open", ("/dev/null", None, os.O_WRONLY))


@pytest.fixture
def null_stdio_observer(monkeypatch, strict_guard):
    """Exercise real Popen setup without installing a hook or launching a child."""
    original_open = os.open
    observation = SimpleNamespace(opens=[], launches=[], caller=None)

    def audited_open(path, flags, *args, **kwargs):
        if os.fsdecode(path) == "/dev/null":
            observation.caller = sys._getframe(1)
            stack, frame = [], observation.caller
            while frame is not None:
                stack.append((frame.f_code.co_filename, frame.f_code.co_name))
                frame = frame.f_back
            observation.opens.append((path, flags, stack))
            # Supply the frame the C-level audit event would see, excluding
            # this test adapter. The actual admission runs through guard.audit.
            strict_guard.audit("open", (path, None, flags | os.O_CLOEXEC))
        return original_open(path, flags, *args, **kwargs)

    class StopBeforeLaunch(Exception):
        pass

    def stop_before_launch(owner, argv, executable, preexec_fn, close_fds, pass_fds, cwd, env, *args):
        observation.launches.append(argv)
        strict_guard.audit("subprocess.Popen", (executable, argv, cwd, env))
        raise StopBeforeLaunch

    monkeypatch.setattr(safety, "sys", SimpleNamespace(
        platform=sys.platform, _getframe=lambda depth: observation.caller))
    monkeypatch.setattr(os, "open", audited_open)
    monkeypatch.setattr(subprocess.Popen, "_execute_child", stop_before_launch)
    observation.stop = StopBeforeLaunch
    observation.guard = strict_guard
    return observation


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_scoped_real_popen_null_sink_setup_is_admitted(null_stdio_observer, stream):
    fixture = null_stdio_observer
    with fixture.guard.processes_allowed("shell"):
        with pytest.raises(fixture.stop):
            subprocess.Popen(["/bin/bash", "-c", "true"], **{stream: subprocess.DEVNULL})
    assert fixture.opens[0][:2] == ("/dev/null", os.O_RDWR)
    assert fixture.guard.violations == []
    assert fixture.guard.processes[0]["kind"] == "shell"


@pytest.mark.parametrize("defect", ["no_scope", "stdin", "direct_get_devnull", "unapproved_argv"])
def test_rdwr_null_exception_requires_sink_caller_and_independent_process_admission(null_stdio_observer, defect):
    fixture = null_stdio_observer
    with fixture.guard.processes_allowed("shell" if defect != "no_scope" else "unapproved"):
        with pytest.raises(safety.ProbeRefused):
            if defect == "direct_get_devnull":
                subprocess.Popen.__new__(subprocess.Popen)._get_devnull()
            else:
                subprocess.Popen(
                    ["/usr/bin/uname", "-p"] if defect == "unapproved_argv" else ["/bin/bash", "-c", "true"],
                    stdin=subprocess.DEVNULL if defect == "stdin" else None,
                    stderr=subprocess.DEVNULL,
                )
    assert len(fixture.guard.violations) == 1
    assert fixture.guard.processes == []


def test_platform_processor_devnull_origin_without_launching_uname(null_stdio_observer):
    fixture = null_stdio_observer
    with pytest.raises(safety.ProbeRefused, match="exact sink policy"):
        platform._Processor.from_subprocess()
    path, flags, stack = fixture.opens[0]
    assert (path, flags) == ("/dev/null", os.O_RDWR)
    assert [function for _, function in stack[:6]] == [
        "_get_devnull", "_get_handles", "__init__", "run", "check_output", "from_subprocess",
    ]
    assert fixture.launches == []


def test_null_exception_preserves_output_boundaries(strict_guard, tmp_path):
    for root in (strict_guard.run_root, strict_guard.outputs[0]):
        strict_guard.audit("open", (root / "result.json", "w", os.O_WRONLY | os.O_CREAT))
    for path in (strict_guard.outputs[0].with_name("H05_extra") / "result.json",
                 strict_guard.repo / "README.md", tmp_path / "unrelated.txt"):
        with pytest.raises(safety.ProbeRefused, match="write outside probe outputs"):
            strict_guard.audit("open", (path, "w", os.O_WRONLY | os.O_CREAT))


@pytest.mark.parametrize("event", ["os.listdir", "os.scandir"])
def test_strict_guard_metadata_exception_permits_exact_directory_listing_only(strict_guard, event):
    directory = strict_guard.metadata_dirs[0]
    strict_guard.audit(event, (directory,))
    with pytest.raises(safety.ProbeRefused, match="directory read outside synthetic/library roots refused"):
        strict_guard.audit(event, (directory / "nested",))
    with pytest.raises(safety.ProbeRefused, match="read outside synthetic/library roots refused"):
        strict_guard.audit("open", (directory / "candidate.whl", "rb", 0))


def test_strict_parent_guard_refuses_read_root_symlink_escape(strict_guard, tmp_path):
    library = strict_guard.read_roots[0]
    library.mkdir()
    (library / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(safety.ProbeRefused, match="read outside synthetic/library roots refused"):
        strict_guard.audit("open", (library / "escape/module.py", "rb", 0))


@pytest.mark.parametrize("site_already_listed", [False, True])
def test_import_isolation_removes_cwd_env_and_symlink_shadows(monkeypatch, tmp_path, site_already_listed):
    from tools.harness_cert import run_h04_h05_h18 as probe

    prefix, base = tmp_path / "venv", tmp_path / "base_python"
    site = prefix / "lib/python3.12/site-packages"
    stdlib = base / "lib/python3.12"
    zip_library = base / "lib/python312.zip"
    shadow = tmp_path / "unapproved_shadow"
    prefix.mkdir()
    shadow.mkdir()
    (prefix / "shadow_link").symlink_to(shadow, target_is_directory=True)
    entries = [
        "", ".", str(shadow), str(prefix / "shadow_link"),
        str(tmp_path / "venv_other/lib"), str(stdlib), str(zip_library),
    ]
    if site_already_listed:
        entries.append(str(site))
    original_path = sys.path
    original_prefix, original_base = sys.prefix, sys.base_prefix
    with monkeypatch.context() as changes:
        changes.chdir(shadow)
        changes.setattr(sys, "path", list(entries))
        changes.setattr(sys, "prefix", str(prefix))
        changes.setattr(sys, "base_prefix", str(base))
        probe.isolate_import_paths(site)
        assert sys.path == [str(stdlib), str(zip_library), str(site)]
        assert sys.path.count(str(site)) == 1
    assert sys.path is original_path
    assert (sys.prefix, sys.base_prefix) == (original_prefix, original_base)


@pytest.fixture
def timezone_guard(tmp_path):
    zone = tmp_path / "frozen_timezone/2026c.1.0/zoneinfo"
    (zone / "America").mkdir(parents=True)
    for name in ("UTC", "America/New_York"):
        (zone / name).write_bytes(b"TZif synthetic unit fixture")
    policy = safety.SystemRuntimeReads(zoneinfo_root=zone.resolve())
    guard = safety.ParentGuard(
        tmp_path / "repo", tmp_path / "run", [tmp_path / "repo/harness_cert/results/H05"],
        [tmp_path / "repo/data"], read_roots=[tmp_path / "trusted_library"],
        system_runtime_reads=policy,
    )
    return SimpleNamespace(root=zone, policy=policy, guard=guard)


@pytest.mark.parametrize("name", ["UTC", "America/New_York"])
def test_timezone_policy_allows_regular_files_in_frozen_tree(timezone_guard, name):
    fixture = timezone_guard
    path = fixture.root / name
    assert fixture.policy.permits_read(path)
    fixture.guard.audit("open", (path, "rb", os.O_RDONLY))
    assert fixture.guard.violations == []


@pytest.mark.parametrize("path_kind", ["directory", "missing", "prefix", "version", "traversal", "symlink"])
def test_timezone_policy_refuses_nonfiles_and_resolved_escapes(timezone_guard, tmp_path, path_kind):
    fixture = timezone_guard
    if path_kind == "directory":
        path = fixture.root / "America"
    elif path_kind == "missing":
        path = fixture.root / "Missing"
    elif path_kind == "prefix":
        path = fixture.root.with_name("zoneinfo_other") / "UTC"
    elif path_kind == "version":
        path = fixture.root.parent.with_name("2026d.1.0") / "zoneinfo/UTC"
    elif path_kind == "traversal":
        path = fixture.root / "../outside_utc"
    else:
        outside = tmp_path / "external_utc"
        outside.write_bytes(b"synthetic external timezone-like file")
        path = fixture.root / "escaped_alias"
        path.symlink_to(outside)
    if path_kind in {"prefix", "version", "traversal"}:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif outside the frozen tree")
    assert not fixture.policy.permits_read(path)
    with pytest.raises(safety.ProbeRefused, match="read outside synthetic/library roots refused"):
        fixture.guard.audit("open", (path, "rb", os.O_RDONLY))


def test_timezone_aliases_resolve_inside_tree_without_extra_read_roots(timezone_guard, tmp_path):
    fixture = timezone_guard
    share_alias = tmp_path / "usr/share/zoneinfo"
    share_alias.parent.mkdir(parents=True)
    share_alias.symlink_to(fixture.root, target_is_directory=True)
    localtime = tmp_path / "etc/localtime"
    localtime.parent.mkdir(parents=True)
    localtime.symlink_to(fixture.root / "America/New_York")
    for path in (share_alias / "UTC", localtime):
        assert fixture.policy.permits_read(path)
        fixture.guard.audit("open", (path, "rb", os.O_RDONLY))
    assert fixture.guard.read_roots == [(tmp_path / "trusted_library").resolve()]


def test_timezone_localtime_alias_outside_tree_is_not_exempt(timezone_guard, tmp_path):
    outside = tmp_path / "external_localtime"
    outside.write_bytes(b"TZif synthetic outside target")
    localtime = tmp_path / "etc/localtime"
    localtime.parent.mkdir()
    localtime.symlink_to(outside)
    assert not timezone_guard.policy.permits_read(localtime)
    with pytest.raises(safety.ProbeRefused, match="read outside synthetic/library roots refused"):
        timezone_guard.guard.audit("open", (localtime, "rb", os.O_RDONLY))


def test_timezone_policy_freezes_root_when_alias_is_repointed(timezone_guard, tmp_path):
    fixture = timezone_guard
    alias = tmp_path / "zoneinfo_alias"
    alias.symlink_to(fixture.root, target_is_directory=True)
    assert fixture.policy.permits_read(alias / "UTC")
    new_root = tmp_path / "new_version/zoneinfo"
    new_root.mkdir(parents=True)
    (new_root / "UTC").write_bytes(b"TZif different version")
    alias.unlink()
    alias.symlink_to(new_root, target_is_directory=True)
    assert fixture.policy.zoneinfo_root == fixture.root.resolve()
    assert fixture.policy.permits_read(fixture.root / "UTC")
    assert not fixture.policy.permits_read(alias / "UTC")
    assert not fixture.policy.permits_read(new_root / "UTC")
    with pytest.raises(FrozenInstanceError):
        fixture.policy.zoneinfo_root = new_root


@pytest.mark.parametrize("flags", [
    os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_APPEND, os.O_RDWR | os.O_CREAT,
])
def test_system_timezone_read_exception_never_allows_write_or_create_flags(timezone_guard, flags):
    with pytest.raises(safety.ProbeRefused, match="parent write outside probe outputs refused"):
        timezone_guard.guard.audit("open", (timezone_guard.root / "UTC", "w", flags))


@pytest.mark.parametrize("event", [
    "os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.chown", "os.utime",
    "os.rename", "os.link", "os.symlink", "os.truncate",
])
def test_system_timezone_resource_never_becomes_mutation_root(timezone_guard, event):
    fixture = timezone_guard
    path = str(fixture.root / "UTC")
    inside = str(fixture.guard.run_root / "synthetic")
    arguments = {
        "os.mkdir": (path, 0o700, -1), "os.remove": (path, -1), "os.rmdir": (path, -1),
        "os.chmod": (path, 0o600, -1), "os.chown": (path, 1000, 1000, -1),
        "os.utime": (path, None, None, -1), "os.rename": (inside, path, -1, -1),
        "os.link": (path, inside, -1, -1), "os.symlink": (inside, path, -1),
        "os.truncate": (path, 0),
    }
    with pytest.raises(safety.ProbeRefused, match="parent mutation outside probe outputs refused"):
        fixture.guard.audit(event, arguments[event])


@pytest.mark.parametrize("event", ["open", "os.chmod", "os.chown", "os.truncate"])
def test_timezone_entry_cannot_be_mutated_even_when_symlink_target_is_an_output(timezone_guard, event):
    fixture = timezone_guard
    target = fixture.guard.outputs[0] / "synthetic.txt"
    target.parent.mkdir(parents=True)
    target.write_text("synthetic", encoding="utf-8")
    link = fixture.root / "linked_output"
    link.symlink_to(target)
    arguments = {
        "open": (str(link), "w", os.O_WRONLY | os.O_TRUNC),
        "os.chmod": (str(link), 0o600, -1),
        "os.chown": (str(link), 1000, 1000, -1),
        "os.truncate": (str(link), 0),
    }
    with pytest.raises(safety.ProbeRefused):
        fixture.guard.audit(event, arguments[event])
    assert target.read_text(encoding="utf-8") == "synthetic"


@pytest.mark.parametrize("event", ["os.listdir", "os.scandir"])
def test_system_timezone_read_exception_does_not_allow_directory_listing(timezone_guard, event):
    with pytest.raises(safety.ProbeRefused, match="directory read outside synthetic/library roots refused"):
        timezone_guard.guard.audit(event, (timezone_guard.root,))


@pytest.mark.parametrize("path", [
    "/private/var/db/unrelated/state.bin", "/etc/passwd",
    "/Users/probe/.ssh/config", "/System/Library/CoreServices/SystemVersion.plist",
])
def test_timezone_policy_does_not_exempt_other_system_or_home_files(timezone_guard, path):
    assert not timezone_guard.policy.permits_read(Path(path))
    with pytest.raises(safety.ProbeRefused, match="read outside synthetic/library roots refused"):
        timezone_guard.guard.audit("open", (path, "rb", os.O_RDONLY))


@pytest.mark.parametrize("precedence", ["protected", "repository"])
def test_protected_and_repository_read_restrictions_precede_timezone_exception(tmp_path, precedence):
    repo = tmp_path / "repo"
    zone = repo / "forbidden_timezone" if precedence == "repository" else tmp_path / "protected_timezone"
    zone.mkdir(parents=True)
    utc = zone / "UTC"
    utc.write_bytes(b"TZif synthetic")
    policy = safety.SystemRuntimeReads(zoneinfo_root=zone.resolve())
    guard = safety.ParentGuard(
        repo, tmp_path / "run", [], [zone] if precedence == "protected" else [],
        read_roots=[], system_runtime_reads=policy,
    )
    assert policy.permits_read(utc)
    expected = "dataset/competition read refused" if precedence == "protected" else "non-probe repository read refused"
    with pytest.raises(safety.ProbeRefused, match=expected):
        guard.audit("open", (utc, "rb", os.O_RDONLY))


def _mock_zoneinfo_discovery(
    monkeypatch, *, platform="darwin", resolved=None, overrides=None, root_is_dir=True,
    resolve_error=None,
):
    """Simulate system metadata selectively; never create or read actual OS trees."""
    alias = Path("/usr/share/zoneinfo")
    root = resolved or Path("/private/var/db/timezone/tz/2026c.1.0/zoneinfo")
    overrides = overrides or {}
    entries = {alias, *alias.parents, root, *root.parents}
    original_resolve, original_is_dir, original_lstat = Path.resolve, Path.is_dir, Path.lstat

    def resolve(path, *args, **kwargs):
        if path == alias:
            assert kwargs.get("strict") is True
            if resolve_error:
                raise resolve_error
            return root
        return original_resolve(path, *args, **kwargs)

    def is_dir(path):
        return root_is_dir if path == root else original_is_dir(path)

    def lstat(path):
        if path not in entries:
            return original_lstat(path)
        override = overrides.get(path)
        if isinstance(override, BaseException):
            raise override
        if override is not None:
            return override
        mode = stat.S_IFLNK | 0o777 if path == alias and root != alias else stat.S_IFDIR | 0o755
        return SimpleNamespace(st_uid=0, st_mode=mode)

    monkeypatch.setattr(safety.sys, "platform", platform)
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "is_dir", is_dir)
    monkeypatch.setattr(Path, "lstat", lstat)
    return alias, root


def test_timezone_discovery_captures_exact_observed_macos_tree_using_metadata(monkeypatch, tmp_path):
    _, root = _mock_zoneinfo_discovery(monkeypatch)
    policy = safety.discover_system_runtime_reads()
    assert policy.zoneinfo_root == Path("/private/var/db/timezone/tz/2026c.1.0/zoneinfo")
    original_is_file = Path.is_file
    files = {root / "UTC", root / "America/New_York"}
    monkeypatch.setattr(Path, "is_file", lambda path: path in files if path.is_relative_to(root) else original_is_file(path))
    assert policy.permits_read(root / "UTC")
    assert policy.permits_read(root / "America/New_York")
    guard = safety.ParentGuard(
        tmp_path / "repo", tmp_path / "run", [], [],
        read_roots=[], system_runtime_reads=policy,
    )
    for path in files:
        guard.audit("open", (path, "rb", os.O_RDONLY))
    assert guard.violations == []


def test_timezone_discovery_accepts_exact_physical_posix_tree(monkeypatch):
    alias, _ = _mock_zoneinfo_discovery(monkeypatch, platform="linux", resolved=Path("/usr/share/zoneinfo"))
    assert safety.discover_system_runtime_reads().zoneinfo_root == alias


@pytest.mark.parametrize("platform,resolved", [
    ("darwin", "/private/var/db/timezone/tz/2026c.1.0/zoneinfo/nested"),
    ("darwin", "/private/var/db/timezone/tz/2026c.1.0/nested/zoneinfo"),
    ("darwin", "/private/var/db/timezone/tz/2026c.1.0/not_zoneinfo"),
    ("darwin", "/private/var/db/timezone/zoneinfo"),
    ("darwin", "/private/var/db/timezone/tz/zoneinfo"),
    ("darwin", "/private/var/db/unrelated/2026c.1.0/zoneinfo"),
    ("linux", "/opt/user_timezone/zoneinfo"),
])
def test_timezone_discovery_refuses_unsupported_root_shapes(monkeypatch, platform, resolved):
    _mock_zoneinfo_discovery(monkeypatch, platform=platform, resolved=Path(resolved))
    with pytest.raises(safety.ProbeRefused, match="unsupported system timezone tree"):
        safety.discover_system_runtime_reads()


@pytest.mark.parametrize("entry,uid,mode", [
    ("/usr/share/zoneinfo", 1000, stat.S_IFLNK | 0o777),
    ("/usr/share", 0, stat.S_IFDIR | 0o775),
    ("/private/var/db/timezone/tz/2026c.1.0/zoneinfo", 1000, stat.S_IFDIR | 0o755),
    ("/private/var/db/timezone/tz/2026c.1.0", 0, stat.S_IFDIR | 0o757),
    ("/private/var/db/timezone", 1000, stat.S_IFDIR | 0o755),
])
def test_timezone_discovery_refuses_untrusted_alias_or_directory_ancestors(monkeypatch, entry, uid, mode):
    _mock_zoneinfo_discovery(monkeypatch, overrides={Path(entry): SimpleNamespace(st_uid=uid, st_mode=mode)})
    with pytest.raises(safety.ProbeRefused, match="untrusted system timezone path"):
        safety.discover_system_runtime_reads()


def test_timezone_discovery_refuses_nondirectory_target(monkeypatch):
    _mock_zoneinfo_discovery(monkeypatch, root_is_dir=False)
    with pytest.raises(safety.ProbeRefused, match="system timezone tree is not a directory"):
        safety.discover_system_runtime_reads()


def test_timezone_discovery_refuses_nonalias_file_metadata(monkeypatch):
    _mock_zoneinfo_discovery(monkeypatch, overrides={
        Path("/usr/share/zoneinfo"): SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644),
    })
    with pytest.raises(safety.ProbeRefused, match="system timezone path is not a directory/alias"):
        safety.discover_system_runtime_reads()


def test_timezone_discovery_missing_alias_leaves_no_resource_exception(monkeypatch, tmp_path):
    _mock_zoneinfo_discovery(monkeypatch, resolve_error=FileNotFoundError("synthetic missing alias"))
    policy = safety.discover_system_runtime_reads()
    assert policy.zoneinfo_root is None
    file = tmp_path / "UTC"
    file.write_bytes(b"TZif synthetic")
    assert not policy.permits_read(file)


def test_timezone_discovery_inspection_failure_refuses_launch(monkeypatch):
    _mock_zoneinfo_discovery(monkeypatch, overrides={
        Path("/usr/share"): PermissionError("synthetic metadata denial"),
    })
    with pytest.raises(safety.ProbeRefused, match="cannot inspect system timezone tree: synthetic metadata denial"):
        safety.discover_system_runtime_reads()
