# H04 / H05 / H18 local certification — 2026-10-04

**H04: PASS. H05: PASS. H18: PASS. Authority: REPRODUCED-LOCAL.
Evidence class: RUNTIME-REPRODUCED. Scope: synthetic local harness only;
no hidden scorer inference.**

Run `20261004T172725Z-wynp5tjr` establishes the declared-tool positive control,
fatal lookup of an undeclared tool, and fatal lookup of a nonexistent tool after
a successful edit. H18's edit survives in the workspace at pre-cleanup
observation, while its returned agent patch is empty. PASS for H04/H18 means the
fatal behavior is reproduced, not that recovery or task solving succeeds.

This finalization independently reviewed all three cases' `result.json`,
`trace.json`, `http.json`, `exceptions.json`, `agent.patch`, `workspace.patch`,
`workspace_app.py`, and synthetic logs. The shared preflight is stored only in
H05's directory. The terminal summary was corroborating information, not the
basis for promotion. Earlier invalid attempts are not certification evidence.

## Run, environment and provenance

The run ID starts at **2026-10-04 17:27:25 UTC (13:27:25 America/New_York)**.
Repository HEAD at admission and finalization is
`ea5b857487ae9e146e94a88e108962742fcbdef8`. The probe implementation was
uncommitted at execution. Its exact source commitments are retained below.

| Component | Version / observed setting |
|---|---|
| Probe Python | CPython 3.12.14, local macOS arm64 |
| swegemma | 0.2.7 |
| google-adk | 1.36.1 |
| adk-submission | 0.2.12 |
| adk-eval-core | 0.1.0 |
| google-genai | 2.11.0 |
| LiteLLM | 1.83.14 |
| Authlib | 1.6.6; `AUTHLIB_IMPORT_OK` recorded |
| urllib3 | 2.8.0 |
| OpenAI SDK | 2.24.0, observed in all HTTP request headers |
| Sandbox | `SubprocessManager`, `system_site_packages=False`, `wheels_dir=None` |
| Budget | 1 minute, 8 counted tools, 6 LLM turns; command timeout 20 seconds |
| Model / transport | scripted `h23-scripted`, local OpenAI-compatible HTTP stub; no Gemma/vLLM/GPU |

Installed source root, denoted **SITE** in the references below:
`/private/tmp/h23-cpu.UA1C8T/harness/lib/python3.12/site-packages`.
Synthetic run root:
`/private/tmp/h23-cpu.UA1C8T/h04-h05-h18-wynp5tjr`.

The documented invocation corresponding to this probe is:

```bash
export H23_CPU_TMP=/private/tmp/h23-cpu.UA1C8T
"$H23_CPU_TMP/harness/bin/python" -B -m tools.harness_cert.run_h04_h05_h18
```

The original shell command transcript is not an archived artifact. This command
identifies the implementation and acquired interpreter; it was **not rerun**
during finalization. Source, runtime and behavior are independently identified
by preflight, HTTP runtime headers, tracebacks and the manifests below.

Preflight checked five v28 wheel SHA256 values and byte equality of installed
Python source against every `.py` member: 20 adk-submission, 542 google-adk,
482 google-genai, 44 adk-eval-core and 36 swegemma files, **1,124 total**.
Finalization independently repeated those read-only byte comparisons and wheel
hash checks successfully. All eight current probe/support-document hashes also
matched the run's preflight before evidence publication edits. This is source
identity evidence for the inspected distribution versions, not a claim about
the hidden scorer's installed stack.

## Independent case reconstruction

