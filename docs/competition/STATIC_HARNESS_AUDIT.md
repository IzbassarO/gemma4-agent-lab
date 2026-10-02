# Static Harness Source Audit (STEP 0, Phase E) — rev 2

**Rev 2 (2026-10-02):** every conclusion now carries an evidence class. Rev 1 statements that read static code as runtime behavior have been corrected; see "Corrections from rev 1" at the end.

## Evidence classes (the only labels used here)

| Label | Meaning |
|---|---|
| **OFFICIAL-DOCUMENTED** | Stated in `HARNESS_README.md` (sha256 `3d6e57a1…`). Documentation, not implementation |
| **STATIC-OBSERVED** | Read directly in code or data present locally (`sandbox/setup.py`, `docker/*`, `sample_submission/*`, `tasks.jsonl` structure). Says what the code *says*, not what happens at runtime |
| **RUNTIME-REPRODUCED** | We executed it and observed the result. The scope is stated: e.g. "git only", not "harness" |
| **COMMUNITY-EVIDENCE** | Forum posts / public notebooks; not reproduced |
| **HOST-UNKNOWN** | Depends on implementation or configuration we do not have (the scorer's harness code and runtime) |

**Headline (STATIC-OBSERVED):** `swegemma`, `adk-submission`, `adk-eval-core`, `google-adk` and `litellm` are not in the local dataset (see `H23_WHEELHOUSE_FINGERPRINT.md`). Every claim about how those packages *behave* is therefore HOST-UNKNOWN unless OFFICIAL-DOCUMENTED, and even documented claims are not runtime facts.

## Locally inspectable sources

`sandbox/setup.py`, `docker/Dockerfile.sandbox`, `docker/Dockerfile.public`, `docker/imp.py`, `docker/telnetlib.py`, `sample_submission/**`, `tasks.jsonl`. Hashes are in `vendor_meta/source_hashes.json` and `vendor_meta/baseline_v0_official_manifest.json`.

## Findings

| # | Finding | Class | What remains unknown (HOST-UNKNOWN) |
|---|---|---|---|
| S1 | The official sample's `sub_agents/code_analyzer.yaml` contains `!include ../prompts/analyzer.md` and `!include ../configs/sampling.yaml` | STATIC-OBSERVED | Whether the current compiler accepts it. README §2.2 says `..` components are blocked (OFFICIAL-DOCUMENTED). The two official sources conflict. |
| S2 | The sample's root declares all 9 tools + `agent_tool` (`skip_summarization: true`); the analyzer declares `read_file` + 3 graph tools, no `run_command` | STATIC-OBSERVED | How undeclared calls and AgentTool history behave (H04, H20) |
| S3 | Sample sampling: `temperature 0.2, top_p 0.95, max_output_tokens 16384, thinking_budget 4096, include_thoughts: true` | STATIC-OBSERVED | How these map to provider requests (H02/H03) |
| S4 | Sample `eval_config.yaml`: `timeout_seconds 60, max_tool_calls 10, max_time_minutes 1, max_turns 50` | STATIC-OBSERVED; README calls it the sample's budget (OFFICIAL-DOCUMENTED) | — |
| S5 | `main_lora` and `tool_lora` are byte-identical (sha256 `dcbedd98…`); config rank 4, alpha 8, `layers_to_transform [0]`, `q_proj`/`o_proj` | STATIC-OBSERVED | Runtime effect (H16/H24, deferred) |
| S6 | `setup_git_exclude` appends `__pycache__/ *.pyc .pytest_cache/ *.egg-info/ build/ dist/ .coverage` | STATIC-OBSERVED; same list in README §4.2 (OFFICIAL-DOCUMENTED) | Whether the scorer runs this function or an equivalent harness-side one |
| S7a | `install_editable_package`: first command uses `--no-index --find-links=/wheels --no-build-isolation --no-deps -e`. **Fallback** on non-zero exit is `pip install --no-deps -e /workspace`, with **no `--no-index`, no `--find-links`, no `--no-build-isolation`** | STATIC-OBSERVED (`sandbox/setup.py:193-213`) | If reached on a host with network access, the fallback is online-capable: it may contact the default index, including for isolated build backends. Whether it is reached, and whether the host has network, is HOST-UNKNOWN. README §4.1 documents `network_mode="none"` for the Docker backend (OFFICIAL-DOCUMENTED) but states no network isolation for the subprocess backend. Not RUNTIME-REPRODUCED in either backend. |
| S7b | Both `subprocess.run` calls in `install_editable_package` pass **no `timeout`** | STATIC-OBSERVED | Whether an outer harness timeout bounds container setup |
| S7c | Exceptions and failures in `install_editable_package` are suppressed (`check=False`, the fallback's result is ignored, `except Exception` prints a notice) | STATIC-OBSERVED | Whether a failed editable install surfaces anywhere in harness logs |
| S8 | `configure_workspace_pytest_ini` writes `addopts = --import-mode=importlib -p no:anyio`, `norecursedirs = .* build dist venv`. It has no hardware/device/example ignores | STATIC-OBSERVED | README §4.2 says the ini ignores "hardware/device/example directories" (OFFICIAL-DOCUMENTED). Which writer produces the scorer's `pytest.ini`: this script or swegemma's `setup_workspace_test_config`? |
| S9 | `configure_workspace_conftest` prepends only a comment line (`# Standard test discovery hook for SWE-gemma public benchmark`) | STATIC-OBSERVED | README says a "hermetic test collection hook" is prepended (OFFICIAL-DOCUMENTED). Same question as S8 |
| S10 | `execute_fast_path` writes `workspace_paths.pth` into site-packages, listing `/workspace` and each non-hidden top-level directory | STATIC-OBSERVED | Whether it runs on the scorer, and its import side effects at runtime |
| S11 | Sandbox images: `FROM python:3.13-slim` (tag, not digest); git, patch, protobuf-compiler, pigz; `pytest`, `pytest-timeout==2.1.0`, build backends; `imp.py`/`telnetlib.py` shims; git identity `Agent <agent@eval>` | STATIC-OBSERVED | The exact image the scorer runs, and its git version |
| S12 | Public `tasks.jsonl` has no `FAIL_TO_PASS` / `PASS_TO_PASS` fields (keys: `instance_id, repo, base_commit, problem_statement, hints_text, patch, test_patch, created_at`) | STATIC-OBSERVED (public data) | README §8.2(5) lists required tests as "FAIL_TO_PASS, PASS_TO_PASS, or test functions extracted from `test_patch`" (OFFICIAL-DOCUMENTED). **Which source the scorer uses, and whether hidden tasks carry F2P/P2P, is HOST-UNKNOWN.** For local scoring replicas, only `test_patch` extraction is possible on public data. |
| S13 | All 129 public tasks have empty `hints_text` | STATIC-OBSERVED (public data) | README says the `## Hints` section and the `hints` state key appear only when hints are non-empty (OFFICIAL-DOCUMENTED). Whether the harness emits anything for empty hints, and whether hidden tasks have hints, are HOST-UNKNOWN. ADK's behavior for an instruction referencing `{hints}` when the key is absent is also HOST-UNKNOWN; candidate certification item. |
| S14 | Every public task maps to a graph `.json` and embedding `.npz` > 100 B under `{repo_short}_{base_commit}` (min 591,348 / 885,713 B) | STATIC-OBSERVED (data) | README §5.2(6) gates the "Code Intelligence Tools" prompt block on such files existing "for `task.repo`" (OFFICIAL-DOCUMENTED). The scorer's lookup key and the actual advertisement are HOST-UNKNOWN (H30). |
| G1 | `git checkout HEAD -- <existing> <nonexistent> 2>/dev/null \|\| true` restores **nothing**, so an edited tracked test stays edited. With only existing paths the reset works | RUNTIME-REPRODUCED, **git only** (git 2.50.1 macOS; `harness_cert/scripts/git_semantics_probe.sh`) | Which paths the harness passes (H11); container git version |
| G2 | After `git add -N .`, a new `repro.py` appears in `git diff HEAD`; files under `build/` and `__pycache__/` with the S6 excludes do not | RUNTIME-REPRODUCED, **git only** | Harness-level `submit_patch` behavior (H12) |

## Per-H-ID status of the implementing source

Every row: **implementing source = HOST-UNKNOWN (not available locally)**, so nothing below is STATIC-OBSERVED harness code.

| H-ID | Expected location (OFFICIAL-DOCUMENTED names) | Documented claim | Related local evidence | Runtime test still required |
|---|---|---|---|---|
| H26 | `adk_submission` `!include` loader | `..` blocked → `PathTraversalError` | S1 contradicts | Compile `agents/baseline_v0_official` + `../../`, absolute, escaping-symlink variants |
| H02/H03 | `adk_submission` / `swegemma.models.registry` → LiteLlm | "use 0 or `include_thoughts: false` to disable thinking"; `thinking_level` → `reasoning_effort`; `litellm.drop_params = True` | S3; COMMUNITY-EVIDENCE C01 agrees with the README wording | Diff outgoing request bodies (scripted mock) |
| H01 | ADK/LiteLLM message conversion; vLLM `gemma4` reasoning parser | Not documented | COMMUNITY-EVIDENCE C02 | Request capture + exact vLLM |
| H04/H18 | `google.adk` tool lookup; `ToolRegistry` | Closed registry; silent on undeclared calls | S2; COMMUNITY-EVIDENCE C03/C04 | Mock emits undeclared / nonexistent calls |
| H06 | `swegemma/tools/workspace.py` | `start_line: int \| None` | COMMUNITY-EVIDENCE C07 | Mock with int and string args |
| H07/H17 | `@budget_gated` → ADK → LiteLLM | Tools return a JSON **string** | COMMUNITY-EVIDENCE C05 (double encoding) | Count escape layers in the next request |
| H08–H10 | `apply_replacement` (adk-eval-core) | 3-tier exact → flexible → regex | COMMUNITY-EVIDENCE C05/C06 | Fixture edits with old_string copied from model-visible text |
| H11 | `harness/verification.py` | Reset via checkout + clean, errors suppressed | G1 (git only); COMMUNITY-EVIDENCE C08 | `verify_task` with a patch touching a tracked test + new `sitecustomize.py` |
| H12 | `submit_patch` / fallback | `git add -N .` + diff vs baseline | S6, G2 (git only) | Harness-level run |
| H13/H14 | `harness/agent_runner.py` | Fallback on finish / nudges / budget / timeout | — | Mock: edit, then time out or exhaust budget |
| H29 | `@budget_gated(count_tool_call=False)` | `get_status` / `submit_patch` free | — | Mock with `max_tool_calls=2` |
| H30 | `build_agent_prompt` | Block gated on data files | S14 | Capture the first request with graph tools undeclared |
| H20 | compiler → `AgentTool` | `skip_summarization` supported | S2 | Diff parent requests true/false |
| H15/H19 | `scripts/inference.py` → `EventsCompactionConfig` | interval 5, overlap 2, threshold 14,336, retention 5 (Getting Started notebook: 15) | COMMUNITY-EVIDENCE C09 | Long mock trajectory with a sentinel |
| H27 | `discover_adapters`, `VllmServer` | `--lora-modules` from discovered adapters | S5 | Inspect argv with no `adapters/` |
| H23 | — | — | Local wheelhouse = sandbox deps (STATIC-OBSERVED) | Kaggle CPU-notebook capture |

## Corrections from rev 1

| Rev 1 wording | Problem | Rev 2 |
|---|---|---|
| S7: "Offline anyway (`network_mode=none`), so the fallback fails quietly. Low risk" | Runtime conclusion from static code plus Docker-only documentation | S7a/b/c: online-capable fallback, no `--no-index` / `--no-build-isolation`, no timeout, all STATIC-OBSERVED; runtime reachability and network HOST-UNKNOWN |
| S7 did not mention missing timeouts | Omission | S7b |
| S12: "the extraction path is the one in use" | Runtime claim | S12: data fact is STATIC-OBSERVED; scorer's source of required tests HOST-UNKNOWN |
| S12: "no `## Hints` section is ever emitted on public tasks" | Runtime claim | S13: documented condition + data fact; emission HOST-UNKNOWN |
| S13: "the graph-tool block is advertised on every public task" (conditional) | Read as fact | S14: data fact + documented gate; lookup key and advertisement HOST-UNKNOWN |
| H11/H12 "Git-level REPRODUCED-LOCAL" | Label outside the vocabulary | G1/G2: RUNTIME-REPRODUCED, git only |
| H07/H17: "structurally consistent with the double-encoding report" | Implied support from static reasoning | Moved to COMMUNITY-EVIDENCE only |
