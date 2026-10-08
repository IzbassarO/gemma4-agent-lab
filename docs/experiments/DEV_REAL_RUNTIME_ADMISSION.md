# Real Linux DEV runtime implementation — 2026-10-07

Implementation record for section A of
[REAL_RUNTIME_TRANCHE_2026-10-07.md](REAL_RUNTIME_TRANCHE_2026-10-07.md).
This is local CPU implementation evidence for a fresh independent Claude Code
review. It is not a launch verdict. No real Linux task, model, GPU, DEV, Kaggle,
or S1 execution was performed.

Starting HEAD and `origin/main` were
`6c3a9ff004e900804477349b98b6dff83b6e0fda`; the starting worktree was clean.
The implementation is represented by the working diff and new files against
that baseline. Git history remains authoritative for any later user commit.
No staging, commit, or push was performed by the implementer.

## Implementation map

| Tranche | Implementation and reference | Admission behavior |
|---|---|---|
| A1 | `eval/runtime.py:46` `RuntimeConfig`; `eval/worker_common.py:36` `validate_request`; `eval/real_contracts.py` | Separate closed `real_public` schema, explicit loopback `/v1`, frozen budget, explicit compaction or null, worker account, preregistration, exact E0; synthetic fields remain rejected in real mode and real fields in synthetic mode. |
| A1 | `eval/runtime.py:577` `run_task`; `eval/runtime_real.py:258` `inspect_official_snapshot` | Public repository/ID admission and matching solver/verifier identities; no private node lists or broad loader. Contract hash/size remains authoritative at copying, validation, and native use. Official Git metadata is preserved and inventoried, including `.git/index` presence. |
| A2 | `eval/runtime_linux.py`; `eval/runtime.py:235` `_prepare_worker` | Privileged Linux coordinator, dedicated non-root worker account, resolved `runuser -u USER --`, `sudo -u USER --` only if runuser is absent, actual protected-root probes, fresh 0700 worker roots owned by the worker. |
| A3 | `eval/runtime_real.py:627` `solver_execute_real`; `eval/runtime_real.py:748` `verifier_execute_real` | Optional native imports follow closed admission; exact safe E0 extraction, pinned native source/lock, config equality, model and both adapter identity checks, native runner/verifier calls and returned-patch authority. |
| A4 | `eval/verifier_data.py:110` `publish_private_test_patches`; `eval/verifier_data.py:192` `read_reference_patch` | Trusted-only no-overwrite publication of 0600 single-link private files under a 0700 root; snapshot refs use the same public admission primitive. Reference reads are verifier-control only. |
| A5 | `eval/runtime_provenance.py`; `eval/runtime.py:527` `_real_provenance`; `eval/runtime.py:558` `_read_real_fingerprint` | Both phase fingerprints mandatory and sealed; source HEAD/dirty/digest, candidate, preregistration and normalized endpoint identities cross-checked before task-result acceptance. |
| A5 | `tools/dev_eval.py` | Six closed commands: fingerprint, serve-argv, preflight, run, leak-scan, report. Frozen ordered S1 only, concurrency one, sealed attempts and strict resume, eight controls, disk admission, P0 stopping and honest partial accounting. |
| A6 | `harness_cert/locks/harness_linux_x86_64_py312.lock`; `eval/runtime_real.py:332` `validate_harness_environment` | Generated Linux Python 3.12 support lock; exact 83 pins, hashes, installed versions and five pinned source-wheel Python source sets checked. |

The existing synthetic adapter, fixtures, public data loader, staging contracts,
result contracts, event semantics and existing tests remain unchanged. Workers
dispatch to the real adapter only for `real_public`.

## Solver and verifier boundaries

The solver receives its closed public task, contract-bound snapshot/graph/
embedding copies, frozen candidate, official public setup/wheels, pinned harness
source wheels, lock and staged runtime code. No dataset rows, test patches,
reference patches, private paths, inherited private descriptors or parent
environment values are passed to it. Trusted intake and verifier modules are
absent from solver staging; isolated import tests exercise both phase packages.