| Observation | H05 positive control | H04 undeclared tool | H18 nonexistent tool after edit |
|---|---|---|---|
| Matrix status | PASS | PASS | PASS |
| Declared / advertised tools, every request | `write_file`, `submit_patch` | `submit_patch` only | `write_file`, `submit_patch` |
| Scripted calls actually returned by server | `write_file`, `submit_patch`, final text | `write_file` | `write_file`, `bash` |
| HTTP requests / `llm_calls_used` | 3 / 3 | 1 / 1 | 2 / 2 |
| Attempted calls in trace | 2 | 1 | 2 |
| Successful tool responses | 2 | 0 | 1 |
| `tool_calls_used` (budget counter) | 1 | 0 | 1 |
| Turn after valid write | Yes | No valid write | Yes |
| Turn after missing-tool request | Not applicable | No second turn | No third turn |
| `patch_submitted` / `submitted_patch` | `true` / exact 134-byte patch | `false` / `null` | `false` / `null` |
| Original exception count / escaped exception | 0 / `null` | 1 / `null` | 1 / `null` |
| `agent_error` | `null` | `Sandbox execution error: ` + H04 message below | `Sandbox execution error: ` + H18 message below |
| Workspace `app.py` | `MARKER = "after"\n`, 17 bytes | `MARKER = "before"\n`, 18 bytes | `MARKER = "after"\n`, 17 bytes |
| Workspace diff / returned agent patch | 134 / 134 bytes; identical | 0 / 0 bytes | **134 / 0 bytes** |
| Infrastructure / observer errors | `true` / empty lists | `true` / empty lists | `true` / empty lists |

### H05 — declared-tool positive control

HTTP response 1 requests `write_file` with exactly
`{"content": "MARKER = \"after\"\n", "filepath": "app.py"}`. Trace records its
response as `{"status": "ok", "filepath": "app.py", "size": 17}`; the manager
records the corresponding file copy into `/workspace/`. HTTP request 2 contains
that successful tool response and advertises the same two tools. Its response
requests `submit_patch` with `{}`. Trace records
`{"status": "ok", "patch_size": 134, "files_changed": 1}`. HTTP request 3
contains both successful tool responses, and the server returns the text-only
completion `Synthetic probe complete.`

The physical workspace snapshot is exactly the expected `after` content.
`agent.patch`, `workspace.patch`, `result.agent_patch`, the observer's diff,
and `context.submitted_patch` are byte-for-byte equal to this synthetic diff:

```diff
diff --git a/app.py b/app.py
index d6042cf..1bcfe49 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-MARKER = "before"
+MARKER = "after"
```

Each patch is 134 bytes with SHA256
`87fde9f7b36aaf02d72d6281d1fc6556a781de1ebe50b29307c5b75c41515f54`.
`control_pass=true`; infrastructure and observer error lists are empty;
`agent_error=null`; `exceptions.json=[]`; `escaped_exception=null`.
The trace has seven entries: two harness prompt entries, write call/response,
submit call/response and final text. H05 satisfies the positive-control statement.

### H04 — registered, undeclared and unadvertised `write_file`

The standard context registry includes `write_file`, but this agent declares
only `submit_patch`, and its sole HTTP request advertises only `submit_patch`.
The scripted response nevertheless requests the same `write_file` arguments
as H05. ADK `_get_tool` raises **`builtins.ValueError`** before any bound tool
executes. There is no function response, second HTTP request, second LLM turn,
submission, final assistant text, write-result record or edited-file copy.

The trace's `tool_call` entry and log's write-call display denote the attempted
model call, not execution: three trace entries end at the attempted call;
the budget counter stays zero; `workspace_app.py` is still the 18-byte `before`
file; both patch artifacts are empty. The error is logged by the official
runner and captured as the original exception, then converted to its documented
return string. Nothing escapes the direct `run_agent_sandbox` call.

The static H04 matrix prediction was undeclared-tool `ValueError` followed by
task termination; the acquisition plan identified `_get_tool` but left fatal
runner behavior untested. This run confirms that statement for the local stack.
The original planned graph-tool example is replaced by the actually exercised
`write_file` case. It does not certify a graph-tool-specific behavior.

### H18 — successful edit, then missing `bash`, with an empty returned patch

Both HTTP requests advertise exactly `write_file` and `submit_patch`. Response 1
requests the valid write; its successful response appears in the trace and in
HTTP request 2. The file copy is observed, and the counted-tool counter becomes
one. Response 2 requests **`bash`**, with
`{"command": "echo synthetic"}`. `bash` is absent from both the declared agent
tools and the nine-tool standard registry. It is a requested tool name; the
probe's setup subprocesses using `/bin/bash` do not constitute execution of
that nonexistent tool or of its `echo synthetic` argument.

