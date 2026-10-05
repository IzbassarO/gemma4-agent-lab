# H13/H14/H29 Local Harness Certification

**H13: PASS. H14: PASS. H29: PASS. Authority: REPRODUCED-LOCAL.**

## Scope

Synthetic local harness only; no hidden scorer inference. Run
`20261005T003205Z-_fgsypg5` was admitted at the pushed baseline
`12c319fa8bcf5b313175f80cee3ce06a77a59619`. Its UTC run date is October 5,
2026 (October 4, 20:32:05 America/New_York). No real Gemma inference, GPU,
vLLM, competition DEV/HOLDOUT contact, hidden task inspection, competitive
agent optimization, LoRA change or Kaggle submission occurred.

This review reconstructed raw preflight, script, HTTP, trace, exceptions,
workspace, patch, verification, JUnit and inventory evidence. Terminal summaries
and `prediction_matches` were not promotion authority. Behavioral observations
are REPRODUCED-LOCAL; correlated source interpretation is source-read/inferred;
unexercised paths remain unknown. This run adds no Kaggle-verified evidence.

## Environment

| Component | Exact version / setting |
|---|---|
| Behavioral probe | CPython 3.12.14, local macOS arm64 |
| swegemma | 0.2.7 |
| google-adk | 1.36.1 |
| adk-submission | 0.2.12 |
| adk-eval-core | 0.1.0 |
| google-genai | 2.11.0 |
| LiteLLM | 1.83.14 |
| Authlib | 1.6.6; Authlib binding / AutoFlow smoke passed |
| urllib3 | 2.8.0 |
| OpenAI SDK | 2.24.0, corroborated by HTTP request headers |
| Verification support | pytest 9.1.1, iniconfig 2.3.0, packaging 26.3, pluggy 1.6.0, Pygments 2.21.0 |
| Sandbox | SubprocessManager; system_site_packages=False; wheels_dir=None |
| Budgets | H13: 0.05 minutes / 3 counted tools; CONTROL/H14: 1 minute / 3 tools; H29/boundary: 1 minute / 2 tools; all max_turns=8 |
| Command timeout | 20 seconds |
| Transport | Scripted h23-scripted responses at http://127.0.0.1:53487/v1/chat/completions |
| Review test interpreter | Repository .venv, CPython 3.14.7; separate from behavioral probe |

Installed source root, **SITE** below:
`/private/tmp/h23-cpu.UA1C8T/harness/lib/python3.12/site-packages`.
Synthetic run root:
`/private/tmp/h23-cpu.UA1C8T/h13-h14-h29-_fgsypg5`.

## Baseline and provenance

HEAD and the locally recorded `origin/main` both equal
`12c319fa8bcf5b313175f80cee3ce06a77a59619`. No remote query was performed
during interpretation; pushed provenance comes from the operator's baseline
and the preparation record. The initial status contains exactly ten prepared
probe/design/test files, identical to the budget profile's recorded admission.
No unrelated operator-run modifications were found.

The new fixed `budget` profile pins that HEAD and only those ten paths. The
historical `dispatch` profile retains
`ea5b857487ae9e146e94a88e108962742fcbdef8`, its original support-document
exception, and its original admission semantics. H04/H05/H18 implementation,
matrix entries and historical evidence remain unchanged.

All twelve recorded probe/support source hashes matched at review entry.
Five v28 wheel hashes and all 1,124 installed Python members were independently
rechecked: 20 adk-submission, 542 google-adk, 482 google-genai, 44 adk-eval-core,
36 swegemma. All twelve relevant installed-source hashes match preflight.

The sole prepared-file change during interpretation restores three original
loopback-test assertions that had been displaced into an unreachable
`pytest.raises` block. No executed runtime probe/observer source changed.
Executed `tests/test_harness_loopback.py` SHA256:
`574bc9d51c6ffc0f138d2281e1dd05e4d53ff1cf9131b0120335881259b8dc2f`.
Reviewed SHA256 after repair:
`b0c2d99b007bfb3c35443446255fc02742786330143b2a7714d0398d7423cb55`.
Raw source commitments retain the executed version.

## Probe design

All cases use the same synthetic repository and immutable FAIL_TO_PASS test.
The intended edit changes a marker from before to after. Expected patch size
is 134 bytes. No evaluator task hydration is invoked.

