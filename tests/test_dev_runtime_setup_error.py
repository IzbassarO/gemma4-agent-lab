"""Dependency setup exceptions cannot masquerade as successful empty solvers."""
import sys
from types import ModuleType, SimpleNamespace

from eval import runtime_real as real


def test_solver_reports_observed_setup_failure_even_if_native_runner_swallows_exception(tmp_path, monkeypatch):
    paths = {}
    for name in ("worker_root", "public_root", "evidence_root", "submission_root"):
        paths[name] = tmp_path / name
        paths[name].mkdir(mode=0o700)
    paths["public_support_identity"] = {"setup_py_sha256": "1" * 64, "wheels_tree_sha256": "2" * 64}
    task = SimpleNamespace(instance_id="fastapi_1", repo="fastapi/fastapi", base_commit="1" * 40,
                           problem_statement="synthetic public fixture", hints_text="",
                           snapshot=SimpleNamespace(source_relative_path="snapshots/fixture.tgz"))
    monkeypatch.setattr(real, "_admit", lambda request: (paths, task, {"site_packages": str(tmp_path)}, {}))
    monkeypatch.setattr(real, "extract_candidate", lambda path, destination: {"sha256": real.E0_SHA256})
    monkeypatch.setattr(real, "parse_candidate_budget", lambda path, expected: dict(real.FROZEN_BUDGET))
    monkeypatch.setattr(real, "_prepare", lambda paths: None)
    monkeypatch.setattr(real, "check_model_identity", lambda endpoint: ({"served_model": real.SERVED_MODEL}, b'{"data":[]}'))
    monkeypatch.setattr(real, "_native_namespaces", lambda root: None)
    monkeypatch.setattr(real, "_compaction", lambda value, root: (None, None, {"compaction_applied": False}))
    config = SimpleNamespace(budget=SimpleNamespace(), harness=SimpleNamespace(), graph_dir=None, embeddings_dir=None)
    monkeypatch.setattr(real, "_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(real, "_mount_public_wheels", lambda *args: None)

    def fail_provisioning(*args):
        raise real.RealAdmissionError("synthetic missing offline dependency")
    monkeypatch.setattr(real, "_provision_dependencies", fail_provisioning)

    class Manager:
        def __init__(self, *, base_dir, **kwargs):
            self.base_dir, self.sandboxes = base_dir, {}
        def start(self):
            root = self.base_dir / "fixture"
            root.mkdir(parents=True, mode=0o700)
            self.sandboxes["fixture"] = {"root": root}
            for name in ("workspace", "tmp", "wheels", "venv"):
                path = root / name
                path.mkdir(mode=0o700)
                self.sandboxes["fixture"][name] = path
            return "fixture"
        def exec(self, *args, **kwargs):
            return SimpleNamespace(exit_code=0, stdout="", stderr="")
        def copy_to(self, *args):
            raise AssertionError("fixture setup fails before copying")
        def stop(self, identifier):
            self.sandboxes.pop(identifier)
        def cleanup_all(self):
            for identifier in list(self.sandboxes):
                self.stop(identifier)

    class Context:
        submitted_patch = None
        patch_submitted = False
        llm_calls_used = tool_calls_used = agent_elapsed_seconds = 0
        def __init__(self, **kwargs):
            pass
        def start_agent_session(self):
            raise AssertionError("failed provisioning must precede agent session")

    def setup_workspace_test_config(manager, identifier, **kwargs):
        return manager.exec(identifier, "fixture native setup barrier")

    async def native_runner(manager, config, task, snapshot, *, context):
        try:
            identifier = manager.start()
            setup_workspace_test_config(manager, identifier)
        except real.RealAdmissionError:
            # Reproduce the pinned runner's catch-and-empty-patch behavior.
            return "", None, None
        raise AssertionError("provisioning exception must occur")

    def module(name, **values):
        value = ModuleType(name)
        value.__dict__.update(values)
        value.__path__ = []
        monkeypatch.setitem(sys.modules, name, value)
    for name in ("swegemma", "swegemma.harness", "swegemma.models", "swegemma.sandbox"):
        module(name)
    module("litellm")
    module("adk_submission", discover_adapters=lambda candidate: SimpleNamespace(adapters=dict.fromkeys(real.ADAPTER_NAMES)))
    module("swegemma.models.registry", setup_gemma_model_registry=lambda **kwargs: object())
    module("swegemma.context", SwegemmaContext=Context)
    module("swegemma.harness.agent_runner", run_agent_sandbox=native_runner)
    module("swegemma.sandbox.subprocess", SubprocessManager=Manager)
    module("swegemma.harness.container_setup", setup_workspace_test_config=setup_workspace_test_config)
    for name in ("eval.verifier_task", "eval.verifier_data", "swegemma.evaluate", "swegemma.harness.verification",
                 "swegemma.harness.sample_verification"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    request = {"runtime": {"budget": dict(real.FROZEN_BUDGET), "model_endpoint": "http://127.0.0.1:8000/v1",
                           "compaction": None}, "observation_enabled": True}
    result = real.solver_execute_real(request)
    assert result["runtime_status"] == "worker_error"
    assert result["returned_patch"] == ""
    assert result["escaped_exception"] == "eval.runtime_real.RealAdmissionError: sandbox dependency setup failed"
    assert result["terminal_reason"] == "ENVIRONMENT_VERIFICATION_ARTIFACT: sandbox dependency setup failed"
    assert result["llm_calls"] == result["counted_tool_calls"] == 0
    assert result["native_result"]["sandbox_ids"] == result["native_result"]["stopped_ids"] == ["fixture"]


def test_coordinator_archives_setup_failure_and_stops_before_private_loading(tmp_path, monkeypatch):
    from dataclasses import replace
    import pytest
    from eval import runtime, worker_common
    from eval._contracts import ContractError
    from eval.runtime_result import SolverRunResult
    from test_dev_runtime_real import coordinator_fixture, invoke_coordinator

    inputs = coordinator_fixture(tmp_path, monkeypatch)
    execute = runtime._execute_worker
    def fail_setup(config, request, root, phase):
        assert phase == "solver", "setup failure must prevent verifier execution"
        result = SolverRunResult.from_dict(execute(config, request, root, phase))
        result = replace(result, runtime_status="worker_error", returned_patch="",
                         returned_patch_sha256=worker_common.patch_sha256(""),
                         terminal_reason="ENVIRONMENT_VERIFICATION_ARTIFACT: sandbox dependency setup failed")
        (root / "evidence/result.json").unlink()
        worker_common.seal_json(root / "evidence/result.json", result.to_dict())
        return result.to_dict()
    monkeypatch.setattr(runtime, "_execute_worker", fail_setup)
    with pytest.raises(ContractError, match="ENVIRONMENT_VERIFICATION_ARTIFACT"):
        invoke_coordinator(inputs)
    config, events, roots = inputs[5:8]
    assert events == ["prepare_solver", "execute_solver"]
    assert not roots["solver"].exists()
    run_root, = config.artifact_root.iterdir()
    assert (run_root / "solver/result.json").is_file()
    assert not (run_root / "verifier").exists() and not (run_root / "task_result.json").exists()
