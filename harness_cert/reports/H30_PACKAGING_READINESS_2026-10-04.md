# H30 observations and H28 E0 packaging readiness — 2026-10-04

H30 is **NOT PROMOTED**. Declaration-independent advertisement was reproduced
under the installed source predicate, but an exact 100-byte file pair disproves
the matrix's unqualified “whenever files exist” wording. The matrix is unchanged.

**NO-GO FOR H28 — current official Kaggle notebook/materialization and
output-selection contract is not preserved locally.** The unchanged E0 bundle
builds deterministically and loads in a detached CPU environment. These results
do not supply the missing operator submission contract. The user prohibited
browsing and submission; external verification is required before reconsidering
GO. No fatal bundle-loading defect was found.

The report date is the client's America/New_York date. Raw runs use UTC and are
dated 2026-10-05. All runtime claims below are synthetic local observations or
artifact-construction observations. No hidden-scorer or score inference follows.

## Baseline and authorized scope

Before any edit, `git status --short` and `git diff --check` were empty;
`git rev-parse HEAD` and `git rev-parse origin/main` both returned
`81ac5b150c719cafc671b884dce7f525219e4ea0`. This checks the local remote-tracking
ref; no fetch was performed. `git log -5 --oneline` contained:

```text
81ac5b1 cert(harness): reproduce H13 H14 H29 locally
12c319f cert(harness): reproduce H04 H05 H18 locally
ea5b857 cert: reproduce H27 local no-LoRA path
2ffedae cert: reproduce H26 include path semantics
4e1658a docs: confirm H23 bootstrap incompatibility
```

The existing dispatch and budget admission profiles still pin
`ea5b857487ae9e146e94a88e108962742fcbdef8` and
`12c319fa8bcf5b313175f80cee3ce06a77a59619`. Read-only admission calls refused
the newer HEAD as designed. H30 has a separate fixed baseline and exactly five
allowed probe/design/test paths. Its added branch does not alter ParentGuard,
historical allowlists, source pins, subprocess policies or filesystem/network
permissions. After this report is added, graph admission intentionally refuses
the extra report path; raw-run source hashes remain those admitted before report
publication. No blanket dirty-tree exception or caller-selectable baseline exists.

The initial tree had no unignored leftover certification artifacts. Existing
ignored raw evidence, temporary interpreter files and four official adapter files
were understood before work. No production agent, model config, adapter bytes,
DEV/HOLDOUT task content, hidden tasks, experiment registry, matrix, installed
source, historical probe, commit or remote was changed. No GPU, real generation,
evaluation, Kaggle upload, download, permission escalation or external browsing
occurred. The non-task official HARNESS_README snapshot was inspected separately;
the live sample and task files were not read or restored in this pass.

## Matrix reconstruction

| Field | H30 | H28 |
|---|---|---|
| Title | graph-tool advertisement vs declared tools | current public-sample submission path |
| Exact hypothesis | The task prompt advertises graph tools whenever graph/embedding files exist, independent of the agent's declared tools. | The official sample packaged deterministically passes the current Kaggle submission path. |
| Status | NOT-REPRODUCED | NOT-REPRODUCED |
| Risk | high | medium |
| Blockers | harness_source | kaggle_submission |
| local_possible | true | false |
| requires_model / requires_gpu / requires_docker | false / false / false | true / true / false |
| Authority | OFFICIAL-HARNESS, README 5.2 item 6 | OFFICIAL-NOTEBOOK; OWN-KAGGLE-RUN (none yet) |
| Minimal reproduction | Omit graph declarations; capture first user prompt and tools[] | Deterministic official-sample ZIP; record SHA; one justified Kaggle submission |

H30's declaration-independence component can be certified locally against exact
acquired source and request serialization. An unqualified PASS for the current
hypothesis would also require the existence predicate to hold; this run found a
counterexample. The source blocker has become stale for this local version, but
the row is deliberately retained without silently rewriting its hypothesis or
turning a partial result into PASS. Actual graph functionality, current Kaggle
source equivalence and hidden orchestration were not tested.

