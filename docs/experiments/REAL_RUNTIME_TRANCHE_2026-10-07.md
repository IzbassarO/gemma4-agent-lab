# Real-runtime admission tranche — 2026-10-07

Purpose: extend the audited synthetic solver/verifier runtime so the unchanged
E0 candidate can run on real public S1 tasks against a real model endpoint on a
Linux GPU host, with the same process-level leakage separation, and so the run
can be preregistered, executed by the operator, and forensically classified.
Scope is exactly what `EXP-20261007-001`
([DEV_E0_FORENSICS_V1_S1_PREREG.md](DEV_E0_FORENSICS_V1_S1_PREREG.md)) needs.
No agent, prompt, budget, adapter, split, screen, Kaggle or training change.

Three audiences, in order of execution:

| Section | Recipient | Role |
|---|---|---|
| A | **Codex** (implementer) | implement the tranche on a branch from clean `main` |
| B | **Claude Code**, fresh session (independent auditor) | read-only audit of the tranche before merge |
| C | **Operator** (the user) | Linux GPU launch of preflight controls and the S1 run |
| D | **Claude Code** (principal architect session) | post-run forensic classification and report |

Baseline facts the implementer must not rediscover:

- `eval/runtime.py:run_task` is the one-task coordinator: stage public assets
  into a fresh solver worker root, run a fresh confined solver process, reap
  it, archive, remove the root, only then read private test bytes, stage a
  fresh verifier root, run a fresh verifier process, seal `TaskRuntimeResult`.
- Admission is hard-wired to synthetic execution in four places:
  `eval/worker_common.py:validate_request` (`mode` ∈ {`isolation_probe`,
  `native_scripted`}, `model_endpoint == "SCRIPTED_ONLY"`),
  `eval/runtime.py:RuntimeConfig.__post_init__` (same modes, worker timeout
  ≤ 600 s), `eval/runtime.py:run_task` (`repo == "synthetic/probe"`),
  `eval/runtime_adapter.py:_admit` and `eval/verifier_worker.py` (synthetic
  repo/ID check).
- `eval/runtime.py:confined_worker_argv` is macOS `sandbox-exec` only and
  raises elsewhere; `_OS_READ_ROOTS` are macOS paths.
- The native functions already exercised by the synthetic path are the ones a
  real run needs: `swegemma.harness.agent_runner.run_agent_sandbox(manager,
  config, task, snapshot_path, context=...)` (compiles the submission itself via
  `adk_submission.compile_submission` from `config.submission_dir`) and
  `swegemma.harness.verification.verify_task(manager, config, task,
  snapshot_path, agent_patch=, agent_error=, trace=, start_time=)`. With empty
  `FAIL_TO_PASS`/`PASS_TO_PASS`, `verify_task` extracts required test functions
  from `test_patch` (`_extract_test_functions_from_patch`) and target files via
  `extract_test_files_from_patch`; this is the official public-row path (L06).
- `swegemma.models.registry.setup_gemma_model_registry(api_base=, num_retries=,
  adapter_manifest=, served_model=, backend="vllm")` binds the alias
  `gemma-4-31b-it-qat-w4a16-ct` and adapter names to an OpenAI-compatible
  endpoint; `adk_submission.discover_adapters(dir)` and
  `adk_submission.server.VllmServer(config).build_cmd()` produce the official
  server argv including `--lora-modules` (H27 local component).
- `swegemma.evaluate.Evaluator` is never used: it hydrates from a sibling
  `secret/` bundle and holds gold in the actor process (program B2 §4).
- The harness interpreter used by native tests is
  `/private/tmp/h23-cpu.UA1C8T/harness/bin/python` (Python 3.12.14, macOS). It
  is volatile; the Linux host gets its own venv from the v28 wheels plus a
  Linux lock.
- Dataset `wheels/` contains cp312 and cp313 builds of every compiled
  dependency, so the official subprocess sandbox works on Colab Python 3.12.
- The E0 ZIP and its member hashes are listed in the preregistration.

---

## A. Implementer prompt — Codex

