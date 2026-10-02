# Submission Format & Build Pipeline

Evidence labels follow `STATIC_HARNESS_AUDIT.md`: **OFFICIAL-DOCUMENTED** (HARNESS_README), **STATIC-OBSERVED**, **RUNTIME-REPRODUCED**, **COMMUNITY-EVIDENCE**, **HOST-UNKNOWN**. Nothing about scorer behavior in this file has been runtime-reproduced.

## 1. What Kaggle receives

A `submission.zip` containing a **declarative agent bundle**: YAML configs, prompt text, optional skills, optional LoRA adapters and an optional budget config. There is no prediction file and no Python entrypoint (OFFICIAL-DOCUMENTED). The scorer unpacks it, validates and compiles it into a Google ADK agent tree, serves the competition model, and runs that agent on hidden tasks (OFFICIAL-DOCUMENTED). Exactly how the ZIP is handed to the scoring notebook (upload path, notebook wrapper) is HOST-UNKNOWN until H28.

## 2. Bundle structure (archive root = bundle root)

| Path | Role | Packaged by our builder |
|---|---|---|
| `agent.yaml` \| `agent.yml` \| `root_agent.yaml` \| `root_agent.yml` | Root agent. **Exactly one.** | yes |
| `eval_config.yaml` | Optional per-task budgets under `evaluation:` | yes |
| `configs/`, `prompts/`, `sub_agents/`, `skills/`, `adapters/` | Contract directories | yes, whole directory |
| any other file reached by `!include` or `config_path` | Referenced content | yes |
| anything else (`README.md`, notes, `*.example`) | Not part of the contract | **no** (`NOT_PACKAGED` warning) |
| `.DS_Store`, `__pycache__/`, `*.pyc`, caches, `.gitkeep` | Junk | **no** (`JUNK_EXCLUDED` warning) |
| `.env*`, keys, `kaggle.json`, credentials; dataset dirs/archives (`snapshots/`, `*.tgz`, `*.whl`, `*.npz`, `*.jsonl`) | Must never ship | **validation error, build refused** |

Documented constraints the validator enforces (OFFICIAL-DOCUMENTED, re-implemented statically; not compiler source):
- agent schema: non-empty `name`; `agent_class` ∈ LlmAgent / SequentialAgent / ParallelAgent / LoopAgent; LoopAgent `max_iterations` int 1–500; string `description`/`instruction` (≤ 1,000,000 chars each, ≤ 10,000,000 instruction chars total); `include_contents` ∈ default / none; boolean `disallow_transfer_*`; documented shapes for `tools` (built-in name, `agent_tool: {config_path, skip_summarization}`, inline agent) and `sub_agents` (`{config_path}` or inline agent); ≤ 500 agents; nesting depth ≤ 50; ≤ 1,000 skills, ≤ 50 MiB each; cumulative `!include` expansion ≤ 50 MiB; include depth ≤ 10;
- generation: `temperature` ≥ 0, `top_p` ∈ [0, 1], `top_k` ≥ 1, `max_output_tokens` 1–32768, numeric penalties, int `seed`, list-of-string `stop_sequences`, string `response_mime_type`, `thinking_budget` 0–32768, boolean `include_thoughts`, `thinking_level` ∈ MINIMAL/LOW/MEDIUM/HIGH/NONE (case-insensitive); excluded fields `tools`, `system_instruction`, `http_options`, `safety_settings`, `response_schema` are errors; undocumented fields are warnings, not errors;
- one base model, `gemma-4-31b-it-qat-w4a16-ct`, after stripping `openai/` `google/` `hosted_vllm/` `custom/`;
- extensions `.yaml .yml .md .txt .py .json .safetensors`;
- unpacked size **< 3,221,225,472** bytes; ≤ 10,000 files; ≤ 1,000 YAML files;
- no symlinks, no absolute or escaping paths;
- the 9 built-in tool names;
- forbidden `generate_content_config` fields; `max_output_tokens` 1–32768; `thinking_budget` 0–32768.

Repo policies stricter than the README:
- `.py` only under `skills/`;
- `.safetensors` only under `adapters/`;
- every `LlmAgent` declares `model` explicitly;
- every `.safetensors` passes a container sanity check (8-byte header length, UTF-8 JSON object header, in-bounds non-overlapping offsets, byte length = shape × dtype). Passing means **SAFETENSORS_CONTAINER_VALID** only, never adapter/model compatibility (H24/H25);
- malformed input (recursive aliases, alias bombs, unhashable keys, wrong shapes) yields stable error codes and exit 1, never a traceback;
- `TEMPLATE_PLACEHOLDER` anywhere in a packaged file blocks the build.

## 3. Stage 1: agent execution (OFFICIAL-DOCUMENTED; runtime NOT-REPRODUCED)

Per task:
1. A sandbox (Container A) gets the repo snapshot at `base_commit`, an editable install, harness-written `pytest.ini`/`conftest.py`, and a `baseline` commit.
2. The root agent receives the task prompt and works through the 9 tools under the budgets.
3. The patch is the `git diff` captured by `submit_patch`, or by the fallback extraction if it is never called.

