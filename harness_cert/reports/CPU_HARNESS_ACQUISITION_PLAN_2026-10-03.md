# CPU harness acquisition plan — 2026-10-03

Initial static inspection at HEAD `4e1658a2561d616a93a16c20d22da7fe201a685c` identified a **Python 3.12 Apple Silicon CPU installation candidate** for the unchanged compiler and scripted-mock harness. The compiler subset has now been successfully created and executed locally; H26 is reproduced. Full scripted-mock harness acquisition and the remaining CPU certification checks are pending.

Scoped acquisition: **compiler = three exact source wheels + 41 PyPI support packages; mock harness = all five source wheels + 82 PyPI support packages total**. These are an import-derived subset, not a dependency-complete installation of the official evaluation stack. The compiler subset is installed; the full harness subset remains proposed. Initial planning used ZIP/metadata inspection without installation or package execution. This documentation checkpoint records the operator's subsequent compiler execution evidence and closes H26; it changes no agent behavior and runs no Kaggle/model/GPU workload.

## Reproduced compiler checkpoint

The operator reports successful creation of the isolated **Python 3.12.14** environment, installation of **41 support packages**, and installation of the three exact v28 compiler source wheels: **adk-submission 0.2.12**, **google-adk 1.36.1**, and **google-genai 2.11.0**. Compiler import returned **COMPILER_IMPORT_OK**; the official baseline's `validate_directory` check returned **PASS**.

Exact compiler execution is now verified locally: a no-LoRA baseline copy with explicit tool/model registries returned **COMPILE_PASS** for the official parent include, producing **LlmAgent / swe_baseline_agent**. Traversal and absolute external escapes returned **COMPILE_REJECT / PathTraversalError**; the symlink escape returned **COMPILE_REJECT / SubmissionValidationError**. The separate YAML-loader checks and evidence scope are recorded in [H26_INCLUDE_PATHS_2026-10-03.md](H26_INCLUDE_PATHS_2026-10-03.md). This establishes the local compiler subset and H26, not full harness execution or hidden-scorer identity.

## Immutable source inventory

All five archives under `artifacts/harness_wheels/v28/` were read as ZIPs: `METADATA`, `WHEEL`, entry points and Python source/AST. All tags are `py3-none-any`; native support dependencies still need compatible wheels.

| Archive | Python requirement | SHA256 |
|---|---|---|
| `adk_submission-0.2.12-py3-none-any.whl` | >=3.11 | `077c438c426e625b9f722081694e1d32856e6f7e932ef625002fc4a11aabdc10` |
| `google_adk-1.36.1-py3-none-any.whl` | >=3.10 | `1a2f6868c509e3151fb0de3575a7d18b45c338be86f420924dad74e7193631a0` |
| `google_genai-2.11.0-py3-none-any.whl` | >=3.10 | `5bc8186100e1d34d691fbe0cba392b7e04e98d286ca952323a6672d054accf95` |
| `adk_eval_core-0.1.0-py3-none-any.whl` | >=3.11 | `194dd8f9aab154857bfa0db9d9ec382db856ee124529e072509634d3071d8643` |
| `swegemma-0.2.7-py3-none-any.whl` | >=3.12 | `27a2f60f8db46c8fef5defc16df722dac0402446c9a6252e7e6b4c280e843c81` |

Entry points: `swegemma = swegemma.cli:main`; `adk = google.adk.cli:main`. The other three have no `entry_points.txt`. Use library APIs for CPU probes; these broader CLIs are not the minimal entry paths.

## Dependency and import map

References below are archive member paths and line numbers. The import map is from the initial **static observations**; executed compiler/H26 results are recorded separately in the checkpoint above.