> You are implementing the real-runtime admission tranche for `gemma4-agent-lab`
> described in `docs/experiments/REAL_RUNTIME_TRANCHE_2026-10-07.md` (this
> file) in service of `docs/experiments/DEV_E0_FORENSICS_V1_S1_PREREG.md`. Work
> on a branch from a clean `main` that already contains the audited synthetic
> runtime layer (`eval/runtime.py`, `eval/runtime_adapter.py`, workers, staging,
> result contracts). Do not change the synthetic execution semantics; every
> existing test must keep passing unchanged except where this spec says to
> extend a test. Do not touch `agents/`, `eval/splits/`, `harness_cert/matrix.yaml`,
> certification reports, or the Kaggle notebook. Stdlib only in `eval/` and
> `tools/` except PyYAML (D005/D010); native harness imports happen only inside
> worker-side `*_execute_real` functions after admission, mirroring
> `runtime_adapter.py`. Fail closed everywhere. Record `null` plus a reason for
> anything unobserved. When this spec and the code disagree on a native
> signature, follow the installed v28 source and note the difference in the
> admission doc.

### A1. Admit a `real_public` mode without loosening synthetic admission

1. `eval/worker_common.py:validate_request`: accept `mode == "real_public"`
   with `synthetic_case is None`, `runtime["sandbox"] == "subprocess"`, and
   `runtime["model_endpoint"]` an explicit `http://127.0.0.1:<port>/v1` URL
   (loopback only; refuse hostnames and non-loopback IPs). Require two new
   runtime keys in real mode: `budget` (closed dict `time_minutes` float,
   `tool_calls` int, `turns` int, `command_timeout_seconds` int) and
   `compaction` (closed dict with the README §7.2 values or `null`). Synthetic
   modes keep today's checks byte-for-byte.
2. `eval/runtime.py:RuntimeConfig`: allow `mode="real_public"`; add fields
   `model_endpoint: str = "SCRIPTED_ONLY"`, `budget: dict | None = None`,
   `compaction: dict | None = None`, `worker_user: str | None = None`,
   `preregistration_sha256: str = "UNKNOWN"`. In real mode require
   `synthetic_case is None`, `budget`, `preregistration_sha256` (64 hex), a
   loopback endpoint, `worker_timeout_seconds` in `1..3600`, and
   `candidate_path` whose bytes hash to `E0_SHA256` (this tranche admits
   exactly the frozen E0 ZIP; any other candidate is refused with a message
   naming the later candidate-admission step). Synthetic mode keeps the 600 s
   ceiling and forbids the real-mode fields.
3. `eval/runtime.py:run_task`: replace the `synthetic/probe` gate by a mode
   dispatch: synthetic modes keep the exact current gate; `real_public`
   requires a `SolverTask` whose `repo` is one of the four public repositories
   and whose snapshot/graph/embedding refs validate against the staged public
   root, and a `VerifierTask` with `fail_to_pass is None and pass_to_pass is
   None` (public rows have no node lists). Add `preregistration_sha256`,
   `model_endpoint`, `budget`, `compaction`, platform and `confinement` to the
   provenance/fingerprint. Assert that no ancestor of the staged public root
   or of the worker root contains a `secret` directory (defense against
   `Evaluator` secret discovery if any native code path ever looks).

   **Official public snapshot policy.** Admit real public snapshots faithfully
   as provided by the competition dataset / scorer-compatible input; do not
   apply the synthetic snapshot Git-metadata allowlist. Record `.git/index`
   presence or absence and the admitted Git-metadata inventory/identity. No
   hidden, private, verifier-only or host-local Git state may be injected into
   a solver snapshot. The `SolverTask` contract snapshot identity remains
   authoritative through staging and worker use: every staged and use-time
   snapshot check must validate against that contract identity. A missing or
   mismatched identity fails closed.
4. `eval/verifier_worker.py`: in `real_public` mode require the task repo to be
   public and the ID to match the public ID pattern; keep the synthetic check
   for synthetic modes.

### A2. Linux worker confinement: user separation

1. `eval/runtime.py:confined_worker_argv` becomes platform-dispatching.
   `darwin` keeps the existing `sandbox-exec` profile unchanged. `linux`
   requires `config.worker_user` and returns
   `["runuser", "-u", <user>, "--", <python>, "-I", "-B", *arguments]`
   (fall back to `sudo -u <user> --` only if `runuser` is absent; record which).
   Any other platform raises.