| Case | Scripted behavior | Required interpretation |
|---|---|---|
| CONTROL | Write, explicit submit, final text; negative and positive official verification | Establish fixture / support closure health |
| H13 | Write; withhold response 2 while real session deadline runs | Inner async timeout recovery and fallback verification |
| H14 | Three changed writes; attempted reverting fourth write; final text | Budget rejection, preservation and fallback verification |
| H29 | Two writes; status; submit; final text | Free tool reachability at exhausted counted budget |
| H29_BOUNDARY | Two writes; final text; later scripted free calls remain available | Outer invocation boundary, supplementary only |

## Artifact integrity

Paths below are relative to each case directory. CONTROL is under
`harness_cert/results/H13/20261005T003205Z-_fgsypg5/CONTROL/`; H13/H14/H29 use
their respective H-ID and this same run ID. H29_BOUNDARY is nested under H29.
All raw artifacts remain ignored and unchanged. No wheel/environment artifacts
are tracked. Inventories exclude inventory files themselves.

CONTROL: 21/21 entries match. H13: 37/37 recursive entries match, including
CONTROL. H14: 16/16 entries match. H29: all 11 main-case entries match.
H29_BOUNDARY: 11/11 entries match its own final inventory.

**One explained historical inventory mismatch remains.** H29's recursive
inventory includes the seeded boundary preflight before that later case ran:
82,841 bytes, SHA256
`8fa7de02a1d122637e8212be891783509af89df1f9b6fb81413f6800da5a49aa`.
The final boundary preflight is 83,039 bytes, SHA256
`a2758ff2c2bbb4c163a959c26a08eb6b998efeb869f2a9866ea6f74c41586e2e`.
Removing `case`, `budget`, and `declared_tools` from the final JSON and using
the probe's exact serialization reproduces the recorded seed bytes and hash.
Source order independently confirms seeding, H29 inventory, then boundary run.
Later boundary files are absent from the earlier parent inventory; their own
final inventory covers them. This P2 inventory-scope limitation does not alter
main H29 evidence. No raw inventory was repaired and no probe rerun was needed.

All returned/workspace patches across five cases are byte-identical to the
independently reconstructed expected diff: **134 bytes**, SHA256
`87fde9f7b36aaf02d72d6281d1fc6556a781de1ebe50b29307c5b75c41515f54`.
CONTROL/H29 submitted patches have the same bytes/hash. H13/H14/boundary
submitted artifacts are empty, SHA256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Every saved agent workspace is 17 bytes, SHA256
`4acbcacc43ca835b7343d9c2433c0dafd80de739ba278b5b565048306e5c326b`.
Fixture snapshot SHA256:
`d3ded879e51cb7f92f4fc5d07fbb15fb5e56815f008a335839721a2cbbb63759`;
synthetic fixture base commit: `7d0877b9e7adc920f8669e40c4030e8c0faa0164`;
immutable test SHA256:
`da39b291239678e8637a30aafa4148ed2a3b32272d6ed345bfa5596e380d4b5e`.

Important saved evidence (hashes independently recomputed from actual files):

