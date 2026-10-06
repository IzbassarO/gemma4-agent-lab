# H28 first real Kaggle E0 certification — 2026-10-06

**H28: PASS. Authority: OWN-KAGGLE-RUN, explicitly operator-reported.**
The exact existing hypothesis is satisfied for this frozen official-control ZIP
and this observed hosted completion. This is not general harness certification.
H30 remains NOT-REPRODUCED; every other matrix item is unchanged.

## Scope and evidence sources

First real technical-control Kaggle submission, E0. The operator's request
supplied on 2026-10-06 reports direct upload, evaluation, final status and score,
and attributes the downloaded ZIP to that completed submission. No authenticated
Kaggle UI/API was accessed in this pass. No screenshot, submission ID, exact
submission/completion timestamp, detailed evaluation logs or task-level results
were provided. The report date is a certification date, not an asserted remote
event timestamp. Operator observations are accepted as explicitly attributed
OWN-KAGGLE-RUN evidence, consistent with the existing H23 reporting convention.

Independent local verification used Python 3.14.7 on Darwin, standard-library
hashing/ZIP inspection, and system `cmp`. Hidden scorer Python, package and
model-server versions are **UNKNOWN**, including swegemma, adk-submission,
adk-eval-core, google-adk, LiteLLM and vLLM. Neither H23's interactive capture
nor local CPU certification versions identify this scored runtime.

Raw local receipt (gitignored):
`harness_cert/results/H28/2026-10-06-e0-certification/local_verification.json`.
Receipt SHA256:
`85b3432fac2f33bb6fa1efd1b8d8c6d28612fdfa3ee0eb0949d2d513d6f09d39`.
It records the local identities/comparisons, official-manifest check, historical
build identities, admission state, and separately labeled operator statements.
It is a review receipt created now, not a Kaggle-generated log or preregistration.

## Repository admission and frozen source identity

Before any modification, the requested read-only admission commands returned:

```text
git status --short --untracked-files=all
?? harness_cert/reports/H28_E0_OPERATOR_RUNBOOK_2026-10-05.md
?? kaggle/e0_submission.ipynb

git rev-parse HEAD
3c6133ea9a1d9440183bfacc72b9f6e823dc6208
git rev-parse origin/main
3c6133ea9a1d9440183bfacc72b9f6e823dc6208

git log -5 --oneline
3c6133e cert(harness): audit H30 packaging readiness
81ac5b1 cert(harness): reproduce H13 H14 H29 locally
12c319f cert(harness): reproduce H04 H05 H18 locally
ea5b857 cert: reproduce H27 local no-LoRA path
2ffedae cert: reproduce H26 include path semantics

git diff --check
exit 0; no output
```

There were no tracked changes or unrelated untracked files. The two anticipated
support files were reviewed and retained as historical preparation, with current
flow notices added. Nothing was silently included in a commit; no commit or push
is authorized or performed in this pass.

**Frozen submission/review repository HEAD:**
`3c6133ea9a1d9440183bfacc72b9f6e823dc6208`.

This is distinct from original build provenance. The preserved
`artifacts/submissions/e0_official_control/provenance.json` records build commit
`199c25943b287b5e58b4067976880a9d53254f7e`, build time
`2026-10-02T23:46:24+00:00`, `git_dirty: true`, `source_in_git: false`, and
`official_control.modified: false`. E0 includes the sample's external official
adapter files. We do not claim the ZIP was built from a clean 3c6133e checkout,
that every source byte is Git-backed, or that a new adapter was trained.
Original ZIP, manifest and provenance remain untouched.

## Exact H28 reconstruction before promotion

| Field | Admitted value |
|---|---|
| id | H28 |
| title | current public-sample submission path |
| hypothesis | The official sample packaged deterministically passes the current Kaggle submission path. |
| status | NOT-REPRODUCED |
| authority | OFFICIAL-NOTEBOOK; OWN-KAGGLE-RUN (none yet) |
| blocked_by | [kaggle_submission] |
| local_possible | false |
| requires_model / requires_gpu / requires_docker | true / true / false |
| risk | medium |
| result_path | harness_cert/results/H28/ |
| minimal_repro | Deterministic zip of agents/baseline_v0_official; record ZIP SHA; submit once when a submission slot is justified. |
| notes | Local part (deterministic zip + validate_directory) is blocked only by harness_source. |

