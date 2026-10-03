# H23 — Harness Provenance Capture Plan (STEP 0B)

**Status of H23 is unchanged: `HOST-UNKNOWN`.** This document prepares a capture; nothing here has been run on Kaggle.

Evidence labels: OFFICIAL-NOTEBOOK, COMMUNITY-EVIDENCE, STATIC-OBSERVED, HOST-UNKNOWN (as in `STATIC_HARNESS_AUDIT.md`).

---

## Phase A — How the official workflow obtains the harness

### Source inspected

| File | sha256 | Run recorded in its metadata |
|---|---|---|
| `~/Downloads/getting-started-gemma-4-developer-agent.ipynb` (official Getting Started; not in this repo) | `0042d8427b7f179f987272b013a74d98ae5fadf1eea8859705214e25f1286346` (equals the hash recorded in `docs/MASTER_HANDOFF.md` §1.1) | papermill 2026-09-27 19:51:10 → 20:06:37 UTC; `language_info.version` 3.12.13 |

Kaggle metadata of that run (`metadata.kaggle`):
- `accelerator: nvidiaL4`, `isInternetEnabled: false`;
- `dataSources`: `competition 149921`, **`datasetVersion 20058023`**, `modelInstanceVersion 897258`.

Cell references below are **0-based indices** into `cells` (index 0 is the title markdown).

### Findings (OFFICIAL-NOTEBOOK unless labelled otherwise)

1. **Bootstrap = cell 2** (first code cell, `execution_count` 1). This is the only installation step in the notebook. It:
   - sets runtime env vars: `LITELLM_LOCAL_MODEL_COST_MAP`, `TRANSFORMERS_NO_TF`, `VLLM_*`, `OTEL_SDK_DISABLED`, `PYTORCH_CUDA_ALLOC_CONF`;
   - points `WHEELHOUSE_DIR = Path('/kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse')`;
   - deletes `*cutlass*.pth` under `/usr/local/lib/python*/{dist,site}-packages`;
   - symlinks every `*.whl` (except cutlass) into `/tmp/wheelhouse`, restoring `'+cu128'` in filenames ("stripped by Kaggle dataset uploads");
   - runs `pip install -q --no-deps --force-reinstall` over the **sorted, selected non-cutlass wheels** (`subprocess.run(..., check=True)`, so a pip failure stops the notebook), then `importlib.invalidate_caches()`.

   Its recorded output: `Installing 41 wheels from /kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse...`.
2. **Source of the packages:** a separate Kaggle dataset, `metric/gemma-4-developer-agent-wheelhouse`, mounted as an input (the `datasetVersion 20058023` data source). They do not come from the competition data, which holds only the sandbox test-dependency wheels (see `H23_WHEELHOUSE_FINGERPRINT.md`). They do not come from the network either: internet is off, and `--no-deps` with explicit wheel paths needs no index.
3. **Which of the target distributions are among the 41 wheels is not shown** (the wheel list is not printed). The notebook never prints a package version.
4. **Imports performed by the notebook:**

   | Cell | Imports |
   |---|---|
   | 4 | `from swegemma.models import load_tasks` |
   | 6 | `from swegemma import graph as sg` |
   | 8 | `import litellm`, `import torch`, `from adk_submission import VllmConfig, VllmServer, discover_adapters`, `from swegemma.config import ALLOWED_ADAPTER_EXTENSIONS`, `from swegemma.models.discovery import validate_single_declared_model` |
   | 10 | `google.adk.agents.context_cache_config.ContextCacheConfig`, `google.adk.apps._configs.EventsCompactionConfig`, `swegemma.config.EvalConfig/build_submission_limits`, `swegemma.evaluate.Evaluator` |
   | 12 | `swegemma.config.ALLOWED_SUBMISSION_EXTENSIONS/MAX_SUBMISSION_SIZE_BYTES` |

   Not imported directly: `adk_eval_core`, `google.genai`, `vllm` (`VllmServer` starts it; how is HOST-UNKNOWN), `transformers`.
5. **Location evidence:** cell 10 stderr shows `/usr/local/lib/python3.12/dist-packages/google/adk/features/_feature_decorator.py`. pip installs into that same location, so this does **not** tell whether `google-adk` came from the wheelhouse or the base image. The capture decides it.
6. **The scorer path is not in the notebook.**
   - The notebook contains no `scripts/inference.py`, `scripts/metric.py`, `setup_vllm_server`, `load_submission_eval_config` or `ALLOWED_MODEL_NAMES`; HARNESS_README attributes all five to the scorer.
   - Its own vLLM config uses `gpu_memory_utilization=0.90` and compaction interval 15, while the README says the scorer uses 0.80 and 5.
   - Cell 12 packages `submission.zip` with `shutil.make_archive`, but how Kaggle hands a bundle to the scorer is not shown (H28).

