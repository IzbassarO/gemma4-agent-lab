"""Mandatory real phase fingerprints and evidence hygiene, without networking."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval._contracts import ContractError
from eval import runtime_provenance as provenance
from tools.common import tree_sha256


def fingerprint(phase="solver"):
    return {
        "schema_version": 1, "phase": phase,
        "eval_infra_source_identity": {"git_head": "a" * 40, "source_dirty": False,
                                       "runtime_source_sha256": "b" * 64},
        "candidate_identity": {"sha256": "c" * 64, "size_bytes": 443572},
        "preregistration_identity": {"sha256": "d" * 64, "size_bytes": 1200},
        "model_endpoint_identity": "http://127.0.0.1:8000/v1",
        "platform": "Linux", "python_version": "3.12.9",
        "package_versions": {package: None for package in provenance.PACKAGES},
        "public_identities": {"tasks_sha256": "e" * 64},
        "model_server_identity": {"served_model": "gemma-4-31b-it-qat-w4a16-ct"},
        "harness_lock_sha256": "f" * 64,
        "confinement": {"backend": "runuser", "limitations": ["filesystem permissions only"]},
    }


@pytest.mark.parametrize("endpoint, expected", [
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000/v1"),
    ("http://127.0.0.1:8000/v1/", "http://127.0.0.1:8000/v1"),
    ("http://localhost/v1", "http://localhost:80/v1"),
    ("https://[::1]/", "https://[::1]:443/v1"),
])
def test_endpoint_identity_is_normalized_without_secret_fields(endpoint, expected):
    assert provenance.normalize_endpoint(endpoint) == expected


@pytest.mark.parametrize("endpoint", [
    "http://remote.example:8000/v1", "http://127.0.0.1.evil:8000/v1",
    "http://user:PASSWORD_SENTINEL@127.0.0.1:8000/v1",
    "http://127.0.0.1:8000/v1?api_key=SECRET_SENTINEL", "http://127.0.0.1:8000/v1#secret",
    "http://127.0.0.1:99999/v1", "file:///tmp/model", "http://127.0.0.1:8000/other",
    "http://127.0.0.1:8000/v1\n", "http://127.0.0.1\\@remote/v1",
])
def test_endpoint_admission_refuses_credentials_nonloopback_and_ambiguous_urls(endpoint):
    with pytest.raises(ContractError) as caught:
        provenance.normalize_endpoint(endpoint)
    assert "SENTINEL" not in str(caught.value)


@pytest.mark.parametrize("phase", ["solver", "verifier"])
def test_each_phase_fingerprint_is_mandatory(phase):
    value = fingerprint(phase)
    assert provenance.validate_real_fingerprint(value, phase=phase) == value
    for missing in (None, {}, {key: child for key, child in value.items() if key != "candidate_identity"}):
        with pytest.raises(ContractError):
            provenance.validate_real_fingerprint(missing, phase=phase)


@pytest.mark.parametrize("identity", provenance.IDENTITY_FIELDS)
def test_crossphase_or_coordinator_identity_mismatch_refuses_acceptance(identity):
    solver, verifier = fingerprint(), fingerprint("verifier")
    provenance.crosscheck_real_fingerprints(solver, verifier, fingerprint("driver"))
    mismatch = copy.deepcopy(verifier)
    if identity == "model_endpoint_identity":
        mismatch[identity] = "http://127.0.0.1:8001/v1"
    elif identity == "eval_infra_source_identity":
        mismatch[identity]["source_dirty"] = True
    else:
        mismatch[identity]["sha256"] = "1" * 64
    with pytest.raises(ContractError, match="mismatch"):
        provenance.crosscheck_real_fingerprints(solver, mismatch)
    with pytest.raises(ContractError, match="mismatch"):
        provenance.crosscheck_real_fingerprints(solver, verifier, mismatch)


@pytest.mark.parametrize("phase", [None, "driver", "solver"])
def test_missing_or_wrong_verifier_phase_is_not_accepted(phase):
    verifier = None if phase is None else fingerprint(phase)
    with pytest.raises(ContractError):
        provenance.crosscheck_real_fingerprints(fingerprint(), verifier)


@pytest.mark.parametrize("change", [
    {"extra": "unknown"}, {"schema_version": True}, {"harness_lock_sha256": "bad"},
    {"model_endpoint_identity": "http://127.0.0.1:8000"}, {"model_server_identity": None},
    {"package_versions": {"swegemma": "0.2.7"}},
])
def test_fingerprint_is_closed_and_validated(change):
    with pytest.raises(ContractError):
        provenance.validate_real_fingerprint({**fingerprint(), **change})


def test_worker_fingerprint_has_no_git_data_or_environment_discovery(monkeypatch):
    core = fingerprint()
    request = {"provenance": {key: core[key] for key in (*provenance.IDENTITY_FIELDS,
                                                        "public_identities", "harness_lock_sha256", "confinement")}}

    def forbidden(*args, **kwargs):
        raise AssertionError("worker attempted privileged discovery")

    monkeypatch.setattr(provenance, "runtime_source_identity", forbidden)
    monkeypatch.setattr(provenance.subprocess, "run", forbidden)
    monkeypatch.setenv("UNCLASSIFIED_PARENT_ENV", "ENV_SECRET_SENTINEL")
    observed = provenance.real_fingerprint_from_request(request, phase="solver",
        package_versions=core["package_versions"], model_server_identity=core["model_server_identity"])
    assert all(observed[key] == core[key] for key in provenance.IDENTITY_FIELDS)
    assert "ENV_SECRET_SENTINEL" not in json.dumps(observed)
    with pytest.raises(ContractError):
        provenance.real_fingerprint_from_request({"provenance": {}}, phase="solver")


def test_runtime_source_digest_records_dirty_state_and_new_untracked_python(tmp_path, monkeypatch):
    (tmp_path / "eval").mkdir()
    source = tmp_path / "eval/runtime.py"
    source.write_text("FIRST\n")
    dirty = False

    def fake_git(argv, **kwargs):
        assert argv[:3] == ["git", "-C", str(tmp_path)]
        assert kwargs["close_fds"] is True and kwargs["capture_output"] is True
        if argv[3:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="a" * 40 + "\n")
        assert argv[3:] == ["status", "--porcelain", "--untracked-files=all"]
        return SimpleNamespace(stdout="?? eval/runtime_new.py\n" if dirty else "")

    monkeypatch.setattr(provenance.subprocess, "run", fake_git)
    clean = provenance.runtime_source_identity(tmp_path)
    records = provenance.runtime_source_records(tmp_path)
    assert clean["runtime_source_sha256"] == tree_sha256({key: value["sha256"] for key, value in records.items()})
    assert clean["source_dirty"] is False
    dirty = True
    source.write_text("SECOND\n")
    modified = provenance.runtime_source_identity(tmp_path)
    assert modified["source_dirty"] is True
    assert modified["runtime_source_sha256"] != clean["runtime_source_sha256"]
    (tmp_path / "eval/runtime_new.py").write_text("UNTRACKED\n")
    new = provenance.runtime_source_identity(tmp_path)
    assert "eval/runtime_new.py" in provenance.runtime_source_records(tmp_path)
    assert new["runtime_source_sha256"] != modified["runtime_source_sha256"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "traversal", "unavailable_git"])
def test_source_identity_fails_closed_on_unsafe_or_unavailable_sources(tmp_path, monkeypatch, kind):
    source = tmp_path / "source.py"
    source.write_text("SOURCE")
    if kind == "symlink":
        alias = tmp_path / "alias.py"
        alias.symlink_to(source)
        paths = ["alias.py"]
    elif kind == "hardlink":
        import os
        os.link(source, tmp_path / "alias.py")
        paths = ["source.py"]
    else:
        paths = ["../source.py"] if kind == "traversal" else ["source.py"]
    if kind == "unavailable_git":
        def unavailable(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args[0])
        monkeypatch.setattr(provenance.subprocess, "run", unavailable)
    with pytest.raises(ContractError):
        provenance.runtime_source_identity(tmp_path, paths)


def test_evidence_sanitizer_strips_auth_keys_values_urls_and_entire_environment(monkeypatch):
    value = {"api_key": "KEY_SENTINEL", "Authorization": "Bearer BEARER_SENTINEL",
             "nested": [{"access_token": "TOKEN_SENTINEL"}],
             "log": "password=PASSWORD_SENTINEL Bearer LOG_SENTINEL",
             "url": "http://user:URL_SENTINEL@127.0.0.1:8000/v1?token=QUERY_SENTINEL",
             "environment": {"UNCLASSIFIED": "ENV_SENTINEL", "PATH": "/usr/bin"},
             "safe": "http://127.0.0.1:8000/v1"}
    monkeypatch.setenv("NEVER_READ_PARENT", "PARENT_SENTINEL")
    cleaned = provenance.sanitize_evidence(value)
    serialized = json.dumps(cleaned)
    for sentinel in ("KEY_SENTINEL", "BEARER_SENTINEL", "TOKEN_SENTINEL", "PASSWORD_SENTINEL",
                     "LOG_SENTINEL", "URL_SENTINEL", "QUERY_SENTINEL", "ENV_SENTINEL", "PARENT_SENTINEL"):
        assert sentinel not in serialized
    assert cleaned["url"] == "http://127.0.0.1:8000/v1"
    assert cleaned["safe"] == value["safe"]
    provenance.assert_secret_free(cleaned)
    with pytest.raises(ContractError) as caught:
        provenance.assert_secret_free(value)
    assert "SENTINEL" not in str(caught.value)


def test_patch_hygiene_refuses_secret_without_modifying_authoritative_bytes():
    patch = "+API_KEY=SECRET_PATCH_SENTINEL\n"
    with pytest.raises(ContractError):
        provenance.assert_secret_free(patch)
    assert patch == "+API_KEY=SECRET_PATCH_SENTINEL\n"


@pytest.mark.parametrize("url", [
    "https://github.com/example/project#usage",
    "https://github.com/example/project?tab=readme-ov-file&view=source",
    "https://github.com/example/project?monkey=sample&token_count=2#signature_format",
    "https://github.com/example/project?%74opic=sample#section=examples&language=python",
    "https://github.com/example/project#token",
    "https://github.com/example/project#/examples?",
])
def test_benign_url_query_and_fragment_are_preserved(url):
    assert provenance.sanitize_evidence(url) == url
    provenance.assert_secret_free(url)


@pytest.mark.parametrize("location", ["query", "fragment", "fragment_query"])
@pytest.mark.parametrize("key", ["api_key", "apikey", "key", "token", "access_token", "password",
                                  "passwd", "secret", "authorization", "auth", "signature", "sig",
                                  "%74%6f%6b%65%6e", "API_KEY"])
def test_credential_url_fields_are_redacted_and_refused(location, key):
    suffix = {"query": "?view=source&", "fragment": "#section=examples&",
              "fragment_query": "#/examples?view=source&"}[location]
    url = "https://github.com/example/project" + suffix + key + "=URL_FIELD_SENTINEL"
    cleaned = provenance.sanitize_evidence(url)
    assert "URL_FIELD_SENTINEL" not in cleaned
    assert "view=source" in cleaned or "section=examples" in cleaned
    provenance.assert_secret_free(cleaned)
    with pytest.raises(ContractError, match="authentication material") as caught:
        provenance.assert_secret_free(url)
    assert "URL_FIELD_SENTINEL" not in str(caught.value)


def test_credential_url_userinfo_is_redacted_and_refused():
    url = "https://reader:URL_USERINFO_SENTINEL@github.com/example/project?view=source#usage"
    cleaned = provenance.sanitize_evidence(url)
    assert cleaned == "https://github.com/example/project?view=source#usage"
    with pytest.raises(ContractError, match="authentication material"):
        provenance.assert_secret_free(url)


@pytest.mark.parametrize("fragment", ["token=URL_FIELD_SENTINEL?view=usage",
                                     "route?token=URL_FIELD_SENTINEL?view=usage",
                                     "api_key=URL_FIELD_SENTINEL?view=usage",
                                     "route?api_key=URL_FIELD_SENTINEL?view=usage"])
def test_credential_fragment_fields_with_question_mark_values_are_redacted_and_refused(fragment):
    url = "https://github.com/example/project#" + fragment
    cleaned = provenance.sanitize_evidence(url)
    assert "URL_FIELD_SENTINEL" not in cleaned
    provenance.assert_secret_free(cleaned)
    with pytest.raises(ContractError, match="authentication material"):
        provenance.assert_secret_free(url)


def test_frozen_style_problem_with_benign_url_passes_real_request_validation(tmp_path, monkeypatch):
    from eval.solver_task import SolverTask
    from eval.worker_common import validate_request
    from test_dev_runtime_real import real_request

    request, _ = real_request(tmp_path, monkeypatch)
    problem = "Please update the example documented at https://github.com/example/project#usage."
    request["task"]["problem_statement"] = problem
    request["provenance"]["solver_contract_sha256"] = SolverTask.from_dict(request["task"]).sha256()
    before = copy.deepcopy(request)
    assert validate_request(request) is request
    assert request == before


@pytest.mark.parametrize("url", ["https://github.com/example/project#usage",
                              "https://github.com/example/project?tab=readme-ov-file"])
def test_ordinary_returned_patch_with_benign_url_keeps_native_bytes(tmp_path, monkeypatch, url):
    from eval import runtime_real
    from test_dev_runtime_real_native import fake_native_runtime

    patch = '+documentation = "' + url + '"\n'
    request, _, _ = fake_native_runtime(tmp_path, monkeypatch, native_patch=patch)
    for name in ("eval.verifier_task", "eval.verifier_data", "swegemma.evaluate", "swegemma.harness.verification",
                 "swegemma.harness.sample_verification"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    result = runtime_real.solver_execute_real(request)
    assert result["returned_patch"].encode("utf-8") == patch.encode("utf-8")


@pytest.mark.parametrize("url", [
    "https://reader:PATCH_CREDENTIAL_SENTINEL@github.com/example/project",
    "https://github.com/example/project?api_key=PATCH_CREDENTIAL_SENTINEL",
    "https://github.com/example/project#token=PATCH_CREDENTIAL_SENTINEL",
])
def test_credential_bearing_returned_patch_is_refused_without_rewriting(tmp_path, monkeypatch, url):
    from eval import runtime_real
    from test_dev_runtime_real_native import fake_native_runtime

    patch = '+documentation = "' + url + '"\n'
    before = patch.encode("utf-8")
    request, paths, _ = fake_native_runtime(tmp_path, monkeypatch, native_patch=patch)
    for name in ("eval.verifier_task", "eval.verifier_data", "swegemma.evaluate", "swegemma.harness.verification",
                 "swegemma.harness.sample_verification"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with pytest.raises(ContractError, match="authentication material"):
        runtime_real.solver_execute_real(request)
    assert request["returned_patch"].encode("utf-8") == before
    assert not (paths["evidence_root"] / "native_trace.json").exists()


@pytest.mark.parametrize("patch", ['+{"api_key":"QUOTED_SECRET_SENTINEL"}\n',
    "+{'password': 'QUOTED_SECRET_SENTINEL'}\n", '+"Authorization": "QUOTED_SECRET_SENTINEL"\n'])
def test_immutable_patch_hygiene_recognizes_quoted_json_and_python_credential_keys(patch):
    before = patch.encode("utf-8")
    with pytest.raises(ContractError):
        provenance.assert_secret_free(patch)
    assert patch.encode("utf-8") == before
    assert "QUOTED_SECRET_SENTINEL" not in provenance.sanitize_evidence(patch)


@pytest.mark.parametrize("line", ['Authorization: "Bearer HEADER_SENTINEL"',
    "Authorization: Bearer HEADER_SENTINEL", "password = 'HEADER_SENTINEL with spaces'",
    r'\"api_key\":\"HEADER_SENTINEL\"'])
def test_auth_assignment_redacts_whole_quoted_or_bearer_values(line):
    assert "HEADER_SENTINEL" not in provenance.sanitize_evidence(line)


def test_credential_argv_flags_and_dummy_auth_are_redacted_but_budget_counts_survive():
    value = {"argv": "server --api-key FLAG_SENTINEL", "api_dummy": "SYNTHETIC_DUMMY",
             "compaction": {"token_threshold": 14336}, "token_count": 123,
             "llm_calls_used": 1, "hf_token": "HF_SENTINEL"}
    cleaned = provenance.sanitize_evidence(value)
    assert "FLAG_SENTINEL" not in json.dumps(cleaned)
    assert "HF_SENTINEL" not in json.dumps(cleaned)
    assert "SYNTHETIC_DUMMY" not in json.dumps(cleaned)
    assert cleaned["compaction"]["token_threshold"] == 14336
    assert cleaned["token_count"] == 123 and cleaned["llm_calls_used"] == 1


def test_fingerprint_rejects_credential_bearing_observation_metadata():
    value = fingerprint()
    value["model_server_identity"] = {"authorization": "SECRET_SENTINEL"}
    with pytest.raises(ContractError):
        provenance.validate_real_fingerprint(value)


def test_confinement_provenance_is_mandatory_and_cross_phase_identical():
    solver, verifier = fingerprint(), fingerprint("verifier")
    verifier["confinement"] = {"backend": "sudo", "limitations": ["filesystem permissions only"]}
    with pytest.raises(ContractError, match="evidence identity"):
        provenance.crosscheck_real_fingerprints(solver, verifier)
    missing = {key: value for key, value in solver.items() if key != "confinement"}
    with pytest.raises(ContractError):
        provenance.validate_real_fingerprint(missing)
