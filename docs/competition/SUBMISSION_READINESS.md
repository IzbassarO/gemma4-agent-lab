# Gemma 4 Developer Agent Competition — Submission Readiness

**Repository:** `https://github.com/IzbassarO/gemma4-agent-lab`  
**Branch:** `main`  
**Foundation commit:** `61f5669` — `chore: establish Step 0 competition research foundation`  
**Status date:** 2026-10-03\
**Competition phase:** STEP 0 — Competition Environment & Harness Certification  
**Current readiness:** **FOUNDATION READY / COMPETITION SUBMISSION NOT YET READY**

---

## 1. Purpose

This document is the operational source of truth for deciding when a Gemma 4 competition candidate is ready to become a Kaggle submission.

It prevents three failure modes:

1. submitting an agent before the current harness is understood;
2. promoting changes because of Public Leaderboard movement without local evidence;
3. losing reproducibility because code, prompts, runtime versions, task splits, or ZIP identity were not frozen.

A candidate is not considered submission-ready because it compiles, produces a patch, or scores well on one public run. It must pass the gates in this document.

---

## 2. Authority hierarchy

When sources disagree, use this order:

1. **OFFICIAL-RULE** — current Kaggle competition rules and competition-specific rules.
2. **OFFICIAL-HARNESS** — current competition `HARNESS_README.md`, installed harness code, and scorer-visible configuration.
3. **OFFICIAL-NOTEBOOK** — pinned Google/Kaggle Getting Started notebook at a recorded version.
4. **REPRODUCED-LOCAL** — behavior reproduced by us with a versioned harness/runtime.
5. **OWN-KAGGLE-RUN** — our own Kaggle execution with exact artifact provenance; capture, bootstrap, and scored outcomes remain distinct.
6. **COMMUNITY-EVIDENCE** — public notebooks/forum reports; useful but version-sensitive.
7. **INFERENCE** — architectural hypotheses awaiting experiment.

Community results and notebook claims must never silently override current harness behavior.

---

## 3. Immutable competition dataset snapshot

External competition data is stored outside Git and accessed through:

```text
GEMMA4_DATASET_ROOT
```

Current verified local snapshot:

| Item | Verified value |
|---|---:|
| Files | 524 |
| Logical bytes | 22,416,766,904 |
| Public tasks | 129 |
| Unique instance IDs | 129 |
| Unique base commits | 127 |
| Non-empty hints | 0 |
| Graph files | 127 |
| Embedding files | 127 |
| Zero-byte graph files | 0 |
| Zero-byte embedding files | 0 |

Repository distribution:

| Repository | Tasks |
|---|---:|
| `fastapi/fastapi` | 67 |
| `Textualize/rich` | 48 |
| `psf/requests` | 13 |
| `encode/httpx` | 1 |

Key hashes:

```text
HARNESS_README.md
3d6e57a13234cb4e783ba24eaab486459af76e0923ea4c6607cd41cc8961bbbb

tasks.jsonl
e4b3fd60f69dbc2b9213e54eeb9636db78aefe92c1d06269d73d9f5f8f3c8ad6

canonical dataset manifest v2
d23eaa3ef26c53da1b48955547147d6f91625463629eafc451881299b2d51d6f
```

The dataset is read-only. Project tooling must reject any output path that resolves inside `GEMMA4_DATASET_ROOT`.

---

## 4. Locked local evaluation split

The public 129-task set is divided before agent development into:

```text
DEV:      80 tasks
HOLDOUT:  49 tasks
```

Split identity:

```text
420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12
```

Distribution:

| Repository | DEV | HOLDOUT |
|---|---:|---:|
| FastAPI | 41 | 26 |
| Rich | 30 | 18 |
| Requests | 8 | 5 |
| HTTPX | 1 | 0 |

Rules:

- tasks sharing the same `base_commit` never cross DEV/HOLDOUT;
- split generation uses non-gold metadata only;
- the locked split is not reshuffled because results are disappointing;
- prompt/agent designers must not inspect `patch` or `test_patch` for HOLDOUT tasks;
- automated verification may consume gold verifier material;
- gold-assisted diagnostics on DEV must be explicitly recorded;
- HOLDOUT per-task gold-assisted debugging is prohibited.

Known limitation: the single HTTPX task is in DEV, so the HOLDOUT is not an unseen-repository benchmark.

---

## 5. Repository foundation status