### Supporting COMMUNITY-EVIDENCE (`additional info/*.ipynb`; not authoritative)

| Notebook (run end, UTC) | Data sources | Observation |
|---|---|---|
| `gemma-4-budget-aware-coding-agent` (2026-10-01) | competition only, CPU, offline | cell 4 output: `Official package unavailable…`. `adk_submission` was **not importable without the wheelhouse** in that image |
| `black-cat-swe-agent-pack-instinct` (2026-09-29) | competition + `datasetVersion 20058023`, CPU | cell 13 installs wheels whose names start with `google_adk-, google_genai-, adk_submission-, adk_eval_core-, swegemma-` using `--no-deps`; compile succeeded |
| `gemma-4-walkthrough-first-submission` (2026-09-30) | competition + `20058023`, CPU | cell 34: same five-prefix install, "as the hosts' notebook does"; `compile_submission: ok` |
| `gemma-and-the-shape-of-doubt` (2026-09-30) | competition + `20058023` + `19959752`, L4 | pins `swegemma==0.2.7`, `adk-submission==0.2.11`, `adk-eval-core==0.1.0`, `google-adk==1.36.1`, `google-genai==2.11.0`, `vllm==0.19.1`, `transformers==5.13.1` (historical; not reproduced) |

### Answers to the four Phase A questions

| Question | Answer | Evidence |
|---|---|---|
| How are the harness packages obtained? | `pip install --no-deps --force-reinstall` of all selected **non-cutlass** wheels of the mounted `metric/gemma-4-developer-agent-wheelhouse` dataset (recorded data source `datasetVersion 20058023`; output "Installing 41 wheels"). This does not mean the hidden scorer uses that dataset version | OFFICIAL-NOTEBOOK cell 2 |
| Preinstalled / local competition files / wheels / network / mounted resource / indirect? | **Wheels from a mounted Kaggle dataset** (not the competition data, not the network). Not preinstalled for `adk_submission` per one community CPU run. For `google-adk`, `google-genai`, `litellm`, `vllm`, `transformers`: wheelhouse vs base image is **not determinable from the notebook** | cell 2; COMMUNITY (budget-aware cell 4) |
| Exact bootstrap commands | Cell 2 as summarised above (`.pth` cleanup, `+cu128` rename symlinks, `pip install -q --no-deps --force-reinstall <sorted non-cutlass wheels>` with `check=True`) | cell 2 |
| Scorer-only components unavailable to an interactive notebook? | The scorer entry points (`scripts/inference.py`, `scripts/metric.py`) and the scorer's wheelhouse/image version at scoring time are not referenced by the notebook. Whether they ship inside the wheels is unknown; the capture's source index searches for them | cells 2–12 |

---

## Phase B–G — The capture notebook (rev 2; rev 3 corrections in the next section take precedence)

**Design rule: no claim over a weak claim.** The logic lives in `tools/h23_capture_core.py` (stdlib only). `tools/build_h23_notebook.py` embeds it **verbatim** as the notebook's code cell, followed by a three-line run cell. A test fails if the committed notebook differs from the module.

### Capture states

| State | Set by | Meaning |
|---|---|---|
| `BOOTSTRAP_NOT_RUN` | notebook | Install skipped (local dry run) or wheelhouse not attached |
| `BOOTSTRAP_FAILED` | notebook | The official `pip install` returned non-zero (or raised). **Fail closed:** no package gets a bootstrap attribution, no source is captured, the archive is named `…_BOOTSTRAP_FAILED.zip` and its README says it is not usable for H23. No automatic fallback |
| `BOOTSTRAP_SUCCEEDED` | notebook | pip returned 0. Packages are still attributed only from verified content (below) |
| `CAPTURE_VALIDATED` | importer | A `BOOTSTRAP_SUCCEEDED` archive that passed every integrity and semantic check |

### Official bootstrap (`official_all`, the default) and the non-official mode