| Case | Artifact | Bytes | SHA256 |
|---|---|---:|---|
| CONTROL | `result.json` | 261697 | `77fc21dc007c8a88024be4e84caaf416d6de9aa6b82d172b48ca2bb514c08a2f` |
| CONTROL | `trace.json` | 3755 | `fbbf77aec8abe8d4ff195c57651a1d0fee58af9665e645489f8387299d71f92f` |
| CONTROL | `http.json` | 45557 | `2e14c6d338b923bc6445284eee394b56f1a178abcc25a236d62ec5304381a771` |
| CONTROL | `exceptions.json` | 59 | `faf4a383f07006551f4ad6652b23c09f572c9afabec1179bd2a9a3ef906b24d8` |
| CONTROL | `preflight.json` | 83016 | `87d3a4106358e817ffd87041dfc3b5c5edd5aafd35f5888f073cedaace5eeaff` |
| CONTROL | `artifact_inventory.json` | 2895 | `9e24e1f9d5de44de51f6aa604310ef7a622f26f79973875a502d9391364168e5` |
| H13 | `result.json` | 206695 | `b75a42468ee039d5f0581423e97e6c2ec47bd7fbe6d14328f4ae7e5e16834edf` |
| H13 | `trace.json` | 3041 | `c72ec5d2b8faac3eff623d841ad7a1530548ba18df47d71b604443ca4a361cfb` |
| H13 | `http.json` | 29058 | `16e7516109055d873ec66caf5df1c25d71b52bb3accc54d012b62361c61d26e9` |
| H13 | `exceptions.json` | 8970 | `7e9e9d76cb1d6ca3910d52bc852684ef445f11fb1384e0de842f78a19ae49f90` |
| H13 | `preflight.json` | 83015 | `41d9e7e7bb47b37cfbcda4b29f6529165ef5038f5708026eefe75b1487f1470e` |
| H13 | `artifact_inventory.json` | 5202 | `4805c6da3b820e2b61e032ab4710e3d253649d401d826e532e011597ac6688d9` |
| H14 | `result.json` | 229858 | `cb84db86edaeaea49c4bf8a108fddd7b5a09fdcfd81de5f8a78fb8facf4f7800` |
| H14 | `trace.json` | 4912 | `ac31cdf58c8ee07177ff3e55cc6521e9a3e155109bcc9eb53c63acae8b673bb6` |
| H14 | `http.json` | 84093 | `9f72782856f4f2a986028d8f8aeacb6903f61a2ea5c4f47026bb799082a16d20` |
| H14 | `exceptions.json` | 59 | `faf4a383f07006551f4ad6652b23c09f572c9afabec1179bd2a9a3ef906b24d8` |
| H14 | `preflight.json` | 83012 | `d58c5d917fa3e93d7b80969d57a14dfae631ccf751fa66c2ba600ca9cf46deab` |
| H14 | `artifact_inventory.json` | 2142 | `c7b599dfbf35c684f9aa6368a31d6fe35ac9a898438e5dd3ab1c6047e7aab2af` |
| H29 | `result.json` | 121982 | `66d9c419e34f6ce78a36a071509b5cb2c9b2602812081ec1daaff8fe75320e61` |
| H29 | `trace.json` | 5003 | `89069a3c114ad5c4221e8a2d5dbec36bba8e5f174dbd9a609fb2ca124c35ac37` |
| H29 | `http.json` | 88609 | `30fb878a42497c29d35f936087a833c373ade1a13c159f993692af6f07b17d61` |
| H29 | `exceptions.json` | 59 | `faf4a383f07006551f4ad6652b23c09f572c9afabec1179bd2a9a3ef906b24d8` |
| H29 | `preflight.json` | 83030 | `3069303c4161490b996ff39141d82c7f295af8ef7806a9a614db640a763ed3b7` |
| H29 | `artifact_inventory.json` | 1533 | `3b6181f2fae5c25255ff7647aec9098b98a6b9202270baa3afccbefd3cfbafc6` |
| H29_BOUNDARY | `result.json` | 129628 | `efa618003d71ba520b5ed614a41ea3492f73e4f9fe028b7c2b3a1d976635b7dd` |
| H29_BOUNDARY | `trace.json` | 3827 | `c20df66a816ca74aeabe6b2950b6db54a5245b5e3f5f37148ed3ef95008c6ffa` |
| H29_BOUNDARY | `http.json` | 48455 | `a4433055994165158f7cfb2ede736a4bcbbea1e9acf59dcd59456d145bd4a324` |
| H29_BOUNDARY | `exceptions.json` | 59 | `faf4a383f07006551f4ad6652b23c09f572c9afabec1179bd2a9a3ef906b24d8` |
| H29_BOUNDARY | `preflight.json` | 83039 | `a2758ff2c2bbb4c163a959c26a08eb6b998efeb869f2a9866ea6f74c41586e2e` |
| H29_BOUNDARY | `artifact_inventory.json` | 1398 | `9a1a6e797cb547533d93455cd42cf4d51e48ff9849482d37ff5e56a71c8fd616` |
| CONTROL | `verification_negative/verification.json` | 89418 | `d49e5c530d8d0683356b3871924e83bae1a79ffbfe8c35a8d702e48f846b6463` |
| CONTROL | `verification_negative/junit.xml` | 625 | `52cb31f8f62dcc7b80401064995be5d872c0c26fa9da9c0698ce45dc5390bcc6` |
| CONTROL | `verification_positive/verification.json` | 111264 | `2aa21b0d0cc1c61a9d43133211ea7a0a78f8be4115cbe11f45e3b6bad5f4f108` |
| CONTROL | `verification_positive/junit.xml` | 321 | `cd975463a86eacaa79ed1f061a4c8e5359b7140a2e366574d0d0c64f64963a89` |
| H13 | `verification_positive/verification.json` | 111295 | `c08248b34ffd93a56574ca9bf1c4f715a458cec88153aa2ae1290d4400b4424d` |
| H13 | `verification_positive/junit.xml` | 321 | `fe28536e057dc8719d3cc2a3f6ea90c919455ab6b9b703e9da283dd608e61188` |
| H14 | `verification_positive/verification.json` | 111294 | `e5ab46dba7a3f6fc63d6b3a2e28faf21e0042496a1e11521d783916ebf738a03` |
| H14 | `verification_positive/junit.xml` | 321 | `2a0516816f139bbdc46a10d106e9b7331f687a0daab330f22290aefa63c62189` |

