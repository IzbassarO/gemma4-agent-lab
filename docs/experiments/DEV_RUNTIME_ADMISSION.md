# DEV solver/verifier runtime admission — 2026-10-06

**SOLVER / VERIFIER RUNTIME LAYER READY FOR INDEPENDENT AUDIT**, subject to the
completed validation record below. This tranche certifies a synthetic runtime
boundary. The next gate is Claude Code's independent, read-only runtime and
isolation audit. A real DEV-E0 S1 run requires a subsequent, separate admission.

Initial repository admission found a clean worktree. HEAD and origin/main both
equaled `53b82d8c0f876fa47f012dafa1405671abbc0fcd`. The previous data-boundary and
screening-manifest tranche is the baseline. This tranche leaves the frozen
split, S1/S2/S3 memberships, agents, competition prompts, sampling, tool budgets,
adapters, Kaggle notebook, H28/H30 reports and certification matrix unchanged.
At audit time, this tranche was represented by the repository diff against
the baseline above; repository history is authoritative for its later
commit/push state.

The frozen E0 ZIP is copied and checked against **443572 bytes** and SHA256
`25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3`.
Its agent is neither rebuilt nor executed. Native certification instead uses
the separate inert `synthetic/probe` repository and fixed scripted replies.

## Architecture and execution admission

```text
trusted coordinator: exact SolverTask + separately admitted VerifierTask
  -> public stream staging and frozen candidate identity checks
  -> fresh, confined solver Python process
       -> fresh native SubprocessManager sandbox
       -> native run_agent_sandbox with solver-only public input
       -> seal returned patch, observations, traces and result
  -> wait/reap solver; terminate surviving process-group descendants
  -> archive solver evidence; remove solver runtime root
  -> admit private test bytes
  -> independently stage fresh verifier runtime root and snapshot
  -> fresh, confined verifier Python process
       -> fresh native SubprocessManager sandbox
       -> native verify_task with sealed returned patch and private test evidence
       -> seal native verification, optional JUnit evidence and result
  -> wait/reap verifier; archive verifier evidence; remove verifier root
  -> immutable TaskRuntimeResult and sealed task_result.json
```

`eval/runtime.py:run_task` is the one-task trusted coordinator. It checks exact
contract types and agreement of instance ID, repository, base commit and full
snapshot reference. There is no scheduler, retry, candidate selection or resume
ledger. A coordinator or worker error before sealing raises an admission/runtime
error and preserves available phase evidence; it does not seek a better result.

Execution is deliberately limited to `native_scripted` and `isolation_probe`,
the `synthetic/probe` repository, synthetic IDs and a closed list of fixture
cases. Requests for a real public task or real model endpoint fail before native
execution. The host subprocess backend is admitted only for the fixed inert
snapshot and fixed commands. It is not an admission of arbitrary competition
repositories or arbitrary agent code on the host.

`RuntimeConfig` requires explicit absolute interpreter, harness, candidate and
artifact roots, and an explicit source-wheel root for native certification.
The effective public, evidence, submission, wheel and setup roots are phase-local.
The model endpoint field is the label `SCRIPTED_ONLY`; no real endpoint starts.
The native subprocess wheel directory contains exactly one genuine, pinned
`adk_eval_core` source wheel, copied into each native sandbox before its first
command. This selects the unchanged native local-wheel branch and skips fallback
root discovery. The synthetic subprocess backend does not install this wheel.
All five source wheels used for source-identity checks live in a separate,
explicit phase-local directory.
An inert setup file supplies the native import-time setup locator. A native
configuration names an unopened tasks locator; no vendor task loader reads it.

## Process, environment and filesystem separation

Each phase is launched by `subprocess.Popen` with a new executable image,
`-I -B`, explicit code bootstrap, explicit cwd, `close_fds=True` and a fresh
process session. This avoids copying the coordinator's Python heap under either
macOS spawn or fork conventions. No multiprocessing fork inheritance is used.
The JSON request travels through stdin rather than argv. Stdout/stderr go to a
private phase log; parent private file descriptors are not passed to the worker.
The coordinator waits for process termination and kills remaining members of
that process group before archiving evidence or reading private test bytes.

