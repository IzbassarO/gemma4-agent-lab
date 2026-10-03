# H23 Kaggle capture — 2026-10-03

**H23: HOST-UNKNOWN. Step 0 remains incomplete. Hidden scorer: SCORER_ONLY_UNKNOWN.**

## Real Kaggle run evidence

Authority: **OWN-KAGGLE-RUN**, recorded from operator `izbassaro`'s explicitly
reviewed run evidence, with a separate [HUMAN-ATTESTED ledger](../attestations/h23_kaggle_2026-10-03.json).
This checkpoint concerns an interactive capture, not a scored submission.

- Notebook: `izbassaro/notebooka2687d8703`, visible **Version 3 of 3**,
  immutable **scriptVersionId 354974356**.
  [Version-specific permalink](https://www.kaggle.com/code/izbassaro/notebooka2687d8703?scriptVersionId=354974356).
- Attached wheelhouse: `metric/gemma-4-developer-agent-wheelhouse`, immutable
  **version 28**, **41 visible files**.
- Reviewed source commit: `df77d5c3f804aed896a09cf0484b9d19dde8c9da`.
- Executed/uploaded reviewed notebook source SHA256:
  `a05fd2ed6dd53cbeea32be67e34d93e722bccc54b636e408c7aef72a3ae3d3db`.

The ledger's `kaggle_notebook.immutable_version` is the scriptVersionId, not
the visible version counter. Local notebook bytes match the reported source
SHA; this byte comparison alone does not prove remote execution.

## Technical validation receipt

- Capture: `artifacts/h23/kaggle/h23_capture_v4_20261003T181239Z.zip`
  (gitignored raw evidence).
- Capture SHA256:
  `b090d51980dc4b8d2a347d3a17fc2242c1aa01e0bb05d0c3d27742a4bacae566`.
- Receipt SHA256:
  `0ac24b582dc0a9e08c0a2460bd113fd44c9e6ad97f8d48ccea8a42c2ef2bad6b`.
- Technical result: **CAPTURE_VALIDATED**. Operator-reported final importer
  result: **durability CONFIRMED**, **warning_codes []**.
- Canonical external bundle basename:
  `b090d51980dc4b8d2a347d3a17fc2242c1aa01e0bb05d0c3d27742a4bacae566.evidence.zip`.
  Raw capture and receipt remain external/ignored; the ledger references this
  curated section, not a private absolute filesystem path.

Local read-only checks matched both hashes, verified the bundled capture equals
the local archive, matched receipt Git/program identity to the current importer,
and independently revalidated the immutable snapshot. The receipt covers
6,694 supplied RECORD rows: 8 byte-verified observations and 6,686 digest-only
observations. Durability and warnings are per-attempt importer outputs, not
mutable claims embedded in the committed receipt.

## Bootstrap and observed environment

Bootstrap was attempted: `executed=true`, recipe `OFFICIAL_NOTEBOOK_CELL_2`,
**diagnostic EXIT_NONZERO**, **return_code 1**. Stderr is a commitment only:
138 bytes, SHA256
`d09a5adf82acde0b9028eb81982601dc8507bb114685cf1f114804808e51364b`.
The canonical archive does not contain stderr text. The separate diagnostic
follow-up below reproduces the exact committed byte count/hash and confirms
the error without changing the canonical capture or its human attestation.

Observed interactive runtime: **Python 3.13.15**, executable `/usr/bin/python3`,
scheme `posix_local`, prefix `/usr`, site root
`/usr/local/lib/python3.13/dist-packages`.

Observed pre-existing distributions after the failed bootstrap:

| Distribution | Observed version |
|---|---|
| google-adk | 2.7.1 |
| google-genai | 2.12.1 |
| litellm | 1.85.7 |
| transformers | 5.16.1 |

These are observations of the remaining interactive environment, not proof that
the bootstrap installed the requested stack.

## Reported wheelhouse commitments

The manifest records **41 wheel hashes/sizes and optional identity projections**;
wheel bytes are omitted and remain reported commitments. Bootstrap requested,
among others: `adk-eval-core 0.1.0`, `adk-submission 0.2.12`,
`google-adk 1.36.1`, `google-genai 2.11.0`, `swegemma 0.2.7`,
`transformers 5.13.1`, `vllm 0.19.1`.

The diagnostic follow-up confirms that nine reported `cp312-cp312` wheels
are incompatible with the observed interactive Python 3.13.15 runtime.

## Confirmed bootstrap failure — diagnostic follow-up

Authority: **OWN-KAGGLE-RUN**, operator-reviewed evidence from a later saved
[diagnostic notebook version, scriptVersionId 354984204](https://www.kaggle.com/code/izbassaro/notebooka2687d8703?scriptVersionId=354984204).
This version was used only to reproduce and diagnose the bootstrap failure.
The **canonical H23 capture remains scriptVersionId 354974356**, with the same
capture SHA, receipt SHA, CAPTURE_VALIDATED result and operator-reported
CONFIRMED durability.
The existing human attestation remains unchanged; this follow-up is not a new
imported or promoted capture.

The diagnostic runtime was **Python 3.13.15**, executable `/usr/bin/python3`,
with `metric/gemma-4-developer-agent-wheelhouse` **version 28**. Its
`packaging.tags` compatibility scan found **exactly nine incompatible wheels**,
all `cp312/cp312` binary wheels:

```text
apache_tvm_ffi-0.1.13.post3-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl
cbor2-6.1.4-cp312-cp312-manylinux_2_28_x86_64.whl
ijson-3.5.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
msgspec-0.21.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
nvidia_cudnn_frontend-1.18.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
outlines_core-0.2.11-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
pybase64-1.5.0-cp312-cp312-manylinux1_x86_64.manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_5_x86_64.whl
setproctitle-1.3.7-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl
xgrammar-0.2.5.post1-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
```

The operator directly reproduced pip's rejection with this diagnostic dry-run:

```bash
/usr/bin/python3 -m pip install --dry-run -q --no-deps --force-reinstall \
/kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse/apache_tvm_ffi-0.1.13.post3-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl
```

**RETURN_CODE = 1.** Exact stderr:

```text
ERROR: apache_tvm_ffi-0.1.13.post3-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl is not a supported wheel on this platform.
```

Stdout: **0 bytes**, SHA256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Stderr, including its final newline: **138 bytes**, SHA256
`d09a5adf82acde0b9028eb81982601dc8507bb114685cf1f114804808e51364b`.
The stderr byte count and SHA **exactly match** the commitment in the canonical
Version 3 capture.

**Confirmed root cause of the canonical OFFICIAL_NOTEBOOK_CELL_2 EXIT_NONZERO:**
the observed interactive runtime was CPython 3.13.15, the all-wheel bootstrap
included `apache_tvm_ffi` built specifically for cp312, and pip rejected that
wheel as unsupported. This is no longer merely a diagnostic lead. It does not
establish that later installation steps would succeed, identify hidden scorer
Python, or prove successful installation of the official evaluation stack.

## Limitations and next blocker

**Successful Kaggle capture != successful bootstrap. Interactive Kaggle
runtime != hidden scorer runtime.** The failed bootstrap prevents claiming
successful reproduction of the official evaluation stack. H23 remains
**HOST-UNKNOWN**; the hidden scorer remains **SCORER_ONLY_UNKNOWN**.

CAPTURE_VALIDATED establishes technical policy conformity, included-byte
verification, internal consistency and committed publication. It does not
authenticate Kaggle origin, prove reported execution or installation causality,
verify omitted wheel/file bytes, establish exhaustive installation, or certify
runtime functionality. The human ledger records the operator's review;
its checker validates format only and does not promote H23 or scorer identity.

**Step 0 is not complete.** The remaining blocker is obtaining a compatible,
usable harness/compiler environment, successfully bootstrapping the required
stack and verifying installed-package identity before CPU certification. This
documentation checkpoint changes no agent/runtime code and launches no
Kaggle, model/GPU or H26 experiment.