ADK raises **`builtins.ValueError`** at lookup. No `bash` function response,
third HTTP request, recovery turn, `submit_patch`, or final assistant text
occurs. The five trace entries contain two harness prompts, valid write
call/response and attempted `bash`. `llm_calls_used=2`, `tool_calls_used=1`,
`patch_submitted=false`, and `submitted_patch=null` agree with that sequence.

Before sandbox cleanup, the observer reads the physical `after` file and
captures the same 134-byte workspace diff as H05. **`agent.patch` is zero bytes**,
`result.agent_patch=""`, and `prior_edit_in_returned_patch=false`.
Physical edit persistence therefore does not imply returned-patch persistence.
The outer fatal runner branch bypasses both submitted-patch assignment and
fallback extraction. This confirms H18's fatality prediction and the prior-edit
loss concern in the returned patch for this exact local path.

## Original exceptions and traceback correlation

For H04 and H18, `exceptions.json` exactly equals `result.original_exceptions`.
Each has one record from `swegemma.harness.agent_runner`, with
`adk_get_tool_in_traceback=true`, `class="builtins.ValueError"` and the complete
traceback. The exact exception messages are:

**H04:**

```text
Tool 'write_file' not found.
Available tools: submit_patch

Possible causes:
  1. LLM hallucinated the function name - review agent instruction clarity
  2. Tool not registered - verify agent.tools list
  3. Name mismatch - check for typos

Suggested fixes:
  - Review agent instruction to ensure tool usage is clear
  - Verify tool is included in agent.tools list
  - Check for typos in function name
```

**H18:**

```text
Tool 'bash' not found.
Available tools: write_file, submit_patch

Possible causes:
  1. LLM hallucinated the function name - review agent instruction clarity
  2. Tool not registered - verify agent.tools list
  3. Name mismatch - check for typos

Suggested fixes:
  - Review agent instruction to ensure tool usage is clear
  - Verify tool is included in agent.tools list
  - Check for typos in function name
```

Both `agent_error` strings are exactly `Sandbox execution error: ` concatenated
with their respective message, without a change of exception text. The raw
tracebacks share this complete Python frame sequence, outermost to innermost;
each path is relative to the exact SITE root above:

| Installed source path:line | Function |
|---|---|
| `swegemma/harness/agent_runner.py:521` | `run_agent_sandbox` |
| `google/adk/runners.py:613` | `run_async` |
| `google/adk/runners.py:596` | `_run_with_trace` |
| `google/adk/runners.py:858` | `_exec_with_plugin` |
| `google/adk/runners.py:585` | `execute` |
| `google/adk/agents/base_agent.py:295` | `run_async` |
| `google/adk/agents/llm_agent.py:488` | `_run_async_impl` |
| `google/adk/flows/llm_flows/base_llm_flow.py:869` | `run_async` |
| `google/adk/flows/llm_flows/base_llm_flow.py:956` | `_run_one_step_async` |
| `google/adk/flows/llm_flows/base_llm_flow.py:1050` | `_postprocess_async` |
| `google/adk/flows/llm_flows/base_llm_flow.py:1178` | `_postprocess_handle_function_calls_async` |
| `google/adk/flows/llm_flows/functions.py:358` | `handle_function_calls_async` |
| `google/adk/flows/llm_flows/functions.py:405` | `handle_function_call_list_async` |
| `google/adk/flows/llm_flows/functions.py:505` | `_execute_single_function_call_async`, re-raise |
| `google/adk/flows/llm_flows/functions.py:491` | `_execute_single_function_call_async`, lookup |
| `google/adk/flows/llm_flows/functions.py:1003` | `_get_tool`, `raise ValueError(error_msg)` |

## Why the observed returns and counters follow the installed source