H28 is the first **technical control**, using the preserved official sample. Its
purpose is upload → evaluator load → agent execution → accepted result. A returned
score, including a poor or zero score, can be technical success if these stages
complete without a technical failure. It is not a candidate-quality experiment.
Before attempting it, preserve a frozen artifact, verify current official upload
and output instructions, account for the supplied runtime, and record a registry
row and operator command. DEV/HOLDOUT competitive promotion gates do not require
competitive evaluation of this unchanged technical control in this pass.

## Submission architecture and evidence level

```text
agents/baseline_v0_official (ten preserved files)
  → tools.package_official_control.package / tools.build_submission.build
  → deterministic submission.zip + external manifest/provenance/SHA256SUMS
  → CURRENT OFFICIAL KAGGLE WRAPPER / MATERIALIZATION / OUTPUT SELECTION [missing]
  → extracted agent.yaml
  → adk_submission discovery, YAML loader and compiler
  → Google ADK LlmAgent + AgentTool + LiteLlm model registry
  → organizer OpenAI-compatible model endpoint / vLLM [not run]
  → swegemma run_agent_sandbox + context-bound tools
  → submitted or fallback git diff
  → per-task prediction / organizer inference wrapper [source boundary]
  → fresh verification sandbox + metric/scorer [not run]
```

| Stage | Exact file/function and configuration | Dependency/artifact | Evidence and remaining assumption |
|---|---|---|---|
| Official source | `agents/baseline_v0_official/agent.yaml`, `sub_agents/code_analyzer.yaml`, `configs/sampling.yaml`, `eval_config.yaml`, prompts and adapters | Ten-file tree; committed `vendor_meta/baseline_v0_official_manifest.json` | All bytes matched recorded official manifest; live sample origin not rechecked |
| Official-control command | `tools/package_official_control.py::package`, CLI `python -m tools.package_official_control --dataset-root DIR` | `preserve_sample.preserve` then deterministic build | Read-only audit only; this command restores/reads live sample and was not executed here |
| Build performed | `tools/build_submission.py::build`, `write_zip`; explicit `WriteGuard`; committed manifest passed as `official_reference` | ZIP, manifest, provenance, SHA256SUMS | Two real builds at the clean baseline; source/config unchanged; no require-clean bypass |
| Inspect/verify | `tools/inspect_submission.py::inspect`, `tools/hash_submission.py::verify`; static `tools/validate_submission.py` | Archive inventory and tri-state evidence checks | Real safe extraction/validation and hash recomputation; source-only compiler assumptions remain distinct |
| Organizer bridge | `swegemma/submission.py::generate_standard_submission` and `get_eval_root` | Searches supplied dir, `/kaggle/working`, `/kaggle/tmp`; writes `submission.parquet` | Exact wheel source only; not invoked; official notebook invocation and selected output unknown |
| Load | `adk_submission/discovery.py::validate_directory`, `discover_declared_models`, `discover_adapters`; `yaml_loader.py::load_yaml`; `compiler.py::compile_submission` | adk-submission 0.2.12; PyYAML 6.0.3 | Real ten-file artifact loaded with `swegemma/config.py::build_submission_limits` constraints |
| Agent tree | `adk_submission/builders/llm.py`, resolvers; Google ADK 1.36.1; google-genai 2.11.0 | Root LlmAgent plus AgentTool child | Actual construction, includes, model aliases and tool schemas verified; AgentTool execution not tested |
| Model routing | `swegemma/models/registry.py::setup_gemma_model_registry`, `resolve_swegemma_adapter` | LiteLLM 1.83.14; OpenAI 2.24.0; organizer endpoint and serving dependencies | Real registry and adapter resolver construction; no request/server/weights loaded |
| Tools/patch | `swegemma/context.py::SwegemmaContext.create_tools`; `swegemma/harness/agent_runner.py::run_agent_sandbox`; sandbox managers | Nine built-ins; git diff captured by submit_patch or fallback | H30 synthetic official runner passed; E0 smoke bound functions but did not invoke them |
| Evaluation boundary | official README `scripts/inference.py`, `scripts/metric.py`; wheel `swegemma/harness/verification.py::verify_task` | Per-task `{id,prediction}` patches; fresh patch verification and metric | README/source evidence only here; script bodies/current wrapper not present among repo files or five wheels; no evaluation run |