The runtime constructs worker environment values from an allowlist rather than
copying `os.environ`. PATH contains system executables only; HOME, TMPDIR and
HF_HOME are worker-local. PYTHONPATH, user-site imports, dotenv discovery,
external pip indices, Hugging Face network access, telemetry and inherited
credentials/private-root settings are excluded. The sole API key is an explicit
synthetic dummy. Native adapter setup replaces the environment again before
optional imports. Recorded environment values are these constructed fixture
values, never the coordinator environment.

This implementation requires macOS `/usr/bin/sandbox-exec` and fails closed on
an unsupported host. Its deny-default profile is inherited by native subprocess
children. File contents and directory listings are readable only inside the
phase root, explicitly admitted Python installation roots and listed OS library
roots. Writes are confined to the phase root and specified system device sinks.
Directory metadata remains readable for loader traversal. Exact ancestor
directory objects are also readable so nofollow traversal can open them; this
can expose those directories' entry names, but grants no reads of their sibling
file contents. The profile does not claim that existence metadata for every
guessed host path is secret. The guessed verifier-private content path remains
denied. Network access is limited to loopback by the OS profile and to the admitted
scripted listener by the additional native client/audit guard. This is a
macOS-specific synthetic certification profile, not a portable container design.
Admission rejects public, private and archived-evidence roots that overlap any
readable OS/Python library root, including inode/case aliases, before output
creation. Broad OS library permissions can still expose unrelated host files
within those library trees; this profile certifies the fixed synthetic topology
and does not claim universal host-secret isolation. The interpreter-prefix
query uses `-S` so no unconfined `.pth` startup code runs before OS confinement.

The solver's code root contains an explicit whitelist, not a repository mount.
It excludes `eval/public_data.py`, `eval/verifier_data.py`, verifier contracts,
the verifier worker and the private synthetic verification fixture. The solver
entrypoint receives only a closed, validated `SolverTask`; it never invokes the
trusted raw-row projection helper. Shared adapter source has a verifier entry
function, whose private imports execute only in the verifier worker; those
private modules and fixture files are absent from solver staging.

Source datasets and verifier storage are outside the solver's readable roots.
Solver and verifier runtime directories have independently unpredictable names;
the verifier runtime root does not exist while solving. Neither phase mounts
the coordinator's source dataset. The verifier receives no solver workspace,
mutable solver root, solver memory or private solver descriptor. Its public
snapshot is independently byte-copied from the admitted public source.

## Descriptor-bound staging and hardlink policy

`eval/staging.py` opens explicit public roots and every namespace component with
descriptor-based nofollow traversal. Only regular files named by the exact
`SolverTask` asset references are staged; optional graph/embedding assets are
never discovered implicitly. Public asset staging is bounded to 8 GiB per
asset, following the existing admission limit.

One opened source descriptor supplies every byte copied to a fresh 0600 file.
Hash and byte count are computed during that copy and must equal the admitted
`PublicAssetRef`. Source descriptor metadata and the current root, namespace
and file identity are rechecked to detect mutation or path replacement. The
fresh destination descriptor is rewound and independently hashed in full before
publication. Source/path/destination identity is checked again. Publication
uses an atomic no-overwrite link to this new temporary inode followed by
removal of the temporary name; it never links a source inode into staging.
Failed staging exposes no completed destination and happens before worker launch.
Workers rehash staged assets before native use; the adapter also checks its
staged snapshot and the native sandbox's snapshot copy before extraction.

| Object | Policy |
|---|---|
| Public snapshot/graph/embedding source | Hardlinks permitted. Nofollow regular-file opens, complete SHA256/size and mutation/path checks determine identity. |
| Published solver/verifier public copy | Fresh private inode, 0600, owned by current user, final link count exactly one; containing staging directories are 0700. |
| Private verifier test evidence | Existing strict private-root/0600 ownership and `st_nlink == 1` checks remain mandatory. |
| Manifests and mixed/gold `tasks.jsonl` | Existing strict single-link enforcement remains unchanged. |
| Sealed runtime evidence | Fresh private regular files, full hash/size references and single-link checks when archived. |

The narrow `eval/public_data.py` change permits hardlinks only for public asset
admission; it does not relax the mixed/gold row source, manifests or private
test reader. No public or private source data is modified. No hardlink connects
solver staging to verifier-private storage.

## Exact native reuse and source admission

Native execution reuses the acquired harness environment: Python 3.12.14,
`swegemma==0.2.7`, `adk-submission==0.2.12`, `adk-eval-core==0.1.0`,
`google-adk==1.36.1`, `google-genai==2.11.0` and `litellm==1.83.14`.
The existing `verify_environment` admission also pins `urllib3==2.8.0`,
`authlib==1.6.6`, the five v28 source-wheel hashes, each installed Python source
member from those wheels, and bundled tokenizer asset hashes. These checks
precede optional native imports; no package installation or download occurs.

