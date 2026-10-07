# DEV-E0-FORENSICS-V1-S1 preregistration — 2026-10-07

**Status: PREREGISTERED, NOT RUN.** Registry row `EXP-20261007-001` (`record_kind: plan`).
This is the first forensic experiment of the competitive-optimization phase. It
runs the **unchanged E0 official control** on the **12 frozen S1 tune tasks**
and records per-task evidence. It changes no agent, prompt, budget or adapter,
trains nothing and authorizes no Kaggle submission. It cannot start until the
real-runtime admission tranche in
[REAL_RUNTIME_TRANCHE_2026-10-07.md](REAL_RUNTIME_TRANCHE_2026-10-07.md) is
implemented, independently audited and merged, and the operator launches the
GPU session. Agents never launch model/GPU runs.

Relation to the program: B3 defines F0 as E0-local on all 80 tune tasks. S1 is
the operator-chosen first slice. S1 results count toward F0 only if a later run
of the remaining 68 tasks uses byte-identical candidate, runtime identity,
budgets, task seed policy and sandbox; otherwise F0 is a separate experiment.

## 1. Frozen identities

| Item | Value |
|---|---|
| Experiment | `EXP-20261007-001` / `DEV-E0-FORENSICS-V1-S1` |
| Parent | none (root forensic control) |
| Candidate | `submission.zip`, 443572 bytes, SHA256 `25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3` (frozen E0; `artifacts/submissions/e0_official_control/submission.zip`, `H28_E0_RESULT/submission.zip`, byte-identical) |
| Artifact build commit / review checkpoint | `199c25943b287b5e58b4067976880a9d53254f7e` / `3c6133ea9a1d9440183bfacc72b9f6e823dc6208` (distinct identities, per D014) |
| Split | `eval/splits/v1.json` SHA256 `420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12` |
| Screen manifest | `eval/splits/screens_v1.json` SHA256 `a6bdb7e9f99c3cbfd59be4130884c3dadcd99d4cfb6a91260bbfcdb1f71832f7`, stage `S1` |
| Public source | `tasks.jsonl` SHA256 `e4b3fd60f69dbc2b9213e54eeb9636db78aefe92c1d06269d73d9f5f8f3c8ad6` |
| Task order | lexicographic `instance_id`, as stored in the manifest; concurrency 1 |
| Sampling seed | none forwarded by the harness; recorded as `seed_forwarding: unsupported` (B4) |

E0 bundle members (sha256 of bytes inside the frozen ZIP):

```text
agent.yaml                              c7fbbbbdc44778419be8e9f53a85800eeb17d0cf151e0846c46e107401b72034
configs/sampling.yaml                   3dab0b2506dae34ce92fef7b380d6073729104b23fe2cf66d44ac1d2f213aa6a
eval_config.yaml                        adb486b67535fa59b427b79d44dafc131176f1f1964d184e16046ed520ede02d
prompts/system.md                       f1cbf7943ee9687382321b1dfd9cd88581d608c1c8b8e6ea61a763bd1a3234af
prompts/analyzer.md                     c762b062032cc8463015e589ad8b4f0e17296062e1b0ff1e78712eef9f5e2d43
sub_agents/code_analyzer.yaml           7a764798e36cc68aa38900256245e4aed969b5439b992c7da4105b6ebcb7e62b
adapters/*/adapter_config.json          75a46da2db7f3c70442e5c728f64059aff52cf64174cee7aeb3a4ec37f6e78fb (both)
adapters/*/adapter_model.safetensors    dcbedd989af34f5201a39606e0bd4014d351a29162dd96da418782cbbe487ad9 (both, 217672 B)
```

Declared E0 configuration (inspectable facts, not causes): `max_time_minutes: 1`,
`max_tool_calls: 10`, `max_turns: 50`, `timeout_seconds: 60`; temperature 0.2,
top-p 0.95, `max_output_tokens` 16384, `thinking_budget` 4096,
`include_thoughts: true`; root agent with nine tools plus `agent_tool`
(`code_analyzer`, `skip_summarization: true`); adapters `main_lora` (root) and
`tool_lora` (analyst), byte-identical rank-4 (L04).

### The 12 S1 tasks