The declarative bundle has no Python entrypoint or bundled installer. The loader
entrypoint is archive-root `agent.yaml`. No wheel, local Python or local wheelhouse
enters the artifact. Dependencies belong to the organizer host/runtime contract.

The exact registry source chooses endpoint from explicit argument, then
`MODEL_PROXY_URL`, `LITELLM_API_BASE`, `LOCAL_INFERENCE_URL`, `OPENAI_BASE_URL`,
then `http://localhost:8000/v1`; key precedence uses explicit argument,
`MODEL_PROXY_API_KEY`, `LITELLM_API_KEY`, `LOCAL_API_KEY`, `OPENAI_API_KEY`, `EMPTY`.
These are organizer-side assumptions, absent from bundle files. The smoke supplied
a dummy numeric loopback endpoint and never connected. Model discovery/serving
paths under `/kaggle/input` are source assumptions only.

The README documents offline repository/tool sandboxes and `/workspace`; this
does not establish internet policy for notebook bootstrap. Source/documentation
mentions `/kaggle/working` and `/kaggle/tmp`, but the current wrapper's mount,
writable-directory, dependency-installation and output-selection policy is
unverified. No network installation was attempted or proposed as a workaround.

## Exact package inventory

Two builds are preserved at
`harness_cert/results/H28/20261005T015227Z-readiness-hir9laoy/{build_a,build_b}/`.
The actual function calls used source `agents/baseline_v0_official`, candidate
`e0_official_control`, experiment `E0-READINESS-ONLY`, separate output directories,
an explicit protected external dataset root, and recorded official file hashes.
Extra provenance states that live origin was not rechecked. This is readiness
evidence, not a registered or submitted E0 experiment.

| Member | Bytes | SHA256 |
|---|---:|---|
| adapters/main_lora/adapter_config.json | 664 | `75a46da2db7f3c70442e5c728f64059aff52cf64174cee7aeb3a4ec37f6e78fb` |
| adapters/main_lora/adapter_model.safetensors | 217672 | `dcbedd989af34f5201a39606e0bd4014d351a29162dd96da418782cbbe487ad9` |
| adapters/tool_lora/adapter_config.json | 664 | `75a46da2db7f3c70442e5c728f64059aff52cf64174cee7aeb3a4ec37f6e78fb` |
| adapters/tool_lora/adapter_model.safetensors | 217672 | `dcbedd989af34f5201a39606e0bd4014d351a29162dd96da418782cbbe487ad9` |
| agent.yaml | 438 | `c7fbbbbdc44778419be8e9f53a85800eeb17d0cf151e0846c46e107401b72034` |
| configs/sampling.yaml | 120 | `3dab0b2506dae34ce92fef7b380d6073729104b23fe2cf66d44ac1d2f213aa6a` |
| eval_config.yaml | 232 | `adb486b67535fa59b427b79d44dafc131176f1f1964d184e16046ed520ede02d` |
| prompts/analyzer.md | 527 | `c762b062032cc8463015e589ad8b4f0e17296062e1b0ff1e78712eef9f5e2d43` |
| prompts/system.md | 3888 | `f1cbf7943ee9687382321b1dfd9cd88581d608c1c8b8e6ea61a763bd1a3234af` |
| sub_agents/code_analyzer.yaml | 361 | `7a764798e36cc68aa38900256245e4aed969b5439b992c7da4105b6ebcb7e62b` |