Current foundation has passed its local engineering audit.

### Confirmed

- Git repository created and pushed to GitHub.
- Foundation commit frozen at `61f5669`.
- 69/69 project tests passed before first push.
- Dataset mutation protection implemented.
- Canonical manifests are deterministic.
- Symlink dereferencing protections implemented.
- Raw harness results are Git-ignored.
- Curated certification reports are separated from raw traces.
- Failure taxonomy is defined.
- H01–H30 certification matrix exists.
- Official sample control is preserved through a reproducible restoration workflow.
- No long Gemma run, LoRA training, or leaderboard-driven tuning has occurred.

### Not yet established

- exact live scorer package versions;
- exact installed `swegemma`, `adk-submission`, `adk-eval-core`, Google ADK, LiteLLM, and vLLM source used by the current scorer epoch;
- runtime behavior of the unresolved harness issues;
- current exact-model reasoning/tool behavior;
- a validated competitive agent candidate.

---

## 6. Critical unresolved harness questions

These are not prompt-design details. They can change whether an otherwise good agent succeeds or fails.

### Reasoning/runtime

- H01 — `reasoning_content` continuity across tool-call turns.
- H02 — real `include_thoughts` semantics.
- H03 — `thinking_budget` forwarding and effect.

### Tool failure and serialization

- H04 — undeclared tool call behavior.
- H05 — declared tool control.
- H06 — `read_file` line-range typing.
- H07 — quotes/backslashes/unicode in `read_file` content.
- H08 — basic exact `edit_file`.
- H09 — triple-quoted/docstring edits.
- H10 — unicode edits.
- H17 — exact JSON/ADK/vLLM serialization depth.
- H18 — nonexistent/hallucinated tool recovery.
- H30 — graph tools advertised in prompt vs actual declared tools.

### Lifecycle and verification

- H11 — protected test/config reset behavior.
- H12 — scratch-file contamination.
- H13 — timeout patch recovery.
- H14 — tool-budget patch recovery.
- H29 — `get_status` and `submit_patch` free-call behavior.

### Context/orchestration

- H15 — compaction state retention.
- H19 — compaction + tool-result retention.
- H20 — `AgentTool(skip_summarization)` behavior.

### Provenance/compiler

- H23 — scorer package/version fingerprint.
- H26 — `../` `!include` semantics.
- H27 — empty/no-LoRA adapter configuration.
- H28 — current public sample submission notebook path.

### Deferred LoRA checks

- H16 — LoRA model-start smoke.
- H24 — adapter output-delta proof.
- H25 — LoRA KV-cache/context-capacity measurement.

LoRA-specific checks are intentionally deferred until a strong non-LoRA baseline exists.

---

## 7. Important historical evidence from public notebooks/community

These points influence what we test, but they are not treated as current scorer truth until reproduced.

- A concise dual-agent coder + analyzer architecture has shown competitive public evidence, but historical bundles contain assumptions that may now be stale.
- Public results have shown meaningful run-to-run / artifact-to-score instability; leaderboard movement alone is not sufficient evidence for a design change.
- More elaborate prompts/rule lists have sometimes scored worse than shorter, more generic SWE prompts.
- A historical Black Cat anchor reportedly scored around 0.10 while a more complicated long-rule variant dropped to around 0.05.
- A historical dual-agent-style artifact was reported around 0.12 and a byte-identical copy around 0.15, reinforcing that Public LB is noisy evidence.
- `Shape of Doubt` recorded local successes and also exposed context overflow and verifier/environment failure modes.
- `search_similar_code` can create large context pressure and should be symbol-first, not natural-language-first.
- Community reports identified possible failures around undeclared tools, double-JSON encoding, edit reliability, reasoning retention, compaction, LoRA, and scorer outages.

These are experiment hypotheses, not acceptance criteria by themselves.

---

## 8. Submission artifact contract

The competition submission is a deterministic agent bundle, not a normal CSV prediction file.

Expected conceptual layout:

```text
submission/
├── agent.yaml
├── eval_config.yaml              # optional
├── configs/
├── prompts/
├── sub_agents/
├── skills/                       # optional
└── adapters/                     # optional, later phase
```

Before packaging, every candidate must satisfy:

- exactly one valid root agent config;
- only the allowed competition base model;
- no accidental absolute/local paths;
- no untracked scratch files;
- no test/config tampering strategy;
- no raw competition dataset embedded in the archive;
- no unsupported binary artifacts;
- total unpacked size below the competition limit;
- deterministic file ordering/metadata where the builder controls it;
- exact per-file hashes recorded;
- final ZIP SHA-256 recorded;
- round-trip extraction/validation succeeds.

The bundle used for Kaggle must be byte-identifiable after submission.

---

## 9. Candidate promotion ladder

No candidate skips directly from prompt editing to Kaggle.

### E0 — Official sample control

Purpose: prove current package/compiler/evaluation mechanics.  
Not a competitive baseline.

### E1 — Historical public reference reproduction

Reconstruct a known public architecture as faithfully as current compatibility permits.  
Purpose: establish a reference point, not copy leaderboard folklore.

### E2 — Compatibility-only baseline

Change only behavior proven necessary by Step 0 certification.

Examples:

- current-safe includes;
- current-safe thinking configuration;
- symbol-first graph instructions;
- tool declaration safety;
- serialization-safe edit behavior.

No architecture expansion yet.

### E3 — Tool/edit robustness

Target measured `EDIT_FAILED`, serialization, malformed tool call, and patch hygiene failures.

### E4 — Localization ablation

Compare, pairwise where possible:

- single root coder;
- root coder + read-only analyzer;
- lexical-first localization;
- graph assistance after confirmed symbols.

### E5 — Reasoning configuration

Only after H01–H03 are resolved.

Compare genuinely supported reasoning modes with fixed tasks/configs.

### E6 — Budget policy

Set budgets from measured p50/p90 time/tool distributions, not public notebook arithmetic.

### E7 — Reviewer / second pass

Add only if failure analysis shows a reviewer converts a meaningful number of incorrect patches into correct patches.

### E8 — Skills

Introduce small deterministic skills only for measured failure classes.

### E9 — LoRA / PEFT

Only after H24 proves an adapter changes output and H25 proves acceptable runtime/context behavior.

### E10 — RL / learned routing

Only after the evaluator and non-trained baseline are stable enough to provide trustworthy learning signals.

---

## 10. Required local evidence before a Kaggle submission

A candidate can be considered for Kaggle only if all of the following are true:

### Environment gate

- [ ] H23 fingerprint for the current submission epoch is captured.
- [ ] Candidate compiles under the current captured harness.
- [ ] Any harness issues relevant to the candidate are classified.
- [ ] No unresolved P0 certification defect affects the candidate path.

### Artifact gate

- [ ] Agent source is committed.
- [ ] Git working tree is clean before build.
- [ ] Submission is built by a deterministic script.
- [ ] Submission validates after clean extraction.
- [ ] Per-file manifest saved.
- [ ] ZIP SHA-256 saved.
- [ ] ZIP size recorded.
- [ ] Model/sampling/prompt hashes saved.

### DEV gate

- [ ] Candidate evaluated on the declared DEV set or a predeclared DEV subset.
- [ ] Parent/baseline evaluated on the same task IDs and environment.
- [ ] Pairwise per-task delta available.
- [ ] No unexplained increase in no-patch, timeout, invalid-tool, or context-overflow failures.
- [ ] Primary improvement maps to an identified controllable failure class.

### HOLDOUT gate

- [ ] Candidate definition frozen before HOLDOUT execution.
- [ ] No human/LLM gold inspection of HOLDOUT patch/test_patch.
- [ ] Candidate evaluated on the locked HOLDOUT.
- [ ] Result compared against the frozen parent candidate on identical tasks/runtime.
- [ ] Failure classification does not reveal or use holdout gold solutions.
- [ ] Improvement is not driven only by a tiny repository/task corner unless explicitly intended.

### Operational gate

- [ ] Runtime budget fits current competition constraints with margin.
- [ ] Context overflow rate acceptable.
- [ ] Tool failure rate acceptable.
- [ ] Patch application failure rate acceptable.
- [ ] No workspace scratch/test pollution.
- [ ] Known platform/scorer outage is not confounded with agent quality.

Only after all applicable gates pass may the candidate be promoted to Kaggle.

---

## 11. Kaggle submission record

Create a ledger row **before** clicking Submit.

Required fields:

```text
submission_id: pending
submission_epoch:
notebook_slug:
notebook_version:
utc_timestamp:
experiment_id:
parent_experiment_id:
git_commit:
working_tree_clean: true/false
submission_zip_sha256:
submission_zip_bytes:
submission_manifest_sha256:
agent_config_sha256:
sampling_config_sha256:
prompt_sha256:
adapters_present: true/false
adapter_hashes: []
harness_fingerprint:
model_name:
expected_task_budget:
local_dev_result:
local_holdout_result:
known_platform_incident: true/false
kaggle_outcome: pending
kaggle_score:
kaggle_runtime:
notes:
```

If Kaggle reports a scorer/platform exception instead of a valid score, do not classify the candidate as worse. Preserve the exact ZIP and evidence first.

---

## 12. Public Leaderboard discipline

The Public Leaderboard is an external validation signal, not the objective function for daily prompt search.

Rules:

- do not submit variants that are locally indistinguishable merely to select the best LB draw;
- do not keep a change solely because Public LB increased;
- do not reject a locally supported change solely because one Public LB observation decreased;
- always compare exact artifacts and submission epochs;
- platform/no-score errors are separate from agent failures;
- final decisions must emphasize reproducible local paired evidence and the locked HOLDOUT.

---

## 13. Failure taxonomy

Every unresolved task must be assigned the first applicable root cause:

1. `PLATFORM_SCORER_EXCEPTION`
2. `MODEL_SERVER_START_FAILURE`
3. `HARNESS_COMPILE_VALIDATION`
4. `INVALID_TOOL_FATAL`
5. `TOOL_SERIALIZATION`
6. `CONTEXT_OVERFLOW`
7. `AGENT_TIMEOUT`
8. `TOOL_BUDGET_EXHAUSTED`
9. `NO_PATCH`
10. `PATCH_MALFORMED_OR_APPLY_FAIL`
11. `SCRATCH_OR_TEST_POLLUTION`
12. `LOCALIZATION_WRONG`
13. `ROOT_CAUSE_WRONG`
14. `EDIT_FAILED`
15. `SYNTAX_ERROR`
16. `TARGETED_TEST_FAIL`
17. `HIDDEN_TEST_FAIL_BEHAVIORAL`
18. `OVERFIX_REGRESSION`
19. `ENVIRONMENT_VERIFICATION_ARTIFACT`
20. `UNKNOWN`

Optimization priority is based on the largest controllable failure class, not the most interesting anecdote.

---

## 14. Current readiness scorecard

| Gate | Status | Comment |
|---|---|---|
| Repository foundation | **PASS** | GitHub repo + frozen foundation commit |
| Dataset integrity | **PASS** | 524 files / 129 tasks / manifests reproducible |
| Dataset immutability protection | **PASS** | Adversarial safety tests added |
| Locked split | **PASS** | 80 DEV / 49 HOLDOUT, no base-commit crossing |
| Experiment protocol | **PASS** | Leakage/LB rules frozen |
| Failure taxonomy | **PASS** | 20 categories |
| Current harness package fingerprint | **HOST-UNKNOWN** | Real Kaggle Version 3 capture validated; bootstrap `EXIT_NONZERO`; hidden scorer `SCORER_ONLY_UNKNOWN` |
| H26 include semantics | **NOT-REPRODUCED** | Requires actual harness/compiler |
| File/edit serialization certification | **NOT-REPRODUCED** | H06–H10/H17 |
| Tool fatal/recovery certification | **NOT-REPRODUCED** | H04/H05/H18/H30 |
| Patch lifecycle certification | **NOT-REPRODUCED** | H11–H14/H29 |
| AgentTool/compaction certification | **NOT-REPRODUCED** | H15/H19/H20 |
| Reasoning certification | **NOT-REPRODUCED** | H01–H03 |
| Exact official control run | **NOT STARTED** | After harness provenance |
| Competitive non-LoRA baseline | **NOT STARTED** | After Step 0 |
| DEV evidence | **NOT STARTED** | No agent experiments yet |
| HOLDOUT evidence | **NOT STARTED** | No agent experiments yet |
| LoRA | **DEFERRED** | Deliberately not current priority |
| Kaggle candidate | **NOT READY** | Correct current status |

---

## 15. Current H23 checkpoint and next milestone

The next task is **not agent development**.

The real **OWN-KAGGLE-RUN** checkpoint is [the 2026-10-03 H23 capture report](../../harness_cert/reports/H23_KAGGLE_CAPTURE_2026-10-03.md), with a separate [operator attestation](../../harness_cert/attestations/h23_kaggle_2026-10-03.json). Notebook `izbassaro/notebooka2687d8703` ran at visible Version 3 of 3, immutable `scriptVersionId=354974356`, with `metric/gemma-4-developer-agent-wheelhouse` v28 (41 visible files). Technical import returned `CAPTURE_VALIDATED`, durability `CONFIRMED`, and no warnings.

