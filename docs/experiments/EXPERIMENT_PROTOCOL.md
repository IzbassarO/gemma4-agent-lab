# Experiment Protocol

Applies to every local run, GPU run and Kaggle submission. An experiment that is not in `experiments/registry.jsonl` did not happen.

## Required record (one JSON object per line in `experiments/registry.jsonl`)

| Field | Meaning |
|---|---|
| `experiment_id` | `EXP-YYYYMMDD-NNN`, never reused |
| `parent_experiment_id` | The experiment this one changes, or `null` for a root control |
| `hypothesis` | What we expect and why, written **before** the run. Name any uncertified H-ID it depends on |
| `single_change` | The one intended change relative to the parent. If more than one is unavoidable, list each and justify |
| `git_commit` | Commit of this repo the artifact was built from (clean tree) |
| `config_hashes` | sha256 of every config that affected the run (`agent.yaml`, sub-agents, prompts, `eval_config.yaml`, run config) |
| `artifact_hashes` | `submission_zip_sha256` and `bundle_tree_sha256` (definition in `tools/preserve_sample.py`) |
| `wheelhouse_fingerprint_sha256` | From `vendor_meta/wheelhouse_manifest.json` at run time |
| `harness_versions` | swegemma / adk-submission / adk-eval-core / google-adk / litellm / vllm **as observed in that runtime**, or `null`. Never copied from a forum post |
| `hardware_runtime` | e.g. `mac-m1pro-subprocess`, `colab-<gpu>`, `kaggle-scorer` |
| `split`, `task_ids` | `v1:dev`, `v1:holdout` or `v1:dev-subset:<name>`, plus explicit IDs |
| `gold_assisted_diagnostics` | `false`, or a description of what gold material was read, for which task IDs, and why (dev only) |
| `metrics` | resolved / total / rate, plus per-task metrics in the results dir |
| `failure_taxonomy` | Counts per `eval.failure_taxonomy.Failure`. `UNKNOWN` must be resolved before a decision |
| `result`, `decision` | Factual summary; `keep` / `reject` / `inconclusive` with a one-line reason |
| `results_path`, `date_utc` | Raw outputs (gitignored) and when |

Line 1 of the registry is a schema example marked `"_is_example": true`; tools skip it.

## General rules

1. **One controlled change per experiment** whenever possible.
2. **Exact artifact identity.** Every bundle/ZIP is rebuilt deterministically from a clean commit and its sha256 recorded.
3. **Preserve known-good fallbacks.** Keep every scored ZIP and its record. Note milestones in `docs/competition/decisions.md`.
4. **Platform ≠ agent.** `PLATFORM_SCORER_EXCEPTION`, `MODEL_SERVER_START_FAILURE` and `ENVIRONMENT_VERIFICATION_ARTIFACT` are never evidence about agent quality. Such runs are `inconclusive`.
5. **Classify by the earliest failing stage**: platform → harness → agent process → agent quality → verification.

## LOCKED HOLDOUT (`eval/splits/v1.json`, split = `holdout`)

- Humans and LLMs designing prompts or agents **must not inspect `patch` or `test_patch`** for holdout tasks, nor anything derived from them (diffs against gold, per-test verifier output naming what the fix should do).
- The evaluator **may** consume gold/verifier material automatically to compute results.
- We **may** inspect aggregate metrics, and per-task records restricted to predeclared, non-revealing fields: resolved bool, the `Failure` category, the stage and error class that ended the run, timings, and tool-call/token counts.
- **No per-task gold-assisted debugging on holdout.** Holdout trajectories are not mined for task-specific fixes.
- Holdout is run only for a candidate already selected on dev. It can veto a promotion. It is never used to choose among sibling variants.
- **v1 is frozen.** It is never reshuffled because results are disappointing. A new split version needs a documented methodological reason, not score improvement (`eval/splits/SPLIT_POLICY.md`).

## DEVELOPMENT (split = `dev`)

- Gold `patch` / `test_patch` may be used **only in explicitly labelled diagnostic research after a run**, never silently and never as input when writing prompts or rules for specific tasks.
- Every experiment that used gold-assisted diagnostics records it in `gold_assisted_diagnostics`, with task IDs.
- Prompt/agent changes must be justifiable as general SWE behavior, not as a repository- or task-specific answer.

## PUBLIC LEADERBOARD

- A candidate must clear the **local promotion gate** below before any Kaggle submission.
- **Never** submit several locally equivalent variants to pick the best Public-LB score.
- The Public LB is external evidence, not the optimization objective. A candidate is **not kept solely because the LB increased**.
- Every Kaggle submission gets a registry row **before** clicking Submit. Its exact ZIP sha256 is recorded and the ZIP preserved.

### Local promotion gate (initial; may only be tightened without a decisions.md entry)

A candidate may be submitted only if all of the following hold:

1. Step 0 exit gate met for every H-ID the candidate relies on (`harness_cert/matrix.yaml`).
2. The bundle compiles and validates with the harness version recorded in the row.
3. It was run on the **full v1 dev split** under one recorded config, with zero `UNKNOWN` failures left.
4. On dev it resolves **strictly more** tasks than its parent under the same config. The paired per-task comparison (tasks newly solved vs newly lost) is recorded. Thresholds beyond "strictly more" are deliberately not set until we have measured our own run-to-run variance.
5. It was run on holdout once, with no regression vs the parent's holdout result.
6. Harness-layer failures (`HARNESS_COMPILE_VALIDATION`, `INVALID_TOOL_FATAL`, `TOOL_SERIALIZATION`) did not increase vs the parent.

### Initial submission policy (conservative)

- No Kaggle submission until the Step 0 exit gate is met. The first submission, if any, is a certification run (H28) of a fully identified artifact, recorded as such.
- At most one submission per day (rule limit). Only predeclared candidates that passed the gate.
- An identical ZIP is resubmitted only to diagnose a suspected platform failure, recorded as such.
- No claims about LB noise or scorer behavior enter decisions unless reproduced (`OWN-KAGGLE-RUN` or `RUNTIME-REPRODUCED`).