2. Add `verify_worker_confinement(config, roots)` (Linux only), run once per
   `run_task` before staging: as the worker user, attempt `os.open(O_RDONLY)`
   on the dataset public root, the private root, `config.artifact_root` and
   the repository `.git` directory; every attempt must raise `PermissionError`.
   Also attempt to open the harness interpreter and `python -I -B -S -c pass`
   must succeed. Refuse the run on any deviation. The result is written to
   `confinement.json` in the run root.
3. After `_prepare_worker` stages code, wheels, candidate and public assets
   into the worker root, `chown -R` the worker root to the worker user (Linux
   only) and set the root to `0700` owned by that user; the coordinator stays
   root and archives/removes the root afterwards. Public staged copies remain
   fresh copies (never hardlinks into root-owned 0600 sources).
4. `worker_environment` on Linux: same scrubbed environment plus
   `HOME=<worker_root>/home`, `TMPDIR=<worker_root>/tmp`, `PATH=/usr/bin:/bin`,
   `HF_HUB_OFFLINE=1`, `LITELLM_LOCAL_MODEL_COST_MAP=True`,
   `LITELLM_MODE=PRODUCTION`, `PYTHON_DOTENV_DISABLED=1`.
5. Record `confinement: "linux_user_separation"` and its known limits in the
   fingerprint: no memory/CPU caps, no network namespace (sandbox commands can
   reach the network; the scorer cannot), no OS read profile beyond file
   permissions.

### A3. Real execution module `eval/runtime_real.py`

Stage it like `runtime_adapter.py` (add to the staged code lists). Two entry
points, dispatched by the existing workers on `request["mode"] ==
"real_public"`:

`solver_execute_real(request) -> dict` (same return schema as
`runtime_adapter.solver_execute`, `schema_version` 1):

1. Admission: request validated; endpoint loopback; `harness_root ==
   sys.prefix`; candidate ZIP in `submission_root` hashes to `E0_SHA256`;
   extract it with a safe extractor (regular files only, no symlinks, no
   absolute or `..` members, exactly the 10 frozen member paths, each member
   hashed against the frozen list in the preregistration); snapshot/graph/
   embedding staged identity checks via `validate_staged_solver_assets`.
   Source pin: reuse `tools.harness_cert.run_h04_h05_h18.verify_environment`
   against the Linux venv; if its macOS-specific assumptions fail, add a
   platform-neutral variant that checks the five v28 distributions' Python
   sources against the source wheels and record which was used.
2. Budgets: parse the candidate's `eval_config.yaml` `evaluation:` block with
   `yaml.safe_load`; refuse unknown keys; the parsed values must equal
   `request["runtime"]["budget"]` (the coordinator passed the preregistered
   values; a mismatch is an admission error, not a silent override).
3. Model registry: `setup_gemma_model_registry(api_base=endpoint,
   num_retries=5, adapter_manifest=discover_adapters(candidate_dir),
   served_model=<served model name from request runtime; default
   "gemma-4-31b-it-qat-w4a16-ct">, backend="vllm")`. Before running, `GET
   <endpoint>/models` and require the served model and both adapter names to
   be listed; record the response bytes.
4. `EvalConfig(tasks_path=<unopened locator>, snapshots_dir=<staged>,
   results_dir=<evidence_root>, submission_dir=<candidate_dir>, models=,
   sandbox="subprocess", wheels_dir=<staged public wheels>, image=
   "SUBPROCESS_NO_IMAGE", graph_dir=, embeddings_dir=, timeout_seconds=,
   max_tool_calls=, max_turns=, max_time_minutes=, display_mode="quiet",
   enable_sandbox_testing=True, events_compaction_config=, context_cache_config=,
   adapter_manifest=)`. Build the compaction/cache objects from
   `request["runtime"]["compaction"]` if the installed `google.adk` exposes
   `EventsCompactionConfig`/`ContextCacheConfig`; if `run_agent_sandbox` does
   not consume them, record `compaction_applied: false` with the reason
   rather than patching native code.
5. Sandbox: `SubprocessManager(timeout_seconds=<command timeout>,
   base_dir=<worker_root>/sandboxes, system_site_packages=False)`, wrapped by
   the same passive command observer used in `runtime_adapter._wrap_manager`
   (factor the observer into a small shared helper or duplicate the minimal
   parts; no synthetic guard logic, no scripted server). Observe every
   `execute`: command, exit code, stdout/stderr byte sizes, wall time; take
   the workspace diff at stop. Flag commands matching `pip install`, `curl`,
   `wget`, `git clone` as `network_attempt` annotations.