- **Selection:** sorted `*.whl` minus cutlass, symlinked into a **fresh** `/tmp/wheelhouse` with the official `'+cu128'` restoration (a name collision aborts).
- **Command:** `[sys.executable, -m, pip, install, -q, --no-deps, --force-reinstall, <sorted links>]`, using the current kernel interpreter.
- **Plan first:** `bootstrap_plan.json` (command, selected wheels with sha256 and wheel METADATA, exclusions) is written into the capture **before** pip runs. Return code and sanitized stdout/stderr tails are recorded after.
- **`harness_only`** must be set explicitly (`H23_BOOTSTRAP_MODE=harness_only`). It is labelled **`NON_OFFICIAL_PARTIAL_BOOTSTRAP`** everywhere and can never yield VERSION-SPECIFIC.

### Per-target evidence model

| Field | Values |
|---|---|
| `pre_state` / `post_state` | `PRESENT`, `ABSENT` |
| `pre_fingerprint` / `post_fingerprint` | sha256 over sorted `(RECORD path, actual size, actual sha256)` of the distribution's regular files. Bytecode and per-install dist-info files (RECORD, INSTALLER, REQUESTED, direct_url.json) are excluded. Missing or unsafe entries are listed, and then `complete: false` |
| `change` | `INSTALLED`, `UNCHANGED` / `REPLACED` (only from two complete fingerprints), `UNDETERMINED`, `REMOVED`, `ABSENT` |
| `bootstrap_attribution` | `OFFICIAL_FULL_BOOTSTRAP` / `NON_OFFICIAL_PARTIAL_BOOTSTRAP` only if pip succeeded, exactly one planned wheel matches name+version, the post fingerprint is complete, and **every package file in that wheel equals the installed file byte-for-byte**. Otherwise `BASE_IMAGE` (unchanged, no matching wheel), `NOT_INSTALLED` or `UNKNOWN` |
| `source_status` | `SOURCE_CAPTURED` (≥ 1 legitimate source/resource file, no unsafe RECORD entry, no normalized collision), `SOURCE_NOT_CAPTURED`, `SOURCE_RECORD_UNAVAILABLE` |
| `metadata_status` / `record_status` | `METADATA_CAPTURED` / `METADATA_UNAVAILABLE`; `RECORD_CAPTURED` / `RECORD_UNAVAILABLE` |
| `scorer_relationship` | always `SCORER_ONLY_UNKNOWN` |
| `evidence_strength` | computed **only by the importer** |

`install_status` is kept for continuity: it is `PACKAGE_BOOTSTRAPPED_FROM_OFFICIAL_NOTEBOOK` exactly when the attribution is `OFFICIAL_FULL_BOOTSTRAP`.

### Security properties

- **No target code runs.** No `import`/`importlib.import_module`/`find_spec` of swegemma, adk_*, google.*, litellm, vllm, transformers or torch. Evidence comes from `importlib.metadata` + RECORD only. Module names are inferred from RECORD-owned files (`module_candidates_from_record`). There is no "importable" field. An AST test enforces all of this.
- **One RECORD resolver.** It rejects absolute, drive, backslash and NUL paths, `''`/`.`/`..` components, and **any symlink component** (dangling or internal). It requires the canonical target to be a regular file **contained** (`Path.relative_to`, never a string prefix) in the distribution's canonical install root.
- **One staging resolver.** Each destination is built component by component below the per-distribution staging directory, rejects symlinks and collisions, and is written with `O_CREAT|O_EXCL|O_NOFOLLOW`.
- **Credentials.** Only the dist-info files METADATA, WHEEL, INSTALLER, REQUESTED, top_level.txt and entry_points.txt are captured, all sanitized: URL userinfo, query and fragment stripped; known token/key/password patterns redacted. Original and captured sha256 plus a redaction count are recorded. **`direct_url.json` is never archived**; only a structural summary (sanitized URL, kind, flags) and the original's sha256 are kept. Environment values exist only for an allowlist and are sanitized. pip output tails are sanitized.

### Archive

`h23_harness_capture_<UTC>_<STATE>.zip` contains `h23_capture/` with:
- `environment.json` (run_context, config_overrides, bootstrap record);
- `bootstrap_plan.json` (when a bootstrap ran);
- `installed_distributions.json`, `wheelhouse.json`, `packages.json`, `source_files.json`, `source_index.json`;
- `metadata/<ns>/`, `records/<ns>.RECORD`, `sources/<ns>/`;
- `CAPTURE_README.md`, `hashes.json`.