| Source reference (under SITE) | Correlation |
|---|---|
| `swegemma/tools/__init__.py:34–46`; `swegemma/harness/agent_runner.py:319–336` | All nine standard tools form the compilation registry. A registry entry does not automatically become an agent's declared tool. |
| `adk_submission/builders/llm.py:62–65,77–96`; `adk_submission/resolvers/tools.py:64–65` | Only `config.tools` entries are resolved from the registry and supplied to the compiled LlmAgent. This explains H04's registered-but-unadvertised write tool. |
| `google/adk/flows/llm_flows/functions.py:988–1005` | `_get_tool` checks the agent's runtime tools dictionary, formats the observed message and raises at line 1003. |
| `google/adk/flows/llm_flows/functions.py:490–505` | Missing lookup invokes plugin/agent tool-error callbacks; a non-null callback response can recover. These cases produce no recovery response and reach the recorded re-raise at line 505. Fatality is not universal across custom callback configurations. |
| `google/adk/flows/llm_flows/functions.py:403–411` | `asyncio.gather` propagates the original error through the function-call list handler. |
| `swegemma/tools/workspace.py:180–208` | Budget-gated `write_file` stages the exact file content, creates the workspace parent, copies the file and returns the observed successful result. |
| `swegemma/tools/base.py:72–103`; `swegemma/context.py:548–550` | The counted-tool budget increments when the bound valid tool reaches its budget gate. A failed lookup never reaches that gate. |
| `swegemma/tools/execution.py:76–103` | `submit_patch` is explicitly `count_tool_call=False`, extracts a git diff and sets `submitted_patch` and `patch_submitted`. Thus H05 has two successful tool responses but only one counted tool. This does not promote H29. |
| `swegemma/context.py:195–198,278–298`; `swegemma/harness/agent_runner.py:378–382,527–528` | Nonpartial model events count LLM calls; tool-response-only events do not. The HTTP request totals independently agree with all three recorded LLM counters. |
| `swegemma/harness/agent_runner.py:741–753` | The inner recovery catch handles timeout/LLM-budget errors, not lookup `ValueError`. No such budget error occurs here. |
| `swegemma/harness/agent_runner.py:758–776` | Submitted patch assignment and unsubmitted-diff fallback follow normal loop exit or the narrow inner catch. H05 reaches submitted patch assignment. H04/H18 bypass this block. |
| `swegemma/harness/agent_runner.py:209–210,799–812` | `agent_patch` starts empty; the outer general exception handler logs the original exception and sets `agent_error`; `finally` stops the sandbox and the runner returns the unchanged empty patch. |

The independent observer in `tools/harness_cert/run_h04_h05_h18.py:281–299`
runs a workspace diff immediately before the original manager stop. This is
the sole H04/H18 observed diff command. Neither case records `git add -N .`,
which the official fallback would execute at `agent_runner.py:763`, nor a
submission tool response. The observer's diff is not runner patch recovery.
The original manager then deletes its sandbox; persistence here means at that
pre-cleanup observation, not indefinite retention of the temporary directory.

H04/H18 continuation responses were available: probe `scripts()` at
`tools/harness_cert/run_h04_h05_h18.py:326–334` leaves submit/final responses
after the missing call. The observed termination therefore does not come from
an exhausted script. The budgets also remain below their limits.

## Safety, transport and artifact integrity

All cases have `infrastructure_ok=true`, `infrastructure_errors=[]`, empty
observer error lists, `escaped_exception=null` and no competition content
access recorded. All six HTTP responses are 200, with complete request bodies,
loopback peer `127.0.0.1`, path `/v1/chat/completions`, zero retry-count headers
and the intended advertised schemas. For every request/response, finalization
verified base64 bytes equal `body_text`, its parsed JSON equals the stored JSON,
and declared Content-Length equals the byte count. Stored patch/file content
and hashes agree with their corresponding result/observer fields.

The parent connects only to `127.0.0.1:64155`. A single separately classified
urllib3 import-time `(::1, 0)` bind is the caller-specific capability check,
not an IPv6 listener or outbound connection. Parent socket/process lists are
**cumulative snapshots**, not per-case counters: connection totals 3 / 4 / 6;
process totals 25 / 34 / 44 at H05 / H04 / H18. These agree with per-case HTTP
counts 3 / 1 / 2. Each case records its own successful sandbox commands and
pre-cleanup workspace observation; no `echo synthetic` command executes.

