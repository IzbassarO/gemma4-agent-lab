# Issue Ledger

Two parts:

- **C-series:** community claims, condensed from `docs/MASTER_HANDOFF.md` §14. They are COMMUNITY-EVIDENCE unless marked otherwise.
- **L-series:** findings from local Step 0 work, with their authority.

Status vocabulary matches `harness_cert/matrix.yaml`. A claim never changes status without a result file.

## L-series (Step 0, 2026-10-02)

| ID | Finding | Authority | Affects | Status |
|---|---|---|---|---|
| L01 | Local `wheels/` contains **no** harness packages (swegemma, adk-submission, adk-eval-core, google-adk, litellm, vllm, networkx, numpy, pandas, pyarrow, docker). It is the sandbox test-dependency cache | REPRODUCED-LOCAL | H23, every static audit item | confirmed locally; scorer versions HOST-UNKNOWN |
| L02 | `swegemma`, `adk-submission`, `adk-eval-core` are not on public PyPI (checked 2026-10-02) | REPRODUCED-LOCAL | H23 | confirmed; source only obtainable from a Kaggle runtime |
| L03 | The **official** `sample_submission/sub_agents/code_analyzer.yaml` uses `!include ../prompts/analyzer.md` and `../configs/sampling.yaml`, contradicting README §2.2 (`..` blocked) | OFFICIAL-HARNESS vs OFFICIAL-HARNESS | H26 | contradiction; NOT-REPRODUCED |
| L04 | The sample's `main_lora` and `tool_lora` are byte-identical rank-4 adapters on layer 0 (`q_proj`, `o_proj`) | REPRODUCED-LOCAL | H16/H24 | deferred |
| L05 | `sandbox/setup.py` `pytest.ini` has no hardware/device/example ignores, and its `conftest.py` "hook" is a single comment line. Both differ from README §4.2 | OFFICIAL-HARNESS (code) vs OFFICIAL-HARNESS (doc) | verification fidelity | HOST-UNKNOWN which writer runs on the scorer |
| L06 | Public `tasks.jsonl` has no `FAIL_TO_PASS` / `PASS_TO_PASS`. A *local* scoring replica can only use `test_patch` extraction; which source the scorer uses is unknown | STATIC-OBSERVED (data) | local scoring replica | data confirmed; scorer HOST-UNKNOWN |
| L07 | All 129 tasks have graph + embedding files > 100 B. If the README's gate is implemented as documented, graph tools would be advertised on every public task regardless of declared tools | REPRODUCED-LOCAL (data) + README | H30, H04 | data confirmed; advertisement NOT-REPRODUCED |
| L08 | Git-level: `git checkout HEAD -- <existing> <nonexistent>` aborts entirely and the error is swallowed by `2>/dev/null \|\| true`. The tracked test edit survives | RUNTIME-REPRODUCED, git only (git 2.50.1, macOS; `harness_cert/reports/git_semantics.md`) | H11 / C08 | git semantics confirmed; harness pathspec list HOST-UNKNOWN; container git version may differ |
| L09 | Compiled deps (pydantic-core, markupsafe, charset-normalizer, sqlalchemy) are `manylinux x86_64` only. Mac sandbox runs need amd64 emulation | REPRODUCED-LOCAL (tags) + INFERENCE (impact) | local Container A/B runs on M1 | to test when the harness is available |
| L10 | README §2.4 says `include_thoughts: false` disables thinking. That is official documentation agreeing with C01 and contradicting the "private reasoning" notebooks | OFFICIAL-HARNESS (doc) | H02 | runtime NOT-REPRODUCED |
| L11 | Sandbox base image is a floating tag (`python:3.13-slim`), not a digest | OFFICIAL-HARNESS | reproducibility | note |
| L12 | `sandbox/setup.py` editable-install fallback drops `--no-index`, `--find-links` and `--no-build-isolation`, and no subprocess call has a timeout | STATIC-OBSERVED | offline/hermeticity of setup | whether it is reached, and with what network, is HOST-UNKNOWN |

## C-series (COMMUNITY-EVIDENCE, 2026-10-01 snapshot)

| ID | Claim | Maps to | Status |
|---|---|---|---|
| C01 | `include_thoughts:false` → `enable_thinking:false` (adk_submission 0.2.12) | H02 | NOT-REPRODUCED (README L10 agrees) |
| C02 | Prior thought sent as `reasoning_content`; pinned vLLM reads `reasoning`. Upstream fix merged 2026-05-21 (vLLM PR #42664) | H01 | NOT-REPRODUCED; Kaggle pin HOST-UNKNOWN |
| C03 | Undeclared tool call → ADK `_get_tool` ValueError → task ends | H04, H18 | NOT-REPRODUCED |
| C04 | Same fatal path discards a good working-tree patch | H04, H13 | NOT-REPRODUCED |
| C05 | Double-JSON tool results; 62/299 `edit_file` failures, 39 attributed to escaping | H07, H09, H10, H17 | NOT-REPRODUCED |
| C06 | Systematic `old_string not found` | H08–H10 | NOT-REPRODUCED |
| C07 | `read_file` range: `'>' not supported between 'int' and 'str'` | H06 | NOT-REPRODUCED |
| C08 | Protected-path reset no-op when the pathspec list has nonexistent files | H11 | git half RUNTIME-REPRODUCED, git only (L08); harness half NOT-REPRODUCED |
| C09 | Compaction loses tool results → loops | H15, H19 | NOT-REPRODUCED |
| C10 | `search_similar_code` can return 100k+ chars | H21 | NOT-REPRODUCED |
| C11 | LoRA shrinks KV cache on 4×L4 | H25 | DEFERRED |
| C12 | Historical LoRA no-op bug | H24 | DEFERRED |
| C13 | Sep 30 / Oct 1 "Notebook Threw Exception" after a wheelhouse update | H23, H27, H28 | HOST-UNKNOWN |
| C14 | GPU outage failures | taxonomy `PLATFORM_SCORER_EXCEPTION` | n/a |
| C15 | 12-hour / concurrency semantics | H22 | HOST-UNKNOWN |
| C16 | Hidden repo composition unknown | design constraint | HOST-UNKNOWN |
| C17–C19 | Small-model LB suggestion; paper-track discussions; sharing rules | — | out of scope for Step 0 |

Historical versions reported in notebooks (~2026-09-25): swegemma 0.2.7, adk-submission 0.2.11, adk-eval-core 0.1.0, google-adk 1.36.1, google-genai 2.11.0, vllm 0.19.1, transformers 5.13.1. COMMUNITY-EVIDENCE, **not** current facts.