| Operation | Implementation and required path |
|---|---|
| Directory validation | `adk_submission/discovery.py:60`, exported through `adk_submission/__init__.py:48–59`. Normal imports eagerly load the compiler/ADK even for validation; there is no supported stdlib-only import bypass. |
| Compilation | `adk_submission/compiler.py:59`: `compile_submission(submission_dir, tool_registry, model_registry, ...)`. Calls directory validation, adapter discovery, YAML/schema loading and ADK agent construction. Requires organizer tool/model registries; constructing agent objects does not launch inference. |
| Compiler modules | `compiler` → `builders/{llm,workflows,sub_agents}`, `context`, `discovery`, `registry`, `schema`, `paths`, `yaml_loader`, `resolvers/{callbacks,generation,models,tools}`; ADK agents, `BaseCodeExecutor`, `AgentTool`, `SkillToolset`, GenAI types, Pydantic and PyYAML. |
| Easy-to-miss eager dependencies | ADK `auth/auth_schemes.py:22–26` imports FastAPI; `skills/_utils.py:27` imports Cloud Storage; `telemetry/tracing.py:40–63` imports OpenTelemetry API/semantic conventions; `utils/model_name_utils.py:22–23` imports packaging. GenAI adds its HTTP/auth CPU closure. |
| Compiler's optional LiteLLM path | Every LLM build calls `resolvers/generation.py:257` → `:139`, attempting `google.adk.models.lite_llm`; `ImportError` is tolerated at `:140–141`. Compiler-only probes can use a registered model string/CPU fake without LiteLLM or a network call. |
| Full harness imports | SWEGemma's eager package initializer loads evaluation/context/models/sandbox. `swegemma/models/registry.py:11` requires ADK LiteLLM; ADK `models/lite_llm.py:46–49` rejects missing LiteLLM. Eval-core's eager sandbox imports `docker` at `sandbox/containers.py:19`. Docker SDK is required; a Docker daemon is not required for the subprocess route. pandas, Rich and dotenv are also eager; graph helpers add cachetools/networkx/numpy. |
| Runtime/model serving only | `adk_submission/server.py:244` optionally imports torch in `build_env`; `:555–560` imports torch/PEFT/Transformers for adapter merging. `VllmServer.build_cmd():691` emits a launch module string; server startup launches vLLM externally. Do not start a server, call `build_env`, or merge adapters. GenAI `_local_tokenizer_loader.py:222` imports Transformers only inside its optional tokenizer loader. |

SWEGemma declares `torchvision`, `transformers>=4.40`, `safetensors`, `accelerate` and `tokenizers>=0.20` as base dependencies despite no direct imports of them in the inspected compiler/tool paths. Tokenizers **is** needed transitively by the selected LiteLLM; the other four are excluded. Google ADK declares 45 base requirements, many cloud/CLI/database services unused here. Normal resolution of the three compiler source wheels alone produced 116 packages; this would obscure the smaller import subset.

Do not install vLLM, torch, torchvision, Transformers, PEFT, accelerate, safetensors, CUDA/NVIDIA packages, Linux-only `cp312`/manylinux wheelhouse binaries, or ADK/provider/proxy/GenAI `local-tokenizer` extras. Omit unused ADK cloud/database services, MCP/CLI/Graphviz/exporter dependencies and OpenTelemetry SDK; mock paths use the API's no-op tracer/meter. This deliberately leaves source-wheel `Requires-Dist` entries unmet: dependency checking will report omissions. Neither a clean dependency check nor successful official-stack reproduction is claimed.

## Exact acquisition and platform feasibility

**Must come from the supplied v28 wheelhouse:** the five source archives above, byte-for-byte. Compiler-only acquisition uses the first three; the harness adds eval-core and SWEGemma. **May come from PyPI:** all support pins below. They are proposed local support versions, not evidence of hidden-scorer versions.