6. Run `run_agent_sandbox` with a `SwegemmaContext` built exactly as the
   synthetic path does; capture `(patch, error, trace)`, runner log records
   (`_RunnerEvidence`), context counters (`llm_calls_used`,
   `tool_calls_used`, `patch_submitted`, `submitted_patch`,
   `agent_elapsed_seconds`), escaped exceptions, and wall times for setup
   (sandbox start to `start_agent_session`), session and total. Save
   `native_trace.json` (`trace.to_dict(format="legacy")`),
   `native_observations.json`, and the first user prompt as
   `agent_prompt.txt` (public text only).
7. Status/terminal reason mapping as in the synthetic path (`timeout`,
   `budget_exhausted`, `error`, `completed`, `worker_error`); add
   `elapsed_at_first_edit_seconds` (first `edit_file`/`write_file` tool event
   with a success response, else `null`) and `nudge_count`, `finish_reasons`
   from the trace when present (else `null` with reason).

`verifier_execute_real(request) -> dict` (same schema as
`runtime_adapter.verifier_execute`):

1. Admission: real-mode request; task identity; `returned_patch` sha matches
   the seal; `test_patch` bytes match `task.test_patch` (sha, size) and
   `verification_config` equals `real_verification_config(...)` and hashes to
   `task.verification_config_sha256`; staged snapshot identity; wheels and
   `sandbox/setup.py` staged from the public root with hashes recorded.
2. `Task(instance_id, repo, base_commit, problem_statement="",
   test_patch=<private>, FAIL_TO_PASS=(), PASS_TO_PASS=())`; `EvalConfig` as
   above with `ModelRegistry()` and no candidate; call `verify_task` with the
   exact keyword set the installed `Evaluator.evaluate_task` passes
   (`agent_patch`, `agent_error`, `trace=None`, `start_time`, and the
   snapshot-path extras only if the signature has them). Do not pass
   `fast_path` unless the Evaluator does; note what the synthetic path passed.
3. Capture native `TaskResult` (`model_dump`), `test_output`, exit code,
   JUnit XML (from the sandbox `/tmp` path the native code uses, if
   retrievable through the observer), apply-pass diagnostics from the command
   observations, and the protected-file reset command list.

`real_verification_config(public_root, budget) -> dict`: closed dict
`{"schema_version": 1, "sandbox": "subprocess", "timeout_seconds":
<command timeout>, "setup_py_sha256": sha256(<public_root>/sandbox/setup.py),
"wheels_tree_sha256": tree hash of <public_root>/wheels/ via
`tools.common.tree_sha256` over per-file sha256}`. Computed on the trusted side
before workers run; its canonical sha256 is the `VerifierTask`'s
`verification_config_sha256`.

### A4. Trusted intake for real tasks and private test bytes

In `eval/verifier_data.py` (trusted side only; never staged into the solver
worker; keep it out of `_COMMON_CODE`):

- `publish_private_test_patches(public_root, task_ids, private_root, *,
  guard: WriteGuard, verification_config) -> dict[str, VerifierTask]`: read
  the public rows (reuse `public_data._read_rows` via a trusted import; do not
  widen `_PUBLIC_FIELDS`), write each task's `test_patch` to
  `<private_root>/<instance_id>.test.patch` with mode `0600`, no overwrite,
  `private_root` mode `0700`, and return `VerifierTask(1, id, repo,
  base_commit, snapshot_ref, PrivateTestPatchRef(sha, size),
  canonical_sha256(verification_config), None, None)`. The snapshot ref must be
  the same `PublicAssetRef` the solver task carries (admit through
  `public_data` so both views agree).
- `read_reference_patch(public_root, instance_id) -> str`: returns the public
  `patch` text for the known-patch control. Only the driver's control path may
  call it; the text is passed to the verifier phase as `returned_patch` and
  written nowhere except the sealed verifier evidence under the control's run
  root. It is never logged or printed.

### A5. Driver `tools/dev_eval.py`

Stdlib + PyYAML. Subcommands:

- `fingerprint --endpoint URL --out DIR`: records git HEAD and dirty state
  (refuse dirty unless `--allow-dirty`, which marks the run `source_dirty:
  true`), platform, Python, harness package versions from the worker venv
  (`importlib.metadata`), `nvidia-smi --query-gpu=name,memory.total,driver_version
  --format=csv`, `GET /version`, `GET /v1/models`, the preregistration file
  sha256, the E0 ZIP sha256, and the dataset identities (`tasks.jsonl`,
  `screens_v1.json`, `v1.json`). Writes `fingerprint.json`.

  Real runs require non-null solver and verifier runtime fingerprints. The
  coordinator cross-checks both phases before accepting a task result: they
  must agree on eval-infra source identity, candidate identity, preregistration
  identity and normalized model-endpoint identity. Each fingerprint also records
  the eval-infra Git HEAD, dirty state and a digest of the runtime source set.
  A missing fingerprint or any cross-phase mismatch fails closed.

  Runtime evidence, fingerprints, process observations, manifests and artifact
  seals must never contain API keys, authorization headers, bearer tokens,
  endpoint credentials or other authentication material. Record endpoint
  identity only in the normalized non-secret form needed for provenance.
- `serve-argv --candidate ZIP --model-path DIR --tp N --gpu-mem 0.80`: extracts
  the candidate to a temp dir, runs `discover_adapters`, builds
  `VllmServer` config with `max_model_len=32768`, `tool_call_parser="gemma4"`,
  `reasoning_parser="gemma4"`, `enable_thinking` default chat template kwarg,
  `enable_lora=True`, `served_model="gemma-4-31b-it-qat-w4a16-ct"`; call native
  `VllmServer.build_cmd()`. Explicitly resolve the executable from the admitted
  vLLM venv and replace the executable argv element directly, without
  shell/PATH-dependent substitution. Print and record the original `build_cmd()`
  argv and the exact executed argv as JSON in `serve_argv.json`. It does not
  start anything; the operator uses the recorded executed argv.
- `preflight --control {no_patch,known_patch} --task-ids ...`: verifier-only
  runs (no solver process): for `no_patch` the returned patch is `""`; for
  `known_patch` it is `read_reference_patch`. Writes a sealed verifier result
  per task plus `controls.json` with pass/fail per the preregistration
  criteria. Known-patch IDs are appended to `gold_assisted_diagnostics` in the
  run manifest. Known-patch control run roots are verifier-only / gold-assisted
  evidence: visibly label them and exclude them from normal experiment report
  rendering, candidate-comparison inputs, designer inspection and solver
  inspection. The reference patch remains unavailable to the solver/designer.

  Real-runtime preflight must inspect free space on every filesystem used for
  model weights, public data, private verifier evidence, the harness environment,
  the vLLM environment, worker roots and exported evidence. Derive and document
  a conservative minimum-free-space threshold from the admitted runtime
  footprint; fail closed below it.
- `run --screen S1 --candidate ZIP --endpoint URL --public-root DIR
  --private-root DIR --artifact-root DIR --worker-user USER --prereg PATH`:
  loads `load_screen_manifest`/`load_solver_tasks(root, screening, "S1")`,
  publishes private test patches, iterates tasks in manifest order with
  concurrency 1, calls `run_task` per task, appends `ledger.jsonl` rows
  (`task_id`, `attempt`, `phase`, `status`, `started_at`, `finished_at`,
  `run_root`), and writes `summary.json` (assigned, started, verified,
  resolved, unresolved, indeterminate, by repo). Resume: a task with a sealed
  `task_result.json` is skipped; `--attempt N` (N ≥ 2) is required to rerun a
  task and keeps earlier attempts. Refuse if `fingerprint.json` is missing,
  stale (> 12 h), or records a different endpoint/candidate/prereg hash.
- `leak-scan --run-root DIR --public-root DIR`: for every task run, collect
  added lines (≥ 40 non-whitespace chars) of that task's `test_patch` and
  `patch`, search all solver-phase evidence bytes (trace, logs, prompt,
  command observations, returned patch is excluded only for `patch` lines
  that the agent legitimately could produce, so report `patch` hits
  separately as `patch_overlap` with counts), and write `leak_scan.json`.
  `patch_overlap` is diagnostic only: overlap between an independently produced
  candidate patch and the public reference fix is not by itself evidence of
  leakage. Only evidence that reference/private bytes entered the solver
  boundary is a leakage finding. Any `test_patch` hit is a P0. This runs
  automatically at the end of `run`.