The hypothesis, title, minimal reproduction, flags and risk are preserved.
Nothing about its wording requires notebook materialization. A successful
current direct upload satisfies the existing submission-path hypothesis.

## Artifact identity and round trip

All paths below are relative to the repository root.

| Artifact | Present | Bytes | SHA256 |
|---|---|---:|---|
| `artifacts/submissions/e0_official_control/submission.zip` | yes | 443572 | `25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3` |
| `../H28_E0_UPLOAD/submission.zip` | yes | 443572 | `25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3` |
| `../H28_E0_RESULT/submission.zip` | yes | 443572 | `25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3` |

| Byte comparison | `cmp` exit |
|---|---:|
| frozen local vs upload | 0 |
| frozen local vs downloaded result | 0 |
| upload vs downloaded result | 0 |

Thus the three accessible copies are byte-identical. Remote upload/download
attribution comes from the operator; local equality is independently reproduced.
A rounded or compressed-looking UI size does not override actual byte counts.

ZIP CRC check passed, exactly ten members matched every size/hash in
`vendor_meta/baseline_v0_official_manifest.json`, and recomputed official tree
SHA256 was `4557fcf418a85aa41c0ad560a9951f05a2726e69b249850aa9607e52c3736e2c`.
This establishes unchanged official-sample content independently of provenance
assertions. Both previously produced ZIPs at
`harness_cert/results/H28/20261005T015227Z-readiness-hir9laoy/{build_a,build_b}/submission.zip`
were also rehashed and compared to the frozen ZIP: each has the same size/hash
and identical bytes. Their prior two-build determinism evidence is retained;
no rebuild or model run was needed here.