`litellm==1.83.14` is chosen within Google ADK 1.36.1's declared extensions range `>=1.83.7,<=1.83.14`, without installing that extra. Its base dependencies include exact pins for OpenAI, tokenizers, tiktoken, fastuuid, HTTP libraries and Pydantic, but no torch/Transformers/vLLM. This is a different, explicitly pinned local support choice from the canonical capture's pre-existing LiteLLM 1.85.7. [Primary LiteLLM metadata](https://pypi.org/pypi/litellm/1.83.14/json).

During initial planning, `uv 0.12.19` performed metadata-only resolution with `--python-version 3.12 --python-platform aarch64-apple-darwin --only-binary :all: --no-python-downloads`. The selected dependencies were constrained by all five source wheels' base version bounds. Both the 41-package compiler and 82-package harness support sets resolved successfully; neither contains a model engine. That planning pass excluded installation/build/import operations. Python 3.12 was not present then; it has since been acquired as Python 3.12.14 and the compiler subset successfully installed and exercised, as recorded above.

Primary PyPI file metadata independently confirms Apple Silicon CPython 3.12/compatible-ABI wheels for [fastuuid 0.14.0](https://pypi.org/pypi/fastuuid/0.14.0/json), [tokenizers 0.22.2](https://pypi.org/pypi/tokenizers/0.22.2/json), [tiktoken 0.12.0](https://pypi.org/pypi/tiktoken/0.12.0/json), [aiohttp 3.13.4](https://pypi.org/pypi/aiohttp/3.13.4/json), [numpy 2.5.3](https://pypi.org/pypi/numpy/2.5.3/json), [pandas 3.0.6](https://pypi.org/pypi/pandas/3.0.6/json) and [pydantic-core 2.41.5](https://pypi.org/pypi/pydantic-core/2.41.5/json). The checked native wheels fit this Mac's arm64/macOS 26.6.2 host; google-crc32c 1.9.0 requires macOS >=12. These metadata checks establish installability evidence; the compiler checkpoint separately records successful imports and execution. Full harness execution remains pending.

Compiler support pins, including transitive dependencies:

<!-- compiler-pins -->
```text
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
certifi==2026.7.22
cffi==2.1.1
charset-normalizer==3.5.2
cryptography==50.0.2
distro==1.9.0
fastapi==0.141.1
google-api-core==2.34.0
google-auth==2.59.1
google-cloud-core==2.8.0
google-cloud-storage==3.16.0
google-crc32c==1.9.0
google-resumable-media==2.11.0
googleapis-common-protos==1.75.5
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.20
importlib-metadata==8.5.0
opentelemetry-api==1.41.1
opentelemetry-semantic-conventions==0.62b1
packaging==26.3
proto-plus==1.29.0
protobuf==7.36.2
pyasn1==0.6.4
pyasn1-modules==0.4.2
pycparser==3.0
pydantic==2.12.5
pydantic-core==2.41.5
pyyaml==6.0.3
requests==2.34.2
sniffio==1.3.1
starlette==0.52.1
tenacity==9.1.4
typing-extensions==4.16.0
typing-inspection==0.4.4
urllib3==2.8.0
websockets==15.0.1
zipp==4.1.1
```

Harness additions; combine with compiler pins for the full 82-package support set:

<!-- harness-extra-pins -->
```text
aiohappyeyeballs==2.7.1
aiohttp==3.13.4
aiosignal==1.4.0
attrs==26.1.0
cachetools==7.2.0
click==8.1.8
docker==7.2.0
fastuuid==0.14.0
filelock==4.0.9
frozenlist==1.8.0
fsspec==2026.9.0
hf-xet==1.6.0
huggingface-hub==1.16.1
jinja2==3.1.6
jiter==0.17.0
jsonschema-specifications==2025.9.1
jsonschema==4.23.0
litellm==1.83.14
markdown-it-py==4.2.0
markupsafe==3.0.4
mdurl==0.1.2
multidict==6.9.1
networkx==3.7
numpy==2.5.3
openai==2.24.0
pandas==3.0.6
propcache==0.5.4
pygments==2.21.0
python-dateutil==2.9.0.post0
python-dotenv==1.2.2
referencing==0.37.0
regex==2026.9.29
rich==15.0.0
rpds-py==2026.6.3
shellingham==1.5.4
six==1.17.0
tiktoken==0.12.0
tokenizers==0.22.2
tqdm==4.70.1
typer==0.27.2
yarl==1.25.1
```

## Isolated-environment acquisition recipe

The compiler acquisition/import checkpoint is complete. The recipe below is retained for repeatability; its full harness commands remain proposed. Run from the repository root. `uv python list 3.12` lists native `cpython-3.12.14-macos-aarch64-none` as downloadable. Python 3.12.14 is a released security update; the binary comes from uv's managed distribution, not a Python.org macOS installer. [Python release announcement](https://blog.python.org/2026/08/python-31214-31116-31021/).

The commands extract the exact pins above, generate wheel-only hash locks, and keep the interpreter, environments and cache in a new temporary directory. Recheck the five source hashes against the inventory before installing them.

```sh
H23_CPU_TMP=$(mktemp -d /private/tmp/h23-cpu.XXXXXX)
export H23_CPU_TMP
export UV_CACHE_DIR="$H23_CPU_TMP/uv-cache"
export UV_PYTHON_INSTALL_DIR="$H23_CPU_TMP/python"
python3 - "$H23_CPU_TMP" <<'PYCODE'
import pathlib, sys
report = pathlib.Path('harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md').read_text()
def pins(name):
    marker = '<!-- ' + name + ' -->\n```text\n'
    return report.split(marker, 1)[1].split('\n```', 1)[0] + '\n'
root = pathlib.Path(sys.argv[1])
compiler = pins('compiler-pins')
(root / 'compiler.in').write_text(compiler)
(root / 'harness.in').write_text(compiler + pins('harness-extra-pins'))
PYCODE
uv python install 3.12.14 --no-bin
uv venv "$H23_CPU_TMP/compiler" --python 3.12.14 --managed-python --no-python-downloads
uv pip compile "$H23_CPU_TMP/compiler.in" --python-version 3.12.14 \
  --python-platform aarch64-apple-darwin --only-binary :all: \
  --no-python-downloads --default-index https://pypi.org/simple \
  --generate-hashes --output-file "$H23_CPU_TMP/compiler.lock"
uv pip install --python "$H23_CPU_TMP/compiler/bin/python" \
  --only-binary :all: --require-hashes -r "$H23_CPU_TMP/compiler.lock"
shasum -a 256 artifacts/harness_wheels/v28/*.whl
uv pip install --python "$H23_CPU_TMP/compiler/bin/python" --no-deps --no-index \
  artifacts/harness_wheels/v28/adk_submission-0.2.12-py3-none-any.whl \
  artifacts/harness_wheels/v28/google_adk-1.36.1-py3-none-any.whl \
  artifacts/harness_wheels/v28/google_genai-2.11.0-py3-none-any.whl
"$H23_CPU_TMP/compiler/bin/python" -c 'from adk_submission import validate_directory, compile_submission, ModelRegistry; print("COMPILER_IMPORT_OK")'

uv venv "$H23_CPU_TMP/harness" --python 3.12.14 --managed-python --no-python-downloads
uv pip compile "$H23_CPU_TMP/harness.in" --python-version 3.12.14 \
  --python-platform aarch64-apple-darwin --only-binary :all: \
  --no-python-downloads --default-index https://pypi.org/simple \
  --generate-hashes --output-file "$H23_CPU_TMP/harness.lock"
uv pip install --python "$H23_CPU_TMP/harness/bin/python" \
  --only-binary :all: --require-hashes -r "$H23_CPU_TMP/harness.lock"
uv pip install --python "$H23_CPU_TMP/harness/bin/python" --no-deps --no-index \
  artifacts/harness_wheels/v28/adk_submission-0.2.12-py3-none-any.whl \
  artifacts/harness_wheels/v28/google_adk-1.36.1-py3-none-any.whl \
  artifacts/harness_wheels/v28/google_genai-2.11.0-py3-none-any.whl \
  artifacts/harness_wheels/v28/adk_eval_core-0.1.0-py3-none-any.whl \
  artifacts/harness_wheels/v28/swegemma-0.2.7-py3-none-any.whl
export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_MODE=PRODUCTION
export HF_HUB_OFFLINE=1
"$H23_CPU_TMP/harness/bin/python" -c 'from swegemma.harness.agent_runner import build_agent_prompt; from swegemma.sandbox import SubprocessManager; print("HARNESS_IMPORT_OK")'
```

LiteLLM import normally attempts a remote cost-map fetch; the environment flags select bundled data and disable its development logger. Its eager tokenizer initialization must use the intact bundled tiktoken cache. Use a loopback OpenAI-compatible scripted server, no remote model endpoint/credentials, and `EvalConfig(sandbox='subprocess', wheels_dir=None, ...)` with synthetic repositories/tasks. Supply model/tool registries explicitly. Git, `/bin/bash` and a native Python venv are required. Do not point the sandbox wheel installer at the all-wheel v28 collection: that would reintroduce incompatible/GPU dependencies. Mock-runner setup may additionally need a small curated **fixture-test** wheel directory; this is separate from harness-host requirements.

## CPU certification scope and remaining blockers

| Check | Current evidence and remaining scope |
|---|---|
| Submission validate/compile; H26 | **REPRODUCED-LOCAL / H26 PASS.** Baseline structural validation passed. Exact YAML loading and compilation accepted the official in-root parent include and rejected traversal, absolute external and symlink escapes; see the H26 report for exception differences. `!include` resolution is distinct from the separate lexical `config_path` policy. |
| H27 compile-side no-LoRA | `discover_adapters` returns an empty manifest when `adapters/` is absent (`discovery.py:398–399`); compile a fixture without adapter keys. Inspect `VllmServer.build_cmd` only: LoRA argv requires enabled LoRA and nonempty modules (`server.py:781`). Server-start/model compatibility remains excluded. |
| H04/H05/H18 | Exact ADK + LiteLLM + SWEGemma tools, scripted declared/undeclared/nonexistent calls; capture exception, subsequent turns and patch fallback. ADK missing-tool lookup raises `ValueError` (`flows/llm_flows/functions.py:988–1005`), with callback recovery paths; fatal runner outcomes are untested. |
| H06–H10/H17 | Script int/string line ranges, quotes/backslashes/docstrings/unicode; compare fixture file bytes and tool content in the **next HTTP request**. Bound workspace wrappers parse raw JSON into dictionaries (`tools/base.py:34–44`, `tools/workspace.py:297–363`), so Python return values alone do not establish message fidelity. Gemma/vLLM parser/template rendering remains unknown. |
| H13/H14/H29 | Synthetic Git repository, edited source, timeout and exhausted budget; inspect fallback/explicit patches and mock history. `submit_patch`/`get_status` are free at tool level (`tools/execution.py:76,106`), but runner budget exhaustion can stop another turn (`agent_runner.py:647–660`). Full patch verification needs a runnable synthetic test fixture, not locked gold. |
| H30 | Synthetic graph/embedding files >100 bytes, bundle without graph tools; compare first request prompt and advertised tool schemas. `agent_runner.py:144–181` checks file existence/size, not declared tools. No embeddings/model computation is needed for advertisement-only checks. |

**Next Step-0 blocker:** acquire and verify the remaining CPU harness subset, then build the loopback scripted mock and synthetic subprocess/Git fixtures to execute the outstanding tool, serialization/edit, patch-lifecycle/budget and graph-advertisement checks. Complete H27's remaining compile-side argv check separately. The compiler environment and H26 no longer block this phase. Record any additional dependency needed by an exercised path and update the exact lock before certification; do not silently broaden into model-serving dependencies.

H26 is now **PASS** based on reproduced local execution; no other H-status is promoted by this checkpoint. H23 remains **HOST-UNKNOWN** and the hidden scorer remains **SCORER_ONLY_UNKNOWN**. Exact local compiler execution cannot establish hidden-scorer identity, successful Kaggle bootstrap, full official-stack installation, or Gemma/vLLM behavior. Step 0 remains incomplete.