The executed `OFFICIAL_NOTEBOOK_CELL_2` bootstrap returned `EXIT_NONZERO` (code 1). Successful Kaggle capture does not mean successful bootstrap. The observed Python 3.13.15 interactive runtime contained pre-existing `google-adk` 2.7.1, `google-genai` 2.12.1, `litellm` 1.85.7, and `transformers` 5.16.1 after that failure; these observations do not prove installation of the requested wheelhouse stack. Some reported wheelhouse files are cp312-specific: the difference from observed Python 3.13.15 is a strong diagnostic lead, not a confirmed root cause.

Interactive Kaggle runtime does not establish hidden scorer runtime. H23 stays **HOST-UNKNOWN**, and the hidden scorer stays **SCORER_ONLY_UNKNOWN**. The failed bootstrap prevents claiming successful reproduction of the official evaluation stack. **Step 0 is not complete.**

The next blocker is determining the exact bootstrap failure and obtaining a usable harness/compiler environment for CPU certification.

Target flow:

```text
Validated real Kaggle capture + failed bootstrap evidence
        ↓
reproduce and confirm the exact bootstrap failure
        ↓
obtain a usable harness/compiler environment
        ↓
verify installed stack identity and relevant source
        ↓
GEMMA4_HARNESS_ROOT
        ↓
CPU-only compiler/harness certification
        ↓
H26 / H06 / H08 / H09 / H10
```

The raw capture and canonical external evidence remain outside curated repository records. Only the report and separate human attestation record this checkpoint; neither promotes a failed bootstrap into scorer-stack reproduction.

---

## 16. GPU policy

Current local machine:

```text
MacBook Pro M1 Pro
16 GB unified memory
```

Use it for:

- repository engineering;
- dataset/harness analysis;
- static certification;
- deterministic builders;
- fake/scripted model tests;
- result analysis.

Google Colab Pro is available with 90+ compute units, but compute units are not the scientific environment definition. Before exact Gemma experiments, record the assigned accelerator and VRAM.

No GPU should be consumed until the intended experiment:

1. cannot be answered with static/CPU tests;
2. has a written hypothesis;
3. has fixed task IDs/config;
4. defines a pass/fail or comparison criterion;
5. records exact runtime/model identity.

Exact scorer hardware remains a separate environment and must not be conflated with whichever Colab GPU is assigned.

---

## 17. Definition of “submission ready”

A candidate is **SUBMISSION READY** only when:

```text
current harness fingerprint captured
        +
relevant harness/runtime behavior certified
        +
deterministic candidate artifact
        +
clean DEV evidence against frozen parent
        +
clean locked-HOLDOUT evidence
        +
acceptable runtime/context/tool reliability
        +
complete provenance ledger
        =
KAGGLE SUBMISSION READY
```

Until then, the correct label is one of:

- `FOUNDATION READY`
- `HARNESS CERTIFICATION IN PROGRESS`
- `LOCAL BASELINE READY`
- `HOLDOUT READY`
- `SUBMISSION READY`

**Current label: `FOUNDATION READY`.**

---

## 18. Non-negotiable rules

- Do not optimize from Public LB alone.
- Do not inspect HOLDOUT gold patches/tests during design.
- Do not train LoRA before a stable non-LoRA baseline.
- Do not add multi-agent complexity without paired evidence.
- Do not treat community reports as current scorer facts.
- Do not treat compiler success as functional success.
- Do not treat a Kaggle platform exception as agent-quality evidence.
- Do not use a smaller local Gemma as evidence that the competition 31B agent improved.
- Do not modify protected tests/config to game verification.
- Do not leave workspace scratch files in the submitted patch.
- Do not run expensive exact-model experiments without a predeclared hypothesis and acceptance rule.
- Always preserve exact artifact identity and a known-good fallback submission.

---

## 19. Next document to produce

After Harness Provenance Acquisition, update this file with:

1. current scorer/harness package fingerprint;
2. source archive/fingerprint identity;
3. H23 verdict;
4. H26 result;
5. first CPU runtime certification results;
6. exact remaining blockers before E0.

Do not advance the readiness label based on assumptions.
