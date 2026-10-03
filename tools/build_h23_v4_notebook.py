"""Render the v4 notebook from exactly the reviewed local runtime modules."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.common import WriteGuard


PROJECT = Path(__file__).resolve().parents[1]
NOTEBOOK = PROJECT / "notebooks" / "h23_harness_capture_v4.ipynb"
RUNTIME_FILES = (
    "tools/__init__.py",
    "tools/h23_v4/__init__.py",
    "tools/h23_v4/schema.py",
    "tools/h23_v4/metadata_policy.py",
    "tools/h23_v4/record_policy.py",
    "tools/h23_v4/filesystem.py",
    "tools/h23_v4/archive.py",
    "tools/h23_v4/capture.py",
)

HEADER = """# H23 protocol v4 observation capture

Run interactively on Kaggle only after local review. This notebook performs the
official all-wheel pip invocation recipe and writes a bounded observation
archive. It excludes CUTLASS wheels and restores stripped cu128 version markers;
it does not delete pre-existing cutlass.pth files or claim whole-cell equivalence.
It never imports a target package or runs a model. A bootstrap failure is
reported as an observation; it does not trigger an automatic fallback.
Use a fresh kernel: an existing tools package causes a fixed failure before the
embedded package is imported.

The archive does not prove Kaggle origin, successful installation, runtime
functionality, or hidden scorer identity. Independent import validates only
the supplied evidence. Human run attestation is a separate project process.
"""


def runtime_sources(project: Path = PROJECT) -> dict[str, str]:
    """Return the complete, exact UTF-8 source mapping used by the notebook."""
    return {name: (project / name).read_bytes().decode("utf-8") for name in RUNTIME_FILES}


def render(project: Path = PROJECT) -> str:
    sources = json.dumps(runtime_sources(project), sort_keys=True, ensure_ascii=True)
    code = (
        "import json\nimport pathlib\nimport sys\nimport tempfile\n\n"
        "if 'tools' in sys.modules:\n"
        "    raise RuntimeError('H23_V4_REQUIRES_FRESH_KERNEL')\n"
        f"H23_RUNTIME_SOURCES = json.loads({sources!r})\n"
        "H23_RUNTIME_DIRECTORY = pathlib.Path(tempfile.mkdtemp(prefix='h23-v4-runtime-'))\n"
        "for H23_NAME, H23_CONTENT in H23_RUNTIME_SOURCES.items():\n"
        "    H23_PATH = H23_RUNTIME_DIRECTORY / H23_NAME\n"
        "    H23_PATH.parent.mkdir(parents=True, exist_ok=True)\n"
        "    H23_PATH.write_bytes(H23_CONTENT.encode('utf-8'))\n"
        "sys.path.insert(0, str(H23_RUNTIME_DIRECTORY))\n"
        "from tools.h23_v4.capture import CaptureConfig, run_capture\n"
        "H23_ARCHIVE = run_capture(CaptureConfig())\n"
        "print(json.dumps({'observation_archive': str(H23_ARCHIVE)}, sort_keys=True))\n"
    )
    notebook = {
        "cells": [
            {"cell_type": "markdown", "id": "h23-v4-purpose", "metadata": {}, "source": HEADER.splitlines(True)},
            {"cell_type": "code", "execution_count": None, "id": "h23-v4-capture", "metadata": {},
             "outputs": [], "source": code.splitlines(True)},
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check exact source embedding without writing.")
    parser.add_argument("--dataset-root", help="Read-only dataset root required when writing.")
    args = parser.parse_args(argv)
    expected = render()
    if args.check:
        return 0 if NOTEBOOK.is_file() and NOTEBOOK.read_bytes() == expected.encode("utf-8") else 1
    WriteGuard.from_cli(args.dataset_root).write_text(NOTEBOOK, expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