The output is one predicted patch per task.

## 4. Stage 2: patch verification (OFFICIAL-DOCUMENTED; runtime NOT-REPRODUCED)

A fresh Container B:
1. rebuilds the baseline;
2. applies the patch (4 fallback passes);
3. resets protected test/config paths;
4. applies the hidden `test_patch`;
5. runs pytest with JUnit XML.

A task is resolved only if pytest exits 0 and every required test passed. The score is the resolution rate.

## 5. Source candidate vs generated ZIP

| | Source candidate | Generated artifact |
|---|---|---|
| Where | `agents/candidates/<candidate-id>/` (committed) | `artifacts/submissions/<candidate-id>/` (**gitignored**, never committed) |
| What | Editable bundle files, may contain README/notes | `submission.zip`, `manifest.json`, `provenance.json`, `SHA256SUMS` |
| Identity | git commit + `source_tree_sha256` | `submission_zip_sha256` |

The official control `agents/baseline_v0_official/` is a historical artifact. It is never edited: its `../` includes are reported (H26), not fixed.

## 6. Deterministic build

```bash
uv run python -m tools.build_submission agents/candidates/<candidate-id> \
    --experiment-id EXP-YYYYMMDD-NNN --require-clean      # --out-dir to override artifacts/submissions/<id>/
                                                         # --dataset-root DIR (else $GEMMA4_DATASET_ROOT; one is required)
```

**Write safety** is one immutable `WriteGuard`: the protected dataset root is passed explicitly (`--dataset-root`, else `$GEMMA4_DATASET_ROOT`; both are protected when both are known). Every writing step (builder, E0 restore, manifests) receives it, and every output path is checked before anything is created. The CLI fails closed when no dataset root is known.

**`--require-clean`** proves, per packaged file, that it is tracked, present in HEAD, has an index entry equal to HEAD, and has working-tree bytes (`git hash-object --no-filters`) equal to the HEAD blob. Ignored-only, untracked, staged-new, staged-different and modified files all fail, and so does any other uncommitted change in the repository. There is no override. `source_in_git` is true only when this proof passes.

The builder:
1. Guards every output path first: never at or below `$GEMMA4_DATASET_ROOT`, never inside the source.
2. Validates the source and refuses on any structural error, writing nothing.
3. Writes the packaged set to a staging ZIP:
   - entries sorted by POSIX path; no directory entries; no wrapper directory (`agent.yaml` at archive root);
   - `ZIP_STORED`, so output is independent of zlib versions;
   - timestamps fixed at 1980-01-01 00:00:00; Unix mode 0644; no extra fields; no comment.
4. Inspects the staged ZIP (safe extraction + re-validation, temp dir deleted). Only then publishes it.
5. Writes:
   - `manifest.json`: deterministic; per-file sha256/bytes, the ZIP policy and the full validation report;
   - `provenance.json`: build time, git state and environment fingerprints;
   - `SHA256SUMS`: zip + manifest.

**Determinism claim (RUNTIME-REPRODUCED locally):** byte-identical source gives a byte-identical `submission.zip` and `manifest.json`. `provenance.json` differs between builds only by `build_time_utc` and git state.

`--require-clean` refuses unless the source is inside a clean git work tree. Use it for anything that may go to Kaggle.

### Official control (E0)

```bash
uv run python -m tools.package_official_control --dataset-root "$GEMMA4_DATASET_ROOT"
```

E0 provenance is truthful about origin: 6 files `git-tracked`, the 4 gitignored adapter files `external-official-control` (sha256 equal to the live dataset sample), and `source_in_git: false`. That label exists only in this E0 tool. It never upgrades the git proof, and normal candidates have no equivalent.

This command:
1. Restores the gitignored adapters from the dataset, never overwriting, and verifies the tree hash.
2. Validates the sample.
3. Builds `artifacts/submissions/e0_official_control/`.
4. Asserts every packaged file's sha256 equals the official sample's.

## 7. Validation

```bash
uv run python -m tools.validate_submission <bundle-dir> [--json]     # exit 0 STRUCTURAL_VALID, 1 STRUCTURAL_INVALID
uv run python -m tools.inspect_submission <submission.zip> [--json]  # archive safety + policy + re-validation
uv run python -m tools.hash_submission <artifact-dir> [--dataset-root DIR] [--strict]  # VERIFIED / VERIFIED_WITH_UNVERIFIABLE_EVIDENCE / FAILED
```

Findings fall into three separate lists:
- **errors**: structural; block the build.
- **warnings**: excluded files, unreferenced adapters.
- **certification_issues**: behavior that depends on an uncertified harness, each tagged with an H-ID. These never produce a pass/fail verdict and never block a build; they are recorded in the manifest and provenance.

`harness_compatibility` is always **`CURRENT_HARNESS_COMPATIBILITY_UNKNOWN`** until H23 is resolved.

`!include` and `config_path` resolution: the README does not say what a relative path is relative to. The validator tries the including file's directory first, then the bundle root:

