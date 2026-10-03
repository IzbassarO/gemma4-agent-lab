"""Fake installed environments, wheels and a fake pip for H23 tests (independent of the capture core)."""
import base64
import hashlib
import json
import zipfile
from pathlib import Path

from tools import h23_capture_core as core


def b64(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def dist_info_name(name: str, version: str) -> str:
    return f"{name.replace('-', '_')}-{version}.dist-info"


def write_record(site: Path, di: str, files: dict, extra_rows=None) -> None:
    rows = [f"{rel},{b64(data)},{len(data)}" for rel, data in sorted(files.items())]
    rows += list(extra_rows or [])
    rows.append(f"{di}/RECORD,,")
    (site / di / "RECORD").write_text("\n".join(rows) + "\n")


def install_files(site: Path, name: str, version: str, files: dict, *, metadata_extra: str = "",
                  direct_url: str | None = None, record: bool = True, extra_record_rows: list | None = None,
                  entry_points: str | None = None, scripts: dict | None = None) -> None:
    """Write a distribution the way pip leaves it: package files + dist-info with a hashed RECORD.
    `scripts` = {name: bytes} are written to <site>/../bin/<name> and recorded as ../bin/<name>."""
    site.mkdir(parents=True, exist_ok=True)
    di = dist_info_name(name, version)
    all_files = dict(files)
    all_files[f"{di}/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{metadata_extra}".encode()
    all_files[f"{di}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    all_files[f"{di}/INSTALLER"] = b"pip\n"
    if entry_points is not None:
        all_files[f"{di}/entry_points.txt"] = entry_points.encode()
    if direct_url is not None:
        all_files[f"{di}/direct_url.json"] = direct_url.encode()
    for rel, data in all_files.items():
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    rows = list(extra_record_rows or [])
    for sname, data in (scripts or {}).items():
        b = site.parent / "bin"
        b.mkdir(exist_ok=True)
        (b / sname).write_bytes(data)
        rows.append(f"../bin/{sname},{b64(data)},{len(data)}")
    if record:
        write_record(site, di, all_files, rows)


def make_wheel(path: Path, name: str, version: str, files: dict) -> Path:
    di = dist_info_name(name, version)
    members = dict(files)
    members[f"{di}/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode()
    members[f"{di}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    rec = [f"{rel},{b64(d)},{len(d)}" for rel, d in sorted(members.items())] + [f"{di}/RECORD,,"]
    members[f"{di}/RECORD"] = ("\n".join(rec) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for rel, d in sorted(members.items()):
            zf.writestr(rel, d)
    return path


def fake_pip(site: Path, staging_parent: Path, calls: list, returncode: int = 0, post=None):
    """Runner that 'installs' each wheel argument into `site` (like pip --no-deps --force-reinstall).
    `post(site)` may tamper with the result afterwards."""
    def runner(cmd):
        calls.append({"cmd": list(cmd), "plan_present": (staging_parent / "h23_capture" / "bootstrap_plan.json").is_file()})
        if returncode != 0:
            return {"returncode": returncode, "stdout_tail": "", "stderr_tail": "ERROR: simulated failure"}
        for w in cmd[7:]:
            with zipfile.ZipFile(w) as zf:
                meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
                hdr = zf.read(meta).decode()
                name = next(l.split(":", 1)[1].strip() for l in hdr.splitlines() if l.startswith("Name:"))
                version = next(l.split(":", 1)[1].strip() for l in hdr.splitlines() if l.startswith("Version:"))
                for old in site.glob(f"{name.replace('-', '_')}-*.dist-info"):  # force-reinstall: remove old
                    for line in (old / "RECORD").read_text().splitlines():
                        p = site / line.split(",")[0]
                        if p.is_file():
                            p.unlink()
                    if old.is_dir() and not any(old.iterdir()):
                        old.rmdir()
                files = {n: zf.read(n) for n in zf.namelist() if ".dist-info/" not in n}
            install_files(site, name, version, files)
        if post:
            post(site)
        return {"returncode": 0, "stdout_tail": "", "stderr_tail": ""}
    return runner


def load_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return {i.filename[len("h23_capture/"):]: zf.read(i) for i in zf.infolist()}


def write_capture(path: Path, files: dict, rehash: bool = True) -> Path:
    files = dict(files)
    if rehash:
        files["hashes.json"] = (json.dumps({"algorithm": "sha256", "files": {
            k: {"sha256": hashlib.sha256(v).hexdigest(), "bytes": len(v)} for k, v in files.items() if k != "hashes.json"}},
            indent=2, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(path, "w") as zf:
        for k, v in sorted(files.items()):
            zf.writestr("h23_capture/" + k, v)
    return path


def edit_json(files: dict, name: str, fn) -> dict:
    files = dict(files)
    d = json.loads(files[name])
    fn(d)
    files[name] = (json.dumps(d, indent=2, sort_keys=True) + "\n").encode()
    return files


def scenario(tmp: Path, *, mode=core.MODE_OFFICIAL, returncode=0, skip=False, post=None):
    """A full fake Kaggle-like environment. Returns (config, calls, site)."""
    site, wh = tmp / "site", tmp / "in" / "datasets" / "metric" / "gemma-4-developer-agent-wheelhouse"
    # base image: google-adk present (not in wheelhouse) and an OLD adk-eval-core that the wheelhouse replaces
    install_files(site, "google-adk", "1.0.0", {"google/adk/__init__.py": b"X = 1\n"},
                  entry_points="[console_scripts]\nadk = google.adk.cli:main\n", scripts={"adk": b"#!/usr/bin/env python\n"})
    install_files(site, "adk-eval-core", "0.0.1", {"adk_eval_core/__init__.py": b"OLD = True\n"})
    make_wheel(wh / "swegemma-0.2.7-py3-none-any.whl", "swegemma", "0.2.7",
               {"swegemma/__init__.py": b"V = '0.2.7'\n", "swegemma/config.py": b"ALLOWED = 1\n", "swegemma/data/x.yaml": b"a: 1\n"})
    make_wheel(wh / "adk_submission-0.2.12-py3-none-any.whl", "adk-submission", "0.2.12",
               {"adk_submission/__init__.py": b"PathTraversalError = None\n"})
    make_wheel(wh / "adk_eval_core-0.1.0-py3-none-any.whl", "adk-eval-core", "0.1.0", {"adk_eval_core/__init__.py": b"NEW = True\n"})
    make_wheel(wh / "vllm-0.19.1cu128-py3-none-any.whl", "vllm", "0.19.1+cu128", {"vllm/__init__.py": b"reasoning = 1\n"})
    make_wheel(wh / "anyio-4.0.0-py3-none-any.whl", "anyio", "4.0.0", {"anyio/__init__.py": b"A = 1\n"})  # non-target dependency
    make_wheel(wh / "nvidia_cutlass_dsl-4.0.0-py3-none-any.whl", "nvidia-cutlass-dsl", "4.0.0", {"cutlass/__init__.py": b""})
    calls = []
    cfg = core.Config(input_root=tmp / "in", output_dir=tmp / "out", staging_parent=tmp / "st", tmp_wheelhouse=tmp / "wh",
                      mode=mode, skip_install=skip, search_paths=[str(site)], scripts_dir=tmp / "bin",
                      pip_runner=fake_pip(site, tmp / "st", calls, returncode, post))
    (tmp / "bin").mkdir(exist_ok=True)
    return cfg, calls, site