Trusted intake may publish the requested test-patch files before execution.
The per-task coordinator does not load their bytes for verification until the
solver exits, descendants are killed and reaped, evidence is archived, and the
solver root is removed. Linux uses subreaper adoption and worker-UID process
inventory as well as process-group cleanup to catch detached descendants.
Any cleanup failure stops before private loading. Verification uses a new
root/process and the sealed native returned patch; workspace diffs are
diagnostics and cannot replace that patch.

Runtime sources have a coordinator identity map and digest. Each phase carries
an authoritative staged-path subset. The worker checks the exact staged code
set and every hash/size against that authority; dropping or rewriting the
on-disk inventory does not establish a new authority. Real code namespaces are
0700 and files are fresh 0600 copies.
Staged code and in-process instrumentation run under the worker UID. These
source checks establish the admitted bytes at worker startup; they do not
provide continuous write protection or tamper-proof telemetry.

`real_fingerprint.json` supplements the existing immutable `RuntimeFingerprint`
without changing its synthetic schema. Both phase artifacts and both existing
result fingerprints are mandatory. Rich fingerprints agree on source,
candidate, preregistration, endpoint, public assets, Linux lock and observed
confinement. The existing result uses an opaque credential-free loopback label;
the companion records the normalized non-secret URL.

Evidence writers scrub authentication keys, headers, bearer tokens, credential
URLs and secret-shaped strings; they never dump parent environment values.
Immutable native patch text is refused if credential-bearing, rather than
rewritten. Worker stdout/stderr is an untrusted temporary spool, sanitized
before export; failed workers export only a sanitized log if no artifact
inventory was sealed. Valid real archives require sealed artifact identities
and secret-free content, refusing unsealed additions.

Known-patch controls create no solver process or synthetic solver result.
Their roots have `GOLD_ASSISTED_VERIFIER_ONLY.json`, explicit exclusion flags,
sealed verifier results and separately sealed control criteria. All control
roots are excluded from normal reports/comparison; known-patch roots are also
excluded from designer and solver inspection. The scanner performs separately
recorded trusted post-solver inspection, never exporting reference lines.
Effective private `test_patch` hits are P0; `patch_overlap` alone is diagnostic and is not evidence
that private/reference bytes crossed the solver boundary. Interrupted solver
archives must have an admitted run identity and ledger association or scanning
fails closed.
Scanning covers raw bytes and decoded JSON/JSONL strings and keys, including
nested serialized payloads and duplicate object keys. Per-line counts use the
larger raw or decoded occurrence count to avoid adding duplicate views.
Findings contain line hashes, artifact identities and counts, never private
lines.
Before scanning, the trusted side excludes candidate private lines whose exact
bytes occur in the task's original admitted public snapshot. The run manifest
retains the original solver contracts before launch; snapshot SHA256/size and
available run/phase contract identities are checked against that authority.
Per-run diagnostics record unique candidate, public-excluded and effective
private needle counts. Zero effective private needles produce
`scan_sensitivity: "none"`, which is not evidence of an absent private leak.
The heuristic can miss compressed or non-UTF8 encodings and reflowed or
transformed lines. Raw non-UTF8 bytes are still searched for exact needle bytes;
the scanner does not unpack arbitrary compressed payloads or normalize other
encodings and transformations.

## Fidelity and actual Linux checks still required

Target: operator-controlled Linux x86_64, harness Python 3.12, separate vLLM
environment, one assigned GPU satisfying at least 40 GB VRAM, TP=1 and
`max_model_len=32768`. Colab Pro is a candidate host with nondeterministic
assignment; A100-class qualifies, L4/T4 abort. The VRAM rule is an engineering
threshold, not a measured memory peak.

User separation is not container isolation. There are no memory/CPU caps or
network namespace; sandbox commands can reach the network. Filesystem access
is governed by permissions. The operator must provision a dedicated
unprivileged system account with no administrative escalation rights. Protected
public/private/artifact roots and repository `.git` must actually deny that
account; protecting the repository parent also satisfies `.git` denial. The
harness interpreter and libraries must remain readable/executable separately.
Actual probes enforce denial and interpreter access before task staging.
The actual `--worker-root` parent must already exist as an unaliased directory
where the privileged coordinator can create/chown roots and archive/remove
them. The worker needs directory read and search permission through that
parent and every ancestor because the descriptor reader opens each ancestor.
That parent must be outside protected roots that deny the worker. No fixed
parent owner or octal mode is enforced by the implementation; each generated
root is recursively worker-owned and `0700`. Harness directories and ancestors
must permit read/search, with worker-readable libraries and an executable
interpreter. Section C of the operator runbook now states these prerequisites.