The shared preflight checks 17 unique wheel-discovery candidate directories
by metadata only; all report no top-level wheels. Both resolver sets are
checked before execution, sandbox start and each command. Sandbox venvs have
`system_site_packages=False`; no wheel auto-discovery exception or task package
installation is admitted. Synthetic workspace configuration includes no remote
or executable Git hook. Protected production-agent/data roots remain excluded.

Source-verified guards remain in the admitted probe: exact loopback transport,
disabled proxies/redirects and retries, dummy credentials, dotenv suppression,
offline/tokenizer settings, callback/cache/exporter disabling, narrow timezone
reads in `/private/var/db/timezone/tz/2026c.1.0/zoneinfo`, exact null-sink opens,
and synthetic/output mutation restrictions. The client deliberately uses
`X-Stainless-OS: Unknown`, avoiding optional SDK `uname` discovery. Authlib 1.6.6
and AutoFlow construction smoke passed before cases started. These guards are
infrastructure controls; they do not alter the official ADK dispatch or runner
exception/fallback behavior being certified.

**The process tree is not globally firewalled.** Python audit hooks cover the
parent and do not propagate into subprocesses or intercept every native-library
operation. Children are admitted through the reviewed exact command policy.
Empty violation lists do not establish a general OS sandbox or hidden-scorer
security property.

## Reproducibility commitments

Evidence directories relative to the repository root:

- H05: `harness_cert/results/H05/20261004T172725Z-wynp5tjr/`
- H04: `harness_cert/results/H04/20261004T172725Z-wynp5tjr/`
- H18: `harness_cert/results/H18/20261004T172725Z-wynp5tjr/`

All raw artifacts remain gitignored and unchanged. Each manifest filename is
relative to its case directory. Hashes commit to the actual saved bytes,
including JSON formatting and ANSI bytes in logs.

| Case | Evidence file | Bytes | SHA256 |
|---|---|---:|---|
| H05 | `result.json` | 18825 | `53ba405d0c41bbc9df4f7e8894aaf29489aedc902b91e33c8d440c1f03054c3b` |
| H05 | `trace.json` | 3740 | `a2d22231901795d514a7e3d3fd8c0d0703b6bf52f53a3ca325fc50ed511e6fad` |
| H05 | `http.json` | 45522 | `8805b766a9a7c69b81f87e55413721fe91781c00ffade78140bdeb5e96627709` |
| H05 | `exceptions.json` | 3 | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| H05 | `preflight.json` | 6096 | `8cb0ba9da10e17502152a737e18284ec372c085477d8f97164ca5e0f8dcb8559` |
| H05 | `agent.patch` | 134 | `87fde9f7b36aaf02d72d6281d1fc6556a781de1ebe50b29307c5b75c41515f54` |
| H05 | `workspace.patch` | 134 | `87fde9f7b36aaf02d72d6281d1fc6556a781de1ebe50b29307c5b75c41515f54` |
| H05 | `workspace_app.py` | 17 | `4acbcacc43ca835b7343d9c2433c0dafd80de739ba278b5b565048306e5c326b` |
| H05 | `logs/synthetic_h05.log` | 664 | `9353d1d4462b83801ed9f9beb8631a255fd5f7eb1deb4bd526f3af490fdf1f14` |
| H04 | `result.json` | 27983 | `be86b0ede5407b5156f01028300c025f56a368acb804e44835f0a6d8435f01a6` |
| H04 | `trace.json` | 2812 | `885cae0dd905c6c014b96efb726409815e7ff8f9cb628f8de9f8a4815224155c` |
| H04 | `http.json` | 12455 | `17c27dad588e3b440550af34752d109d8536559c5c57bd6583c678f5b87549b8` |
| H04 | `exceptions.json` | 4244 | `c81d9d8e9a878ace6f1298b04a4ac27930a07b2b1c23fa66ef6c7c32ddb92262` |
| H04 | `agent.patch` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| H04 | `workspace.patch` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| H04 | `workspace_app.py` | 18 | `aef6b2fb2182f712201dcd0cf9abeb49fe8fd143d03c30d2d38f8f8a3b1b9ae4` |
| H04 | `logs/synthetic_h04.log` | 342 | `5c192c6c9f2a766af505014b5f5738f106d978af6a8b5a07e1ebea3f79059600` |
| H18 | `result.json` | 35345 | `15966375ce74395f0f3831773fc2e159cdfb02bb22b82e78789a0ab21decc451` |
| H18 | `trace.json` | 3317 | `fd46d0a81bb399613d98589d0f9efdc5e1ba9b8b4dffdd5f06d77b4b7623893f` |
| H18 | `http.json` | 29629 | `5447e217cf721f17cb85e8dd0b1afcf72f99e9b67a9f9f0358187f9a78ad030e` |
| H18 | `exceptions.json` | 4256 | `4dcc16f36dbff5d7a6d501602ae95520bfe033f4cc461e3df773e1aca1976b72` |
| H18 | `agent.patch` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| H18 | `workspace.patch` | 134 | `87fde9f7b36aaf02d72d6281d1fc6556a781de1ebe50b29307c5b75c41515f54` |
| H18 | `workspace_app.py` | 17 | `4acbcacc43ca835b7343d9c2433c0dafd80de739ba278b5b565048306e5c326b` |
| H18 | `logs/synthetic_h18.log` | 499 | `b6593e88bab25ded8a9b1263923c602bcfcea9744f92e07a7386a88aa58a6819` |