| `instance_id` | repo | base commit | snapshot MB | problem chars |
|---|---|---|---:|---:|
| fastapi_14186 | fastapi/fastapi | 6e49dc0295 | 219.9 | 1009 |
| fastapi_14372 | fastapi/fastapi | 566e3157a5 | 224.5 | 1160 |
| fastapi_14794 | fastapi/fastapi | 464c359bb0 | 254.8 | 816 |
| fastapi_14978 | fastapi/fastapi | 94a1ee749e | 273.0 | 112 |
| fastapi_15763 | fastapi/fastapi | 9a9c4ad5d0 | 326.9 | 1064 |
| fastapi_9555 | fastapi/fastapi | 4976568fc7 | 228.0 | 2934 |
| httpx_3672 | encode/httpx | 4acf5c2c37 | 1.6 | 243 |
| requests_7502 | psf/requests | 661970d171 | 37.5 | 67 |
| rich_3468 | Textualize/rich | ae15db6009 | 92.8 | 146 |
| rich_3676 | Textualize/rich | 2b5d648b2c | 94.4 | 42 |
| rich_3777 | Textualize/rich | 49117ced66 | 95.2 | 341 |
| rich_4079 | Textualize/rich | 19c67b9a34 | 97.1 | 71 |

Public assets for S1 total about 2.0 GB (snapshots, graphs, embeddings) plus
`wheels/` (27.8 MB), `sandbox/setup.py` and the full `tasks.jsonl` (2.0 MB; the
admitted loader checks the whole-file hash). No hints are non-empty.

## 2. Question and hypotheses

Question: **where in the pipeline does E0 lose each S1 task, and which losses
are addressable by runtime/budget, policy, or architecture changes?** The
public score 0.05 identifies nothing per task; S1 supplies the first
task-level evidence. S1 is 12 tasks: one task is 8.33 points. It is a
diagnostic slice, not a rate estimate.

Hypotheses are ranked by prior plausibility from inspectable configuration and
certified harness behavior (H13/H14/H29). Each lists the S1 fields that
discriminate it. None is assumed true before the run.

| ID | Hypothesis | Prediction if true | Discriminating evidence |
|---|---|---|---|
| H-E0-1 | **Session-time starvation.** The 1-minute session budget ends most sessions before an edit lands; a 4096-token thinking turn on a 31B model can approach or exceed 60 s. | Majority of tasks terminate with `Agent exceeded session timeout (1 min)`; ≤2 completed LLM turns; fallback diff empty; `NO_PATCH` primary with `AGENT_TIMEOUT` terminal. Resolved tasks, if any, have an edit before ~45 s. | terminal reason, completed vs partial LLM events, elapsed at first edit, fallback used, returned-patch bytes |
| H-E0-2 | **Tool-call budget (10) exhaustion** on tasks that survive the minute. | `Agent exceeded tool call budget (10 calls)` terminal reason; edits present but untested. | counted tool calls, terminal reason, test commands before termination |
| H-E0-3 | **Analyst delegation burns the budget.** The root delegates to `code_analyzer` first; analyst reads consume shared time/calls before any root edit. | AgentTool invocation precedes first edit; analyst wall time is a large share of the session; root never reaches `edit_file`. | trace: analyst events, time in analyst, calls before first edit, H20 AgentTool naming/behavior as observed |
| H-E0-4 | **Output/thinking truncation.** `MAX_TOKENS` finish reasons or unclosed tool-call tags trigger nudges and waste turns. | `finish_reason` length/max-tokens events; nudge prompts in trace; partial tool calls. | finish reasons, nudge count, unclosed-tag detection |
| H-E0-5 | **Adapters degrade or are inert.** The identical rank-4 layer-0 adapters change behavior or do nothing. | Not discriminable in S1 alone. | requires the later no-adapter control (R0); S1 records adapter routing only |
| H-E0-6 | **Policy quality when the agent does act.** "Submit immediately" prompt yields wrong or incomplete edits. | For tasks with a nonempty patch: apply succeeds but targeted tests fail; wrong file or wrong diagnosis. | verifier outcome, changed paths, post-run labelled tune-gold comparison (recorded) |
| H-E0-7 | **Environment artifacts.** Sandbox dependency install or protected-file reset failures (L05, L08, L12) produce false negatives. | Known-patch control fails or no-patch control passes on some repo. | preflight controls (section 4), verifier logs, exit codes |