macOS real execution fails closed. Linux UID changes, runuser/sudo, kernel
subreaper behavior, actual native Linux dependency installation, GPU identity,
model/adapter serving and full real verification remain unexecuted here. CPU
tests mock those host operations or use fabricated public fixtures. A fresh
independent Linux review must validate the host and native path; no runtime
evidence or launch acceptance is inferred from mocks.

Native v28 `run_agent_sandbox` consumes the ADK compaction/cache objects; the
adapter records whether the configured objects were actually applied. Verifier
keywords match installed `Evaluator.evaluate_task`: patch/error/trace/start-time
and supported snapshot/patch-path extras. Real verification does not explicitly
pass `fast_path`; the unchanged synthetic control does.

Disk preflight inspects model, public, private, harness, vLLM, actual worker
parent, artifact and export storage. Reservations derive from observed tree
bytes, expanded admitted snapshots, candidate/public duplication and export
headroom. Reservations on one filesystem are summed; insufficient free bytes
fail closed. This conservative duplication envelope is not a measured peak.

## Linux lock generation

Lock: 199232 bytes, SHA256
`40c3b8a158eb4ab38289cfd3976759c7d2a5ceea6ddb91924f7121719d904c2d`.
Input was the committed `compiler-pins` and `harness-extra-pins` blocks in
`harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md` (83 pins).
uv 0.12.19 resolved metadata/hashes only; no packages were installed.

```sh
uv pip compile harness.in --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 --only-binary :all: \
  --generate-hashes --no-python-downloads \
  --default-index https://pypi.org/simple \
  --output-file harness_linux_x86_64_py312.lock
```

Only temporary input/output paths in generated comments were normalized. The
five v28 source wheels remain separate `--no-deps` inputs checked by their
recorded wheel hashes and installed Python source identities. vLLM is excluded
from the support lock and must remain in its separate environment.

## Explicit CLI inputs for the later operator

These describe the implemented CLI, not authorization to execute S1. The user
must first obtain the independent section-B launch verdict and perform Git
actions. No command below was used for a real run during implementation.

All commands require `--public-root`. Fingerprint/preflight/run additionally
require `--candidate`, `--prereg`, `--harness-python`, `--harness-root`,
`--harness-lock`, `--source-wheels-root`, `--model-path`, `--vllm-root` and
`--endpoint`. Fingerprint requires `--out`; dirty source can be recorded only
with `--allow-dirty`, and real execution still refuses dirty source.

Serve-argv requires candidate/model/vLLM/output/harness/lock/source-wheel roots;
it uses native `VllmServer.build_cmd()` and records the original argv, explicit
vLLM-venv Python executable substitution and exact executed argv. It keeps the
extracted adapter files under the ignored output root for the later operator
launch and starts no server. Run it with the admitted harness interpreter.

Preflight/run require `--private-root`, `--artifact-root`, `--worker-root`
(the actual temporary-worker parent), `--export-root`, `--worker-user` and
`--compaction` (a file containing the closed documented JSON object or null).
Preflight takes the exact four control IDs and `--control`; run accepts only
`--screen S1`. Attempt 2 requires selected S1 IDs and an admitted diagnosed
platform/environment repair. Earlier attempts and P0 invalidation are retained.
Run additionally requires explicit `--worker-timeout-seconds 3600`; preflight
defaults to the same per-phase infrastructure timeout. This does not change
E0's one-minute agent session, ten counted tool calls, fifty turns or
sixty-second command timeout. Attempt-1 resume preserves and skips recorded
terminal failures while continuing to never-started tasks. A valid higher
attempt seal also permits traversal; a dangling start without a terminal row
or later valid seal still refuses resume.

## Original local validation record (before independent audit)

Repository test interpreter: Python 3.14.7. Acquired native harness interpreter:
Python 3.12.14 on macOS. All new tests use deterministic fabricated data and
mocked host/model operations. Existing native certification tests use a local
scripted endpoint, with no model inference.