ZIP: **443572 bytes**; unpacked payload: **442238 bytes**. Exactly ten sorted
root-relative regular-file members, `ZIP_STORED`, timestamp 1980-01-01, Unix
0644, no wrapper, directory entries, executable members, symlinks, comments or
extra fields. All members and CRCs matched. The archive and manifest are
byte-identical across the two builds. Size is below the pinned README/source
3 GiB cap (3221225472 bytes); this is not verification of a current online rule.

| Artifact/fact | SHA256 |
|---|---|
| submission.zip (both builds) | `25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3` |
| manifest.json (both builds) | `32f70849f5a27ae859afb3d18a0b4abc5e23979f3e731474df6c2c75606bef2d` |
| provenance.json (both builds in this run) | `1d12e10a8ef58fe326ea5aa097a8f4bc6ba50ae2d44a5936c46be1fa13bef48c` |
| SHA256SUMS | `70df9e786298a6068bfb38e618f27c84cd4023b3f4df3b92f3c7cefa5eb829a7` |
| Source tree | `4557fcf418a85aa41c0ad560a9951f05a2726e69b249850aa9607e52c3736e2c` |
| packaging.json | `de8da03f0d7626cba36f4ef90877172b2f93ac9e28136a2ae5dd35bf5c42c053` |
| package_inventory_audit.json | `2ee20103fad2b1a1f08685871b8d9f2ab9d996ea9ff880f05fb4ed1739820ae7` |

Identical provenance here also reflects the same recorded build-time granularity;
the determinism guarantee is ZIP and manifest, not arbitrary provenance metadata.
Build provenance records clean Git state, six Git-backed file origins and four
ignored `external-official-control` adapter origins, `source_in_git: false`.
`--require-clean` was not used because it correctly rejects ignored adapter files;
the existing official-control origin path was used, not upgraded into Git proof.
Both independent verifications returned **VERIFIED_WITH_UNVERIFIABLE_EVIDENCE**,
zero mismatch, exactly six unverifiable facts: live dataset task/README hashes
and four live sample adapter origins. These facts were left unverifiable rather
than reading competition task content or claiming a stronger status.

Pattern scans of packaged text/config content found no `/Users/`, `/private/tmp/`,
Homebrew, local interpreter/venv, temporary certification, site-packages,
localhost/127.0.0.1, private-key, AWS access-key or specified long-token matches.
Binary adapter integrity is established separately by exact official-manifest
hash equality. The official
prompt's `/workspace` and warning against searching `/usr/local/lib/` are
instructions, not runtime dependencies. Source/ZIP member equality excludes
accidental raw evidence, Python caches, Git metadata and sidecar provenance from
shipping. Existing adapters were preserved; no LoRA work was performed.

## Dependency audit and detached compilation smoke

No package installation/download occurred. The artifact declares no Python
dependencies and bundles none; the competition host must supply its runtime.
Local smoke used the previously acquired harness venv, never the active project
`.venv` for competition imports. Exact Python was **3.12.14** with isolated mode
and bytecode disabled. The five source wheels and **1124 installed Python files**
were checked byte-for-byte before runtime imports:

| Package | Version | Wheel SHA256 | Matching .py files |
|---|---|---|---:|
| adk-submission | 0.2.12 | `077c438c426e625b9f722081694e1d32856e6f7e932ef625002fc4a11aabdc10` | 20 |
| google-adk | 1.36.1 | `1a2f6868c509e3151fb0de3575a7d18b45c338be86f420924dad74e7193631a0` | 542 |
| google-genai | 2.11.0 | `5bc8186100e1d34d691fbe0cba392b7e04e98d286ca952323a6672d054accf95` | 482 |
| adk-eval-core | 0.1.0 | `194dd8f9aab154857bfa0db9d9ec382db856ee124529e072509634d3071d8643` | 44 |
| swegemma | 0.2.7 | `27a2f60f8db46c8fef5defc16df722dac0402446c9a6252e7e6b4c280e843c81` | 36 |