- `report --run-root DIR`: builds `records.jsonl` (one B3-shaped record per
  assigned task, `null` with reason for unobserved fields) and
  `report.md` with the histograms listed in the preregistration §6. Primary
  classification is automatic only for platform/harness/agent-process/
  verification layers (timeout, budget, no patch, apply failure, environment
  artifact, undeclared tool); agent-quality categories are `UNKNOWN` pending
  manual annotation, and resolved tasks get `null`.

### A6. Linux harness lock

Generate `harness_cert/locks/harness_linux_x86_64_py312.lock` with
`uv pip compile --python-version 3.12 --python-platform x86_64-manylinux_2_28
--only-binary :all: --generate-hashes` from the same 83 pins recorded in
`harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md`, plus the
five v28 source wheels installed from `artifacts/harness_wheels/v28` with
`--no-deps`. Record the lock sha256 in the admission doc. vLLM is not in this
lock; it lives in a separate venv (section C).

### A7. Tests (CPU, Mac, no model, no dataset)

Extend the existing suites; synthetic tests unchanged:

- `tests/test_dev_runtime_real.py`: real-mode request validation (loopback
  only, closed budget/compaction dicts, synthetic fields rejected in real
  mode and vice versa); `RuntimeConfig` real-mode rules including the exact-E0
  candidate rule and timeout ceilings; `run_task` real-mode dispatch with a
  stubbed worker (identity checks, `fail_to_pass is None` rule, `secret`
  ancestor refusal, provenance fields present); Linux argv construction with
  `sys.platform` monkeypatched and a fake `runuser`; confinement verification
  refusing when a probe unexpectedly succeeds; safe ZIP extraction rejecting
  symlink/absolute/traversal/extra members and verifying member hashes;
  budget parsing refusing unknown keys and mismatches.
- `tests/test_dev_leakage.py`: `publish_private_test_patches` writes 0600
  single-link files under a 0700 root, no overwrite, returned `VerifierTask`
  hashes match, and a solver request built for the same task serializes
  without the keys `test_patch`, `patch`, `FAIL_TO_PASS`, `PASS_TO_PASS` and
  without any private path; `read_reference_patch` is importable only from
  trusted modules (assert it is not in the staged solver code list).
- `tests/test_dev_eval_driver.py`: ledger/resume accounting with stubbed
  `run_task`, attempt semantics, stale/mismatched fingerprint refusal,
  `summary.json` counts including never-started tasks, `leak-scan` detecting
  a planted sentinel line and ignoring short lines, `report` producing one
  record per assigned task with `null` reasons.