Combined focused CPU target: 315 collected, 315 passed, zero failed/skipped,
in 3.54 seconds.
The first combined run had two packaging-fixture initialization failures;
the fixtures were corrected to let real staging create its private code root,
then all 310 passed. Five additional JSON leak-scan regressions brought the
final focused target to 315. The existing tests were not edited.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest \
  tests/test_dev_runtime_real.py tests/test_dev_runtime_linux.py \
  tests/test_dev_runtime_real_native.py tests/test_dev_runtime_provenance.py \
  tests/test_dev_real_leakage.py tests/test_dev_eval_driver.py \
  -q -o addopts='' -p no:cacheprovider
```

Existing runtime/data/tooling target: 411 collected, 411 passed, zero
failed/skipped, in 504.81 seconds.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest \
  tests/test_dev_task_contracts.py tests/test_dev_manifests.py \
  tests/test_dev_public_data.py tests/test_dev_leakage.py \
  tests/test_dev_runtime_staging.py tests/test_dev_runtime_process.py \
  tests/test_dev_runtime_result.py tests/test_dev_runtime_adapter.py \
  tests/test_tooling.py -q -o addopts='' -p no:cacheprovider
```

Full CPU suite: 2387 collected, 2387 passed, zero failed/skipped, in 552.30
seconds.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest \
  -q -o addopts='' -p no:cacheprovider
```

Native-interpreter target: 24 collected, 24 passed, zero failed/skipped, in
509.10 seconds. The direct command initially exited before collection
because the volatile native environment lacked pytest. Test-only pure Python
copies of pytest 9.1.1, pluggy 1.6.0 and iniconfig 2.3.0 were placed under
`/private/tmp/gemma-native-pytest-feeer4r5` from the local repository venv.
The harness installation was not changed; isolated worker launches scrub
`PYTHONPATH`.

```sh
PYTHONPATH=/private/tmp/gemma-native-pytest-feeer4r5 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /private/tmp/h23-cpu.UA1C8T/harness/bin/python -B -m pytest \
  tests/test_dev_runtime_adapter.py -q -o addopts='' -p no:cacheprovider
