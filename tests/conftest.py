import json
import zipfile
from pathlib import Path

import pytest

from tools.common import scan_tree, sha256_file

C1, C2 = "a" * 40, "b" * 40


def make_wheel(path: Path, name: str, version: str, dist_name: str | None = None) -> Path:
    dist = f"{(dist_name or name).replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{name.replace('-', '_')}/__init__.py", "x = 1\n")
        zf.writestr(f"{dist}/METADATA", f"Metadata-Version: 2.1\nName: {dist_name or name}\nVersion: {version}\nRequires-Python: >=3.9\n\nbody\n")
        zf.writestr(f"{dist}/RECORD", "")
    return path


@pytest.fixture
def fake_dataset(tmp_path) -> Path:
    root = tmp_path / "dataset"
    (root / "sample_submission" / "prompts").mkdir(parents=True)
    (root / "sample_submission" / "agent.yaml").write_text("name: a\ninstruction: !include prompts/system.md\n")
    (root / "sample_submission" / "prompts" / "system.md").write_text("hi\n")
    (root / "HARNESS_README.md").write_text("# readme\n")
    tasks = [
        {"instance_id": "fastapi_1", "repo": "fastapi/fastapi", "base_commit": C1, "problem_statement": "p",
         "hints_text": "", "patch": "GOLD", "test_patch": "GOLD", "created_at": "2024-01-01T00:00:00Z"},
        {"instance_id": "fastapi_2", "repo": "fastapi/fastapi", "base_commit": C1, "problem_statement": "p",
         "hints_text": " ", "patch": "GOLD", "test_patch": "GOLD", "created_at": "2024-01-02T00:00:00Z"},
        {"instance_id": "rich_3", "repo": "Textualize/rich", "base_commit": C2, "problem_statement": "p",
         "hints_text": "a hint", "patch": "GOLD", "test_patch": "GOLD", "created_at": "2023-01-01T00:00:00Z"},
    ]
    (root / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
    for d in ("snapshots", "graphs", "embeddings", "wheels", "docker", "sandbox"):
        (root / d).mkdir()
    for t in tasks:
        (root / "snapshots" / f"{t['instance_id']}.tgz").write_bytes(b"\x1f\x8b" + t["instance_id"].encode() * 1000)
    (root / "graphs" / f"fastapi_{C1}.json").write_text("{}" * 100)
    (root / "graphs" / f"rich_{C2}.json").write_text("")  # zero-byte on purpose
    (root / "embeddings" / f"fastapi_{C1}.npz").write_bytes(b"PK" * 100)
    (root / "embeddings" / f"rich_{C2}.npz").write_bytes(b"PK" * 10)  # under the 100-byte threshold
    make_wheel(root / "wheels" / "pydantic-2.13.4-py3-none-any.whl", "pydantic", "2.13.4")
    make_wheel(root / "wheels" / "typing_extensions-4.15.0-py3-none-any.whl", "typing_extensions", "4.15.0")
    (root / "docker" / "Dockerfile.sandbox").write_text("FROM python:3.13-slim\n")
    (root / "sandbox" / "setup.py").write_text("print('x')\n")
    (root / ".DS_Store").write_bytes(b"junk")
    return root


def snapshot_state(root: Path) -> dict:
    """Content + mtime state of every regular file and every symlink, to prove a tool did not mutate the dataset."""
    files, links = scan_tree(root, ignore_names=())
    state = {p.as_posix(): (sha256_file(p), p.lstat().st_mtime_ns) for p in files}
    state.update({p.as_posix(): ("link", str(p.readlink()) if p.is_symlink() else "special") for p in links})
    state["<dirs>"] = sorted(str(d) for d in root.rglob("*") if d.is_dir() and not d.is_symlink())
    return state