The 4+ hour observed E0 wall-clock is not evidence for any hypothesis: hidden
task count and concurrency are unknown (H22).

## 3. Design and fidelity

Single arm, no comparison, no change. One attempt per task; a second attempt is
allowed only after a diagnosed platform/environment failure and is recorded as
attempt 2 with the first attempt retained.

Execution environment: Google Colab Pro, Linux x86_64, one assigned GPU, the
official harness v28 source wheels in a Python 3.12 venv, the official
`--sandbox subprocess` backend (no Docker on Colab), vLLM in a separate venv
serving `gemma-4-31b-it-qat-w4a16-ct` with E0's two adapters mounted. The
assigned accelerator is recorded before anything else; **the run is aborted if
VRAM < 40 GB** or if the server cannot start with `max_model_len 32768`.
Reducing context length is not permitted (it changes runtime identity).

| Dimension | Scorer (README, HOST-UNKNOWN) | This run | Fidelity note |
|---|---|---|---|
| Model / parsers | 4×L4 TP=4, `tool_call_parser gemma4`, `reasoning_parser gemma4`, `enable_thinking: True`, `gpu_memory_utilization 0.80`, `max_model_len 32768` | 1 GPU TP=1, same parsers/flags, argv generated by `adk_submission.VllmServer.build_cmd()` with discovered adapters | numerics and latency differ; throughput differs. Record observed vLLM version and argv. |
| Budgets | `inference.py` loads `eval_config.yaml` → 1 min / 10 calls / 50 turns / 60 s | identical values passed to `EvalConfig` from the bundle's `eval_config.yaml` | mapping per README §7.1; scorer loader not captured |
| Compaction / cache / retries | compaction 5/2/14336/5, cache min 2048, retry ×5 | README values passed through `EvalConfig` if the native runner consumes them; otherwise recorded `not_applied` | README-documented, not runtime-verified |
| Sandbox | Docker `python:3.13-slim`, 4 GiB, 2 vCPU, no network | official `SubprocessManager`, Colab Python, dataset `wheels/` (cp312 and cp313 both present), dataset `sandbox/setup.py` | no memory/CPU caps; network present but the agent prompt declares offline. Record observed Python. |
| Verification | fresh Container B, 4-pass apply, protected reset, `test_patch`, pytest+JUnit | native `verify_task` in a fresh verifier process and fresh sandbox; required tests extracted from `test_patch` (no F2P/P2P fields, L06) | same code path as the local CLI; scorer internals unknown |
| Phase separation | one evaluator process holds gold in memory | solver process has only the public view; private test bytes are read after the solver is reaped | stricter than the official CLI by design |

## 4. Preflight controls (verifier-only, before any agent run)

Run on the smallest S1 task per repository: `httpx_3672`, `requests_7502`,
`rich_3468`, `fastapi_14186`.

| Control | Input to verifier | Pass criterion |
|---|---|---|
| no-patch | empty patch | `resolved == false`, pytest exit ≠ 0 or required tests not passed, no apply error |
| known-patch | the task's public reference `patch`, read on the trusted side only and passed directly to the verifier phase | `resolved == true` |

Both must pass for every repository before the S1 agent runs are interpreted.
A failing known-patch control marks that repository's S1 outcomes
`indeterminate` with `ENVIRONMENT_VERIFICATION_ARTIFACT` until repaired; it
does not count as agent failure. The known-patch control is evaluator-side gold
use and is recorded in `gold_assisted_diagnostics` with the four IDs. No human
or designing model reads those patches.

Stop rules: model server start failure (`MODEL_SERVER_START_FAILURE`), any
P0 (gold exposure in solver evidence, corrupted returned patch, contaminated
verification), or a failed control that cannot be diagnosed in-session stops
the run. Partial evidence is archived and reported as such.

## 5. Per-task record and classification

Every assigned task gets a record, including tasks never started. The sealed
`TaskRuntimeResult` plus native artifacts (ATIF trace, runner logs, command
observations, patches, verifier output, JUnit) are the raw evidence; the
normalized record follows the B3 groups. Unobserved values are `null` with a
reason, never zero.

