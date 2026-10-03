# H23 — Wheelhouse / Scorer Package Fingerprint

**Status: `HOST-UNKNOWN`**
**Date:** 2026-10-02 · **Machine:** MacBook Pro M1 Pro, macOS 26.6.2, Python 3.14.7
**Repro:** `GEMMA4_DATASET_ROOT=… python -m tools.wheelhouse` → `vendor_meta/wheelhouse_manifest.json` (deterministic: two runs are byte-identical)

## Verdict in one paragraph

The local `wheels/` directory was fully fingerprinted: 124 wheels, 41 projects, no corrupt zips, METADATA present in every wheel, and filename and METADATA versions agree for all 124. **None of the scorer's harness stack is in it**: no `swegemma`, `adk-submission`, `adk-eval-core`, `google-adk`, `google-genai`, `litellm`, `vllm`, `transformers`, `networkx`, `numpy`, `pandas`, `pyarrow` or `docker`. Its contents are sandbox test dependencies (STATIC-OBSERVED). `docker/Dockerfile.public` copies it to `/wheels` (STATIC-OBSERVED), and the README documents `/wheels` as the offline source for the in-container editable/test installs (OFFICIAL-DOCUMENTED). Whether the scorer's `/wheels` is byte-identical to this directory is HOST-UNKNOWN. The harness versions running on the Kaggle scorer therefore **cannot be determined from local data**. H23 cannot be PASS. It is not FAIL either, because nothing contradicts a fingerprint. The [2026-10-03 interactive Kaggle capture](../../harness_cert/reports/H23_KAGGLE_CAPTURE_2026-10-03.md) now has a `CAPTURE_VALIDATED` technical receipt, but its bootstrap reported `EXIT_NONZERO` (code 1). H23 remains **HOST-UNKNOWN**, and the hidden scorer remains **SCORER_ONLY_UNKNOWN**.

## Wheelhouse fingerprint

| Field | Value |
|---|---|
| wheel count | 124 |
| distinct projects | 41 |
| total bytes | 27,810,465 |
| `wheelhouse_fingerprint_sha256` | `acb7649a9bfa1e5c5f8843423dee434d45f51df093070eeb7428182cf3ec5709` |
| definition | sha256 of `filename\tsha256\n` lines for every wheel, sorted by filename |
| anomalies | bad zip: 0 · no METADATA: 0 · filename≠METADATA: 0 |
| platform tags | `py3-none-any` / `py2.py3-none-any`, plus 8 **`manylinux x86_64`** binaries (cp312 + cp313): charset_normalizer, markupsafe, pydantic_core, sqlalchemy |
| Requires-Python | ≥2.7 … ≥3.10; none pins 3.14 |

Re-run before every submission epoch. A changed fingerprint means the sandbox dependency set changed.

## Key packages requested by the plan

| Package | In local wheelhouse? | Version(s) | Source `.py` inspectable from the wheel? |
|---|---|---|---|
| swegemma | **absent** | — | no |
| adk-submission | **absent** | — | no |
| adk-eval-core | **absent** | — | no |
| google-adk | **absent** | — | no (public on PyPI; latest there is 2.11.0, but that says nothing about the scorer pin) |
| litellm | **absent** | — | no |
| vllm | **absent** | — | no |
| pydantic | present | 1.10.15, 2.13.4 | yes (27 / 105 `.py` files) |
| pydantic-core | present | 2.46.4 (cp312 + cp313 manylinux x86_64) | compiled extension only |
| networkx | **absent** | — | — |
| numpy | **absent** | — | — |
| pandas | **absent** | — | — |
| pyarrow | **absent** | — | — |
| docker | **absent** | — | — |

Other notable versions: pytest 6.2.5 / 8.3.4 / 9.1.1 · fastapi 0.141.1 · starlette 0.19.0 → 1.6.0 (56 versions) · rich 15.0.0 · requests 2.33.0 / 2.34.2 · httpx 0.28.1 · sqlmodel 0.0.24 / .25 / .27 / .31 / .39 · flask 2.2.5 / 2.3.3 / 3.1.3 · typer 0.16.0 / 0.21.1 / 0.26.7. The full list is in `vendor_meta/wheelhouse_manifest.json → project_versions`.