`<ns>` is `swegemma`, `adk_submission`, `adk_eval_core`, `google_adk`, `google_genai`, `litellm`, `vllm` or `transformers`.

`run_context` is `kaggle_kernel` only when `KAGGLE_KERNEL_RUN_TYPE` is set and no configuration was overridden.

---

## Phase H — Local import (`tools/import_h23_capture.py`)

```bash
uv run python -m tools.import_h23_capture ~/Downloads/h23_harness_capture_<UTC>_<STATE>.zip \
    --harness-root ~/gemma4-harness-evidence --dataset-root "$GEMMA4_DATASET_ROOT" \
    --attest-notebook-version <Kaggle notebook version> --attest-wheelhouse-version <dataset version>
```

1. **Archive safety and integrity.**
   - Regular files under `h23_capture/` only, with canonical paths: no traversal, absolute paths, symlink or directory entries, or normalized duplicates.
   - Only the expected top-level members; bounded sizes.
   - `hashes.json` covers exactly every other member.
2. **Semantic validation** against a literal `TARGET_SCHEMA` (8 targets, fixed namespaces):
   - **Identity:** each target's namespace matches the schema; states and attribution are consistent with the bootstrap state and mode.
   - **Fingerprints:** must be internally consistent.
   - **Wheel evidence:** `UNCHANGED`/`REPLACED` must match the fingerprints. Official or partial attribution requires the wheel to be in the plan *and* in the wheelhouse inventory with the same sha256 and version, and the content check to be verified.
   - **Metadata/RECORD:** references must point at real captured members with the right hash.
   - **Sources:**
     - `SOURCE_CAPTURED` requires `archive_dir == sources/<ns>`, a non-empty file list, and sizes (integers ≥ 0) and sha256 that equal the actual members;
     - the counts and tree hash must be recomputed and match, and metadata + RECORD must have been captured;
     - no orphaned, unknown-namespace or cross-distribution members are allowed.
   - **Bootstrap plan:** it equals the recorded plan, the command is exactly the official invocation over the planned sorted wheels, and the pip return code agrees with the state.
   - **Credentials and malformed input:** `direct_url` summaries must be sanitized, and no credential-like content is allowed in any non-source member. Malformed input yields `CaptureError` (`MALFORMED_CAPTURE`), exit 1, and no traceback.
3. **Destination safety.**
   - The harness root is canonicalized once.
   - Below it, every component (`h23`, `<capture_id>`, `h23_capture`, file parents) is checked: any symlink is rejected, even one pointing inside the root.
   - Each directory is created one at a time and **re-resolved after creation**, which catches a swap-to-symlink race. It is then checked against the Git repository, the dataset roots, interpreter prefixes and site-packages.
   - Files are written exclusively. A partially extracted capture is removed.
4. **Immutable evidence.**
   - Absent → create.
   - Byte-identical → idempotent.
   - Different → **`EVIDENCE_CONFLICT`**, for both the extracted directory and `vendor_meta/h23_captures/<capture_id>.json`.
5. **Promotion.** `evidence_strength = VERSION-SPECIFIC` **only if** all of the following hold. Otherwise `NONE`, with every reason listed.
   - `BOOTSTRAP_SUCCEEDED` with `official_all`;
   - `run_context == kaggle_kernel` with no overrides;
   - all three harness distributions `OFFICIAL_FULL_BOOTSTRAP` + `SOURCE_CAPTURED`, with every captured file matching its RECORD hash, and complete fingerprints;
   - both human attestations (notebook version, wheelhouse dataset version) supplied.
6. **What it does not change.** The summary keeps `scorer_relationship: SCORER_ONLY_UNKNOWN` and `h23_matrix_status: HOST-UNKNOWN` (the matrix is edited by hand). The importer never installs, imports or executes anything and starts no subprocess.

**Limit:** integrity and semantic checks cannot prove that an archive came from Kaggle. A deliberately forged archive is out of scope. The human attestation (notebook version + dataset version, checkable on Kaggle) is the provenance anchor.

---

## Rev 3 — evidence integrity (after the second STEP 0B audit)

**Rule: the archive is untrusted input.** The importer recomputes every claim; promotion is derived, never accepted.

### Archive additions (capture schema 3)