Metadata records LiteLLM 1.83.14, Authlib 1.6.6, PyYAML 6.0.3, httpx 0.28.1,
OpenAI 2.24.0, docker 7.2.0, networkx 3.7, numpy 2.5.3 and pandas 3.0.6.
Requires-Python: adk-submission/adk-eval-core >=3.11; swegemma >=3.12;
google-adk/google-genai >=3.10; LiteLLM >=3.10,<3.14; Authlib >=3.9.
Python 3.12.14 satisfies those declarations. Exact current Kaggle versions and
dependencies are not inferred from these local values.

The acquired environment has **88 distributions** and **30 missing applicable
base metadata edges** (26 ADK, four SWEGemma), no recorded installed-version
mismatch. Missing ADK edges include cloud/database/MCP/CLI/exporter packages and
pyarrow; missing SWEGemma edges are torchvision, transformers, safetensors,
accelerate. vLLM is absent. The full list, markers and versions are in
`smoke-ri9fihfc/dependency_metadata.json`. This is a deliberately partial CPU
path, not pip-check PASS, dependency-complete evaluator or model-ready runtime.
No local package omission is asserted to exist in Kaggle.

Actual launch from repository cwd:

```sh
/private/tmp/h23-cpu.UA1C8T/harness/bin/python -I -B \
  harness_cert/results/H28/20261005T015227Z-readiness-hir9laoy/clean_room_smoke.py
```

The script moved to a private temp directory and removed repository/cwd,
PYTHONPATH, project venv and user site from import lookup. Runtime sys.path
contains only acquired CPython stdlib/dynload and acquired harness site-packages;
ENABLE_USER_SITE is false. Standard `_virtualenv.pth` runs at interpreter startup
before script guard installation; its bytes and `_virtualenv.py` were pinned and
rechecked, not bypassed. Five helper modules were copied byte-for-byte from the
admitted Git HEAD into ignored frozen evidence because the working guard had
concurrent H30 admission edits. Their original runtime policies were retained;
the historical probe's admission function was not invoked under a substituted
baseline. Fixed HEAD/artifact/source/startup commitments provide this separate
artifact-smoke admission.

The complete unmodified ZIP was extracted to
`/private/tmp/h23-cpu.UA1C8T/h28-clean-room-ri9fihfc/unpacked`. Real standard
directory/model/adapter discovery, YAML loading, submission limits, registry,
adapter resolver and compiler were called. Tools were real context-bound
functions with no attached sandbox; none was executed. Root became
`LlmAgent / swe_baseline_agent / openai/main_lora`, with nine built-ins and one
AgentTool. Child became `LlmAgent / code_analyzer_agent / openai/tool_lora`, with
read_file and all three graph tools, skip_summarization true. Both exact prompts,
sampling temperature 0.2/top_p 0.95/output 16384 and thinking budget 4096/
include_thoughts true were checked. eval_config parsed timeout 60, ten tools,
one minute and 50 turns; actual organizer application of these budgets was not
tested. Adapter discovery found two rank-four safetensors containers; no weights
were loaded or served. Authlib binding and AutoFlow construction passed.

**Result: PASS / exit 0**, 2026-10-05 02:01:06.012491Z–02:01:15.606040Z,
approximately 9.59 seconds; stdout 498 bytes, stderr zero. Zero model requests,
tool invocations, connects or guard violations, and no child process after guard
installation. Six read-only `/usr/bin/git` admission/provenance calls ran before
the guard was installed: one `git rev-parse HEAD` and five `git show`. Only the
existing reviewed urllib3 IPv6 capability-detection bind occurred; no listener.
The guard is a Python parent audit hook, not a global OS firewall. Imports after
guard installation also had additional DNS/non-capability listener refusals.
All ten extracted hashes, helper hashes and startup hashes remained unchanged.
The import inventory records 3285 module entries / 3229 unique files; independent
read-only review recomputed their hashes and roots.