The synthetic tar copy has SHA256
`7797f8cd952ca6aeff361f5d9be4026896689b52a5a7d4984cd87ac23155e332`
in all three cases. The saved workspace/patch artifacts define the reproducible
fixture outcome independently of temporary tar timestamps and sandbox names.

Source wheels under `artifacts/harness_wheels/v28/`, verified against preflight:

| Wheel | SHA256 |
|---|---|
| `adk_submission-0.2.12-py3-none-any.whl` | `077c438c426e625b9f722081694e1d32856e6f7e932ef625002fc4a11aabdc10` |
| `google_adk-1.36.1-py3-none-any.whl` | `1a2f6868c509e3151fb0de3575a7d18b45c338be86f420924dad74e7193631a0` |
| `google_genai-2.11.0-py3-none-any.whl` | `5bc8186100e1d34d691fbe0cba392b7e04e98d286ca952323a6672d054accf95` |
| `adk_eval_core-0.1.0-py3-none-any.whl` | `194dd8f9aab154857bfa0db9d9ec382db856ee124529e072509634d3071d8643` |
| `swegemma-0.2.7-py3-none-any.whl` | `27a2f60f8db46c8fef5defc16df722dac0402446c9a6252e7e6b4c280e843c81` |

Key installed source hashes, all matched to their exact wheel members:

| Path under SITE | SHA256 |
|---|---|
| `swegemma/harness/agent_runner.py` | `dff7d2505340633c3fe50ba7073f2609fb2b41becd69c356d9af25cc4c1f98dd` |
| `swegemma/tools/workspace.py` | `d38ba2f68636c5e33c9f607bd9e5401dbf81d77125b966ef87c799a1615b05c6` |
| `swegemma/tools/execution.py` | `f2ce1cefb41edddaeffeacbff910a54bcd0174b548efa9c6c283320e5a509912` |
| `swegemma/tools/base.py` | `535a0e6d121549b41906685d0e440e8709c4b8b434bc31dcc82fa7740ec66281` |
| `swegemma/tools/__init__.py` | `e19d1e81d90976fda93f346104d7f34f74018747ac81838a363de2e357358c31` |
| `swegemma/context.py` | `97f06280c50df3132d1af7ef6126884b66b48887a1cf94b293af01e5907dc2a7` |
| `google/adk/flows/llm_flows/functions.py` | `551bf06d5e6bc0f257eef899deb431493d0b6c45e415f7731f5ed7bac4e95e06` |
| `adk_submission/builders/llm.py` | `97c27324ce60ac6dc466941bcc19f5482f7693af9fc02fb760a40526c84c10b5` |
| `adk_submission/resolvers/tools.py` | `ae44a5ca815a05edfd763b92283d0444c77755afbd827fc35bcb87eb2633259e` |

