# H30 observation design — 2026-10-04

The exact matrix hypothesis is: “The task prompt advertises graph tools whenever
graph/embedding files exist, independent of the agent's declared tools.” H30 is
NOT-REPRODUCED, high risk, locally possible without model/GPU/Docker, and currently
blocked by harness_source. Packaging is a separate readiness check for H28.

The acquired swegemma 0.2.7 source is available. build_agent_prompt checks
recognized graph and embedding filenames and requires a file in each directory
whose size is strictly greater than 100 bytes. It does not receive agent tool
declarations. These are source observations; actual request evidence is required
before deciding what the hypothesis establishes. Literal existence is broader
than this predicate, so threshold controls may prevent promotion of the exact
matrix wording even if declaration independence is reproduced.

## Fixed baseline and safeguards

The new graph admission profile pins
`81ac5b150c719cafc671b884dce7f525219e4ea0` and exactly five named probe/design/test
files. The dispatch and budget profiles retain their historical baselines,
allowlists and support-document behavior. No caller-supplied baseline or report/
matrix publication edit is admitted by the new profile.

Runtime imports remain behind exact CPython 3.12.14/version/source-wheel
preflight. The existing ParentGuard, wheel refusal, fixed synthetic command/copy
policy, protected data/agent roots, offline/proxy/telemetry controls and isolated
subprocess venvs are reused unchanged. Standard bundled ensurepip venv bootstrap
is admitted by the existing policy; no task dependency installation occurs.

Responses travel through a fixed in-process httpx.MockTransport. Actual
AsyncOpenAI/LiteLLM/ADK request construction and official run_agent_sandbox
execute; no network listener, connection, real model or GPU is used. The numeric
loopback URL labels the captured endpoint. ParentGuard.port remains None, so
socket connection attempts fail closed. The existing caller-specific urllib3
IPv6 import capability check remains an optional import-time bind. The inherited
guard's listener policy is unchanged; this probe creates no listener and marks
any recorded non-capability bind as invalid evidence.

## Six controls

Every case runs the same synthetic write_file → submit_patch → final sequence,
with a one-minute budget, eight counted tools and six model events allowed.
The usual successful 134-byte before/after diff supplies a healthy shared control.

| Case | Recognized synthetic data | Declared graph tools | Source prediction |
|---|---|---|---|
| ABSENT | Neither file | None | Hint block absent |
| GRAPH_ONLY | Graph >100 bytes only | None | Hint block absent |
| EMBEDDING_ONLY | Embedding >100 bytes only | None | Hint block absent |
| THRESHOLD | Both exactly 100 bytes | None | Hint block absent |
| H30 | Both 101 bytes | None | Hint block present; graph schemas absent |
| DECLARED_CONTROL | Both 101 bytes | All three | Hint block present; graph schemas present |

Files are stat-only availability fixtures, not operational graph/embedding data.
No graph handler is called. The cases record actual serialized request/response
bytes and JSON, first user prompt, advertised tool schemas, YAML declarations,
fixture paths/sizes/hashes, trace, counter/submission state, patches, exceptions,
guard evidence and per-case artifact inventories. Inventories have no nested
case directories and cannot capture a later child preflight seed.

Infrastructure validity and behavioral predictions remain separate. No terminal
prediction flag changes the matrix. The full literal existence claim and the
declaration-independence component receive separate evidence conclusions.

## Separate packaging readiness

At the initially clean baseline, the existing deterministic builder packaged
the unchanged ten-file official E0 snapshot twice. It used the committed official
manifest, explicit WriteGuard and the existing external-official-control
provenance path for four ignored adapter files. It did not restore/read the live
dataset sample. ZIP and manifest bytes matched across builds, with truthful clean
Git provenance; no --require-clean override was used.

A separate isolated import/compilation smoke may validate the exact unpacked ZIP
under the acquired interpreter without generation or adapter weight loading.
This is a narrow CPU import subset, not a dependency-complete Kaggle runtime.
Current pinned official notebook/materialization/output-selection instructions
are absent locally. The user forbids browsing in this pass; this gap remains an
external verification requirement and prevents an unsupported H28 GO.

Only after raw reconstruction may a curated report be written. No competitive
agent, LoRA, DEV/HOLDOUT, Kaggle upload, commit or push is authorized.