Two preliminary refusals are preserved: the mutable working helper did not equal
HEAD, then the expected startup .pth bytes incorrectly included a newline. The final
attempt pinned the inspected exact bytes. No denied runtime action was granted.
`clean_room_attempts.json`, scripts and logs retain both failures.

| Smoke evidence | SHA256 |
|---|---|
| final clean_room_smoke.py | `e184877882c73dd5e60496ddb6ffb30df64012a88943c1104e5a7353ee869dc0` |
| smoke-ri9fihfc/result.json | `c50147d800b13fdf8a013a5e23b610900d274e38b31ae50481b892fa7511ffab` |
| smoke-ri9fihfc/inventory.json | `6c075c75d823ccf8bafe6748193a022d412d9e7ffaf15b7152db88f6c3933b94` |
| clean_room_evidence_index.json (22 authored evidence files) | `a7fb0d8efbf6a8cc2e7ba10aaec4f2cf531b7f9eb1114b5e271007fcccc520d6` |
| startup _virtualenv.pth | `69ac3d8f27e679c81b94ab30b3b56e9cd138219b1ba94a1fa3606d5a76a1433d` |
| startup _virtualenv.py | `cfb3db86aaa53bb62b5ff764970bec2d71c9228590a0ebec57f6ec926cc0bf1a` |

## H30 actual-run evidence

```sh
H23_CPU_TMP=/private/tmp/h23-cpu.UA1C8T \
  /private/tmp/h23-cpu.UA1C8T/harness/bin/python -B -m tools.harness_cert.run_h30
```

Exit 0, all six cases completed at
`harness_cert/results/H30/20261005T020413Z-32_z2vfp/`. Exact unchanged official
run_agent_sandbox, compiler, ADK, LiteLLM and AsyncOpenAI request construction ran.
Responses were delivered through bounded `httpx.MockTransport`, never socket
traffic. This records serialized HTTP bytes/JSON, not a network-wire capture.
Port admission remained None; zero socket connects, no network listener. A single
reviewed urllib3 IPv6 detection bind is recorded cumulatively in each case.
Synthetic subprocess sandboxes used unchanged constrained command/copy policies;
85 cumulative admitted parent process records by the final case, not 85 per case.
No global process-tree firewall claim is made.

`swegemma/harness/agent_runner.py::build_agent_prompt` lines 144–181 requires
configured graph and embedding directories, recognized repository/commit
filenames, and an existing candidate path with **st_size strictly greater than
100 bytes in each category**. It uses exists/stat without an is_file check.
It has no agent-tool argument. JSON and empty NPZ containers were synthetic
stat-only fixtures, never operational graph data. No graph tool was invoked.

| Case | Graph / embedding bytes | Graph schemas | Actual hint block | Patch control |
|---|---|---|---|---|
| ABSENT | absent / absent | absent | absent | passed |
| GRAPH_ONLY | 101 / absent | absent | absent | passed |
| EMBEDDING_ONLY | absent / 101 | absent | absent | passed |
| THRESHOLD | 100 / 100 | absent | absent | passed |
| H30 | 101 / 101 | absent | present | passed |
| DECLARED_CONTROL | 101 / 101 | all three | present | passed |

H30 and DECLARED_CONTROL have byte-identical first user prompts. The omitted case
has only write_file/submit_patch in all three serialized schema lists; declared
control additionally has search_similar_code/get_code_neighbors/get_code_subgraph.
Thus conditional declaration independence is observed in actual requests. All
cases emitted three bounded scripted completions, two successful tool responses,
three completed LLM calls, one counted write and a free submit. Returned,
submitted, observed workspace and expected patches are the same **134 bytes**,
SHA256 `87fde9f7b36aaf02d72d6281d1fc6556a781de1ebe50b29307c5b75c41515f54`.
No fallback extraction, agent error, original/escaped exception, transport refusal
or infrastructure error occurred. Predictions were checked after observation and
never promoted the matrix.