Executed repository source commitments (the test repair is identified above):

| Repository path | Executed SHA256 |
|---|---|
| `tests/test_harness_budget_admission.py` | `cab972b1cf990f817f1bb3bc54555ecc4e62c398c8909c64385aef1332633b2d` |
| `tests/test_harness_budget_probe.py` | `d267a22921530a1036030b4df2507ac10f6b4b9fb21973c978f55df65b92f69d` |
| `tests/test_harness_loopback.py` | `574bc9d51c6ffc0f138d2281e1dd05e4d53ff1cf9131b0120335881259b8dc2f` |
| `tests/test_synthetic_verification.py` | `b0460e70288c76c5ddbebd348c7f0eb06745adf8317ecec81e5edc59d575e6eb` |
| `tools/harness_cert/H13_H14_H29_DESIGN.md` | `499563f23901daf20b4f3e1d32f4bc8ea686815a75ba0f39836394f60f9b2894` |
| `tools/harness_cert/README.md` | `4eb3d1c32d579d7d9e0d6db43614df58e60a1857aa55a4f534bb13e8f3575fd4` |
| `tools/harness_cert/__init__.py` | `7a1fde67cf6aa9a2ff67587746045e52a7594340f8f042209747a2092f87f36f` |
| `tools/harness_cert/_probe_safety.py` | `728228c966402a2182a9dd06ed0e664285d7bece3666b14354d1023de3fbfc63` |
| `tools/harness_cert/_scripted_loopback.py` | `4ce8ec561804519d56203d5b585e773c913ca245b0345f1d1e20ca232b389aeb` |
| `tools/harness_cert/_synthetic_verification.py` | `47d4708b822d6379a05c37ae0874216c284fd8c3d2234ab0434bbf764434c47f` |
| `tools/harness_cert/run_h04_h05_h18.py` | `bbc7b49d35e78ac0209a640d3501db3d7fa909749362f50abd614375cfd66b92` |
| `tools/harness_cert/run_h13_h14_h29.py` | `382250e58869fae0c21da272900b9ca1cf4a77e6a4659cd579391742a450aee9` |

Pinned source wheels under `artifacts/harness_wheels/v28/`:

| Wheel | Matched installed .py files | SHA256 |
|---|---:|---|
| `adk_submission-0.2.12-py3-none-any.whl` | 20 | `077c438c426e625b9f722081694e1d32856e6f7e932ef625002fc4a11aabdc10` |
| `google_adk-1.36.1-py3-none-any.whl` | 542 | `1a2f6868c509e3151fb0de3575a7d18b45c338be86f420924dad74e7193631a0` |
| `google_genai-2.11.0-py3-none-any.whl` | 482 | `5bc8186100e1d34d691fbe0cba392b7e04e98d286ca952323a6672d054accf95` |
| `adk_eval_core-0.1.0-py3-none-any.whl` | 44 | `194dd8f9aab154857bfa0db9d9ec382db856ee124529e072509634d3071d8643` |
| `swegemma-0.2.7-py3-none-any.whl` | 36 | `27a2f60f8db46c8fef5defc16df722dac0402446c9a6252e7e6b4c280e843c81` |

Relevant installed source commitments, relative to SITE:

| Source | SHA256 |
|---|---|
| `adk_eval_core/sandbox/subprocess_sandbox.py` | `09dc213bd71f4ad33a2a403732271efbc78bd09db2d2999817d55f4e630ac6fc` |
| `adk_submission/builders/llm.py` | `97c27324ce60ac6dc466941bcc19f5482f7693af9fc02fb760a40526c84c10b5` |
| `adk_submission/resolvers/tools.py` | `ae44a5ca815a05edfd763b92283d0444c77755afbd827fc35bcb87eb2633259e` |
| `google/adk/flows/llm_flows/base_llm_flow.py` | `b20beb63a63aa143ef49c6246a1efed75e0dfcb9d8fae45b46c390d0cacab1fd` |
| `google/adk/tools/function_tool.py` | `3d74d850137e959804f74d3cf81f964745d6f08ce161d8b5994d8788cc1f950b` |
| `swegemma/context.py` | `97f06280c50df3132d1af7ef6126884b66b48887a1cf94b293af01e5907dc2a7` |
| `swegemma/evaluate.py` | `96a46f49267ffcf861f7f90b98efbe828d03f27553ceefce745819028589de1e` |
| `swegemma/harness/agent_runner.py` | `dff7d2505340633c3fe50ba7073f2609fb2b41becd69c356d9af25cc4c1f98dd` |
| `swegemma/harness/verification.py` | `5602f33acb49062de8e99073199745679d86445297be9dd075ccec60c41b60dd` |
| `swegemma/sandbox/subprocess.py` | `a26e9265d93f0387dd0a820ea451d1c530884bebca41583738f3ab2cc31df1f2` |
| `swegemma/tools/base.py` | `535a0e6d121549b41906685d0e440e8709c4b8b434bc31dcc82fa7740ec66281` |
| `swegemma/tools/execution.py` | `f2ce1cefb41edddaeffeacbff910a54bcd0174b548efa9c6c283320e5a509912` |

## Preflight and infrastructure validity

All five preflights agree on common provenance, support and environment. Exact
Python/prefix admission, dependency metadata, source-wheel comparisons, bundled
tokenizer hashes and Authlib/AutoFlow smoke are present in the matched source
and recorded admission. Each verification lists the same 501-file support
manifest, independently rehashed with aggregate SHA256
`3694df089cbd36a34d0d79ae5cb5ac7bbb67963aef1250661377400a10263b76`.
No task/dependency package installation, general host site-packages, .pth or
task wheel exception is admitted. Standard isolated-venv creation does run
its bundled `ensurepip --upgrade --default-pip` bootstrap, recorded in the
process evidence; this is not a dependency fetch or support-closure override.

Every case has empty infrastructure errors and workspace observer errors,
no original infrastructure exception and no escaped exception. Verification
observers also have no errors. H13's recovered timeout is the intentional
behavior under test. No launch-error artifact exists.

All 18 HTTP requests have complete bodies, the expected declared schemas,
loopback peers and the exact endpoint. Encoded bytes, text, JSON and recorded
Content-Length agree. Seventeen responses are sent; H13 response 2 is withheld
and discarded. Its recorded HTTP-200 payload is a prepared response, not a
delivered model event. Cumulative parent connection snapshots contain 3/5/10/15/18
loopback connections in case order. The only other socket binding is the
caller-specific urllib3 `(::1, 0)` capability probe, not an external connection.

Matched guards protect production-agent and competition-content roots, constrain
parent reads/writes and loopback transport, and admit only fixed synthetic child
commands/copies. Wheel-discovery checks inspect directory metadata only and
find no task wheels; they do not read DEV/HOLDOUT content. Synthetic Git
configuration has no remote or executable hook. Proxy/offline, telemetry/cache,
dotenv, timezone and null-sink safeguards remain enabled.

**The process tree is not globally firewalled.** The Python parent audit hook
does not propagate into subprocesses or prove all native-library operations.
Child protection relies on the reviewed exact command/copy policy and isolated
venvs. Empty recorded violation lists support this guarded run; they are not a
general OS containment certification. No permission or safeguard was broadened.

## CONTROL observation

Request 1 delivers write_file: successful body, size 17. Request 2 delivers
submit_patch: successful body, patch_size=134, files_changed=1. Request 3 returns
final text `Synthetic budget probe complete.` There are three completed LLM
events, two attempted/executed tools and one counted tool. Final context has
patch_submitted=true and the expected submitted patch; fallback is absent.
Returned/submitted/workspace patches have the exact shared bytes/hash above.
No original, recovered or escaped exception occurs.

Fresh negative official verification receives an empty patch: pytest exit 1,
one JUnit failure, resolved=false. Fresh positive verification applies the
returned patch with exit 0: pytest exit 0, one JUnit pass, resolved=true.
Both verification results have orchestration status SUCCESS; the negative
test outcome still fails. This establishes that the shared fixture and support
closure distinguish the unpatched and patched states.

## H13 observation

Request 1 delivers the successful counted write. Request 2 includes its success
response; its own scripted final response is withheld and never sent. Counts
are two HTTP requests, one completed LLM event and one counted tool. Workspace
contains the changed marker. No submit_patch call occurs; patch_submitted=false,
submitted_patch=null, submitted.patch empty; no final assistant event occurs.