The exact reused interfaces are:

- `swegemma.context.SwegemmaContext`, constructed with the solver-only public
  namespace, explicit native budget/harness settings and explicit public asset roots.
- `swegemma.harness.agent_runner.run_agent_sandbox(manager, config, public_task,
  snapshot, context=context)`, whose returned patch/error/trace tuple is preserved.
- `swegemma.sandbox.subprocess.SubprocessManager`, with a fresh manager in each
  newly executed phase and `system_site_packages=False`.
- `swegemma.config.EvalConfig` and `adk_submission.ModelRegistry`, using explicit
  roots and a local fixed `LiteLlm` scripted transport in the solver fixture.
- `adk_submission.compile_submission`, called by the unchanged native runner
  for the inert scripted submission. The frozen E0 ZIP is only identity-checked.
- `swegemma.models.task.Task`, constructed solely inside the verifier entry with
  its admitted private test evidence and native test-node fields.
- `swegemma.harness.verification.verify_task(manager, config, verifier_task,
  snapshot, fast_path=True, agent_patch=returned_patch, agent_error=agent_error,
  start_time=started)`, preserving native patch application, test patch,
  test configuration, JUnit interpretation and result model.

The native package's eager `swegemma/__init__.py` and `harness/__init__.py`
re-exports load broad evaluator/verification modules. The adapter creates explicit
namespace package paths only after source admission, then imports the unchanged
native leaf modules. Solver execution does not import native verification,
`Evaluator`, `evaluate_task` or sample verification, and never invokes a vendor
task loader. The source-pinned native models package can re-export the
`load_tasks` function definition; no task rows or private bytes are loaded. This narrowly
bypasses eager aggregators without replacing the leaf implementations.

Command/copy admission is active with observation ON and OFF. It admits native
setup/patch/test commands for the fixed synthetic fixture, checks copied bytes
and destinations, keeps native timing and budget settings, and prevents wheel
fallback discovery. Synthetic fixture declarations and budgets do not modify
the frozen competition agent. The verification fixture reuses the existing
synthetic support staging, with a pinned support-manifest identity.

The synthetic archive omits `.git/index`, and admission rejects any archive
containing it. An inert scratch reproduction found that `assume-unchanged`
entries in a stale index survive the native baseline `git add -A` and can restore
hidden protected Python/config files during the verifier's later checkout.
Without the stale index, native Git builds a fresh index from admitted workspace
files and trusted native-generated config; the hidden files are not restored.
No native Git command or agent behavior is replaced to enforce this boundary.

## Returned patch, verification and passive evidence

The actual patch from the native solver return tuple is authoritative. Every
solver result validates its SHA256 against the exact UTF-8 returned text.
Verifier input carries that text and seal, never a workspace-diff substitute.
`TaskRuntimeResult` requires the verifier's patch seal and task identity to agree
with the solver. Workspace diagnostics are evidence only.

An H18-like successful edit followed by fatal undeclared-tool lookup can leave
changed workspace bytes and an empty returned patch. The verifier still receives
the empty returned patch and native agent error. Conversely, an H13-like timeout
may return a patch recovered by the native runner's own fallback. The recovered
native return is verified without an additional extraction step. Empty clean
final answers, empty explicit submission, patch-apply errors and verification
failure are retained as different native observations; there is no blanket
invented no-patch policy or semantic failure classifier.

Observation records the native return tuple, context counters/submission state,
scripted transport request count, native log/exception evidence, admitted command
results and sandbox start/stop identities. Manager observation adds no command
and reads terminal fixture state before the existing stop/cleanup. It does not
write into the solving workspace, call Git for diagnostics, add model turns,
modify prompt/context, change call order, sampling, budgets or timeouts.