The root independently reconstructed all request/response base64/text/JSON/SHA
equivalence, complete content lengths, response delivery, prompts, schemas,
fixture lengths/hashes, patches/counters, each case inventory and **106** final
root inventory entries. Probe source bytes still match preflight. This final
inventory includes completed child inventories and has no stale seeded preflight.

| H30 evidence | SHA256 |
|---|---|
| preflight.json | `67d7d35f06f0e74b5e4947384a3954b6805c94d20dc62e7507a99135dc627962` |
| summary.json | `82f90c238ef562d428fa29e6e55dc186150fca2314e9fa66c8edcfe9898b84de` |
| artifact_inventory.json | `0fbb12bb33d22b9c848be29befa13d9cd61f508f14e428a39fb6e8bf65f6017c` |
| run_h30.py | `b08072f4e1dda3b8e64fbdf24d92f069646b02548fe4b528a71f0fd54a3a3a4a` |
| test_harness_graph_probe.py | `8d77c68f44b4eaf0ed36917ea918a99b4246fe17b1fa1efce893e64238c89511` |

## Current contract gap, risks and readiness

`docs/competition/SUBMISSION_FORMAT.md` explicitly calls the upload/notebook
wrapper HOST-UNKNOWN until H28. Its “no prediction file” wording is incomplete
at the organizer interface: exact wheel `swegemma/submission.py` searches an
extracted agent directory and writes `submission.parquet` with row IDs and
agent-directory predictions. This differs from per-task patch predictions inside
the evaluation stage. The only checked-in notebook is H23 environment capture;
it is not an official E0 submission wrapper. Community encoded-ZIP CPU recipes
are not an official authority. We cannot responsibly choose between direct ZIP
upload and notebook-generated output, or invent the exact command.

The bridge was not executed: it discovers evaluation mounts/sample rows and
requires a parquet engine absent from the CPU subset. Inspecting its source did
not inspect those files. Its SHA256 is
`d387b402223300d06eb6f4f960faf82e997098050f5317961c64e6b95365974a`.
The non-task official README SHA256 is
`3d6e57a13234cb4e783ba24eaab486459af76e0923ea4c6607cd41cc8961bbbb`.
These pins identify retained evidence, not current online-rule verification.

| Readiness item | Verdict |
|---|---|
| Clean baseline and historical source gates | satisfied |
| Short CPU tests, no secrets or task-data access | satisfied within audited scope |
| Exact deterministic artifact, size, entrypoint, inventory, no local runtime paths | satisfied |
| Detached imports/standard compiler/ADK initialization | satisfied for partial acquired CPU path |
| Known fatal artifact loading incompatibility | none found |
| Full current organizer dependency/serving environment | not verified locally |
| Patch interface vs organizer standard-submission interface distinguished | satisfied at pinned source level |
| Current official notebook/materialization/upload/output contract | missing; readiness blocker |
| Kaggle acceptance, execution, evaluator result | untested; H28 purpose |

**P0:** No observed fatal bundle-loading defect. The missing official operator
contract is a P0 readiness/evidence blocker: no concrete justified submission
command can be prepared from retained official evidence.

**P1:** The CPU subset is incomplete and does not establish vLLM/model/adapter
serving, parquet, endpoint wiring, thinking translation, AgentTool execution or
current Kaggle source equivalence. H23 remains HOST-UNKNOWN; the prior captured
CPython 3.13.15/cp312 TVM bootstrap incompatibility is not a hidden-scorer defect
claim. These are technical-control questions, not invented fatal agent findings.