Read-only commands sufficient to repeat the transport checks:

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib
for name in (
    'artifacts/submissions/e0_official_control/submission.zip',
    '../H28_E0_UPLOAD/submission.zip',
    '../H28_E0_RESULT/submission.zip',
):
    p = Path(name)
    print(name, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
PY
cmp artifacts/submissions/e0_official_control/submission.zip ../H28_E0_UPLOAD/submission.zip
cmp artifacts/submissions/e0_official_control/submission.zip ../H28_E0_RESULT/submission.zip
cmp ../H28_E0_UPLOAD/submission.zip ../H28_E0_RESULT/submission.zip
```

## Upload procedure and result: KAGGLE-OBSERVED

These are the operator's directly observed real Kaggle facts, supplied as text:

- The current live route was **Submit to Competition → File Upload**.
- The live UI said **Your submission should be named submission.zip.**
- The frozen upload copy was submitted directly as `submission.zip`.
- Kaggle created a submission record and launched its evaluation workflow.
- The final displayed status was **Succeeded** and numeric score was **0.05**.
- Observed end-to-end wall-clock duration was **approximately 4+ hours**.
- The operator downloaded the submission artifact after completion and placed
  it at `../H28_E0_RESULT/submission.zip`.

The proposed `kaggle/e0_submission.ipynb` materialization route was unnecessary
for this actual flow. Notebook preparation/output existence is not used as run
evidence. No second submission is required to review E0 and none was made.

## What was established, inferred, and remains unknown

**Established:** the operator-observed current hosted path accepted the frozen
official control and reached successful scored completion. Independent local
checks establish the sample's exact content, retained deterministic-build
identity, and the three-copy byte equality. The specific `kaggle_submission`
blocker for H28 is resolved.

**INFERRED:** Kaggle processed the upload through enough of its hosted workflow
to emit the displayed successful scored result. This is a run-specific operational
inference, not proof of each internal compilation/model/tool/scoring step.

**UNKNOWN:** hidden package/Python/model-server versions; internal scorer details;
hidden task count; whether every task executed; per-task outcomes; solved-task
count; exact inference latency; exact evaluation runtime; exact submission and
completion timestamps; submission ID; and the reason for score 0.05. Successful
scoring does not independently promote H27, H30, or other untested hypotheses,
and it does not prove the sample adapters influenced outputs.

**Score boundary:** 0.05 is E0's first technical baseline, **not an optimization
result**. Do not convert it into a number of solved tasks or attribute it to
budgets, architecture, adapters, tool behavior or any other untested cause.
Public DEV outcomes are not assumed to map exactly to hidden Kaggle outcomes.

## H28 matrix decision

`NOT-REPRODUCED → PASS`, scoped to the one identified E0 artifact and
operator-reported hosted completion. Replace `OWN-KAGGLE-RUN (none yet)` with
the dated, attributed real scored result using the existing authority convention;
retain `OFFICIAL-NOTEBOOK`. No new authority/schema vocabulary is introduced.
The existing OWN-KAGGLE-RUN convention is direct hosted-outcome evidence for
H28's claim; REPRODUCED-LOCAL alone could not establish it.

Scoped `PASS` was selected over `VERSION-SPECIFIC` because the exact H28 hypothesis was satisfied on the operator-reported successful Kaggle submission path reviewed on 2026-10-06; hosted internal versions remain **UNKNOWN**, and version invariance is not claimed.

Remove only `kaggle_submission`, set the precise raw-result path, and add this
curated report and explicit evidence boundary. Keep all other entries unchanged,
including H04/H05/H13/H14/H18/H26/H29 PASS and H30 NOT-REPRODUCED.
The certification README clarifies why hidden-version UNKNOWN does not prevent
this narrow hosted-path PASS while component-behavior certification still needs
its runtime versions. H28 is not a declaration that Step 0 is globally complete.

## Support history and next phase

Retain both pre-E0 support files with prominent historical/superseded notices.
Notebook code and frozen constants are preserved; explanatory Markdown now
identifies direct File Upload as the actual E0 route. The runbook retains its
original proposed procedure under a historical label so future operators do not
mistake it for the preferred current path. The older H30 readiness report remains
dated historical evidence; its pre-E0 NO-GO is superseded for H28 by this report,
without changing H30's literal hypothesis or certification state.

Next phase is competitive optimization **design**, followed by
`Claude Code — independent H28 Kaggle certification and competitive-plan audit`.
The design is in
`docs/experiments/COMPETITIVE_OPTIMIZATION_PROGRAM_2026-10-06.md`.
After that audit, the next implementation tranche is DEV evaluation/forensics
infrastructure and leakage isolation, before competitive prompt tuning. E0's
registry backfill must explicitly be retrospective and preserve missing fields
and original build provenance. No agent change, prompt tuning, training, GPU/model
experiment, second submission, commit or push is part of this pass.

## Validation

Validation used `.venv/bin/python` (Python 3.14.7, pytest 9.1.1, PyYAML 6.0.3).

| Command / check | Result |
|---|---|
| `.venv/bin/python -m pytest tests/test_tooling.py` | 18 passed in 0.18 s |
| `.venv/bin/python -m pytest` | 1679 passed in 20.12 s |
| `python3 -m tools.build_split --dataset-root ../gemma-4-developer-agent --check` | v1 unchanged; expected SHA256 matched |
| `git diff --check` | exit 0 |
| Matrix YAML compared to `git show HEAD:harness_cert/matrix.yaml` | only H28 differs; schema/status vocabulary and all other entries identical |
| Notebook before/after JSON comparison | all five code cells and metadata identical; only explanatory Markdown changed |
| `git diff -- agents/` | empty |

Only the existing matrix metadata test was extended for H28. Its prior explicit
PASS set and every local tool/budget/fallback evidence-boundary assertion remain,
with H28 added and an explicit H30 non-promotion assertion. Tests use CPU/fake
fixtures and do not certify Kaggle facts. No model/GPU experiment was run.