exceptions.json records the real handled builtins.TimeoutError and traceback:
HTTP-response-header awaiting is cancelled, then asyncio/timeouts.py:115 raises
TimeoutError at SITE/swegemma/harness/agent_runner.py:498. The inner runner catch
logs at line 746; Phase 1 error is
`Agent exceeded session timeout (0.05 min)`. The observer reads the active
exception and does not synthesize it. Installed runner lines 433–443 configure
asyncio.timeout(0.05 * 60), a three-second deadline (source-read).

The write completed at trace elapsed_s=0.285; timeout occurs during the subsequent
async model wait, outside that synchronous body. No timeout timestamp or measured
deadline latency is archived. Trace duration_s=0.285 ends at the last write event
and must not be interpreted as session timeout duration. The held response has
a finite eight-second bound and is discarded after runner completion/release.

Fallback executes once: `cd /workspace && git add -N .`, followed by
`cd /workspace && (git diff --binary _swegemma_baseline 2>/dev/null || git diff --binary HEAD)`;
both exit 0, and runner line 771 records recovery of 134 bytes. The second
recorded diff is the pre-cleanup stop observer, not another fallback. No
unrelated recovery path or observer substitution occurs. Returned/workspace/
expected patches are byte-identical. Direct official positive verify_task
applies the returned patch in a fresh pristine sandbox and passes one immutable
test, pytest exit 0, resolved=true, despite the original agent timeout error.

## H13 interpretation

**PASS / REPRODUCED-LOCAL:** this real timeout during an async model wait reaches
the inner runner recovery, preserves the unsubmitted workspace diff, and yields
a patch accepted by direct official Phase 2 verification. Synchronous mid-tool
timeout/cancellation remains unknown. Full evaluator orchestration is source-read
only. CONTROL supplies the shared baseline negative, not a separate H13 negative.

## H14 observation

max_tool_calls=3. The counted gate/body evidence reconstructs this sequence:

| Request | Tool / response | Counted budget before → after |
|---|---|---|
| 1 | write_file(after), ok, size 17 | 0 → 1 |
| 2 | write_file(after), ok, size 17 | 1 → 2 |
| 3 | write_file(after), ok, size 17 | 2 → 3 |
| 4 | Attempted write_file(before), BudgetExceeded | 3 → 3 |
| 5 | Includes rejection; model returns final text | 3 → 3 |

Exact rejected response:
`{"status":"error","error_type":"BudgetExceeded","error_message":"Tool call budget exhausted (3 calls)"}`.
There are five HTTP requests / completed LLM events, four attempted tools,
three successful counted target bodies, three changed-file copies and no fourth
copy. The rejected reverting target body does not execute. The final workspace
is changed; no intermediate full workspace snapshots were captured. Counter
transitions combine ordered raw responses/copies, final context and matched
budget source; dedicated before/after counter snapshots do not exist. Admission
increments before an allowed target body, so the general counted budget must
not be defined as only successful tool bodies (source-read).

Request 5 delivers the rejection to the model and receives final text. After
that invocation, runner line 655 terminates for exhausted budget; error is
`Agent exceeded tool call budget (3 calls)`. No submit_patch or submitted state
exists. Fallback runs once with the same commands/log as H13. Returned/workspace/
expected patches are byte-identical, 134 bytes. Direct official positive
verification applies the recovered patch with exit 0; pytest exit 0, one JUnit
pass, resolved=true. H14 has no separate negative verification.

## H14 interpretation

**PASS / REPRODUCED-LOCAL:** exhausting three counted calls leaves the successful
edit recoverable through official fallback and direct Phase 2 verification.
Attempt four is rejected before its target body and does not increment the
counter. It is not a fourth executed counted tool. The model can continue within
this invocation after rejection; outer termination follows final text. This
does not certify all budget/error paths or full evaluator orchestration.

## H29 observation

All five requests advertise write_file, get_status and submit_patch with the
expected schemas; the two free calls have empty-object arguments. Sequence:

| Request | Tool / response | Counted budget before → after |
|---|---|---|
| 1 | write_file(after), ok, size 17 | 0 → 1 |
| 2 | write_file(after), ok, size 17 | 1 → 2 |
| 3 | get_status, ok; used=2, remaining=0 | 2 → 2 |
| 4 | submit_patch, ok; patch_size=134, files_changed=1 | 2 → 2 |
| 5 | Includes submit success; final text | 2 → 2 |