`eval/runtime_events.py` produces frozen events in append order. Lifecycle events
come from actual worker lifecycle. Post-run SessionTrace legacy entries provide
tool attempts, tool responses and final model response observations; entry hashes
and references replace duplicated prompts, arguments and responses. Known
`get_status` response budget fields are preserved as canonical immutable JSON.
A tool-call entry proves an attempt and does not prove body execution. Actual
`get_status` and `submit_patch` call entries additionally produce activity events
with the same native entry reference/hash; these labels assert activity rather
than success. A tool response with the exact native structured
`error_type: BudgetExceeded` additionally produces `tool_rejected`, retaining
the generic response evidence. An error-message string alone does not produce
that label. Missing model requests, tool starts or rejection events are not
invented. Basic timeout
and budget outcome flags use source-pinned native error predicates; fallback uses
the native runner's recorded fallback log. These basic labels remain distinct
from direct patch, counter and trace evidence.

Each event contains sequence, elapsed time, task ID, phase and type, with optional
agent, tool, native reference/hash and observed budget state. `elapsed_origin`
explicitly distinguishes worker monotonic lifecycle time from the exact native
trace `elapsed_s` origin; missing elapsed values remain unknown. Observations
with instrumentation OFF and ON must preserve the native scripted outcome.
The native command wrapper's passive profiler records the original manager's
wheel-path queries in both modes; completed synthetic tests observe only each
sandbox's local wheel root and zero fallback queries.
Trace entry hashes seal the exact canonical entry. Reference-only native outcome
events point to files whose complete hashes/sizes are sealed in the phase result.
The process-only isolation probe emits lifecycle events and no verification event.

## Results, provenance and artifact lifecycle

`eval/runtime_result.py` defines frozen, slotted, closed, recursively immutable
records. Unknown fields, duplicate JSON keys, mutable nested inputs, wrong exact
types, invalid hashes and nonfinite/negative timing fail validation. Serialization
returns fresh containers. Optional observations use `None` for unobserved values;
fingerprint strings use literal `UNKNOWN`.

`SolverRunResult` includes schema/task/runtime status, start/elapsed time, returned
patch/hash, optional submitted/workspace patch, native agent error, escaped
exception, LLM/counted-tool/attempt counters, fallback/timeout/budget observations,
terminal reason, immutable artifact references and fingerprint. `VerifierRunResult`
includes status, verified returned-patch hash, native resolved/error fields,
canonical full native result JSON, elapsed time, artifacts and fingerprint.
`TaskRuntimeResult` combines the matching phase results and exposes only the
native verifier's resolved value. No verifier result means resolved is unknown.
These records contain no promotion, leaderboard score or holdout accounting.

Provenance captures coordinator Git HEAD; frozen candidate SHA256; canonical
solver-contract SHA256; supplied task-manifest SHA256 or `UNKNOWN`; actual worker
Python/platform/architecture; installed swegemma, adk-submission, adk-eval-core,
google-adk, google-genai and litellm versions; configured sandbox-image identity
or `UNKNOWN`; and the nonsecret scripted endpoint label. The effective native
configuration and source-admission receipt are separately sealed. Each phase
also seals `runtime_sources.json` with staged runtime code hashes/sizes and
`worker_launch.json` with its actual argv, confinement profile, cwd, constructed
environment and descriptor/session settings. Fingerprints
never copy environment credentials or endpoint URLs. This runtime implementation
is identified for audit by its source identity and by the repository diff against
the recorded baseline HEAD.

Worker inputs/workspaces live in private temporary roots outside source data.
Sealing uses fresh 0600 files and atomic no-overwrite publication. Coordinator
archival checks referenced full hashes/sizes, rejects symlinks, hardlinks and
duplicate artifact paths, and
preserves phase evidence before removing runtime roots. Canonical parsed result
bytes supply the expected `result.json` seal, so changes between collection and
archival fail closed. Generated evidence is
restricted to ignored `artifacts/dev_runtime/<run-id>/{solver,verifier}` plus
the sealed task result. It is not added to tracked documentation or repository
result tables. Solver logs and diagnostics are not sent to the verifier.

## Validation record

The existing committed full CPU baseline is **1919 passed**. Final tranche
validation was run on 2026-10-07. All tests in this
tranche are synthetic and CPU-only. They do not launch Gemma, use a GPU, download
packages, execute a public DEV/holdout task or contact Kaggle.

Contract validation includes **63 passed** in `tests/test_dev_runtime_result.py`.
Final validation results are recorded by the coordinator before handoff:

Data/process commands use `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p no:cacheprovider`
followed by the paths listed below. The full suite uses the same command without
paths. The separate native target used `.venv/bin/python -m pytest tests/test_dev_runtime_adapter.py -q -x`.
After the stale-index fix, the two focused snapshot-admission regressions used
`.venv/bin/python -m pytest tests/test_dev_runtime_adapter.py::test_snapshot_without_stale_index_is_admitted tests/test_dev_runtime_adapter.py::test_snapshot_refuses_hooks_and_external_git_discovery -q`.
Process/native tests run outside the outer tool
sandbox so each worker can apply the nested macOS confinement profile.

