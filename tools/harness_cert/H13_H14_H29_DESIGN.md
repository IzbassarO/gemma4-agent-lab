# H13/H14/H29 first-pass design, 2026-10-04

This is a design and source audit, not certification evidence. Matrix statuses
remain unchanged. The operator executes the actual probe; a curated certification
report is written only after independent reconstruction of a valid run.

## Baseline

The initial working tree was clean. Local HEAD, origin/main and a read-only
remote `refs/heads/main` query all returned
`12c319fa8bcf5b313175f80cee3ce06a77a59619`
(`cert(harness): reproduce H04 H05 H18 locally`). The previous dispatch probe
retains its reviewed `ea5b857487ae9e146e94a88e108962742fcbdef8` pin. The new
budget probe uses a separate fixed profile for the newly pushed HEAD, admitting
only specifically named probe implementation/tests/design files. Neither profile
accepts an arbitrary replacement baseline. No report/matrix change is admitted
by the new profile.

## Reconstruction completed before implementation

All three matrix entries require local harness source, permit synthetic local
testing without a model, GPU or Docker, and currently name `harness_source` as
their blocker. The installed and source-wheel-verified environment now makes
that source available. Their current authority is OFFICIAL-HARNESS; it is not
runtime reproduction.

| Case | Exact title and hypothesis | Status / risk | Required evidence and existing notes |
|---|---|---|---|
| H13 | **timeout patch recovery**: “When max_time expires without submit_patch, fallback extraction preserves the working-tree diff and it is verified.” | NOT-REPRODUCED / high | Successful edit, real session deadline, no submission, recovered returned diff, official Phase 2 verification. Minimal repro: edit then sleep past max_time_minutes, inspect patch and Phase 2. Note: also test timeout firing mid-tool-call. |
| H14 | **tool-budget patch recovery**: “Exhausting max_tool_calls without submit_patch still yields a verified fallback patch.” | NOT-REPRODUCED / high | Budget exhaustion, exact rejected-call response, prior edit, no submission, recovered diff, official Phase 2 verification. Minimal repro: budget 3, edit call 1, spend calls 2–4. Note: record budget-exceeded response visible to model. |
| H29 | **get_status / submit_patch free-call behavior**: “Both remain callable when tool_calls_used == max_tool_calls.” | NOT-REPRODUCED / medium | Both actual tool bodies succeed at exhaustion, counters stay at limit, status reports remaining zero, submission is returned. Minimal repro: budget 2, spend two calls then status and submission. Note: determine whether termination prevents another model turn. |

For H13/H14, valid positive evidence must include official `verify_task` running
real pytest in a fresh synthetic sandbox and validating JUnit. Phase 1 recovery
alone is incomplete. A valid negative observation is a different budget/timeout
outcome, lost/incorrect fallback, or failed verification attributable to the
fixture/runtime. Infrastructure/observer failure, an unexpected termination
mechanism or absent evidence is inconclusive, never a behavioral failure or PASS.
For H29, a valid negative is an observed inability to call either free tool at
the limit; incomplete dispatch/accounting evidence is inconclusive.

## Source observations (exact installed versions)

Paths below are relative to the acquired Python 3.12.14 site-packages. Preflight
compares every Python source in the five v28 source wheels to its installed file.

- swegemma 0.2.7 `tools/base.py:72–107`, `context.py:467–550`: budget gating
  precedes the body; rejected attempts do not increment counted calls.
- `tools/execution.py:76–132`: status and submission are free counted calls;
  free calls still obey the time gate. Submission records an explicit diff/state.
- `harness/agent_runner.py:498–660`: the outer exhausted-tool-budget check occurs
  after ADK's invocation finishes, rather than immediately after a tool response.
- google-adk 1.36.1 `flows/llm_flows/base_llm_flow.py:862–875`: an invocation
  continues after tool responses. This predicts H29 reachability within that
  continuation; a final-text boundary can terminate before a new invocation.
- `tools/function_tool.py:221–238`: synchronous standard tools execute directly
  on the event loop. A sleeping synchronous tool does not prove immediate async
  cancellation mid-tool. This tranche's H13 experiment uses an async model wait;
  mid-tool cancellation remains unknown and is not silently certified.
- swegemma `harness/agent_runner.py:741–795`: inner timeout recovery precedes
  fallback extraction. Outer fatal exceptions at 797–812 bypass that extraction.
- `context.py:276–298`: LLM accounting counts completed model events, which can
  differ from admitted HTTP requests when a response is deliberately withheld.
- `evaluate.py:324–378`: a nonempty recovered patch proceeds toward verification
  despite an agent error. The probe invokes official Phase 1 and Phase 2 directly
  and avoids evaluator task hydration. That orchestration gate stays source-read.