Exact get_status payload:

```json
{"status":"ok","tool_calls_used":2,"patch_submitted":false,"patch_size":0,"tool_calls_remaining":0,"max_tool_calls":2,"time_seconds_remaining":59.923386292066425,"max_time_minutes":1,"agent_elapsed_seconds":0.07661566592287272,"max_turns":8,"command_timeout_seconds":20}
```

Exact submit result: `{"status":"ok","patch_size":134,"files_changed":1}`.
There are five HTTP requests / completed LLM events, four successful tool
responses: two counted writes and two free tools. Raw status and final context
both show two counted calls; matched free-call decorators establish that neither
increments the counter. Individual counter transitions are reconstructed, not
dedicated snapshots. The two prior successful copies establish the changed
workspace before status/submission; the pre-cleanup workspace patch agrees.
context.submitted_patch is populated and patch_submitted=true. Submitted,
returned, workspace and expected patches have identical 134 bytes/hash.
No agent error, original/recovered/escaped exception or fallback occurs.
No H29 Phase 2 verification was executed or is claimed.

## H29 interpretation

**PASS / REPRODUCED-LOCAL:** both free tool bodies execute at fully exhausted
counted budget within the ongoing ADK invocation, and the submission is returned
unchanged. Free does not mean unconditionally reachable after an invocation
ends; the supplementary boundary defines that limit. Free handlers still obey
the time gate (source-read); behavior after time expiry is unexercised.

## H29 boundary observation

Two successful writes exhaust two counted calls. Request 3 returns final text.
The script still has get_status, submit_patch and another final response, but
there is no fourth request or free-call attempt. Outer runner line 655 terminates
the exhausted invocation before a later invocation, records the two-call budget
error, and fallback line 771 recovers the same 134-byte patch once. Submission
state remains false/null. This demonstrates the final-text invocation boundary;
it does not demonstrate immediate termination after the second tool, test free
handler rejection, or replace/weaken the main H29 evidence.

## Verification evidence

Official Phase 1 run_agent_sandbox and Phase 2 verify_task are invoked directly.
No evaluator task hydration or custom resolution shortcut runs. The synthetic
repo/base commit and explicit FAIL_TO_PASS specification reach the real patch
application, pytest and JUnit logic. Every positive starts in a separate pristine
sandbox with unchanged source/test; exact returned patch bytes are copied and
applied by official apply_patch_in_container with exit 0. Verification does not
reuse the agent workspace. The immutable test hash remains unchanged.

| Verification | Patch | pytest exit | JUnit | resolved | Duration seconds |
|---|---|---:|---|---|---:|
| CONTROL negative | Empty | 1 | 1 test, 1 failure | false | 2.740279749967158 |
| CONTROL positive | Expected returned | 0 | 1 test, 0 failures/errors/skips | true | 2.8172832910204306 |
| H13 positive | Timeout fallback | 0 | 1 test, 0 failures/errors/skips | true | 2.8191080830292776 |
| H14 positive | Budget fallback | 0 | 1 test, 0 failures/errors/skips | true | 2.891578666982241 |