Probe/support source hashes as executed, from the shared preflight:

| Repository path | SHA256 |
|---|---|
| `tools/harness_cert/run_h04_h05_h18.py` | `bbc7b49d35e78ac0209a640d3501db3d7fa909749362f50abd614375cfd66b92` |
| `tools/harness_cert/_probe_safety.py` | `5b0b2ca9bf7cdf7c9ac086de92cca899e737f077f271d81a53947c20ded4d47e` |
| `tools/harness_cert/_scripted_loopback.py` | `b6e177265a00d1eb2f9fb50ff33c95f41720c3146117b2ece8792ec2641c4250` |
| `tools/harness_cert/__init__.py` | `7a1fde67cf6aa9a2ff67587746045e52a7594340f8f042209747a2092f87f36f` |
| `tools/harness_cert/README.md` | `ea5b8827923f6d1e3683d478197be33f2323926cd1b569a09eb4418f7597f423` |
| `tests/test_harness_probe.py` | `997e10381f6a309651d76adc4e69969b7c3bc34a21a765e4669f50a26898ac94` |
| `tests/test_harness_loopback.py` | `0821ec170f6633302b8786c57cb6ff15dad613267f5c4b979a03f5876a217b5f` |
| `harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md` | `bacc52dd519d55c4316237f52daee4da32c4c808598512a18f4924a372a258c0` |

## Matrix changes, limitations and remaining concerns

Only H04, H05 and H18 change from NOT-REPRODUCED to PASS. Each gains
REPRODUCED-LOCAL authority, explicit synthetic-local scope, the exact run result
path, actual exercised reproduction and a reference to this report. Their
`harness_source` blockers are removed for this acquired local stack. All other
matrix entries, production agents, E0, H23, H26, H27, LoRA and competition
DEV/HOLDOUT remain unchanged. The existing matrix metadata test is updated to
require the three scoped promotions and their report rather than rejecting
every PASS other than H26.

The modeled responses are predetermined, with zero token usage supplied by
the stub. The observations certify local ADK/LiteLLM/tool dispatch and official
runner returns, not model quality, parser behavior, scoring, patch application,
test verification, Docker parity, callback-based recovery or all error paths.
Timeout/tool-budget recovery and a fatal call after a previous submission were
not exercised. H30 advertisement and other matrix hypotheses remain untested
by this report. **H23 stays HOST-UNKNOWN; the hidden scorer stays
SCORER_ONLY_UNKNOWN; Step 0 is incomplete.**

There is no remaining P0/P1 evidence-validity blocker for these three local
certifications. A **P0 candidate-robustness concern remains**: a missing-tool
request can terminate this local session and lose an unsubmitted returned
patch despite a valid earlier workspace edit. PASS documents that risk; no
agent mitigation is implemented here. The **P1 coverage limitation** remains
the unverified hidden-scorer environment and other candidate-relevant harness
paths; these results supply no authority to infer their outcomes.

This report, the matrix edits and the metadata-test update are publication
changes made after the run. They are outside the probe's strict dirty-file
allowlist at the reviewed baseline, so this working tree is not immediately
eligible for a fresh probe run. Historical evidence remains valid. No admission
rule was widened, no probe was rerun, and no commit/push was performed.

## Finalization validation

- `.venv/bin/python -m pytest`: **1,269 passed in 28.86 seconds**. This is the
  repository test environment (CPython 3.14.7), separate from the acquired
  CPython 3.12.14 behavioral-probe environment.
- `git diff --check`: **PASS**, with no whitespace errors. The new, untracked
  report was also checked with `git diff --check --no-index /dev/null <report>`;
  no whitespace diagnostics (exit 1 denotes the new-file content difference).
- Independent read-only checks confirmed all 25 artifact sizes/hashes, the
  source commitments, each case's cross-artifact consistency and changes to
  exactly the H04/H05/H18 matrix rows.
- No behavioral probe rerun, agent change, commit or push during finalization.