```

No scientific results are generated by these fixtures. Checks requiring nested
macOS `sandbox-exec` ran with the execution sandbox escalation approved for
these CPU tests. There were no installs or model launches during validation.

Read-only scope checks compared 143 protected tracked-file SHA256 identities
with the baseline Git blobs: zero mismatches. Tracked changes are exactly the
five runtime/worker/verifier-data files; thirteen implementation files are new.
The Git index is empty. `git diff --check` passed, and all eighteen changed/new
files passed the trailing-whitespace/final-newline checks, including untracked
files.

Frozen E0 (443572 bytes, SHA256
`25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3`),
preregistration, registry, S1/S2/S3 membership, budgets, hypotheses, control
membership/pass criteria, decision tree, holdout discipline, agents, prompts,
sampling, adapters, notebook and existing certification evidence are unchanged.

Next gate: fresh Claude Code read-only review under section B. Only the later
independent verdict can authorize the user to launch preflight/model/S1 work.

## Narrow corrective pass after independent audit

The independent Claude Code findings supplied for this pass reported **0 P0
and 3 P1**. The auditor gave a commit-safety verdict while withholding operator
launch acceptance. The user requested these corrections before choosing to
commit. That verdict did not authorize the implementer to stage, commit or
push. The original passing CPU evidence above is retained as history; it did
not cover the three reproduced defects.

This pass began from the same HEAD and `origin/main`, with eighteen existing
uncommitted implementation paths and an empty index. New edits are limited to
`eval/runtime_provenance.py`, `tools/dev_eval.py`, their two focused test files,
this document and section C of `REAL_RUNTIME_TRANCHE_2026-10-07.md`. Section C
needed the explicit admitted timeout and host-permission prerequisites. No
other runtime source, frozen scientific file or previous certification evidence
was changed during this correction.

### P1 corrections and deterministic before/after evidence

**P1-1, ordinary URL rejection.** `_sanitize_string.clean_url` previously
removed every query and fragment. `assert_secret_free` interpreted that benign
mutation as authentication material, rejecting public requests and immutable
returned patches. The cleaner now preserves benign URL bytes, removes userinfo,
and matches percent-decoded, case-folded secret field names exactly. Fragment
anchors and following fragment query fields are both checked, including `?`
inside a credential value. Generic secret-value regexes and endpoint admission
are unchanged. Credential-bearing returned patches still refuse without being
rewritten.

Regression names in `tests/test_dev_runtime_provenance.py`:

- `test_benign_url_query_and_fragment_are_preserved`
- `test_credential_url_fields_are_redacted_and_refused`
- `test_credential_url_userinfo_is_redacted_and_refused`
- `test_credential_fragment_fields_with_question_mark_values_are_redacted_and_refused`
- `test_frozen_style_problem_with_benign_url_passes_real_request_validation`
- `test_ordinary_returned_patch_with_benign_url_keeps_native_bytes`
- `test_credential_bearing_returned_patch_is_refused_without_rewriting`

Before the fix, the primary selected regressions produced 8 failed and 3 passed
(11 selected from 101 collected, 90 deselected, zero skipped). The additional
fragment-value edge produced 2 failed and 2 passed before its correction
(4 selected from 106 collected, 102 deselected, zero skipped). Final owned
target: 106 collected/passed, zero failed/skipped. All fixtures use synthetic
strings; no frozen private/reference text was printed.

**P1-2, stranded resume.** `run_s1` previously refused any same-attempt ledger
history without a seal, conflating a dangling start with a terminal failure.
Attempt-1 traversal now preserves and skips a started/terminal-failed attempt
without rerunning it, then reaches later never-started tasks. An existing
attempt-2 seal is validated before historical attempt-1 checks. Dangling starts
without a terminal row or later valid seal still refuse. Ledger history,
diagnosed attempt-2 requirements and permanent P0 invalidation remain intact;
an old failure is not counted as success.

Regression names in `tests/test_dev_eval_driver.py`:

- `test_attempt1_resume_preserves_terminal_failure_and_reaches_later_tasks`
- `test_attempt1_resume_dangling_start_still_refuses`
- `test_attempt1_resume_dangling_start_with_valid_later_seal_skips`
- `test_attempt1_resume_refuses_invalid_later_attempt_seal`

**P1-3, public snapshot false P0.** Added test-patch lines were previously
treated as private without checking legitimate public occurrence. The driver
now persists the original admitted `SolverTask` contracts before solver launch.
Trusted scanning uses that exact snapshot reference and original SHA256/size,
checks available run/phase admission identities, and excludes lines already in
public snapshot file bytes. Diagnostics report unique total candidate,
public-excluded and effective private needle counts and the public snapshot
identity. No private needle text is exported. Effective private hits remain P0;
reference overlap remains diagnostic. Zero effective needles record
`scan_sensitivity: "none"`; no clean-private-boundary proof follows from that
value. JSON/JSONL, nested serialized strings, duplicate keys and raw-byte
scanning remain supported.

Regression names in `tests/test_dev_eval_driver.py`:

- `test_private_line_absent_from_public_snapshot_still_triggers_p0`
- `test_public_snapshot_line_is_excluded_from_private_leak_p0`
- `test_mixed_public_private_needles_only_private_evidence_triggers_p0`
- `test_all_candidate_private_needles_public_records_no_sensitivity`
- `test_snapshot_filter_refuses_bytes_changed_from_admitted_contract`
- `test_snapshot_filter_refuses_changed_admission_contract_map`
- `test_public_snapshot_needle_crossing_read_chunks_is_excluded`
- `test_unsealed_archive_filters_original_admitted_snapshot`
- `test_filtered_private_lines_preserve_nested_json_and_duplicate_key_scan`
- `test_run_s1_persists_original_solver_contracts_before_worker_launch`

The selected driver regressions before production edits produced 10 failed and
2 passed (12 selected from 69 collected, 57 deselected, zero skipped). This
selection reproduced terminal-failure blocking, public-line false P0,
missing authority/sensitivity diagnostics and the missing required CLI timeout.
One pretty-JSON-versus-JSONL fixture error was corrected before that valid
pre-fix reproduction. Final owned driver target: 75 collected/passed, zero
failed/skipped.

### Launch-relevant P2 hygiene only

P2-A: `run` requires explicit `--worker-timeout-seconds 3600`; `preflight`
defaults to 3600. `test_real_s1_cli_requires_explicit_3600_infrastructure_timeout`
checks omitted/incorrect values refuse and the admitted value passes. This is
per-phase process infrastructure. Frozen E0 evaluation values remain
`time_minutes=1`, `tool_calls=10`, `turns=50`,
`command_timeout_seconds=60`.

P2-B: section C and the host-prerequisite paragraph above now state actual
protected-root denial, repository `.git` denial, worker-parent/ancestor
read-search access, coordinator creation/chown/cleanup access, and readable/
executable harness requirements. No fixed worker-parent mode or owner is
invented; generated roots retain the implemented worker ownership and `0700`.

P2-C: the current scan description reflects public-line exclusion and records
residual compressed/non-UTF8 and reflowed/transformed-line blind spots. Zero
effective private needles are explicitly insensitive, rather than proof of an
absent leak.

### Corrective validation record

Required gates run in order: changed-boundary regressions, complete focused
real runtime, existing runtime/data/tooling, then the full CPU suite.

| Target | Collected | Passed | Failed | Skipped | Seconds |
|---|---:|---:|---:|---:|---:|
| Changed boundaries | 272 | 272 | 0 | 0 | 2.61 |
| Complete focused real runtime | 392 | 392 | 0 | 0 | 3.83 |
| Existing runtime/data/tooling | 411 | 411 | 0 | 0 | 343.49 |
| Full CPU suite | 2464 | 2464 | 0 | 0 | 390.93 |

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest \
  tests/test_dev_runtime_provenance.py tests/test_dev_eval_driver.py \
  tests/test_dev_real_leakage.py tests/test_dev_runtime_real.py \
  -q -o addopts='' -p no:cacheprovider

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest \
  tests/test_dev_runtime_real.py tests/test_dev_runtime_linux.py \
  tests/test_dev_runtime_real_native.py tests/test_dev_runtime_provenance.py \
  tests/test_dev_real_leakage.py tests/test_dev_eval_driver.py \
  -q -o addopts='' -p no:cacheprovider

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest \
  tests/test_dev_task_contracts.py tests/test_dev_manifests.py \
  tests/test_dev_public_data.py tests/test_dev_leakage.py \
  tests/test_dev_runtime_staging.py tests/test_dev_runtime_process.py \
  tests/test_dev_runtime_result.py tests/test_dev_runtime_adapter.py \
  tests/test_tooling.py -q -o addopts='' -p no:cacheprovider

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest \
  -q -o addopts='' -p no:cacheprovider
```

