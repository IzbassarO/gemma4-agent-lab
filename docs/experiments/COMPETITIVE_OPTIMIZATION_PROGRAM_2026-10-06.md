# Competitive optimization program — 2026-10-06

This is a design for the phase after independent H28 certification review. It does not implement an agent, change a prompt, run a model, train an adapter, or authorize another submission. The frozen repository checkpoint is `3c6133ea9a1d9440183bfacc72b9f6e823dc6208`. E0 is the technical control: user-observed Kaggle status `Succeeded`, score `0.05`, approximate observed wall-clock of 4+ hours. That observation does not identify per-task results, solved-task count, scorer versions, or the cause of the score.

Grounding: `docs/competition/official_facts.md`, `eval/splits/{v1.json,SPLIT_POLICY.md}`, `tools/build_split.py`, `docs/experiments/EXPERIMENT_PROTOCOL.md`, `eval/failure_taxonomy.py`, `harness_cert/matrix.yaml`, the H04/H05/H18 and H13/H14/H29 certification reports, the H27 local component report, and the local official `HARNESS_README.md`. The [current official overview](https://www.kaggle.com/competitions/gemma-4-developer-agent/overview) describes issue-resolution scoring and a 12-hour inference allowance including setup, with validation outside it. This does not resolve hidden scheduling/concurrency (H22) or identify hidden package versions (H23).

## B1. Objective and controls

Maximize real issue solve rate under the competition runtime. Local paired resolution rate is the selection instrument; Kaggle is scarce external confirmation. The public DEV rate is not a calibrated prediction of the hidden score. Report both task-weighted resolution and per-repository counts; a macro-average can be diagnostic but does not replace the competition objective. Never optimize a platform exception or a verifier-environment failure as though it were an agent-quality failure.

Two controls are needed later:

- **E0-local:** the exact official-control bundle, including its supplied adapters, analyst and published budgets. Run it unchanged for first diagnostics. It is not a no-adapter baseline.
- **R0:** a future no-adapter control with inherited official architecture and declared shared tournament budgets. Establish H27 startup and the runtime bridge first. Record every difference from E0; do not attribute the E0-to-R0 difference to one mechanism when several fields differ.

The official sample currently declares `max_time_minutes: 1`, `max_tool_calls: 10`, `max_turns: 50`, command `timeout_seconds: 60`, temperature `0.2`, top-p `0.95`, output limit `16384`, thinking budget `4096`, and `include_thoughts: true`. These are inspectable configuration facts, not an explanation of `0.05`. They motivate controlled budget and architecture experiments. E0's build provenance and later frozen repository checkpoint must remain separate identities.

## B2. Preserve the existing split and admit its evaluator

Reuse **v1: 80 tune tasks / 49 local holdout tasks**, approximately **62.02% / 37.98%**. Do not invent a new split or reshuffle existing membership. The repository already designates v1 locked in D006 and its split policy. Membership remains frozen/locked; leakage isolation and evaluator admission have not yet been demonstrated by a competitive DEV pipeline. Competitive prompt tuning starts only after the admission checks below pass. This distinction does not revoke the existing lock or claim that historical isolation has been audited.

| Repository | Public tasks | Tune (`dev`) | Holdout | S1 | S2 |
|---|---:|---:|---:|---:|---:|
| `fastapi/fastapi` | 67 | 41 | 26 | 6 | 20 |
| `Textualize/rich` | 48 | 30 | 18 | 4 | 15 |
| `psf/requests` | 13 | 8 | 5 | 1 | 4 |
| `encode/httpx` | 1 | 1 | 0 | 1 | 1 |
| **Total** | **129** | **80** | **49** | **12** | **40** |

There is only one HTTPX task (`httpx_3672`). It exercises the fourth repository in tune and both screens; it cannot appear in both partitions without leakage. Holdout estimates new tasks within three represented repositories, not unseen-repository generalization. Five Requests holdout tasks and one HTTPX tune task cannot support strong repository-level statistical claims.

The deterministic split seed is the string `gemma4-agent-lab/split/v1`. Project onto `instance_id`, `repo`, `base_commit`; group by base commit; allocate 49 holdout slots by per-repository largest remainders; sort groups by `sha256(seed + ':' + base_commit)` with commit tiebreak; greedily fill each quota without splitting groups. There are 127 unique base commits, 79 tune / 48 holdout. `requests_6589` and `requests_6629` remain together in tune; `rich_3882` and `rich_3894` remain together in holdout. No base commit crosses the split. Cross-commit issue similarity and possible base-model pretraining contamination remain unmeasured.

```text
tasks.jsonl SHA256:       e4b3fd60f69dbc2b9213e54eeb9636db78aefe92c1d06269d73d9f5f8f3c8ad6
v1.json SHA256:           420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12
input projection SHA256: 5a6188365392b2febde6d3067f5fea9e8c5dddcd7389a6e02a87e4333245df30
```

This pass regenerated v1 in memory from the available dataset's non-gold projection using `python3 -m tools.build_split --dataset-root '../gemma-4-developer-agent' --check`; it reported unchanged membership and the expected hash. No reference patch/test patch was inspected for this design. All 129 task IDs are recorded in the existing manifest; the complete proposed memberships and screening IDs are also recorded in Appendix A.

For S1 and S2, use the new screening seed `gemma4-agent-lab/screen/v1`. Within each repo's existing tune partition, group by base commit and sort groups by `sha256(seed + ':' + repo + ':' + base_commit)`, then commit. Greedily include a group if it fits the table's exact quota. The concrete memberships in Appendix A are nested, S1 ⊂ S2 ⊂ tune. Validate quotas and nesting before publishing a future screening manifest; fail rather than silently change membership when inputs differ. Category labels may be derived afterward from tune problem statements for diagnostics, but do not change membership because results or difficulty look inconvenient.

Evaluator admission, before any competitive tuning:

1. Reproduce dataset/split hashes, all 129 unique IDs, exact repository counts, complete snapshot/graph lookup, and disjoint base commits. Keep dataset read-only and use `tools.common.WriteGuard` for outputs.
2. Audit existing experiment history for holdout reference exposure; record known exposures and uncertainty. A discovered leakage channel needs a documented methodological review, not an automatic reshuffle. Do not attest that holdout is pristine without evidence.
3. Construct an **agent view** containing only task ID, repository/base identity, problem statement and non-gold hints, plus a pristine snapshot and base-commit graph assets. Keep gold `patch`, `test_patch`, raw `tasks.jsonl`, evaluator fixtures and result directories inaccessible to the agent and its shell/sub-agents. Enforce the same boundary for shared caches, temporary paths, prior result artifacts and analyst/root task context. Omitting fields from the prompt is insufficient if the raw dataset is mounted in its workspace.
4. Give only the evaluator its gold view. Verify patches in a separate fresh sandbox; never reuse the solver workspace. Verify that published test patches are introduced only after inference. The public rows lack explicit FAIL_TO_PASS/PASS_TO_PASS fields: certify real public task hydration and required-test construction instead of guessing those lists or using a hand-written `resolved` rule. **Static source risk:** independent source review of captured `swegemma 0.2.7` found `evaluate.py::evaluate_task` calls `_hydrate_task_from_secret` before the actor phase and builds context from the hydrated task; missing snapshots can also trigger secret-bundle discovery. Redacting fields and calling that evaluator unchanged is insufficient admission. Build a public-only loader, fail closed on missing public assets, disable secret hydration/discovery, and separate actor-redacted Phase 1 from evaluator-only Phase 2 entrypoints. Test these boundaries with synthetic sibling `secret` fixtures. This is a source-observed risk, not a claim that a leak occurred or hidden data was read; existing direct synthetic probes did not exercise that orchestration.
5. Prove the boundary with synthetic sentinel gold, an agent attempting filesystem access, agent-visible request/trace inspection, no-patch and known-correct-patch controls, and fresh-workspace assertions. Include paths, arguments, errors and logs, not just schema fields.
6. Holdout exports contain only allowed non-revealing aggregates and predeclared per-task fields. Restrict raw holdout patches, traces, tool arguments/output, test names and verifier logs to automated evaluation; designers must not mine them for fixes. An automatic harness-stage category is allowed, but unobserved quality causes remain unknown rather than inferred from hidden gold.
7. Record admission version, tests, config/hash, exposure attestation and date. Only then mark the evaluator admitted to the existing locked membership and schedule E0 diagnostics. No unreviewed architecture/prompt work runs ahead of this gate.

Published tune `patch` / `test_patch` may be used for labelled **post-run offline diagnostics**; record exact IDs and derived material. They must never be fed to the solving agent on an evaluation task. Holdout gold remains evaluator-only, including derived answer-revealing artifacts. Tune gold access is not permission to encode task-specific answers into policy.

## B3. First forensic experiment and failure schema

After CPU infrastructure admission and later authorization of an appropriate exact-Gemma runtime, preregister **F0: frozen E0-local on all 80 tune tasks**. Start with two selected tune tasks for runtime health, then the rest under the same immutable config; those two runs count toward the 80 if task seed, source, runtime and configuration match. Freeze the baseline before policy edits. Do not run all 129 for exploratory debugging. Holdout parent measurements are automated, isolated and used only for the eventual veto gate.

Every assigned task gets a record, including tasks never started, setup failures, interrupted sessions and incomplete verification. Do not improve the reported rate by dropping hard tasks. Report assigned total, started, verified, resolved, unresolved, and indeterminate separately. A platform or verifier-environment failure makes promotion inconclusive until repaired/repeated under a declared rule; it is not silently scored as agent success or excluded from the denominator.

| Record group | Required fields |
|---|---|
| Identity | experiment/run/attempt IDs, `instance_id`, `repo`, `base_commit`, split/hash, task seed; optional problem category plus derivation version and confidence |
| Outcome | tri-state `resolved` (`true/false/null`), verifier status/error class, required-test summary, patch generated/nonempty, returned patch hash/bytes, changed paths, patch-apply status/pass, explicit submission success, fallback extraction, terminal reason |
| Patch boundary | agent workspace diff hash if observed, submitted/returned diff hashes, extraction method, apply and verification sandbox IDs; compare bytes when available, do not invent unobserved workspace state |
| Time | setup, session, verification and total wall time separately; model-wait/tool/test/retry time when measurable; start/end UTC, timeout scope |
| Model | root and per-analyst LLM turns, completed vs partial events, retries, nudges, prompt/completion/reasoning tokens if observed, finish reasons, truncation and effective model/sampling settings |
| Tools | ordered names; declared names; attempted/admitted/executed/responded/error calls; authoritative counted usage; args/result byte sizes; free status/submission calls; undeclared-tool attempts and fatality; exact error classes |
| Tests | command attempts/results/exit codes, targeted vs broad scope, before/after/repair cycles, tests absent/blocked, time/call budget remaining at test and submit |
| Graph | each tool/query/symbol, result sizes, resolved/ambiguous/no-match status, source confirmations, calls before first edit, retrieved-to-edited correspondence, graph routing decision and fallback |
| Context/delegation | compaction events if observable, context estimate/measurement and basis, repeated reads/actions, analyst starts/turns/tools/tokens/time, analyst recommendations used, summary sizes, read-only violations |
| Forensics | primary existing `Failure` value/layer, independent annotation list, evidence pointers, confidence, classification status, gold-assistance flag/IDs, observation limits |

Normalize official `summary.json`, `task_results.jsonl`, patches, ATIF-compatible traces, agent logs, test outputs/JUnit and runner counters without replacing raw artifacts. Preserve exact bytes and an inventory. Missing metrics are `null` with a reason, not zero. H14 shows counted admission increments before a tool body, so counted calls are not just successful tools. H04/H18 show invalid lookup can fail before counting. LLM turns, HTTP requests, attempted tools and counted tools are separate quantities.

Keep the existing earliest-failing-layer primary taxonomy (`eval.failure_taxonomy.Failure`) and add **secondary diagnostic annotations**, rather than breaking old enums or forcing simultaneous symptoms into one exclusive bucket:

| Requested annotation | Existing primary category when established; interpretation |
|---|---|
| `NO_PATCH` | `NO_PATCH`; identify no edit, reverted edit, failed edit or extraction separately |
| `WRONG_FILE`, `NAVIGATION_FAILURE` | `LOCALIZATION_WRONG` if supported; failed navigation with no edit may primarily be `NO_PATCH` |
| `UNDERSTANDING_FAILURE` | `ROOT_CAUSE_WRONG`; requires actual evidence about diagnosis |
| `WRONG_EDIT`, `INCOMPLETE_EDIT` | `ROOT_CAUSE_WRONG`, `EDIT_FAILED`, `TARGETED_TEST_FAIL` or another evidenced category |
| `NO_TEST` | secondary behavior; a task may resolve without an agent test, or tests may be unavailable |
| `TEST_FAILURE_NOT_REPAIRED` | `TARGETED_TEST_FAIL`/`OVERFIX_REGRESSION` if verified; distinguish agent-run failure from verifier failure |
| `PREMATURE_SUBMIT` | secondary causal hypothesis; submission alone does not establish prematurity |
| `TOOL_ERROR` | error event; use `TOOL_SERIALIZATION`, `EDIT_FAILED` or an evidenced primary category if terminal |
| `UNDECLARED_TOOL_FATALITY` | `INVALID_TOOL_FATAL`; record exact advertised, declared and attempted names |
| `BUDGET_EXHAUSTION`, `TIMEOUT` | `TOOL_BUDGET_EXHAUSTED`/`AGENT_TIMEOUT`; can coexist with a nonempty recovered, even resolved patch |
| `CONTEXT_FAILURE` | `CONTEXT_OVERFLOW` when established; compaction information loss needs separate evidence |
| `GRAPH_UNDERUSE`, `GRAPH_MISUSE` | secondary research labels; zero graph calls alone is not underuse; symbol/no-match/large-output misuse can be observed |
| `DELEGATION_FAILURE` | secondary process label with analyst/root trace evidence; record its earliest terminal layer |
| `PATCH_APPLY_FAILURE` | `PATCH_MALFORMED_OR_APPLY_FAIL`; distinguish malformed diff, base mismatch and environment problems |
| `OTHER` | described annotation with evidence; never an empty catch-all that conceals unknown cause |

Retain all existing platform, harness, contamination, syntax, behavioral, regression and environment categories. `UNKNOWN` remains valid during analysis; it prevents keep/reject/promotion under the existing protocol. Insufficient evidence yields `inconclusive`, not a guessed root cause. Nonterminal budget/timeout/tool flags can appear on resolved tasks without falsely making them quality failures. On holdout, do not open forbidden logs to satisfy classification; unresolved unknowns make the gate inconclusive.

For a resolved task, primary `Failure` is `null`; no causal failure diagnosis is required. Preserve terminal error/symptom annotations independently. For a genuinely failed or indeterminate task, do not treat that null as a completed classification. The baseline report should rank lost outcomes by evidenced stage, then feasible interventions. For each proposed policy change, cite counts and 2–3 tune-only trace examples, plus counterexamples. Treat graph/delegation causality as a tournament question until the ablations answer it.

## B4. Compute-efficient funnel

Use the same model/runtime, effective config, task seeds and order policy within each paired comparison. A version or config change invalidates automatic reuse. Predeclare the first seed `20261006` and replication seed `20261007`; record unsupported/non-effective seed forwarding instead of calling the result deterministic. Fix task-order permutation separately; interleave or randomize candidate run blocks to expose time/runtime drift.

| Stage | Candidate ceiling | Task set | Gate |
|---|---:|---:|---|
| S1 | A/B/C (3), plus R0 | 12, exact Appendix A | At least one additional solve versus R0; zero new fatal-tool/compile/serialization failures; no increase in no-patch count; cost guard below. At most two advance. A tie without a net gain is inconclusive, not a promotion. |
| S2 | ≤2 plus R0 | 40, nested | At least two additional solves (+5 points) versus R0; paired gains/losses and repo counts recorded; no new P0, no increased fatal/harness/no-patch failures. Select one candidate before holdout. |
| S3 | One selected candidate plus R0 | All 80 tune | At least three additional solves (+3.75 points) on first full run, no increased harness failures/no-patch; reproduce on all 80 with the second seed, positive solve delta in each run, pooled delta ≥6/160. Complete classification and provenance. |
| S4 | Selected candidate plus parent only | All 49 locked holdout | One confirmatory run per config; no regression in resolved count versus parent's admitted result, no increased harness failures, no P0. Never compare sibling candidates on holdout. |

Initial cost guard: advance only if measured total session/model time is ≤1.25× parent on the same tasks, or if solve gain is at least 20% relative and solves per measured model-hour improve. For a zero-solve parent, use the absolute solve gate and model-hour cap, not division by zero. Candidate C receives no extra session/call allowance. Test and setup time are reported separately; hardware GPU-hours come from measured allocation time, never assumed token prices or E0's approximate wall-clock.

S1/S2 are noisy elimination screens, not statistical proof. One of 12 is 8.33 points; one of 40 is 2.5; one of 80 is 1.25; one of 49 is 2.04. Publish paired gain/loss tables and uncertainty intervals; account for the two shared-commit pairs and repeated-task observations (group/paired bootstrap if used). Do not describe this sample as powered for small improvements. Keep selection and confirmation data roles explicit. If the gates produce no winner, revisit tune evidence with a new preregistered hypothesis; do not consume holdout or submit to break ties.

At the stated ceilings, the first architecture sweep requires **212 unique task-config assignments**: R0 full tune 80 + winner 80 + second candidate through S2 40 + third through S1 12. Immutable nested-stage reuse avoids rerunning S1/S2 unnecessarily. Full paired replication adds 160, and parent/candidate holdout adds 98: **470 task-config assignments** in total. E0's initial 80-task forensics is separate when its config differs. Up to three 12-task runtime/budget bridge pilots add 36, for a planned ceiling of **586** before optional ablations. This is a design accounting ceiling, not promised GPU-hours, and includes no runs in this pass.

Stop early on a new P0, repeat only after diagnosis, and ledger invalidated runs. A 12-task one-change ablation should need at most 12 incremental assignments if its immutable parent data can be reused; promotion of an ablation still needs the same larger-stage gates. Do not launch a Cartesian product of every budget, graph, analyst and thinking knob.

## B5. First architecture tournament

The first tournament is no new training, and preferably no adapters at all after full H27 startup admission. All agents use `gemma-4-31b-it-qat-w4a16-ct`. Hold a common shared budget fixed across A/B/C/R0: proposed pilot ceiling **40 counted tools / 5 session minutes / 50 turns / 60-second command timeout**, with observed forwarded sampling held at E0's settings. Freeze the exact shared budget after the tune-only bridge pilots, before screening; if it changes, rerun affected controls. These are local experimental limits, not claims about hidden throughput or an optimal submission budget.

| Candidate | Intended advantage and structure | Likely failure | Additional cost | Metrics and required ablation |
|---|---|---|---|---|
| **A — Conservative Solver** | One root solver; preserve all nine built-in declarations initially, but use the six repository/file/execution/lifecycle tools. Localize, form hypothesis, make bounded edit, targeted test, repair, review diff, explicit submit. It supplies the clean architecture reference. | Mislocalizes cross-module issues; broad command output truncation; spends scarce budget testing prematurely. | No intended analyst/graph call overhead; same graph schema tokens as B; potentially more root reads. | Localization calls/time, tests/repairs, no-patch and explicit/fallback return; compare inherited-policy single-root control vs lifecycle policy after infrastructure, rather than attributing every A/R0 difference to one clause. |
| **B — Graph-Guided Solver** | Same built-in declarations as A; change use policy to adaptively routed symbol retrieval, bounded neighborhoods and selected subgraphs, with source confirmation before edit. | Unresolvable natural-language embedding query; ambiguous symbols; irrelevant/large neighborhoods; tool/context waste. | Initial graph allowance at most 3 counted calls per routed task, within the same 40-call total. No graph service or second model. | Graph-call yield, source-confirmed candidates, navigation-to-first-edit, tokens and solve cost; graph-use disabled, symbol retrieval only, retrieval+neighbors, full selected subgraph sequence with schemas held fixed. |
| **C — Analyst + Solver** | A root delegates one bounded localization/analysis request to a read-only analyst, which returns source-grounded recommendations. Root owns all edits/tests/status/submission. Analyst initially declares `read_file` plus the three read-only graph tools, while graph use is off; graph-use policy is a later controlled extension. | Duplicate reads; unsupported analyst advice; root over-trust; budget/context drain; missing output or undefined AgentTool. | One analyst call, proposed ≤6 analyst counted read calls and ≤20% of session time, included in shared total after H20 counter admission. It uses the same base model. | Root/analyst calls/tokens/wall time, recommendations used and duplicate navigation; analyst off (A), same analyst contract with summarization on/off only after H20, one delegation vs repeated delegation. |

R0 retains inherited architecture so E0 continuity is measurable; A versus R0 is deliberately a compound architecture comparison. B versus A isolates graph-use routing and C versus A isolates analyst delegation. C refines an analyst architecture already present in E0/R0; it does not introduce delegation to the project for the first time. A B+C hybrid is not in the first tournament. Admit H20's exact AgentTool name, `skip_summarization` behavior and shared-budget accounting before C and before quality comparisons relying on R0's analyst; deny edit/write/submit tools to the analyst and do not give it unrestricted shell access disguised as read-only analysis. The six-read/20%-time analyst limits are initial behavioral targets unless admission establishes supported hard enforcement; do not invent per-agent budget fields or custom submission code. If H20 cannot be admitted, C is deferred and A/B may undergo local diagnostic screening against a separately declared single-root control, without claiming the full R0 tournament gate passed or C failed scientifically. Tool-removal experiments are separate: H30 shows task hints can advertise graph tools independently of declarations, while H04/H18 show missing declarations can be fatal. Certify schema/hint alignment before attempting a six-tool schema arm; first graph ablations keep declarations fixed and measure unintended graph use as policy noncompliance.

## B6. Policy principles to evaluate later

These are general candidate principles, not a replacement competitive system prompt:

1. Restrict every action to the tools actually advertised and declared. H04/H18 reproduced fatal invalid-tool behavior; inventing `finish`, an undeclared writer or an implicit shell tool is a runtime issue.
2. Return the useful patch through declared `submit_patch`, then require an observed success response. Final prose and workspace edits are separate states. Record explicit submission and official fallback; do not score a timeout with a recovered correct patch as zero merely because submission was absent.
3. Use `get_status` at stage transitions and near the reserve, not after every read. Free tools still incur model/time overhead and require time left.
4. Maintain a recovery reserve: initially **4 counted calls and max(30 seconds, 10% of session allowance)** for a focused test/repair/diff/submission sequence. Below reserve, stop optional graph/delegation/exploration. A task with less than the reserve available should use its best recoverable patch promptly, with test limitations recorded. Test the reserve instead of assuming it is optimal.
5. Run the smallest meaningful available test before submitting when budget permits, repair a clearly relevant failure, and distinguish test setup failure from code failure. Put temporary repro material in `/tmp`; preserve protected tests/configuration. Do not train the agent to exploit verifier resets.
6. Keep hypothesis, file/symbol locations, edit state and latest test outcome concise and explicit enough to survive compaction. Require source confirmation for graph/analyst recommendations; do not copy guessed names directly into edits.

F0 determines priority among these principles. Certification establishes runtime vulnerabilities and recovery boundaries, not the solve-rate effect of a future policy.

## B7. Graph strategy and ablations

All 129 public tasks have nonempty (>100-byte) base-commit graph/embedding files, with 127 unique graph/embedding pairs. Availability does not prove useful retrieval or graph-tool benefit, and H30's literal broad hypothesis remains separate from the actual >100-byte advertisement predicate.

Route using only issue text and ordinary repository navigation seen by the agent:

```text
Explicit file/symbol and localized behavior
  -> bounded repository search/read -> hypothesis -> edit/test

Ambiguous location or cross-module behavior, or two unsuccessful localization searches
  -> identify a real symbol via repository search
  -> search_similar_code(symbol, k=5)
  -> get_code_neighbors(confirmed_symbol, max_neighbors=10)
  -> optional get_code_subgraph(3-6 selected symbols)
  -> confirm actual source -> edit/test
```

The official README says `search_similar_code` resolves a stored node-key/suffix and compares its existing embedding: it is **not an offline natural-language embedding service**. Use a real class/function/module symbol, not a free-form issue description. A no-match/ambiguous response triggers ordinary search; do not repeatedly rephrase sentences. Inspect actual installed signatures and schemas at admission, including edge-type vocabulary, output size and truncation. Start with at most three graph calls total; graph calls consume counted budget.

Preregister: **G0** no intended graph use (A); **G1** symbol retrieval use only; **G2** retrieval + bounded-neighbor use; **G3** selected-subgraph use added; **G4** unconditional graph-first only as a challenger if adaptive routing shows promise. Keep declarations fixed; change one graph-use policy at a time on the nested tune screens. For each routed task, measure localization time/calls, selected-source correctness, graph output/context bytes, repeated queries, first-edit timing, end solve and model-seconds per solve. Evaluate both all-task paired outcomes and the routed subgroup, with routing fixed before seeing outcome; avoid reporting only successful graph uses. H21 response-size admission is required before graph output policies. Zero graph calls on a simple successful task is not graph underuse.

## B8. Context, budget and runtime experiments

H13/H14/H29 supply **local reproduced constraints**, not guarantees for all error paths:

- **H13:** an async model-wait timeout preserved a completed workspace edit via fallback and direct official Phase 2 accepted the returned patch. Synchronous mid-tool cancellation and full evaluator orchestration were not reproduced.
- **H14:** a fourth counted attempt was rejected before its body after three admitted writes; the existing edit survived through fallback and direct verification. Counting is admission-based, not successful-body-based. The model continued inside the invocation, but final text preceded outer exhausted-budget termination.
- **H29:** status and submit bodies remained callable at exhausted counted budget inside the ongoing invocation. After final text ended that invocation, the outer budget gate prevented another invocation. Free tools still obey the time gate; do not depend on a later free-call turn after exhaustion.

Therefore explicit submission must happen before a final-text boundary/time expiry; fallback is resilience evidence, not a policy target. Always report terminal session error and verified patch outcome independently.

| Study | Initial controlled values | Required observation/gate |
|---|---|---|
| Adapter/runtime bridge | E0 exact; then no-adapter equivalent on S1 | Full H27 real startup before no-adapter quality claims; identical architecture/budgets/sampling, exact adapter hashes recorded; no claim E0 certifies no-adapter startup |
| Counted calls | 10 vs 40, initially same 1-minute budget | Isolate call ceiling, see whether time makes the larger ceiling inactive; count admitted/rejected calls and useful edits |
| Session time | 1 vs 5 minutes with selected 40-call ceiling | Isolate time expansion; measure saturation, retries, time-to-edit/test/submit and pipeline elapsed; do not infer hidden task scheduling |
| Thinking | Later 0 / 2048 / 4096 at fixed output limit | H01–H03 request/parser/forwarding admission first; tokens/latency/truncation/solve. Do not call `include_thoughts:false` private reasoning; semantics are not certified |
| Output/context | Later 8192 vs 16384 output limit, same thinking | Observe effective request limits; combined prompt+generation must fit within 32768 with a recorded recovery reserve. Do not double-count thinking if included in engine completion tokens; measure payloads/truncated calls, finish reasons and retries |
| Turns | 50 vs 100 only if turn limit is observed binding | Changes invocation opportunity, not counted-tool budget. Separate LLM turns from tools; no automatic expansion of every ceiling |
| Command time | 30 vs 60 seconds on tasks with observed test/command timeouts | Command timeout is different from session timeout; one longer test can remove recovery margin |
| Recovery reserve | 2 vs 4 counted calls; time reserve 30s vs 60s separately | Fixed overall budget; test/repair/submit success, premature exits and no-patch outcomes |
| Test loop | One focused validation vs one additional relevant repair/retest | Same overall budget; measure repaired failures, newly introduced regressions and remaining submission margin |
| Context policy | Bounded reads/results vs concise state recap after edit/test | H15/H19 compaction sentinel admission; repeated reads, lost state and observed context. Compaction itself can use LLM calls/time |
| Delegation | Off vs one capped analyst; then summarization setting | H20 counters/return semantics admitted; shared ceiling, root/analyst latency and duplicate context |

The three initial bridge pilots are no-adapter, counted-call expansion, then time expansion, up to 36 S1 assignments; they are declared engineering bridges, not proof of an optimal budget. Stop after runtime failure and defer dependent studies. Later parameters form sequential one-change hypotheses driven by F0; do not cross every combination. Reuse immutable parent data and reserve at most one additional 12-task screening variant at a time.

The documented scorer context limit is 32768; command output is capped at 5000 characters and reads at 150 lines/10000 characters. Official README compaction settings are interval 5, overlap 2, threshold 14336 and retention 5. These are harness-side defaults, not submission YAML knobs; record actual local settings and label any modified local-only compaction stress run as infrastructure research. H15/H19 behavior and H01–H03 model-side reasoning behavior need their own scoped evidence. Local scripted mocks can test wiring, but cannot establish real Gemma solve rate or output semantics.

The Mac M1 Pro/16 GB environment is for CPU infrastructure, mock probes, schema validation and analysis. It is not an exact 31B model benchmark. Future performance runs need an authorized suitable GPU runtime, exact base model and recorded harness/container/server versions. Start concurrency at 1 for measurement, then independently test 2; do not equate local throughput with hidden scheduling. Use the documented 12-hour inference allowance as a packaging/runtime planning constraint, including setup; do not divide E0's approximate 4+ hours by an invented hidden task count. Until H22 is resolved, show conservative throughput scenarios and measured setup/inference costs, without claiming all hidden tasks will finish.

## B9. Kaggle submission ladder

| Milestone | Purpose | Required local evidence before consuming a submission |
|---|---|---|
| **E0 — done, 0.05** | Hosted acceptance of identified official technical control | H28 certification and artifact round trip; no task-level quality claim |
| **E1 — robust no-training agent** | Selected non-trained policy/architecture; prefer no adapters after H27 | Evaluator admission, F0 forensics, S3 improvement and full replication, S4 no regression, dependency-specific harness admission, runtime/provenance/packaging gate |
| **E2 — graph/context extension** | Only a separately evidenced architecture improvement over E1 | Same paired tune+holdout gate; graph/compaction or delegation dependency evidence; skip this submission if no useful independent advance exists |
| **E3 — selected architecture + adapter** | Test a trained adapter's added value | Data eligibility/deduplication, stable base policy, H16/H24/H25 startup/output effect/context, adapter hash, paired training ablation and full local gate |
| **Final candidate** | Freeze best locally justified artifact | Reproducible build/inference, dependency and regression checks, preserved fallback artifact, rules recheck and exact final-selection policy |

These labels are milestones, not a mandatory submission quota. Predeclare at most **three selected post-E0 candidate holdout checks**, one each for E1/E2/E3, plus one initial matched parent control. Skip unearned milestones. Final selection reuses the same frozen admitted candidate/result; it does not automatically spend another holdout check. A required matched-parent rerun after material runtime drift or an extra confirmatory check needs a documented methodological decision and amended use ledger before seeing results; it must not enable sibling fishing. Select locally and register the candidate before upload. Package compiler/static validation, archive inspection, size/extension checks and deterministic identity must all pass. No new P0 runtime issue or increased fatal/harness failure can be traded for solve gain. Preserve every scored ZIP; direct `Submit to Competition → File Upload` with `submission.zip` is the real E0 path established by the user. No notebook materialization is required by that evidence.

The existing experiment protocol's minimum requirements remain in force: complete full-v1 tune evaluation, strictly more solves than parent, zero unresolved `UNKNOWN`, one holdout run with **no regression**, and no increased harness failures. This plan tightens the solve/replication gates; it does not loosen holdout tolerance to “one acceptable lost task.” Rules (submission limits, final selections, external-data eligibility, deadline/license) must be rechecked from current official rules before relying on them; the local rules summary explicitly was not reverified. Avoid using Kaggle scores to select locally tied hyperparameters or repeatedly probing the same holdout after a veto.

## B10. LoRA roadmap and data integrity

Do not begin LoRA immediately after H28. First establish an admitted evaluator, a stable selected no-training policy, repeatable local solve gains, and a tune failure analysis identifying learnable behavior that policy/runtime fixes did not solve. Before training investment, complete **H16 startup**, **H24 adapter output-delta**, **H25 memory/KV/context capacity** on an appropriate current runtime. Official sample adapters in E0 are supplied controls, not evidence that a newly trained adapter is effective or safe.

Roadmap:

```text
Admitted evaluation and selected stable policy
  -> tune-only successful, failed/repaired trajectories with verifier outcomes
  -> corpus eligibility / provenance / deduplication / contamination checks
  -> small supervised SFT/QLoRA pilot
  -> same architecture, base-versus-adapter controlled validation
  -> optional preference learning or RL only if reliable rewards justify it
```

Useful targets include declared-tool selection, concise localization/planning, repair after a real test failure, grounded edits, avoiding repeated navigation, budget-aware lifecycle decisions and explicit patch submission. A failed trajectory is not a positive target without an evidenced repair or a correctly labelled negative preference. Successful solve alone does not justify copying long incidental thought or exposing reference answers to evaluation agents.

Use the provided public training tasks and **our tune-only trajectories** for the competition's published local-development/offline-evaluation purpose. Their public availability is verified; it is not a blanket license/redistribution determination. Before training ingestion, verify current competition/model/source eligibility and record terms/provenance. External SWE datasets, synthetic trajectories generated by other models, internet patches and redistribution of competition artifacts need eligibility checks; do not silently assume they are permitted. No hidden tasks, hidden results or scraped private tests enter training.

Training may consume allowed tune reference fixes as labelled supervision in an explicitly registered training experiment. Evaluation of those same tune tasks is then **in-sample** and cannot be presented as generalization. Within the 80 tune tasks, reserve a separate group-wise training-validation subset selected before adapter fitting, record its IDs/hash and exclude it from weights/preferences/reward fitting; membership must respect base-commit groups. A proposed inner split is 64 training / 16 validation (validation quotas: 8 FastAPI, 6 Rich, 2 Requests, 0 HTTPX; its sole task remains in training); verify exact group fit before freezing a later manifest. The 49 primary holdout remains untouched and evaluator-only. Training design and hyperparameters use only inner tune validation; require positive paired gain on that frozen excluded subset in both declared evaluation seeds before selecting an adapter. Full-80 evaluation still satisfies the existing protocol's complete-task operational gate and strict parent improvement, but its post-supervision gain is not generalization evidence. Inner validation measures incremental adapter benefit against the same fixed, previously tune-selected policy; it is not a fresh holdout for that policy. Only the locally selected adapter reaches primary holdout once as a veto.

Deduplicate by task/base commit and inspect source/task/patch similarity against a restricted automated holdout boundary. Log overlap counts and rejection without exposing holdout answers to designers. Disallow primary-holdout problem statements, solutions, test patches, trajectories, verifier outputs and derived preferences/rewards in the corpus. Honor existing exposure history and do not claim independence if an external corpus contains matching issues. Keep training records and reference material outside the committed agent/eval artifacts. Every adapter records base revision, training inputs/manifest, seed, hyperparameters, framework/runtime, output hash and checkpoint-selection rule.

Begin with one small adapter and test rank/memory cost, not several per-agent adapters. The documented serving limits are max rank 128, max 8 adapters and unpacked bundle <3 GiB; sizes and memory must be measured for the actual adapter/runtime. Preference/RL comes only after stable supervised benefits and reliable task/verifier rewards, with explicit reward-hacking and protected-test checks. No adapter is trained or even selected in this pass.

## B11. Persistent registry design

Extend, rather than replace, `experiments/registry.jsonl` and the current schema example/protocol. This pass writes no registry rows and does not pretend E0 was preregistered. Proposed `schema_version: 2` remains readable alongside current rows. Each hypothesis gets a pre-run record; immutable result/decision amendments reference it by ID, so crash/resume does not overwrite the scientific plan.

| Field/group | Proposed representation |
|---|---|
| Identity/linkage | `schema_version`, unique `experiment_id`, `parent_experiment_id`, `run_id`, `record_kind` (`plan/result/decision/retrospective`), timestamp, operator/evidence authority |
| Question | `hypothesis`, `single_change`, dependent H-IDs and their scoped evidence, stage, preregistration status, cost/stop/gate plan |
| Source | `git_commit`, `git_tree`, source clean/dirty attestation, artifact build commit, frozen checkpoint commit, external-source provenance and hashes; never fabricate clean-source proof |
| Configuration | `config_hashes`, explicit `agent_config_sha256`, `prompt_sha256`, `adapter_sha256` (per adapter or `null`), model/base revision, requested and observed sampling/generation settings, tool schemas and declared names |
| Evaluation | effective eval config and hash, runtime seed plus forwarding evidence, task-order seed, split/version/hash, exact ordered `task_ids`, subset manifest/hash, repeat/shard/resume policy, isolation admission/hash |
| Runtime | OS/arch/Python, container digest, GPU/type/count/VRAM, driver/CUDA, observed package/server versions or `null`+reason, wheelhouse fingerprint, concurrency, model startup/context/cache/compaction settings |
| Data boundaries | dataset hash, gold-assistance scope/IDs, tune/holdout access policy, corpus IDs/hash and eligibility record when trained, exposure/contamination admission, export policy |
| Aggregates | assigned/started/verified/resolved/indeterminate totals, task-weighted rate, per-repo counts, paired gained/lost IDs (tune only), fatal/tool/no-patch/apply/timeout/budget counts, runtime/token/model-hour metrics, repeated-run uncertainty |
| Per-task outputs | path/hash to normalized records, outcome schema version and exact task coverage; restricted holdout export instead of raw leakage |
| Artifacts | paths+SHA256 for raw official results, patches, traces, test logs/JUnit, normalized records, reports, compiled bundle, provenance, adapter manifest and submission ZIP; content inventory |
| Decision | `keep/reject/inconclusive`, concrete reason, passed/failed gate IDs, approver/audit reference, material limitations, next experiment; Kaggle record/result/operator evidence when present |

Unknown runtime facts stay `null` with evidence limits. Metrics missing from E0 cannot be backfilled from its aggregate score. Reject reused IDs, missing hashes, task-set mismatch, parent-config mismatch, unclassified decisions and silent rule changes. Validation should reconcile attempts, shard assignments and coverage, and distinguish interrupted from completed runs. Append atomically using the existing output guard; keep large/raw outputs in ignored artifact directories, with sanitized summaries in docs.

A future retrospective E0 row may record hosted acceptance/operator evidence and exact artifact bytes, but must retain the distinction between artifact build commit `199c25943b287b5e58b4067976880a9d53254f7e` and frozen review checkpoint `3c6133ea9a1d9440183bfacc72b9f6e823dc6208`. Its official-control provenance has `source_in_git: false` for external official adapters. Exact hidden task IDs/count/runtime versions are unknown. Record `preregistered: false` and operator provenance rather than inventing a pre-submission experiment, clean competitive source or task metrics. Existing registry example-only tests remain unchanged in this pass; update them only when real registry tooling/backfill is implemented later.

## B12. Promotion and rejection rules

Maintain the current protocol floor and the tighter S1–S4 table. A new candidate cannot reach Kaggle solely because its pooled score improved once. Full tune replication must show positive paired gain in each run; publish gained/lost counts, fatal/runtime outcomes and cost. The full holdout veto requires **no resolved-count regression**, no increased harness-layer failures, and no unknown decision outcomes. Parent holdout data may be reused only with an identical admitted runtime/eval config; otherwise a fresh matched parent is necessary and its limited use must be recorded.

No new P0 is acceptable: unsafe gold exposure, unresolved runtime/tool fatality, corrupted patch return, invalid/contaminated verification, model startup failure or packaging invalidity stops promotion. A no-patch-rate increase also blocks these initial gates. Strict numerical screens are operational selection rules, not claims of significance; small repo strata and one holdout measurement leave material uncertainty. A holdout veto rejects promotion or makes it inconclusive; do not inspect its answers, choose another sibling from it, or repeatedly retune until it passes. Further work returns to a separately declared tune hypothesis, with a documented holdout-use ledger.

Candidate-specific dependency admission is required (e.g. H27 no-adapter startup, H20 delegation, H21 graph sizes, H01–H03 thinking, H15/H19 compaction, H16/H24/H25 LoRA). H28 acceptance does not promote those items or resolve H22/H23. The repository's broader Step 0 requirements remain applicable; independent review must resolve a candidate's reliance on unadmitted behavior before any quality claim/submission. H30 remains separately NOT-REPRODUCED until its literal hypothesis has evidence; do not use the H28 success to promote it.

## Exact next implementation tranche after Claude approves

The next external reviewer is **Claude Code — independent H28 Kaggle certification and competitive-plan audit**. After its approval, implement the **CPU DEV evaluation and forensics infrastructure**, not competitive prompts. Proposed deliverables (paths are a future implementation plan, not files created here):

1. `eval/task_views.py`: frozen-v1 membership checks and safe agent/evaluator projections; dataset/source guards; refuse duplicate/missing IDs and gold mounts. Generate the screening manifest at `eval/splits/screens_v1.json` with Appendix A's exact IDs, seed, algorithm and hashes, without changing v1. Record evaluator admission separately at `eval/splits/v1_admission.json` only after tests and historical exposure review pass.
2. `eval/runner.py` and `tools/dev_eval.py`: explicit selection of tune/screen/holdout, compile/effective-config receipt, reproducible task ordering, public-only task/asset loading with secret hydration/discovery disabled, isolated agent and fresh verifier sandboxes, faithful official public-test construction, bounded concurrency, resume/attempt ledger, no-patch and known-patch synthetic controls. Default to dry-run/mocks on Mac; model/GPU execution requires the later runtime admission and authorization already outside this pass's scope.
3. `eval/forensics.py`: official-artifact normalization, per-task metrics in B3, preserved raw hashes, earliest-stage primary classification plus evidence-backed annotations, `null` for unobserved state, and restricted holdout exports. Keep the existing enum stable or add versioned extensions with compatibility tests; do not infer tool counts from successful responses alone.
4. `eval/registry.py` and versioned JSON schema: plan/result/decision validation, hashes and source identity, task coverage, parent pairing, append/resume safety, protocol-compatible gates, retrospective E0 backfill with unknowns/operator provenance if reviewed. Update the example-only test deliberately as part of that future registry change.
5. Focused synthetic tests covering sentinel leakage through prompt/shell/sub-agent/logs/shared caches, forbidden sibling-secret hydration/discovery and missing-public-asset fail-closed behavior, pristine verifier separation, no-patch and good/bad-patch verification, shared-commit isolation, S1/S2 nesting/exact IDs, coverage/resume accounting, H13/H14/H29-compatible patch outcome vs terminal-error handling, unknown/inconclusive behavior, taxonomy mapping, holdout export restrictions and provenance enforcement. Add a sanitized infrastructure admission report naming versions and limits.
6. Pass targeted infrastructure tests and full short CPU pytest, then freeze the evaluator/config/splits/exposure attestation. Only afterward preregister F0 E0-local forensics. Run later exact-model experiments in the admitted runtime; do not change `agents/`, train or submit during this infrastructure tranche.

Acceptance: all 129 IDs accounted for; exact 80/49 and 12/40 memberships reproduced; zero synthetic gold leakage; correct fresh-sandbox verification; complete per-task accounting including interruptions; immutable artifact/hash provenance; no misleading zeroes for unknowns; holdout output restrictions enforced; existing certification and packaging tests preserved. If those checks fail, infrastructure remains unadmitted and prompt tuning does not start.

## Appendix A. Exact proposed task memberships

The authoritative split remains `eval/splits/v1.json`; these IDs were derived from its non-gold rows and checked against the actual dataset projection. No new split/manifest was written in this design pass.

**S1 — 12 tasks**

```text
fastapi_14186 fastapi_14372 fastapi_14794 fastapi_14978 fastapi_15763 fastapi_9555
httpx_3672
requests_7502
rich_3468 rich_3676 rich_3777 rich_4079
```

**S2 — 40 tasks**

```text
fastapi_11194 fastapi_11355 fastapi_14186 fastapi_14349 fastapi_14356 fastapi_14372
fastapi_14448 fastapi_14458 fastapi_14583 fastapi_14794 fastapi_14873 fastapi_14953
fastapi_14964 fastapi_14978 fastapi_15023 fastapi_15030 fastapi_15280 fastapi_15763
fastapi_15800 fastapi_9555
httpx_3672
requests_6757 requests_7315 requests_7328 requests_7502
rich_2725 rich_3043 rich_3063 rich_3064 rich_3468 rich_3472 rich_3486 rich_3521
rich_3535 rich_3676 rich_3777 rich_3930 rich_3935 rich_3938 rich_4079
```

**Full tune — 80 tasks**

```text
fastapi_11194 fastapi_11355 fastapi_12942 fastapi_13207 fastapi_13786 fastapi_14077
fastapi_14099 fastapi_14186 fastapi_14258 fastapi_14303 fastapi_14306 fastapi_14349
fastapi_14356 fastapi_14360 fastapi_14372 fastapi_14448 fastapi_14455 fastapi_14458
fastapi_14479 fastapi_14485 fastapi_14492 fastapi_14512 fastapi_14583 fastapi_14605
fastapi_14616 fastapi_14791 fastapi_14794 fastapi_14851 fastapi_14873 fastapi_14953
fastapi_14962 fastapi_14964 fastapi_14978 fastapi_15023 fastapi_15030 fastapi_15280
fastapi_15589 fastapi_15763 fastapi_15800 fastapi_5624 fastapi_9555
httpx_3672
requests_6589 requests_6592 requests_6629 requests_6757 requests_7315
requests_7328 requests_7502 requests_7505
rich_2725 rich_3043 rich_3061 rich_3063 rich_3064 rich_3130 rich_3278 rich_3296
rich_3468 rich_3469 rich_3470 rich_3472 rich_3486 rich_3506 rich_3521 rich_3535
rich_3675 rich_3676 rich_3718 rich_3772 rich_3777 rich_3905 rich_3930 rich_3934
rich_3935 rich_3938 rich_3942 rich_4070 rich_4075 rich_4079
```

**Locked membership holdout — 49 tasks (evaluator admission pending)**

```text
fastapi_13537 fastapi_13713 fastapi_13920 fastapi_14246 fastapi_14262 fastapi_14266
fastapi_14297 fastapi_14301 fastapi_14361 fastapi_14371 fastapi_14419 fastapi_14430
fastapi_14459 fastapi_14463 fastapi_14482 fastapi_14487 fastapi_14609 fastapi_14786
fastapi_14986 fastapi_15588 fastapi_15661 fastapi_15745 fastapi_15785 fastapi_5077
fastapi_9425 fastapi_9753
requests_6644 requests_7205 requests_7309 requests_7427 requests_7433
rich_2943 rich_3006 rich_3052 rich_3067 rich_3105 rich_3180 rich_3454 rich_3471
rich_3480 rich_3518 rich_3782 rich_3882 rich_3894 rich_3944 rich_3953 rich_4006
rich_4076 rich_4077
```
