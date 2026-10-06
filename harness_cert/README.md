# Harness Certification

`matrix.yaml` is the source of truth for H01–H30. Its status vocabulary is exactly:
`PASS · FAIL · VERSION-SPECIFIC · HOST-UNKNOWN · NOT-REPRODUCED · DEFERRED`.

## Layout

| Path | Committed? | Content |
|---|---|---|
| `matrix.yaml` | yes | One entry per H-ID (hypothesis, authority, requirements, status, repro) |
| `scripts/` | yes | Runnable probes. Each prints what it proves and what it does not |
| `fixtures/` | yes, tiny only | Synthetic files for serialization/edit tests (quotes, backslashes, unicode) |
| `results/<H-ID>/` | **never** (entire dir gitignored, any extension) | Raw/generated output: traces, model conversations, request/response captures, tool outputs, verifier material, anything that may contain gold data |
| `reports/<H-ID or topic>.md` | yes, curated | Manually written, sanitized summaries: runtime, versions, command, observed result, evidence class, pointer to the raw results path |

## Rules

- A status changes only together with a curated `reports/<H-ID>.md` that states runtime, versions, command and raw-output location.
- Nothing in `results/` becomes committable by being Markdown. Copy findings into `reports/` by hand, and never paste gold patch/test content, holdout trajectories or raw captures.
- Evidence classes: OFFICIAL-DOCUMENTED, STATIC-OBSERVED, RUNTIME-REPRODUCED (with scope), COMMUNITY-EVIDENCE, HOST-UNKNOWN. Reading source is STATIC-OBSERVED at best and never produces PASS.
- Hosted outcomes use the existing `OWN-KAGGLE-RUN` authority. Reports distinguish `KAGGLE-OBSERVED` operator observations from independently reproduced local facts, inference and unknowns; a local byte check alone does not establish remote execution or origin.
- A git-level or mock-level reproduction is labeled with its scope. It does not certify the scorer.
- Every component-behavior result names the harness versions it ran against. Without them the result is at most `VERSION-SPECIFIC`. A hosted-path hypothesis such as H28 can be `PASS` for one identified artifact and dated operator-observed outcome with hidden versions explicitly `UNKNOWN`; this certifies that observed acceptance/completion only and does not certify package semantics or other matrix items.

## Current blocker

All harness-level items need the `swegemma` / `adk-submission` / `adk-eval-core` distributions, and those are not in the local dataset (see `docs/competition/H23_WHEELHOUSE_FINGERPRINT.md`). The planned local technique, once they exist, is a **scripted mock**: a local OpenAI-compatible stub that returns predetermined tool calls and records every request body. It certifies the ADK → LiteLLM → tool → serialization path without running Gemma.

## Available now

```bash
bash harness_cert/scripts/git_semantics_probe.sh   # git-level H11/H12 semantics (no harness); curated summary: reports/git_semantics.md
```