- `inventories/<ns>.pre.json` and `inventories/<ns>.post.json`: **every RECORD row** with its category, its RECORD hash and size, and the actual size and sha256.
- `wheels/<wheel>.json`: for each wheel used for attribution, its member list (name, bytes, sha256) and the wheel's own RECORD.
- The archive never contains `evidence_strength`, `capture_state` or `record_hash_match`. The importer rejects these keys as `CAPTURE_CONTRADICTION`.

### RECORD row categories (fingerprint completeness)

Every row is classified. A fingerprint is `complete` only if **all** rows are `ok`, `excluded_volatile` or `console_script`, a RECORD exists, and it was archived unredacted. These categories all force incompleteness:
- `malformed` (wrong field count, non-sha256 or badly encoded hash, non-integer size);
- `duplicate` (canonical path / Unicode / case);
- `outside_root`;
- `unsafe` (absolute, drive, UNC, backslash, `..`, any symlink component, dangling links);
- `missing`, `unreadable`;
- `hash_mismatch`, `size_mismatch` (actual bytes vs RECORD);
- `ambiguous_ownership` (two target distributions own the same installed file).

**`console_script` is deliberately narrow.** pip records console scripts as `../…/bin/<name>`; without this category, any distribution with a CLI could never be complete. A row qualifies only when all of these hold:
- its path is exactly `(../)+bin/<name>`;
- `<name>` is declared in the distribution's `entry_points.txt`;
- it resolves to the interpreter's scripts directory;
- it is a regular file whose bytes match the RECORD hash.

Everything else outside the root, e.g. `../outside.py`, is `outside_root`.

### Importer recomputation

| Claim | Recomputed from |
|---|---|
| Fingerprints, `complete`, categories, counts | The inventory rows. Every `ok`/`console_script` row's RECORD hash and size must equal its content evidence; `ok` rows must have safe paths; outside-root rows must be labelled so; duplicates must be labelled; console scripts must be declared. The post inventory must equal the captured RECORD row for row |
| `change` | Recomputed pre/post fingerprints |
| Ownership ambiguity | Overlapping `ok` paths across target distributions at the same location |
| Wheel identity | Must be the single planned wheel of that distribution. Filename (after `+cu128` restoration), wheel METADATA name/version, distribution name/version, plan entry, inventory sha/size and manifest must all agree. The manifest's members must match the wheel's own RECORD |
| `matched / mismatched / absent_in_install / extra_in_install` | Wheel manifest vs post inventory. Claimed counters must equal the recomputed ones. A strong attribution needs `matched > 0`, the other three `0`, and a complete post fingerprint |
| Source ownership / `record_hash_match` | `verify_source_claims`: per claim, a canonical unique path and exactly one owning RECORD row; actual bytes = claim = RECORD hash and size = installed file |
| Bootstrap consistency | **Contradictions fail**: `skip_install`+result, `dry_run`≠`skip_install`, `pip_executed` vs state, pip result without execution, return code vs state, simulated pip without the override, kernel context with overrides, plan ≠ inventory selection, command ≠ official invocation over the planned sorted wheels |

### Promotion (derived)

`VERSION-SPECIFIC` ⇐ all of the following:
- schema valid;
- bootstrap succeeded, with `official_all`, not dry-run or skip-install, real pip executed with return code 0;
- Kaggle kernel context with no overrides;
- both attestations supplied;
- scorer relationship `SCORER_ONLY_UNKNOWN`;
- for each harness distribution:
  - attribution `OFFICIAL_FULL_BOOTSTRAP`, recomputed wheel/install match strong, post fingerprint complete;
  - source captured **and** verified, with every source file's RECORD hash present and equal;
  - METADATA verified, RECORD verified.

Each primitive is individually load-bearing (`test_each_promotion_primitive_is_required`, plus a 42-mutant sweep with 0 survivors).

### Race-safe extraction (`tools/safe_fs.py`)

- The canonical harness root is the trust anchor. It is opened with `O_DIRECTORY|O_NOFOLLOW` and confirmed via fstat/lstat inode equality.
- Below it, every directory is created and opened **relative to its parent's fd** with `O_NOFOLLOW`, and files are created with `O_EXCL|O_NOFOLLOW`.
- Evidence is written into `h23/.staging-<cid>-<random>`, then renamed to `h23/<cid>` relative to the same directory fd.
- All semantic checks run before the first write. A pre-existing or raced symlink makes the open fail before any byte is written. A path swapped after opening cannot redirect writes, which follow the opened inode.
- The platform must support `dir_fd`, `O_NOFOLLOW` and symlink-safe rmtree, or extraction refuses.

