# H27 no-LoRA local component — 2026-10-03

**LOCAL COMPONENT PASS. Full H27: NOT-REPRODUCED.**
Authority: **REPRODUCED-LOCAL**. Evidence class: **RUNTIME-REPRODUCED**, scoped
to adapter discovery, compilation and argv construction.

## Environment and evidence

Baseline commit: `2ffedaec97571c1e332babb541d60e544562e51a`.
The operator supplied the reproduced local execution outcomes below.

| Component | Exact version |
|---|---|
| Python | 3.12.14 |
| adk-submission | 0.2.12 |
| google-adk | 1.36.1 |
| google-genai | 2.11.0 |

The three compiler source wheels came from **wheelhouse v28**. The no-LoRA
fixture removed `adapters/` and all `adapter:` keys. Repository agent files were
not modified. The exercised APIs were `discover_adapters(...)`,
`compile_submission(...)` and `VllmServer(...).build_cmd()`.

The supplied evidence is the operator's outcome summary, not a raw transcript
or an exact shell invocation. `harness_cert/results/H27/` remains the reserved,
gitignored raw-results path; this report does not claim logs exist there.

## Reproduced local results

| Check | Observed result |
|---|---|
| Empty adapter discovery | `ADAPTER_COUNT = 0`; `ADAPTER_NAMES = []`; `EMPTY_ADAPTER_DISCOVERY_OK` |
| No-LoRA compilation | `NO_LORA_COMPILE_OK`; `AGENT_TYPE = LlmAgent`; `AGENT_NAME = swe_baseline_agent` |
| Empty manifest, `enable_lora=False` | `LORA_DISABLED_ARGV_OK`; all four LoRA flags below absent |
| Same empty manifest, default `enable_lora=True` | `DEFAULT_ENABLED_EMPTY_ARGV_OK`; all four LoRA flags below absent |

Neither argv variant contained:

- `--enable-lora`
- `--lora-modules`
- `--max-loras`
- `--max-lora-rank`

Final local marker: **H27_LOCAL_PASS**. The no-adapter configurations therefore
did not generate an empty `--lora-modules` argument: the flag itself was absent.

The exact source explains both cases. In the v28 `adk_submission` wheel,
`discovery.py:398–399` returns `AdapterManifest(adapters={})` when `adapters/`
is absent. `server.py:781–798` adds LoRA flags only when **both**
`config.enable_lora` is true and `modules_to_mount` is non-empty. A default-enabled
configuration with no adapters does not enter that LoRA block.

## Full H27 scope and remaining blocker

H27's full hypothesis includes actual server startup: a bundle without
`adapters/` or adapter keys starts the server and compiles cleanly.
Only discovery, compilation and command construction have been reproduced.
**Actual vLLM process startup, model loading and GPU runtime behavior remain
untested.** Constructing argv does not establish that the process accepts it,
loads the model or serves requests.

The `harness_source` blocker is removed for the local component. The remaining
H27 blocker is **real vLLM/model/GPU server-start certification**. Full H27
remains **NOT-REPRODUCED**; no other H-status is promoted.

This checkpoint does not identify the hidden scorer or establish successful
Kaggle bootstrap. **H23 remains HOST-UNKNOWN; the hidden scorer remains
SCORER_ONLY_UNKNOWN. Step 0 remains incomplete.** No Kaggle, model/GPU or vLLM
server run was performed as part of this documentation update.