- `tests/test_tooling.py`: keep the registry test shape introduced with the
  preregistration row (plan rows validated).

Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p
no:cacheprovider` (full CPU suite) and the native target
`/private/tmp/h23-cpu.UA1C8T/harness/bin/python -m pytest
tests/test_dev_runtime_adapter.py -q` if the interpreter still exists; record
both counts.

### A8. Documentation

Write `docs/experiments/DEV_REAL_RUNTIME_ADMISSION.md` in the style of the two
existing admission docs: what was admitted, exact function/line references,
fidelity limits (user separation, no network namespace, no cgroup caps, TP=1),
validation record with counts, and the next gate (section B). Do not edit the
preregistration except to add the eval-infra commit placeholder note if needed.
Do not commit or push; leave the branch for the audit.

---

## B. Audit prompt — Claude Code (fresh, independent session)

> Perform a read-only audit of the real-runtime admission tranche on the
> implementer's branch of `gemma4-agent-lab` against
> `docs/experiments/REAL_RUNTIME_TRANCHE_2026-10-07.md` section A and
> `docs/experiments/DEV_E0_FORENSICS_V1_S1_PREREG.md`. Produce a dated audit
> report under `docs/experiments/` with PASS / FAIL per item, exact file:line
> evidence, and a merge recommendation. Do not modify code. Do not run the
> model. You may run the CPU test suite.

Items:

1. **Solver boundary.** The solver worker request and every file staged into
   the solver worker root contain no `test_patch`, `patch`, node lists,
   private paths, or dataset-root descriptors. `eval/verifier_data.py` and the
   `read_reference_patch` helper are absent from the staged solver code.
   Private bytes are read only after the solver process is reaped and its root
   removed. Verify by reading `run_task`, `_prepare_worker`, `_stage_code` and
   the real-mode tests.
2. **Synthetic path unchanged.** Diff the synthetic branches of
   `validate_request`, `RuntimeConfig`, `run_task`, `runtime_adapter.py`,
   workers; confirm semantic identity and that all pre-existing tests pass
   unmodified.
3. **Linux confinement.** `confined_worker_argv` Linux branch, confinement
   verification probes, chown/0700 handling, scrubbed environment, and the
   recorded limits. Confirm the probes fail closed and that a successful open
   of a private root refuses the run.
4. **Candidate identity.** Exact-E0 rule, safe extraction, member hash list
   equal to the preregistration, served model and adapter names required from
   `/v1/models`.
5. **Budget and config fidelity.** `eval_config.yaml` parsing, equality with
   preregistered budget, compaction handling honesty (`compaction_applied`),
   verification config construction and hash binding to `VerifierTask`.
6. **Verification fidelity.** `verify_task` call matches the installed
   `Evaluator.evaluate_task` keyword set; empty node lists; protected reset
   and JUnit evidence captured; no `swegemma.evaluate` import anywhere in
   workers; `secret` ancestor refusal.
7. **Driver accounting.** Ledger/resume/attempt semantics, never-started
   tasks in `summary.json`, fingerprint staleness/mismatch refusal, control
   pass criteria equal to the preregistration §4, gold use recorded.
8. **Leak scan.** Correctness of line extraction and search scope; `test_patch`
   hit is P0; `patch_overlap` reported separately. Expected reference-fix overlap
   alone is not classified as leakage.
9. **Provenance.** Mandatory solver and verifier fingerprints; cross-phase
   endpoint/candidate/preregistration/eval-infra identity checks; Git HEAD,
   dirty state and runtime-source digest; official real-snapshot Git-metadata
   policy and `.git/index` presence recording; Linux lock SHA256; secret-free
   evidence/seals; admission doc validation record with real test counts.
10. **Scope discipline.** No changes under `agents/`, `eval/splits/`,
    certification reports, matrix, notebook; no network calls in tests; no
    new third-party dependency.

Outcome vocabulary: `READY FOR OPERATOR LAUNCH` or `NOT READY` with the
blocking item numbers.

---

## C. Operator runbook — Linux GPU runtime (the user launches; agents do not)

Preconditions: audit PASS, tranche merged to `main`, clean tree, registry row
`EXP-20261007-001` present. Keep every printed identity; they go into the
result record.

1. **Runtime.** Use an operator-controlled Linux GPU runtime. Colab Pro is the
   current candidate host; accelerator assignment is nondeterministic. Run
   `nvidia-smi` and continue only if the actually assigned accelerator satisfies
   `>=40 GB VRAM`; an A100-class assignment qualifies, while L4/T4 assignments
   abort. This is an engineering admission threshold, not a measured peak-memory
   claim: quantized weights are approximately 16–18 GB, with additional
   32k-context KV/runtime allocations, adapter serving and safety headroom.
   Keep vLLM TP=1 and the official `subprocess` sandbox; do not lower
   `max_model_len` below 32768. The Mac does not run the 31B model.
2. **System.** `apt-get install -y git patch pigz`; `useradd --system
   --no-create-home --shell /usr/sbin/nologin gemma_worker`. Record
   `python3 --version` (3.12 or 3.13 both work with the dataset wheels; the
   harness venv must be 3.12 for the lock).
3. **Repository and harness venv.** Clone `gemma4-agent-lab` at the merged
   commit; `uv venv --python 3.12 /opt/harness`; install the five v28 wheels
   from `artifacts/harness_wheels/v28` with `--no-deps`, then
   `harness_cert/locks/harness_linux_x86_64_py312.lock` with
   `--require-hashes`. The venv must be readable/executable by the worker
   account; admission does not require a particular venv mode.
   The repository clone or at least its `.git` must deny the worker account:
   the confinement probe requires opening `.git` to raise `PermissionError`.
   Keep the readable harness installation outside that protected repository.
4. **Dataset subset.** Place under `/content/data` (root, `0700`): full
   `tasks.jsonl`, `wheels/`, `sandbox/setup.py`, and for the 12 S1 IDs the
   snapshot `.tgz`, graph `.json` and embedding `.npz` (about 2.1 GB; from
   Drive or Kaggle). `python -m tools.inventory_dataset`-style hashing is not
   required; the admitted loader hashes what it reads and fails closed on
   missing assets.
   Before execution, protected public/private/artifact roots must exist and
   deny the worker account's `os.open(O_RDONLY)` probes. The actual
   `--worker-root` parent must be an existing unaliased directory where the
   privileged coordinator can create/chown/archive/remove fresh roots. The
   worker must have directory read and search access through this parent and
   every ancestor: the descriptor reader opens each ancestor directory.
   Keep that parent outside roots that deny the worker. The implementation
   requires no fixed parent owner or octal mode; each generated worker root
   is recursively worker-owned and `0700`. The harness interpreter, libraries
   and their ancestors must be readable/searchable, with interpreter execution
   permitted. Actual Linux probes and worker startup must verify this layout.
5. **Model.** Download the `gemma-4-31b-it-qat-w4a16-ct` weights (HF or Kaggle
   Models; record the exact repo id, revision and `sha256sum` of the
   safetensors). About 17 GB.
6. **vLLM venv.** Separate venv `/opt/vllm`; install a vLLM release whose
   `vllm serve --help` lists `gemma4` for `--tool-call-parser` and
   `--reasoning-parser`; record `pip show vllm` and `vllm --version`.
7. **Server argv.** In the harness venv:
   `python -m tools.dev_eval serve-argv --candidate
   artifacts/submissions/e0_official_control/submission.zip --model-path
   /content/model --tp 1 --gpu-mem 0.80`. Use the explicitly resolved vLLM-venv
   executable and the exact executed argv recorded by `serve-argv`
   (`nohup ... &`), wait for `curl http://127.0.0.1:8000/v1/models` to list
   the served model, `main_lora` and `tool_lora`. If startup fails, record
   `MODEL_SERVER_START_FAILURE` and stop.