| Group | Fields (minimum) |
|---|---|
| Identity | experiment/run/attempt, `instance_id`, repo, base commit, split/screen hashes, task order index |
| Outcome | `resolved` tri-state, verifier status/error class, required tests extracted, patch nonempty, returned patch sha/bytes, changed paths, apply status, explicit `submit_patch` observed, fallback used, terminal reason |
| Time | setup, session (agent loop), verification, total; start/end UTC; elapsed at first edit if observed |
| Model | LLM turns (completed vs partial), finish reasons, nudges, prompt/completion/reasoning tokens from trace if present, truncation flags |
| Tools | ordered tool names; attempted vs counted vs executed; free `submit_patch`/`get_status` calls; undeclared-tool attempts; error classes |
| Tests | commands, exit codes, targeted vs broad, before/after edit |
| Graph | graph tool calls, result sizes, no-match/ambiguous, calls before first edit |
| Delegation | analyst invocations, analyst turns/tools/time, recommendations used, duplicate reads |
| Forensics | primary `Failure` (earliest failing stage), secondary annotations with evidence pointers, confidence, gold-assistance flag, observation limits |

Rules: primary classification by earliest failing layer using the existing
`eval.failure_taxonomy.Failure`; secondary annotations per the B3 mapping table;
`UNKNOWN` stays `UNKNOWN` when evidence is insufficient; a resolved task has
primary `null` and keeps its terminal flags; budget/timeout flags may coexist
with a resolved outcome. Platform and verifier-environment failures are never
agent-quality failures.

Gold policy: the solver sees only the public view. After the run, tune
reference `patch`/`test_patch` may be opened for labelled diagnosis of
specific tasks; every such opening is recorded with task IDs and purpose. Holdout
is untouched. A post-run automated scan of all solver-phase evidence for added
lines of the task's `test_patch` and `patch` must report zero hits; a hit is a
P0 and invalidates the run.

## 6. Reporting and what follows

Report assigned 12, started, verified, resolved, unresolved, indeterminate, by
repository; terminal-reason histogram; per-task record table with primary
category and annotations; elapsed-at-first-edit distribution; analyst share of
session time; 2–3 trace excerpts per dominant failure mode (public text only).

Decision tree after S1 (operational, not statistical):

1. If ≥8 of 12 end by session timeout with ≤2 completed LLM turns and no edit,
   H-E0-1 is the leading operational cause. The next experiment is the B5
   shared-budget control **R0** (no adapters, 40 calls / 5 min / 50 turns /
   60 s) on the same 12 tasks, preregistered as `EXP-...-002`. Architecture
   candidates A/B/C are compared against R0, not E0.
2. If most sessions complete but end in `NO_PATCH` with analyst time dominant,
   H-E0-3 leads; R0 is still the next control, and candidate A (single root,
   analyst off) becomes the first architecture arm.
3. If nonempty patches are common but fail verification, H-E0-6 leads;
   labelled tune-gold diagnosis is scheduled for those task IDs, and policy
   principles in B6 are prioritized over budget changes.
4. If controls or runs expose environment artifacts (H-E0-7), the runtime is
   repaired and the affected tasks rerun as attempt 2 before any inference.

S1 cannot: estimate the hidden score, attribute 0.05 to a cause, promote or
reject any candidate, or resolve H20/H22/H23/H27. It can: fix the runtime
identity for later paired comparisons and rank where E0 loses tasks.

## 7. Launch checklist

1. Real-runtime tranche merged on `main` after independent audit; clean tree;
   commit recorded as `eval_infra_commit` in the registry row at launch.
2. Operator session: GPU identity, VRAM, driver, Python, vLLM version, harness
   package versions, argv, model revision and file hashes recorded to
   `fingerprint.json` before any task.
3. Preflight controls pass per repository.
4. S1 executed sequentially via the admitted driver with a plan-bound
   preregistration hash; evidence archived under
   `artifacts/dev_runtime/EXP-20261007-001/` and copied off the session.
5. Forensic classification on the Mac; report in `docs/experiments/`;
   registry `result` and `decision` records appended; `decisions.md` entry.

Expected cost: one suitable operator-controlled Linux GPU session of roughly
3–4 hours if Colab assigns an accelerator satisfying the admitted hardware rule
(setup, model download, 8 control verifications, 12 tasks at about 5–10 minutes
each). This
is an envelope, not a commitment.