Final native evidence is retained under
`artifacts/dev_runtime/pytest-fad3356050f34731ae484f4128ffaa5c`: 13 completed
solver/fresh-verifier pipelines, plus invalid-patch and native JUnit-helper
evidence. Native worker fingerprints report Python 3.12.14, Darwin and arm64.

| Check | Completed result |
|---|---|
| Runtime/data-boundary targeted suite | **344 passed**: `tests/test_dev_task_contracts.py tests/test_dev_public_data.py tests/test_dev_manifests.py tests/test_dev_leakage.py tests/test_dev_runtime_staging.py tests/test_dev_runtime_result.py` |
| Process-isolation certification | **25 passed**: `tests/test_dev_runtime_process.py` |
| Native scripted certification on final source | **24 passed** in the final full suite: `tests/test_dev_runtime_adapter.py`; the earlier standalone native target passed all 23 then-existing tests. |
| Final snapshot-admission regressions | **2 passed**: the two targeted node IDs above. |
| Full CPU suite | **2072 passed in 497.72 seconds**, zero failures/skips: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p no:cacheprovider`. This final run includes the stale-index admission fix and all 24 native tests. |
| `git diff --check` and new-file whitespace review | **PASS**; all new text files have a final newline and no trailing whitespace. |
| Protected paths, generated-artifact tracking and source identity review | **PASS**; changes confined to the 17 intended runtime/data-admission/test/doc paths; generated runtime evidence ignored and untracked; E0 size/SHA256 unchanged. |

The result tests cover immutable/closed contracts, unknown observations,
returned-patch authority, identity/resolution mismatch, independent fallback and
budget representation, exact provenance, native trace references and passive
projection equivalence. Staging tests exercise mutation after admission and
during descriptor copy, truncation, append, path/root/namespace replacement,
same-size replacement, wrong SHA/size, staged-reread corruption, no-overwrite
publication, public source hardlinks, private hardlink rejection, symlinks and
special files. Native/process certification covers write/submit control,
undeclared-tool fatality, edited-workspace/empty-return fatality, timeout fallback,
counted budget rejection, free submit/status at exhaustion, empty patch variants,
invalid patch application, fresh verification, clean process/environment/FD
isolation and worker filesystem confinement. Only completed runs may populate
the final result table.

## Carried-forward P2 notes and next gate

| Prior audit note | Runtime treatment |
|---|---|
| Admission-to-staging rehash | Implemented on the exact copy stream, independent staged reread and pre-native worker revalidation. |
| Public hardlink policy | Explicitly permits linked public sources and requires fresh private single-link staged copies; private evidence and manifests remain strict. |
| Trusted raw-row projection helper | API retained in trusted intake; not staged or imported by the solver worker. |
| Manifest parsing duplication | Existing audited manifest implementation retained; no cleanup expansion. |
| Bare-module cross-test import fragility | No broad test-layout refactor; runtime fixtures are explicit package modules and prior tests retain their existing admission. |

Current limits are deliberate: macOS confinement only; fixed inert synthetic
repositories and commands; scripted local transport only; no real model endpoint,
production container/image admission, general snapshot-extraction admission,
arbitrary agent execution, DEV solver run, S1, tuning system, registry migration,
resume/scheduling, candidate comparison, semantic taxonomy, holdout execution,
training/LoRA or Kaggle submission. Native local forensic behavior is not asserted
to match hosted Kaggle behavior in every respect. The source-pinned native
semantics, process boundary and synthetic evidence are the subject of this audit.

The next external agent is **Claude Code — independent read-only runtime /
isolation audit**. Its review must cover the staged solver import/filesystem
surface, host confinement, descriptor/environment isolation, solver termination
before private staging, returned-patch authority, source pinning, passive
instrumentation, evidence sealing and the completed CPU/native validation record.
No real DEV-E0 S1 run is authorized by this document. After a passing audit,
separately admit the production sandbox, real endpoint/runtime identity and an
operator-controlled first forensic DEV-E0 run using the unchanged E0 candidate.

**RUNTIME LAYER READY FOR INDEPENDENT AUDIT.**
