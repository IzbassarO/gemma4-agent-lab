"""Generate notebooks/h23_harness_capture.ipynb from tools/h23_capture_core.py (embedded verbatim).

Usage:
    uv run python -m tools.build_h23_notebook --dataset-root DIR    # (re)write the notebook
    uv run python -m tools.build_h23_notebook --check               # verify the committed notebook is current; writes nothing

The core module is the single source of truth; the notebook adds only a markdown header and a two-line run cell.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.common import REPO_ROOT, DatasetRootError, UnsafeOutputError, WriteGuard

CORE = REPO_ROOT / "tools" / "h23_capture_core.py"
NOTEBOOK = REPO_ROOT / "notebooks" / "h23_harness_capture.ipynb"

HEADER = """# H23 — Harness Provenance Capture (CPU only)

Captures what the competition's **official bootstrap** (Getting Started notebook, cell 2) makes available in an
*interactive* Kaggle notebook: wheelhouse inventory, pre/post content fingerprints of the target distributions,
sanitized dist-info metadata, RECORD files, and the source of `swegemma` / `adk-submission` / `adk-eval-core`.

**Never:** imports a target package or torch, starts vLLM, runs tasks, uses a GPU, calls the Kaggle API, submits,
or writes under `/kaggle/input`. Only output: `/kaggle/working/h23_harness_capture_<UTC>_<STATE>.zip`.

**Settings:** Accelerator **None** · Internet **Off** · inputs: the competition data and the dataset
**`metric/gemma-4-developer-agent-wheelhouse`**. Write down the dataset **version** and, after committing, the
**notebook version**; the local importer needs both before any promotion.

**Fail closed:** if the official `pip install` fails, the capture is labelled `BOOTSTRAP_FAILED`, contains no
source claim, and must not be used for H23. Do **not** switch to `harness_only` automatically; that mode is a
separate, explicitly non-official experiment (`NON_OFFICIAL_PARTIAL_BOOTSTRAP`) that can never establish H23
evidence.

The cell below is `tools/h23_capture_core.py` from gemma4-agent-lab, embedded verbatim (stdlib only)."""

RUN = """CONFIG = Config.from_env()  # defaults are the Kaggle paths and the official_all bootstrap
RESULT = run_capture(CONFIG)
report(RESULT)"""


def render() -> str:
    cells = [("markdown", HEADER), ("code", CORE.read_text(encoding="utf-8")), ("code", RUN)]
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"},
                       "kaggle": {"accelerator": "none", "isInternetEnabled": False, "isGpuEnabled": False,
                                  "language": "python", "sourceType": "notebook"}},
          "cells": []}
    for i, (kind, src) in enumerate(cells):
        cell = {"cell_type": kind, "id": f"h23-cell-{i}", "metadata": {}, "source": src.splitlines(keepends=True)}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        nb["cells"].append(cell)
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dataset-root", help="protected dataset root (default $GEMMA4_DATASET_ROOT)")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        ok = NOTEBOOK.is_file() and NOTEBOOK.read_text(encoding="utf-8") == text
        print("notebook current" if ok else "notebook STALE: regenerate with tools.build_h23_notebook")
        return 0 if ok else 1
    try:
        WriteGuard.from_cli(args.dataset_root).write_text(NOTEBOOK, text)
    except (DatasetRootError, UnsafeOutputError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    print(f"wrote {NOTEBOOK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