Final read-only scope checks confirm exactly the six corrective paths above
changed from the starting working tree; the other 155 tracked/new file
identities match that starting state. All 142 protected tracked files match
the committed baseline. The operator specification's sections outside C are
unchanged. E0 remains 443572 bytes with its frozen SHA256. Current Git status
contains six modified and thirteen untracked implementation paths, with an
empty index. `git diff --check` and explicit whitespace/EOF checks over all
nineteen current changed/new paths pass. No staging, commit or push occurred.

### Deferred P2 and Linux-host remainder

Deferred without changes: environment-evidence redesign, model-identity timing
redesign, ValueError/ContractError taxonomy, repeated dataset hashing
optimization, endpoint-normalizer refactoring, separate `not_started` summary
accounting, same-UID telemetry hardening and duplicate-constant style cleanup.

Actual Linux account/protected-root/worker-parent permissions, runuser or its
admitted fallback, descendant/subreaper cleanup, native dependency installation,
GPU/VRAM admission, model/adapter serving and real verification remain
unestablished on macOS. Existing CPU tests use deterministic fixtures/mocks;
they do not establish host acceptance. No real model, GPU, DEV, Kaggle or S1
workload ran. S1 is still not run. No launch verdict is claimed.

Next gate: fresh read-only Claude Code delta audit of these corrections and the
remaining Linux-host acceptance requirements. Staging, committing and pushing
remain user actions.