### Credential sanitizer

- One recursive `sanitize_value(v) -> (value, did_redact)`, with the flag ORed upward.
- Sensitive **keys** are matched case-insensitively, `-`/`_` tolerant, including suffixed forms like `x_api_key`. The full list:
  - `api_key`, `apikey`, `token`, `access_token`, `refresh_token`, `id_token`;
  - `password`, `passwd`, `pwd`;
  - `secret`, `client_secret`;
  - `authorization`, `auth`;
  - `signature`, `sig`;
  - `credential(s)`, `cookie`, `session`, `private_key`, `bearer`.

  Their values become `<REDACTED>`.
- **Value** patterns cover URL userinfo/query/fragment, `ghp_`, `github_pat_`, `gh?_`, `hf_`, `sk-`, `AKIA`/`ASIA`, `xox?-`, `Bearer …`, and `key=value`/`"key": "value"` forms. Patterns are token-boundary aware, so RECORD base64 hashes are not mangled.
- Every archived JSON document passes `sanitize_value`; every text member passes `sanitize_text`.
- `direct_url.json` is never archived. Its summary keeps the sanitized URL, kind, allowlisted VCS fields, dropped field names, the original sha256 and a nested `redacted` flag.
- The importer independently rejects unredacted sensitive keys anywhere in a JSON document and credential-like values in any non-source member.

---

## Why a normal Kaggle CPU notebook cannot capture everything

| Gap | Why | Consequence |
|---|---|---|
| Hidden scorer's own environment | Scoring runs in Kaggle's private scoring pipeline (4×L4); an interactive notebook cannot attach to it | Every result stays `SCORER_ONLY_UNKNOWN`; H23 can become at most `VERSION-SPECIFIC` for the interactive bootstrap |
| Scorer entry points (`scripts/inference.py`, `scripts/metric.py`) | Not referenced by the official notebook; may not ship in the wheels | Source index `scorer_entrypoints` shows whether any exist in the captured distributions |
| Wheelhouse version used by the scorer at scoring time | The dataset can get new versions; the notebook sees the version attached at run time, and the version number is not visible from inside the kernel | The human records the attached dataset version; a mismatch with later scorer behavior stays possible |
| GPU/vLLM runtime behavior (parsers, reasoning, LoRA) | CPU only by design; vLLM is not started | Source is indexed/fingerprinted; behavior still needs runtime certification |
| CUDA wheels on a CPU image | `official_all` installs the cu128 wheels exactly as cell 2 does; nothing is imported, so CUDA availability is irrelevant to the capture. If pip itself fails on the CPU image, the capture is `BOOTSTRAP_FAILED` | A failed official install is evidence about the CPU image, not the scorer. `harness_only` is only a separate non-official experiment |

---

## Human run instructions (Kaggle)

1. On the competition page open **Code → New Notebook**, then **File → Import Notebook** and upload `notebooks/h23_harness_capture.ipynb`.
2. Settings: **Accelerator: None**, **Internet: Off**, Environment: leave the default and record which one it is.
3. **Add Input:**
   - the competition data (`gemma-4-developer-agent`);
   - the dataset **`metric/gemma-4-developer-agent-wheelhouse`** (search "gemma-4-developer-agent-wheelhouse"). **Record the version number shown** (the official run used `datasetVersion 20058023`).

   Check it mounts at `/kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse`. If not, cell 4 falls back to a glob and records `wheelhouse_found_by`.
4. **Save Version → Save & Run All (Commit)**, so the output is tied to a notebook version. Do not use a GPU session.
5. When it finishes, copy the printed **CAPTURE STATE / SHA256 / SIZE / TARGET PACKAGE VERSIONS** and the notebook version number, then download `h23_harness_capture_<UTC>_<STATE>.zip` from the **Output** tab. It is the only output file.
   - If the state is **`BOOTSTRAP_FAILED`**, stop. Keep the archive as failure evidence and do **not** rerun in another mode as a substitute. A `harness_only` run is a separate, manually started experiment (new capture ID) and can never establish H23.
6. Locally:
   - `shasum -a 256 <zip>` must equal the printed CAPTURE SHA256;
   - then run the import command above with an external `--harness-root` and both `--attest-*` values (without them the result is `evidence_strength: NONE`).
7. Review `vendor_meta/h23_captures/<capture_id>.json` before committing it. Never commit the archive or the extracted source. The source is competition-provided harness code: keep it private to the team.
