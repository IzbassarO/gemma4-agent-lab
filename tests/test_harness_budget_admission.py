"""Tranche admission checks without harness imports or certification runs."""

import hashlib

import pytest

from tools.harness_cert import _probe_safety as safety


def fake_git(monkeypatch, repo, *, head=safety.BUDGET_BASELINE, status="", root=None):
    responses = {
        ("rev-parse", "--show-toplevel"): str(root or repo) + "\n",
        ("rev-parse", "HEAD"): head + "\n",
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): status,
    }
    calls = []

    def run(candidate, *args):
        assert candidate == repo
        calls.append(args)
        return responses[args]

    monkeypatch.setattr(safety, "git", run)
    return calls


def test_tranches_keep_distinct_static_reviewed_baselines():
    assert safety.BASELINE == "ea5b857487ae9e146e94a88e108962742fcbdef8"
    assert safety.BUDGET_BASELINE == "12c319fa8bcf5b313175f80cee3ce06a77a59619"
    assert safety.BUDGET_PROBE_FILES == frozenset({
        "tools/harness_cert/_probe_safety.py",
        "tools/harness_cert/_scripted_loopback.py",
        "tools/harness_cert/run_h13_h14_h29.py",
        "tools/harness_cert/_synthetic_verification.py",
        "tools/harness_cert/README.md",
        "tools/harness_cert/H13_H14_H29_DESIGN.md",
        "tests/test_harness_budget_probe.py",
        "tests/test_harness_budget_admission.py",
        "tests/test_synthetic_verification.py",
        "tests/test_harness_loopback.py",
    })
    assert "tools/harness_cert/run_h04_h05_h18.py" in safety.PROBE_FILES
    assert "tools/harness_cert/run_h04_h05_h18.py" not in safety.BUDGET_PROBE_FILES


def test_budget_profile_admits_clean_exact_new_baseline(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = fake_git(monkeypatch, repo)
    assert safety.check_repository(repo, profile="budget") == []
    assert calls == [
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    ]


@pytest.mark.parametrize("head", [safety.BASELINE, "0" * 40, safety.BUDGET_BASELINE + "evil"])
def test_budget_profile_refuses_any_other_baseline_before_status(monkeypatch, tmp_path, head):
    repo = tmp_path / "repo"
    calls = fake_git(monkeypatch, repo, head=head)
    with pytest.raises(safety.ProbeRefused, match=f"reviewed baseline {safety.BUDGET_BASELINE}"):
        safety.check_repository(repo, profile="budget")
    assert calls == [("rev-parse", "--show-toplevel"), ("rev-parse", "HEAD")]


@pytest.mark.parametrize("explicit_profile", [False, True])
def test_dispatch_profile_still_refuses_new_baseline(monkeypatch, tmp_path, explicit_profile):
    repo = tmp_path / "repo"
    fake_git(monkeypatch, repo)
    options = {"profile": "dispatch"} if explicit_profile else {}
    with pytest.raises(safety.ProbeRefused, match=f"reviewed baseline {safety.BASELINE}"):
        safety.check_repository(repo, **options)


@pytest.mark.parametrize("profile", ["", "other", "BUDGET", safety.BUDGET_BASELINE, None])
def test_unknown_profile_refused_before_any_git_call(monkeypatch, tmp_path, profile):
    def unexpected_git(*args):
        pytest.fail("Unknown profiles must not inspect repository state")

    monkeypatch.setattr(safety, "git", unexpected_git)
    with pytest.raises(safety.ProbeRefused, match="unknown repository admission profile"):
        safety.check_repository(tmp_path, profile=profile)


def test_repository_admission_has_no_caller_supplied_baseline(monkeypatch, tmp_path):
    def unexpected_git(*args):
        pytest.fail("An unsupported baseline argument must not inspect Git")

    monkeypatch.setattr(safety, "git", unexpected_git)
    with pytest.raises(TypeError, match="baseline"):
        safety.check_repository(tmp_path, profile="budget", baseline="0" * 40)


@pytest.mark.parametrize("status", ["??", " M", "M ", "MM", "A ", "AM"])
def test_budget_admits_exact_registered_change_surface(monkeypatch, tmp_path, status):
    repo = tmp_path / "repo"
    paths = sorted(safety.BUDGET_PROBE_FILES)
    fake_git(monkeypatch, repo, status="".join(f"{status} {path}\0" for path in paths))
    assert safety.check_repository(repo, profile="budget") == paths


@pytest.mark.parametrize("path", [
    "tools/harness_cert/run_h04_h05_h18.py",
    "tools/harness_cert/__init__.py",
    "tools/harness_cert/run_h13_h14_h29.py.evil",
    "tools/harness_cert/unrelated.py",
    "tests/test_harness_probe.py",
    "tests/unrelated.py",
    "agents/baseline/agent.yaml",
    "agents/e0/agent.yaml",
    "harness_cert/matrix.yaml",
    "harness_cert/reports/H13_H14_H29_LOCAL_CERTIFICATION_2026-10-04.md",
    "harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md",
    "README.md",
])
def test_budget_refuses_other_tranche_production_matrix_and_reports(monkeypatch, tmp_path, path):
    repo = tmp_path / "repo"
    fake_git(monkeypatch, repo, status=f" M {path}\0")
    with pytest.raises(safety.ProbeRefused, match="repository is dirty outside the probe"):
        safety.check_repository(repo, profile="budget")


@pytest.mark.parametrize("record", [
    "R  tools/harness_cert/run_h13_h14_h29.py\0tools/old_probe.py\0",
    " R tools/harness_cert/run_h13_h14_h29.py\0tools/old_probe.py\0",
    "D  tools/harness_cert/run_h13_h14_h29.py\0",
    " D tools/harness_cert/run_h13_h14_h29.py\0",
    "AD tools/harness_cert/run_h13_h14_h29.py\0",
    "UU tools/harness_cert/run_h13_h14_h29.py\0",
    "X\0",
])
def test_budget_refuses_renames_deletions_conflicts_and_malformed_git_records(monkeypatch, tmp_path, record):
    repo = tmp_path / "repo"
    fake_git(monkeypatch, repo, status=record)
    with pytest.raises(safety.ProbeRefused, match="unreviewed Git change"):
        safety.check_repository(repo, profile="budget")


def test_budget_requires_actual_repository_root(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    fake_git(monkeypatch, repo, root=tmp_path)
    with pytest.raises(safety.ProbeRefused, match="not the repository root"):
        safety.check_repository(repo, profile="budget")


def test_reviewed_support_document_exception_is_dispatch_only(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    name = next(iter(safety.REVIEWED_SUPPORT_DOCUMENTS))
    document = repo / name
    document.parent.mkdir(parents=True)
    document.write_text("Reviewed synthetic correction\n", encoding="utf-8")
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    monkeypatch.setattr(safety, "REVIEWED_SUPPORT_DOCUMENTS", {name: digest})
    fake_git(monkeypatch, repo, head=safety.BASELINE, status=f" M {name}\0")
    assert safety.check_repository(repo) == [name]
    fake_git(monkeypatch, repo, status=f" M {name}\0")
    with pytest.raises(safety.ProbeRefused, match="repository is dirty outside the probe"):
        safety.check_repository(repo, profile="budget")