8. **Fingerprint.** `python -m tools.dev_eval fingerprint --endpoint
   http://127.0.0.1:8000/v1 --out artifacts/dev_runtime/EXP-20261007-001/`.
9. **Preflight.** `preflight --control no_patch --task-ids httpx_3672
   requests_7502 rich_3468 fastapi_14186`, then the same with `known_patch`.
   All eight must pass per the preregistration §4. If one fails, stop and
   report; do not run S1.
10. **S1.** `python -m tools.dev_eval run --screen S1 --candidate
    artifacts/submissions/e0_official_control/submission.zip --endpoint
    http://127.0.0.1:8000/v1 --public-root /content/data --private-root
    /content/private --artifact-root artifacts/dev_runtime/EXP-20261007-001
    --worker-user gemma_worker --worker-timeout-seconds 3600 --prereg
    docs/experiments/DEV_E0_FORENSICS_V1_S1_PREREG.md`. Expect 1–2 hours.
    Supply the additional required path arguments documented in
    `DEV_REAL_RUNTIME_ADMISSION.md`. The 3600-second per-phase process timeout
    is infrastructure; E0's frozen agent budget remains one minute, ten counted
    tool calls, fifty turns and a sixty-second command timeout.
    Watch for `P0` lines; the driver stops itself on them.
11. **Archive.** `report`, then zip `artifacts/dev_runtime/EXP-20261007-001/`
    and copy it off the session (Drive). Never commit it. Stop the GPU.

Return to the architect session: the archive location, `fingerprint.json`,
`controls.json`, `summary.json`, `leak_scan.json`, and anything that deviated
from this runbook.

---

## D. Post-run — Claude Code (principal architect session)

Classify the 12 records per preregistration §5 from the archived evidence,
produce `docs/experiments/DEV_E0_FORENSICS_V1_S1_REPORT.md`, append `result`
and `decision` registry records, add a `decisions.md` entry naming the leading
operational cause and the next preregistered experiment from §6, and prepare
the R0 and first-architecture prompts. Opening any tune reference patch during
classification is recorded per task.