| Situation | Reported as |
|---|---|
| Path uses `..` | `H26_UNRESOLVED_PARENT_INCLUDE` |
| A nested file resolves only under one interpretation | `H26_REFERENCE_BASE_UNCERTIFIED` |
| Both interpretations exist and differ | `H26_REFERENCE_BASE_AMBIGUOUS` |
| Escaping the bundle, absolute paths, missing targets, cycles, depth > 10 | errors |

**Candidates we create should keep every referencing YAML at the bundle root**, where both interpretations coincide, and never use `../`.

## 8. Provenance (`provenance.json`, outside the ZIP) and its verification

`hash_submission` trusts files, not provenance. Each check ends in exactly one of `ok`, `mismatch` or `unverifiable`. Informational fields get `informational`. Nothing is coerced from one state to another.

| Reported `class` | When | State |
|---|---|---|
| **recomputed** | The fact was re-derived from evidence available here. Always applies to the ZIP-local facts: zip sha256/bytes, SHA256SUMS, manifest sha256, archive members, per-file hashes and sizes, unpacked bytes (≥ 0), source tree hash, fresh structural validation (must be STRUCTURAL_VALID), certification codes, adapter hashes, `lora_present`. Also applies to provenance-internal origin consistency (label ↔ `git_status`; `source_in_git` ↔ "every origin is git-tracked"). Applies to the following *when their evidence is present*: repo-metadata fingerprints (split hashed directly from `v1.json`, sidecar cross-checked); live dataset hashes; E0 official-sample origins; git-backed origins (blob at the recorded commit) | `ok` / `mismatch` |
| **environment** | The evidence a fact needs is not available here: repo metadata missing from this checkout, no live dataset, no official sample, or the recorded commit / a needed tree object missing from the clone being checked | `unverifiable` |
| **informational** | `build_time_utc`, `git_dirty`, `experiment_id`, `builder_version` | `informational` (reported, never verified, never `ok`) |

**Git evidence for `source_in_git: true`** (the field records that the *build* proved Git backing; the verifier never rewrites it):
1. Facts observable from provenance alone are always checked, whatever the clone: a git-tracked origin without a commit/source path, a label contradicting `git_status`, or `source_in_git` disagreeing with the origins. Any contradiction is a `mismatch`.
2. Each git-tracked file is classified on its own:
   - recorded commit and tree objects available: blob equal to the archive member → `ok`; different or absent at that path → `mismatch`;
   - commit or a needed tree object unavailable → `unverifiable`, for that file only.
3. The aggregate `git_evidence` is `FAILED` if any file or internal fact mismatches, else `UNVERIFIABLE` if any file is unverifiable, else `VERIFIED`. It is `NOT_CLAIMED` when `source_in_git` is false. A partial clone therefore still fails on every contradiction it can observe.

**Overall status and exit code:**

| Status | Meaning | Exit |
|---|---|---|
| `FAILED` | at least one `mismatch` | 1 |
| `VERIFIED_WITH_UNVERIFIABLE_EVIDENCE` | no mismatch, at least one `unverifiable` fact; every unverifiable fact is listed. Not a pass for those facts | 0 (2 with `--strict`) |
| `VERIFIED` | no mismatch and nothing unverifiable | 0 |

| Group | Fields |
|---|---|
| Identity | `candidate_id`, `experiment_id`, `builder_version`, `provenance_schema` |
| Build | `build_time_utc` (metadata only) |
| Git | `git_commit`, `git_dirty`, `git_source_dirty` |
| Content | `source_tree_sha256`, `source_files` (per-file sha256), `submission_zip_sha256`, `submission_zip_bytes`, `unpacked_bytes`, `manifest_sha256` |
| Environment | dataset manifest sha256, `tasks.jsonl` sha256, `HARNESS_README` sha256, wheelhouse fingerprint, split version + sha256, `harness_versions` (`null` until H23) |
| LoRA | `lora_present`, `adapter_hashes` |
| Validation | validation summary |

Environment values come from committed `vendor_meta/` and `eval/splits/`, not from re-reading the dataset.

## 9. What is currently certified

| Area | Status |
|---|---|
| Builder determinism, entry policy, Zip Slip / duplicate / normalized-duplicate / symlink / absolute-path / comment / extra-field rejection, output-path guard, git source proof, independent provenance verification | RUNTIME-REPRODUCED **locally** (`tests/test_submission.py`, `tests/test_safety.py`, `tests/test_corrective_audit.py`) |
| E0 control packaged byte-identically to the official sample (zip sha256 `25d07b64…`) | RUNTIME-REPRODUCED locally |
| Validator rules | STATIC: re-implemented from README documentation, **not** from adk-submission source |
| The scorer accepting any of our ZIPs | **Not certified** |

## 10. Still blocked

- **H23**: harness versions/source unknown. Our validator may disagree with the real compiler in either direction.
- **H26**: `../` includes and the include base directory. The official control depends on them.
- **H28**: actual Kaggle submission path for a built ZIP.
- **H02/H03, H04/H30, H20, H16/H24/H25**: flagged per bundle as certification issues.
- Runtime certification overall (H01–H22, H27, H29): nothing about Stage 1 / Stage 2 behavior has been reproduced.