The exact 501-file pure Python support closure is staged, rehashed and copied
into isolated verification venvs. No host .pth, system site-packages, wheel
discovery override or task/dependency installer is used. The standard bundled
ensurepip venv bootstrap is admitted separately. Inherited pytest configuration is
cleared. The observer records both official and executed commands, adding only
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` to the admitted pytest command because the
subprocess manager clears inherited PYTEST variables. It does not replace test
outcome/JUnit logic. CONTROL negative and positive establish closure health for
H13/H14's identical fixture. Full evaluate.py routing remains source-read only.

## Matrix decision

Hypothesis wording was confirmed against matrix.yaml before promotion and is
unchanged. H13: “When max_time expires without submit_patch, fallback extraction
preserves the working-tree diff and it is verified.” H14: “Exhausting
max_tool_calls without submit_patch still yields a verified fallback patch.”
H29: “Both remain callable when tool_calls_used == max_tool_calls.”

Only H13, H14 and H29 move NOT-REPRODUCED → PASS. Each receives the exact
run-specific REPRODUCED-LOCAL authority, synthetic-local scope, result path,
observed minimal reproduction and this report reference; the acquired local
harness_source blocker is cleared. No other matrix entry changes. H04/H05/H18,
H26 and H27 guarantees remain intact. The explicit PASS set is
H04/H05/H13/H14/H18/H26/H29; deferred H16/H24/H25 remain unchanged.

## Robustness implications

- **P0:** No new finding or certification blocker in this tranche. Historical
  missing-tool fatality and returned-patch loss remain outside these recovery paths.
- **P1:** The observed exhausted final-text invocation prevents later free-call
  reachability. Main H29 proves reachability during continuation, not across that
  boundary. Mid-tool synchronous cancellation remains a coverage limitation.
- **P2:** The frozen H29 parent inventory contains the earlier child seed, while
  the child final inventory is valid. Preserve/document this historical scope
  limitation. Three accidentally unreachable prepared-test assertions were
  restored; runtime probe sources and historical raw commitments are unchanged.

No competitive agent mitigation or optimization is implemented. Timeout and
tool-budget recovery evidence applies to these exact inner/outer paths; it does
not overturn the distinct fatal missing-tool path reproduced by H04/H18.

## Limitations

No synchronous mid-tool cancellation certification or measured timeout latency;
no real model, vLLM, GPU, Docker or Kaggle environment; no hidden scorer or
competition-score implication. Predetermined responses report zero token usage.
There are no dedicated per-tool counter or intermediate workspace snapshots.
H29/boundary do not run Phase 2. Evaluator hydration/orchestration is unexercised.
Parent audit controls are not a general process-tree firewall. The explained
parent/child manifest timing discrepancy remains in raw evidence.

## Hidden-scorer boundary

This run establishes synthetic local Phase 1 recovery/free-call behavior and
direct official Phase 2 acceptance of CONTROL/H13/H14 patches on the installed
versions. Kaggle package parity, callbacks, hidden recovery, workspace visibility,
verification/orchestration, model behavior and score impact remain unknown.
H23 remains HOST-UNKNOWN, SCORER_ONLY_UNKNOWN remains the hidden-scorer boundary,
and Step 0 is incomplete. No result here is Kaggle-verified.

## Reproduction

The exact operator command supplied for this run was:

```bash
H23_CPU_TMP=/private/tmp/h23-cpu.UA1C8T \
  /private/tmp/h23-cpu.UA1C8T/harness/bin/python -B -m tools.harness_cert.run_h13_h14_h29
```

This temporary path and its acquired environment are machine-specific. The raw
artifacts do not independently archive the original shell transcript; execution
identity is corroborated by admission/source commitments, HTTP headers and
tracebacks. The certification probe was not rerun during interpretation.

Matrix/report/test publication edits now fall outside the fixed probe admission
allowlist. This working tree is intentionally ineligible for a fresh run; no
admission rule was widened. The external next gate is Claude Code's independent
read-only audit of the entire uncommitted result, before any commit or push.
H30/H28 and Kaggle submission were not started.

## Finalization validation

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p no:cacheprovider tests/test_harness_probe.py tests/test_harness_budget_admission.py tests/test_harness_budget_probe.py tests/test_synthetic_verification.py tests/test_harness_loopback.py tests/test_tooling.py`:
  **785 passed in 1.04 seconds**.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p no:cacheprovider`:
  **1,537 passed in 18.38 seconds**, the same short CPU-only suite. These tests
  use synthetic/fake inputs; they do not rerun the behavioral certification.
- `git diff --check`, `git status --short`, `git diff --stat`: passed / reviewed.
  Untracked tranche files were also checked with
  `git diff --check --no-index /dev/null <file>`; no whitespace diagnostics.
- Independent read-only source/raw checks corroborated five source-wheel pins,
  all 1,124 installed Python files, twelve relevant source hashes, the complete
  501-file verification closure and all case evidence. The sole historical
  inventory mismatch is explained above and remains unmodified.
- Structural matrix comparison against HEAD confirms exactly H13/H14/H29
  changed, with all thirty hypothesis strings preserved. The only prepared-file
  edit is the restored loopback assertions; all runtime probe sources remain
  byte-identical to the operator run. All 80 unique raw evidence files remain
  unchanged. The thirteen uncommitted paths are the ten prepared tranche paths
  plus matrix.yaml, this report and tests/test_tooling.py.
- Interpretation changed only matrix.yaml, this report, tests/test_tooling.py
  and the test-only loopback repair. No DEV/HOLDOUT access, competitive-agent
  change, LoRA change, Kaggle submission, permission expansion, environment/wheel
  leak, secret or unexpected generated file was found in the final change set.
  No commit, push, H30/H28 work or certification rerun occurred.

**Ready for Claude Code's independent read-only certification audit.**