- `harness/verification.py:223–260,307–353,482–556`: test specification is
  mandatory; the returned patch is applied to a fresh workspace; pytest and JUnit
  determine verification. No historical synthetic-mock bypass is used.
- adk-submission 0.2.12 `builders/llm.py:62–82`: explicit YAML declarations
  select actual tool objects. Each HTTP request must advertise exactly its case's
  declaration. No competitive agent is read or loaded.

These are source observations and predictions; no H13/H14/H29 run has occurred.

## Minimum experiment and controls

All files are synthetic: app.py contains `MARKER = "before"`, and an immutable
test asserts `MARKER == "after"`. Expected app diff is the previous 134-byte
before/after diff. Every case preserves pre-cleanup workspace, returned patch,
explicit submitted state/patch, trace, HTTP wire evidence, original/recovered
exceptions, preflight, script hash and artifact inventory.

| Case | Script / budget | Expected requests / accounting | Expected termination / patch |
|---|---|---|---|
| CONTROL | write, submit, final; budget 3 | 3 HTTP/LLM events; 1 counted write plus free submit | No agent error; workspace = returned = submitted expected diff. Official verifier negative baseline fails, returned-patch positive passes. |
| H13 | write, withhold response to request 2; 0.05-minute session | 2 admitted HTTP requests; 1 completed LLM event; 1 counted write | Real inner TimeoutError; no submission; fallback equals workspace; official verifier passes. The held response is discarded after cancellation and is never represented as delivered. |
| H14 | budget 3; write AFTER three times, attempt BEFORE write, final | 5 HTTP/LLM events; 4 attempted tools; 3 executed/countable writes | Attempt 4 gets exact BudgetExceeded JSON and cannot undo the edit; runner budget error; no submission; fallback equals workspace; verifier passes. |
| H29 | budget 2; write twice, status, submit, final | 5 HTTP/LLM events; 2 counted writes plus 2 successful free tools | Status sees used 2 / remaining 0; submission succeeds; no agent error; explicit submitted = returned = workspace diff; fallback is absent. |
| H29_BOUNDARY | budget 2; write twice, final; further status/submit responses available | 3 HTTP/LLM events; 2 counted writes | Invocation ends before consuming available free calls; budget error; no submission; fallback expected diff. This control scopes reachability. |

Phase 2 support is a separately pinned, hashed pure Python closure from the
repository's existing pytest installation, staged before installing the parent
audit hook. Only those exact regular files are copied into the synthetic
verification sandbox's isolated venv. No host site-packages root, .pth file,
wheel directory, package installer, system-site-packages setting or network
permission is added. Plugin autoload is disabled and inherited pytest settings
are cleared. The subprocess runtime drops inherited pytest environment variables;
the verifier observer adds the exact inline `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`
assignment to the already admitted official pytest command, recording both
original and executed commands. Every verification manager has separate observations; it cannot
overwrite the agent workspace evidence.

The support versions are pytest 9.1.1, iniconfig 2.3.0, packaging 26.3,
pluggy 1.6.0 and Pygments 2.21.0. Its 501-file manifest SHA256 is
`3694df089cbd36a34d0d79ae5cb5ac7bbb67963aef1250661377400a10263b76`.
Preflight preserves the individual file hashes as well as this aggregate pin.

The old timezone, caller-specific urllib3 IPv6 detection, exact null sink,
wheel refusal, protected data paths, loopback-only transport, proxy/offline,
Authlib/AutoFlow smoke and exact command/copy guards remain required.

## Unknown boundaries and next steps

A valid run can establish only the installed local scripted Phase 1/Phase 2
path. It says nothing about Kaggle packages, callbacks, hidden recovery,
workspace visibility, score impact, real Gemma, vLLM, or evaluator paths not
actually invoked. Timeout firing during a synchronous tool remains unknown.

Short deterministic tests validate the implementation. The user then executes
the certification command. Raw artifacts must be reconstructed before any
matrix promotion or certification report. No commit/push is authorized here;
Claude Code independently reviews the eventual changes before commit.

## Implementation validation (not certification)

The short CPU-only repository suite passed: `1537 passed in 19.08s` using
`.venv/bin/python -m pytest`. This includes admission, scripted transport,
budget evidence, fake-runtime artifact, exact verification-command and support
tamper tests. A read-only actual repository admission check accepted exactly
the ten intended tranche files. `git diff --check` passed. A separate read-only
source audit found no P0/P1 readiness blocker. No actual certification case,
real model, evaluator hydration, competition data access, matrix promotion,
certification report, commit or push was performed.