## Where the harness packages are *not*

| Location checked | Result |
|---|---|
| `$GEMMA4_DATASET_ROOT/**` (all 524 files) | no swegemma / adk_* / google-adk / litellm / vllm files of any kind |
| Public PyPI (`pypi.org/pypi/<name>/json`, 2026-10-02) | `swegemma`, `adk-submission`, `adk-eval-core`: **not on PyPI** · `google-adk` latest 2.11.0 · `litellm` latest 1.103.2 · `vllm` latest 0.30.0 |
| This Mac's Python (`pip show`) | none installed |

`google-adk`, `litellm` and `vllm` are public, but the latest PyPI versions are **not** evidence of the scorer pin.

## Known vs unknown

| Claim | Label | Status |
|---|---|---|
| Local wheelhouse contents, sizes and hashes | REPRODUCED-LOCAL | known exactly |
| Local wheelhouse = sandbox test deps, not the harness | STATIC-OBSERVED (contents; `Dockerfile.public` copies it to `/wheels`) | known locally |
| Docker sandbox base `python:3.13-slim`, `pytest-timeout==2.1.0`, git, patch, pigz | OFFICIAL-HARNESS (`docker/Dockerfile.*`) | known statically; image digest not pinned (`FROM python:3.13-slim` floats) |
| `swegemma 0.2.7`, `adk-submission 0.2.11`, `adk-eval-core 0.1.0`, `google-adk 1.36.1`, `google-genai 2.11.0`, `vllm 0.19.1`, `transformers 5.13.1` | COMMUNITY-EVIDENCE (`gemma-and-the-shape-of-doubt.ipynb`, `gemma-4-walkthrough-first-submission.ipynb`, ~2026-09-25) | **not reproduced**; historical |
| `adk_submission 0.2.12` in use after a Sep 30 update | COMMUNITY-EVIDENCE (forum C01 / C13) | **not reproduced** |
| Exact harness versions on the Kaggle scorer today | — | **HOST-UNKNOWN** |
| The scorer's `/wheels` equals this local `wheels/` | INFERENCE (likely: same dataset) | not proven; the README says the scorer also streams *cached unpacked site-packages*, which we do not have |

## Side findings that matter for later certification

1. **Mac architecture gap.** The four compiled dependencies (pydantic-core, markupsafe, charset-normalizer, sqlalchemy) ship only `manylinux x86_64` wheels. On an M1:
   - A `--sandbox subprocess` run cannot install them from `/wheels`.
   - A Docker run needs `--platform linux/amd64` emulation.

   So FastAPI-task sandbox runs on this Mac either need amd64 emulation or are not faithful. (INFERENCE; to be tested when the harness is available.)
2. **Python 3.12 and 3.13 binaries are both present**, but the Dockerfile is 3.13. Some other runtime (perhaps the Kaggle host interpreter for the subprocess backend) uses 3.12. (INFERENCE.)

## Current capture checkpoint and remaining blocker

The [2026-10-03 checkpoint](../../harness_cert/reports/H23_KAGGLE_CAPTURE_2026-10-03.md)
records an operator-reported Kaggle Version 3 run (`scriptVersionId=354974356`)
with `metric/gemma-4-developer-agent-wheelhouse` v28. Its archive has a
`CAPTURE_VALIDATED` technical receipt; the operator reports import durability
`CONFIRMED` and no warnings. These are observations from an interactive notebook,
not a hidden scorer fingerprint.

The `OFFICIAL_NOTEBOOK_CELL_2` bootstrap reported `EXIT_NONZERO` (code 1).
Observed pre-existing distributions after that failure do not prove successful
installation of the requested wheelhouse stack. Neither technical validation
nor the separate operator attestation establishes successful scorer-stack
reproduction. The local 124-wheel fingerprint above remains a separate static
observation.

The next blocker is confirming the exact bootstrap failure and obtaining a
usable harness/compiler environment for CPU certification. H23 stays
**HOST-UNKNOWN**; the hidden scorer stays **SCORER_ONLY_UNKNOWN**.
