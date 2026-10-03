import ast
import json
import shutil

from tools import build_h23_v4_notebook as builder


def embedded_sources(notebook_path=None):
    notebook = json.loads((notebook_path or builder.NOTEBOOK).read_bytes().decode("utf-8"))
    source = "".join(notebook["cells"][1]["source"])
    assignment = next(
        statement for statement in ast.parse(source).body
        if isinstance(statement, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "H23_RUNTIME_SOURCES" for target in statement.targets)
    )
    assert isinstance(assignment.value, ast.Call)
    assert isinstance(assignment.value.args[0], ast.Constant)
    return json.loads(assignment.value.args[0].value), source


def test_notebook_embeds_exact_runtime_sources():
    mapping, source = embedded_sources()
    assert set(mapping) == {
        "tools/__init__.py", "tools/h23_v4/__init__.py", "tools/h23_v4/schema.py",
        "tools/h23_v4/metadata_policy.py", "tools/h23_v4/record_policy.py",
        "tools/h23_v4/filesystem.py", "tools/h23_v4/archive.py", "tools/h23_v4/capture.py",
    }
    for name, content in mapping.items():
        assert content.encode("utf-8") == (builder.PROJECT / name).read_bytes()
    assert "from tools.h23_v4.capture import CaptureConfig, run_capture" in source
    assert builder.main(["--check"]) == 0


def test_meaningful_runtime_change_fails_drift_check(tmp_path, monkeypatch):
    for name in builder.RUNTIME_FILES:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(builder.PROJECT / name, destination)
    capture = tmp_path / "tools/h23_v4/capture.py"
    capture.write_text(capture.read_text(encoding="utf-8") + "\ndef changed_capture_behavior():\n    return 42\n", encoding="utf-8")
    changed = builder.render(tmp_path)
    assert changed != builder.NOTEBOOK.read_bytes().decode("utf-8")
    monkeypatch.setattr(builder, "render", lambda: changed)
    assert builder.main(["--check"]) == 1


def test_notebook_check_rejects_byte_only_line_ending_drift(tmp_path, monkeypatch):
    notebook = tmp_path / "line-endings.ipynb"
    notebook.write_bytes(builder.render().encode("utf-8").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(builder, "NOTEBOOK", notebook)
    assert builder.main(["--check"]) == 1


def test_crlf_runtime_source_bytes_survive_embedding(tmp_path, monkeypatch):
    for name in builder.RUNTIME_FILES:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(builder.PROJECT / name, destination)
    capture = tmp_path / "tools/h23_v4/capture.py"
    crlf_bytes = capture.read_bytes().replace(b"\n", b"\r\n")
    capture.write_bytes(crlf_bytes)
    assert b"\r\n" in crlf_bytes
    assert builder.runtime_sources(tmp_path)["tools/h23_v4/capture.py"].encode("utf-8") == crlf_bytes
    changed = builder.render(tmp_path)
    rendered_notebook = tmp_path / "rendered.ipynb"
    rendered_notebook.write_bytes(changed.encode("utf-8"))
    mapping, source = embedded_sources(rendered_notebook)
    assert mapping["tools/h23_v4/capture.py"].encode("utf-8") == crlf_bytes
    assert "H23_PATH.write_bytes(H23_CONTENT.encode('utf-8'))" in source
    assert changed != builder.NOTEBOOK.read_bytes().decode("utf-8")
    monkeypatch.setattr(builder, "render", lambda: changed)
    assert builder.main(["--check"]) == 1


def test_notebook_runtime_has_no_dynamic_execution_or_target_imports():
    mapping, source = embedded_sources()
    forbidden = {"torch", "vllm", "swegemma", "adk_submission", "adk_eval_core", "litellm", "safetensors"}
    for content in [source, *mapping.values()]:
        for node in ast.walk(ast.parse(content)):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in forbidden for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"__import__", "exec", "eval"}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"import_module", "run_module", "run_path"}