**P2:** Literal H30 wording omits its source size/name predicate. Existing static
validator notices and broad old submission docs still describe some H04/H26/
runtime items as unreproduced despite later matrix/report evidence. Their
structural result remains valid; behavioral notices are not authoritative PASS
or failure claims. No unrelated documentation/validator cleanup was bundled here.

Kaggle alone can verify current mounted runtime/source versions, model/server
startup and aliases, adapter serving, endpoint behavior, actual orchestration,
wrapper-selected output portability, evaluator acceptance and returned result.
The current official notebook/rules can be externally verified without submitting
when separately authorized; this pass did not browse. No hidden scorer inference
or requirement for a good score is introduced.

**NO-GO FOR H28 — current official Kaggle notebook/materialization and
output-selection contract is not preserved locally.** E0 is not activated, no
submission command is guessed, and no registry row or submission identifier exists.
After that contract is verified, a separate operator plan should freeze reviewed
commit plus this artifact/config identity, prepare the exact official command,
record timestamp/ID/status/logs/score, classify packaging/import/dependency/runtime/
model-harness/output/evaluator failures, and retain poor-score technical success
separately from agent-quality experiments. Diagnose failures before any tuning.

## Validation, publication and independent review

Actual checks:

```text
python -m tools.validate_submission agents/baseline_v0_official --json
  project .venv, -B: exit 0, STRUCTURAL_VALID, no errors/warnings
  CURRENT_HARNESS_COMPATIBILITY_UNKNOWN retained
two direct tools.build_submission.build calls at clean baseline
  exact ten-file manifest match, byte-identical ZIP and manifest
inspect_submission.inspect and hash_submission.verify, both builds
  safe valid archive; VERIFIED_WITH_UNVERIFIABLE_EVIDENCE, zero mismatch
clean-room command above
  exit 0, PASS; zero model requests, tool invocations, connects or guard violations
  no child process after guard installation; six read-only /usr/bin/git
  admission/provenance calls before guard installation (one rev-parse HEAD, five show)
H30 command above
  exit 0; six valid observations and patch controls; no matrix promotion
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/test_harness_graph_probe.py
  142 passed in 4.73s
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p no:cacheprovider
  1679 passed in 19.08s
git diff --check; status/stat and new-file whitespace checks
  checked again after report publication
```

The full short CPU suite includes packaging/submission, provenance, safety,
corrective audit, tooling, historical admission and new graph tests. Fake unit
tests do not replace the separately executed installed-harness observations.
H30 test coverage includes strict fixed baseline/five-path admission, refusal of
matrix/report/production/historical-probe edits, fixture generation, request
origin/body/size/script refusals, evidence corruption, missing controls and
prediction/observation separation.

Six uncommitted paths are intended:

- `tools/harness_cert/_probe_safety.py`: new fixed graph admission branch only.
- `tools/harness_cert/run_h30.py`: bounded six-case operator observation probe.
- `tests/test_harness_graph_probe.py`: fake unit/admission/evidence regression tests.
- `tools/harness_cert/H30_DESIGN.md`: hypothesis, controls and evidence limits.
- `tools/harness_cert/README.md`: scoped operator command and design pointer.
- this report: package/readiness/raw-evidence reconstruction and NO-GO decision.

No matrix item changed. Matrix SHA256 remains
`e2d9312f91631eb77c724ce6fef0a58a07bcb83f832c495b2bda1540b20752b8`.
Raw artifacts and exact smoke script stay in ignored results; temporary acquired
paths are provenance references only and do not ship. Code, tests and report are
uncommitted for **Claude Code — independent read-only audit**. That audit should
recompute both raw inventories and the ZIP/member/origin hashes, inspect the
unchanged guard diff and strict admission branch, review counterexample handling
and distinguish local construction from missing current external contract. Do not
execute a model, historical probe, wrapper, data restoration or Kaggle submission
as part of that review. No commit or push has been made.

**NOT READY — BLOCKER FOUND**
