# Google – The Gemma 4 Developer Agent Competition
## MASTER HANDOFF / SOURCE-OF-TRUTH NOTES
**Project goal:** compete seriously for the top of the Kaggle leaderboard while keeping the work reproducible, rule-compliant, and evidence-driven.

**Snapshot date:** 2026-10-01, approximately 22:20 EDT.

**Purpose of this file:** this is a handoff document for a new ChatGPT/Claude/Codex session. It is intentionally redundant and detailed because the original chat reached its maximum length. A new assistant should read this file before proposing architecture changes, prompts, GPU spending, LoRA training, or Kaggle submissions.
---

## 0. Read this first: current state and non-negotiable working rules

The user is currently downloading the full Kaggle competition dataset to a MacBook. At the last update the download was **14.4 GB of 22.42 GB**. Do not assume the local dataset is complete until the user confirms the download finished and we verify file counts/sizes/hashes where practical.

The user's machine is a **MacBook Pro M1 Pro, 16 GB unified memory, 1 TB SSD**. It is suitable for almost all repository/data/harness engineering, but it is **not a faithful machine for sustained exact inference with the required 31B competition model**. The official scoring runtime uses 4× NVIDIA L4 GPUs. Do not tell the user to buy a server before measurements justify it.

The user also has **Google Colab Pro**. A screenshot on 2026-10-01 showed about **92.92 compute units available**, a CPU-backed session with about **12.7 GB system RAM** and **225.8 GB disk**. No GPU was visible in that screenshot. When exact model experiments begin, first switch runtime type and inspect the actual assigned accelerator; do not assume an A100/L4 is available.

### Working principles

1. **Competition rules and the current official harness are the highest authority.** Public notebooks and forum posts are evidence, not truth.
2. **Reproduce before optimizing.** The current forum contains multiple reports of scorer/harness regressions. First certify the environment.
3. **One controlled change per experiment/submission whenever possible.** Public-LB noise is large relative to a one-task improvement.
4. **Never exploit verifier bugs, modify hidden/public tests to game scoring, or use private sharing outside the team.** Fix source code as intended.
5. **Do not inspect or manually tune against reference `patch` / `test_patch` on the locked local holdout.** They are available in public training data for research, but using them during prompt design would leak the answer into our validation process.
6. **Do not start with LoRA, RL, or a complicated multi-agent tree.** Establish a strong reproducible non-LoRA baseline first.
7. **No expensive GPU runs until the experiment is well specified and passes CPU/static certification.**
8. **Record exact artifact hashes and runtime versions.** “Same prompt” is not enough; wheelhouse and scorer changes can alter results.
---

## 1. Source hierarchy and evidence labels

Use the following authority order whenever two sources disagree:

1. **OFFICIAL-RULE:** Kaggle competition rules and competition-specific rules.
2. **OFFICIAL-HARNESS:** the current downloaded `HARNESS_README.md` and code/wheels from the current competition dataset.
3. **OFFICIAL-NOTEBOOK:** the pinned “Getting Started – Gemma 4 Developer Agent” notebook, at the version/date it was executed.
4. **REPRODUCED-LOCAL:** behavior we ourselves reproduce with the exact current wheels/harness.
5. **OWN-KAGGLE-RUN:** behavior from our own Kaggle submission, with exact ZIP SHA, notebook version and timestamp.
6. **COMMUNITY-EVIDENCE:** participant forum posts and public notebooks. Useful, sometimes excellent, but version-sensitive and not automatically authoritative.
7. **INFERENCE:** our architectural hypothesis or recommendation. Must be tested.

### Important rule for future assistants

If a community notebook says one thing and the current harness or our reproduction says another, prefer the current harness/reproduction. Preserve the old claim in the issue ledger as historical evidence; do not silently rewrite history.
### 1.1 Local source snapshot

These are the exact uploaded files used to build this handoff:

| File | Size | SHA-256 |
|---|---:|---|
| `HARNESS_README.md` | 48.2 KiB | `3d6e57a13234cb4e783ba24eaab486459af76e0923ea4c6607cd41cc8961bbbb` |
| `Pasted markdown(2).md` | 34.6 KiB | `5e04bc6a0a9bd7c9922a0bf0d0c9798f36f2c2281f02ea9e2cdcb70e7ffa79d9` |
| `getting-started-gemma-4-developer-agent.ipynb` | 85.6 KiB | `0042d8427b7f179f987272b013a74d98ae5fadf1eea8859705214e25f1286346` |
| `gemma-4-dual-agent-reproducible-baseline.ipynb` | 21.3 KiB | `f6515dac54ea3c4c71ea08189898c13c0660ee539cd5e944ab38b4e7a54cebe9` |
| `gemma-4-superagent.ipynb` | 887.8 KiB | `2a34901ddad31e353fe6aaa0690edac89c82074e5d542d121a57b11b20568994` |
| `black-cat-swe-agent-pack-instinct.ipynb` | 88.2 KiB | `f63624590c0be3fe125084d6304e663919409d9e90ed6f35b2f53af6a85f474c` |
| `gemma-4-walkthrough-first-submission.ipynb` | 81.8 KiB | `a913dc4c936a71148aa81063f91eb8cde8019332a6d14c717fa13800d1336858` |
| `pathfinder-gemma-4-agent-eda-baseline.ipynb` | 218.8 KiB | `80fc054dddaf6e413839256a26c85d28e68eb4c09cba5a030242243ffd6d75bc` |
| `gemma-and-the-shape-of-doubt.ipynb` | 2.49 MiB | `091f71a40e28570b1275c216afa437fbed0974038c1c89beca48dd3c84a8b0f3` |
| `gemma-4-budget-aware-coding-agent.ipynb` | 15.9 KiB | `ba4f686db1a3d850fa298f4a3f85d45fdaef3281c258a980d2a3f6a25a809f38` |
| `gemma-develop-first-try.ipynb` | 588.8 KiB | `50b48aef710053799e620edfa6fba30a25460bdb44962f097044bc064d6f05da` |
| `gemma-4-the-complete-guide-expanded.ipynb` | 22.1 KiB | `305c644732fc2894226cdd3de20f067cbe70a50185b5fbf907223b771d1b238a` |
| `gemma-4-swe-agent-complete-eda-adk-starter-kit.ipynb` | 392.6 KiB | `b1ad4e20cdff43d1b4ca054dad3b8f6c06a4c1af71d9edd711cd5171eb8da6a5` |

This hash table is important: if a later Kaggle notebook or wheelhouse changes, we can distinguish a real environment change from a memory error.
---

## 2. Competition facts: what is officially known

### 2.1 Identity and objective

- Competition: **Google – The Gemma 4 Developer Agent Competition**.
- Sponsor: **Google LLC / Google DeepMind/Gemma team** as shown on the Kaggle competition page.
- Competition task: post-train/configure **Gemma 4** as an autonomous software engineering agent that can navigate repository snapshots, make code changes, and submit patches for real software issues.
- The scored object is not a CSV prediction model in the usual Kaggle sense. We submit an **agent bundle (`submission.zip`)** containing declarative YAML, prompts, optional skills, optional LoRA adapters, and optional evaluation configuration. Kaggle compiles and runs that agent against hidden software-engineering tasks.

### 2.2 Main prizes and optional paper track

Main competition rules specify **$65,000** total:

- 1st: **$37,000**
- 2nd: **$18,000**
- 3rd: **$10,000**

The Kaggle overview also showed an **optional paper track totaling $35,000**, making $100,000 across both tracks on that page. Treat the main competition and paper track as separate award mechanisms; the competition-specific rules file itself names the $65,000 main prize pool.

Paper-track themes shown by Kaggle included tuning/optimization, code comprehension, tasks/benchmarks, and graph reasoning. This aligns naturally with our planned ablations, but the main leaderboard remains the primary objective.

### 2.3 Timeline (shown on Kaggle as of this snapshot)

- Start: **2026-09-23**
- Optional research paper deadline: **2026-11-12**
- Entry deadline: **2026-11-25**
- Team merger deadline: **2026-11-25**
- Final submission deadline: **2026-12-02**
- Deadlines shown as **11:59 PM UTC** unless otherwise noted.

### 2.4 Submission/team limits

From the official rules:

- Maximum team size: **5**.
- Maximum submissions: **1 submission per day**.
- Up to **2 final submissions** may be selected for final judging.
- One Kaggle account per participant; multiple accounts are prohibited.
- Private sharing of competition code/data outside the official team is prohibited. Public competition code sharing is allowed only under the competition's public-sharing rules.

### 2.5 Licensing and winner obligations

- Winner license type: **Open Source / Apache 2.0** is stated in the competition-specific rules.
- Competition data access/use is listed as **Competition Use and Commercial Apache 2.0**.
- A winner may be required to provide complete reproducible code and documentation, including training/inference code, computational environment and methodology.
- External data/models/tools are allowed if they satisfy the host's accessibility/reasonableness requirements. The rules explicitly frame excessive cost/access restrictions as a concern.
- **No purchase is necessary to enter or win.**

### 2.6 Public vs private leaderboard

The leaderboard page states the public leaderboard uses approximately **50% of the test data** and the final/private leaderboard uses the other portion. Final rank is determined by the private leaderboard subject to rule compliance.

At the screenshot time on 2026-10-01, the visible top public score was **0.17**, with several teams at **0.15**. This is a snapshot, not a target guarantee and not evidence of final standing.

If the public split is around 60 tasks, one solved task is roughly 0.0167, so 0.15 vs 0.17 can plausibly differ by only one task. The exact denominator/rounding is not confirmed here, so use this only as an intuition for score granularity.
---

## 3. Dataset anatomy

### 3.1 Official dataset footprint

The Kaggle Data page showed:

- **22.42 GB**
- **524 files**
- major directories/files: `docker/`, `embeddings/`, `graphs/`, `sample_submission/`, `sandbox/`, `snapshots/`, `wheels/`, `HARNESS_README.md`, `tasks.jsonl`.

### 3.2 `tasks.jsonl`

The public dataset contains **129 development/training tasks**. A task record includes, at minimum:

- `instance_id`
- `repo`
- `base_commit`
- `problem_statement`
- `hints_text`
- `patch` — reference solution patch in the public development data
- `test_patch` — verifier tests for local development/evaluation
- `created_at`

The public-facing dataset exposes training/development tasks. Kaggle scoring uses a hidden test set under a similar pipeline.

### 3.3 Public repository composition observed in the supplied walkthrough notebook

One public notebook counted:

- FastAPI: **67** tasks
- Rich: **48**
- Requests: **13**
- HTTPX: **1**
- Total: **129**

The same notebook reported 127 distinct base commits, no non-empty `hints_text` values in that public snapshot, mean problem text length about 804 characters, median 418, max 10,095. Reference patch and test-patch file counts were typically small but had long tails.

Do **not** assume hidden scoring uses only these four repositories. A forum question explicitly asks whether hidden tasks use the same repos, and the dataset description says the hidden set is curated under a similar pipeline and mentions private repositories. Until the host states otherwise, design a **general Python SWE agent**, not a FastAPI/Rich specialist.

### 3.4 Snapshots

Each task has a compressed Git working tree snapshot. The official harness reconstructs repository history only up to `base_commit`, with future commits removed. This prevents trivial retrieval of the answer from future Git history.

### 3.5 Graphs

The dataset provides serialized NetworkX-style AST/call/dependency graphs. The official Getting Started notebook demonstrated a FastAPI task graph with **4,263 nodes and 2,430 edges**.

The graph tools should be treated as structural/navigation aids. Public notebook observations about exact edge-type distributions or missing async nodes are snapshot-specific; verify on the completed dataset rather than assuming they generalize.

### 3.6 Embeddings

The embedding archives store vectors keyed by code graph symbols. Public notebook inspection describes **256-dimensional** vectors. The important operational fact from the harness is that `search_similar_code()` does **not** have a live text embedding model in the offline task sandbox. The query is resolved against stored symbol keys/names; therefore an existing function/class/module symbol is a much safer query than free-form English.

### 3.7 Wheels / offline environment

The scorer and local harness are designed for an air-gapped sandbox. The dataset includes offline wheels and setup infrastructure. Agents should not waste time trying `pip install` from the internet.
---

## 4. Official evaluation stack: exact mental model

The current `HARNESS_README.md` describes three cooperating packages:

| Package | Role |
|---|---|
| `adk-submission` | Declarative agent compiler; validates YAML; compiles agent/sub-agent trees to Google ADK; manages vLLM/Transformers serving; deliberately avoids arbitrary competitor Python entrypoints/imports. |
| `adk-eval-core` | Task/result models, sandbox backends, robust file editing, budgets, retry/display plugins, trajectory tracing. |
| `swegemma` | SWE benchmark harness/scoring engine; task prompt construction; 9 bound tools; sandbox lifecycle; patch extraction; hermetic pytest verification. |

### 4.1 High-level pipeline

```text
submission.zip / agent directory
        |
        v
validate declarative schema + single-model rule
        |
        v
start local vLLM server (official scorer: 4 x L4)
        |
        v
compile Google ADK agent tree
        |
        v
CONTAINER A: agent works in /workspace
  - inspect repository
  - call tools
  - edit source
  - run targeted tests
  - submit patch
        |
        v
extract unified git diff
        |
        v
CONTAINER B: clean verification sandbox
  - recreate baseline
  - apply agent patch
  - reset protected tests/config
  - apply hidden task.test_patch
  - run hermetic pytest + JUnit
        |
        v
resolved = True / False
        |
        v
resolution rate
```

This two-container separation is central. The agent's interactive environment is not the final verifier. A patch must survive a fresh application and hidden tests.
### 4.2 Declarative-only submission contract

Competitors do **not** submit an arbitrary `agent.py` entrypoint. The root and subordinate agents are described in YAML. Python is permitted for sandboxed ADK skill scripts, but it is not a free replacement for the competition's agent compiler/orchestration layer.

Valid root config names: exactly one of:

- `agent.yaml`
- `agent.yml`
- `root_agent.yaml`
- `root_agent.yml`

Typical layout:

```text
submission/
├── agent.yaml
├── eval_config.yaml              # optional
├── configs/
│   └── sampling.yaml
├── prompts/
│   ├── system.md
│   └── analyzer.md
├── sub_agents/
│   └── code_analyzer.yaml
├── adapters/                     # optional LoRA .safetensors
└── skills/                       # optional SKILL.md + scripts/resources
```

Supported high-level agent classes:

- `LlmAgent`
- `SequentialAgent`
- `ParallelAgent`
- `LoopAgent`

A submission can therefore be multi-agent, but the building blocks and tools are host-controlled.

### 4.3 Archive/structural limits

Current README states a primary total unpacked submission-size cap of **< 3 GiB (3,221,225,472 bytes)** including adapters. Safety ceilings are generous: up to 10,000 files, 1,000 YAMLs, 500 agents, nesting depth 50, 1,000 skills and loop iterations up to 500. Allowed file types are tightly restricted to formats needed for YAML/prompts/skills/adapters.

### 4.4 `!include`

The compiler supports `!include` for markdown/text and YAML. The official README says absolute paths, NULs, path traversal (`..`) and escaping symlinks are blocked. However, the public dual-agent bundle uses `../prompts/...` inside `sub_agents/code_analyzer.yaml` and was packaged as a scored public baseline. This is a **documented-vs-observed discrepancy** that must be tested with the current `adk-submission` version before copying it blindly.
---

## 5. Model and scoring hardware

### 5.1 Required competition base model

For scored competition submissions the allowed base model is:

`gemma-4-31b-it-qat-w4a16-ct`

All LLM agents/sub-agents in one submission must share the same base model. Other Gemma aliases are registered for local CLI experimentation but are not the scored competition model under the current rules/harness.

### 5.2 Official scoring hardware from `HARNESS_README.md`

- **4 × NVIDIA L4**
- 24 GB VRAM each, 96 GB physical VRAM total
- `tensor_parallel_size = 4`
- `gpu_memory_utilization = 0.80`
- about 76.8 GB target usable VRAM under that setting
- `max_model_len = 32768`
- `enable_auto_tool_choice = True`
- tool-call parser: `gemma4`
- reasoning parser: `gemma4`
- default chat-template kwargs include thinking enabled
- LoRA enabled, `max_loras = 8`, `max_lora_rank = 128`

The README estimates the W4A16 competition model weight footprint around **16–18 GB total**, sharded across the four GPUs. This runtime footprint estimate is different from the on-disk model-file size seen on model hosting sites.

### 5.3 Consequence for the user's M1 Pro 16 GB

The Mac has only 16 GB unified memory shared by macOS and all applications. Even if a highly compressed representation can technically be loaded with swap/aggressive quantization, it would not be a stable or faithful reproduction of the official 32K-context vLLM agent runtime. Therefore:

- use the Mac for dataset/harness/repository analysis, prompts/YAML, static validation, graph/embedding exploration, CPU/subprocess tests and result analysis;
- use a sufficiently large NVIDIA GPU environment for exact 31B inference;
- do not spend money until a specific experiment has passed static/local gates.

### 5.4 LoRA capability

The scorer supports different LoRA adapters on different agents while keeping the same base model. README sizing guidance for a 31B model is approximately:

- rank 16: 110–220 MB/adapter
- rank 32: 220–450 MB
- rank 64: 450–900 MB
- rank 128: 0.9–1.8 GB

Maximum LoRA rank is 128; total submission must still fit under 3 GiB.

**Current strategy:** LoRA is a later phase, not Step 0. Community reports describe LoRA-specific memory/KV-cache problems and historical adapter bugs. We must first prove that an adapter changes model output and that its runtime is stable in the current scorer-equivalent stack.
---

## 6. Exact two-phase lifecycle

### 6.1 Container A: agent execution

The harness roughly performs:

1. Prepare offline wheels.
2. Extract the task repository snapshot at `base_commit`.
3. Configure Git excludes for generated artifacts.
4. Editable install of the repository with offline/no-dependency flags.
5. Install/copy cached test dependencies.
6. Write standardized hermetic `pytest.ini` / `conftest.py` support.
7. Create a `baseline` Git commit.
8. Start the agent-session timer.
9. Send the structured task prompt to the compiled agent.
10. Run the multi-turn tool loop until `submit_patch`, timeout, budget exhaustion or nudge termination.
11. Extract the patch from the working tree.
12. Destroy/clean the agent sandbox.

Docker backend parameters documented by the harness:

- network: none / air-gapped
- memory: 4 GiB
- CPU: 2 vCPUs
- workspace: `/workspace`
- default single shell command timeout: 300 seconds

A subprocess backend exists for environments without a Docker daemon.

### 6.2 Container B: verification

The verifier then creates a **fresh** repository environment and:

1. recreates the baseline;
2. applies the agent patch using multiple resilient `git apply` / GNU `patch` fallbacks;
3. resets protected test/config files to baseline;
4. applies the task's verifier `test_patch`;
5. runs hermetic pytest with JUnit XML;
6. declares the task resolved only when the required tests explicitly pass.

### 6.3 Resolution criterion

A task counts as resolved only if all relevant conditions are satisfied, including:

- pytest exit code 0;
- a valid JUnit report exists;
- at least one test passed;
- zero failures and errors;
- every required test node extracted from the task's verification specification explicitly passed and was not skipped.

Overall metric:

`resolution_rate = resolved_tasks / total_evaluation_tasks`

This is why a nearly-correct patch still earns 0 for that task.
---

## 7. Harness-to-agent prompt and control loop

### 7.1 Session state

The harness injects `problem_description` and, when present, `hints` into ADK session state. Agent instructions can reference these placeholders.

### 7.2 Initial task message

The root agent receives a structured user message containing, depending on task/config:

- repository/task header;
- complete problem statement;
- hints if any;
- active budget values;
- execution-environment rules;
- standard instructions to work in `/workspace`, verify and submit;
- a Code Intelligence Tools section when graph/embedding data is available;
- an initial workspace listing.

Important fixed caps called out in that message:

- command output: about **5,000 chars**
- `read_file`: at most **150 lines** and **10,000 chars** per call
- sandbox is offline; dependencies are already staged

### 7.3 Continuation nudges

If an ADK turn ends without `submit_patch`, the harness can send continuation prompts. Special messages are used for a truncated `<|tool_call|>` and max-token conditions. There are at most a small number of consecutive nudges (README says max 3).

### 7.4 Automatic patch fallback

If the agent made working-tree changes but failed to call `submit_patch` before timeout/budget termination, the harness can still run `git add -N .` + `git diff` and record a nonempty patch. This is a safety net, **not** the intended workflow. Explicitly verify and call `submit_patch` whenever possible.
---

## 8. The nine built-in tools: capabilities, cost and strategy
| Tool | Counts toward ordinary tool-call budget? | Core behavior | Strategic note |
|---|---:|---|---|
| `run_command(command)` | Yes | Runs `/bin/bash -c` under `/workspace`; output capped; command timeout applies. | Use narrow grep/sed/pytest commands; redirect large diagnostics to `/tmp`. |
| `submit_patch()` | **No** | Stages intent-to-add and captures binary git diff; marks patch submitted. | Always call last after verification/diff inspection. |
| `get_status()` | **No** | Returns live calls/time/turns/patch status. | Use regularly because it is free. |
| `read_file(path, start_line?, end_line?)` | Yes | Reads workspace file with 150-line/10k-char cap. | Use tight ranges; current forum reports a possible line-range typing regression that needs certification. |
| `edit_file(path, old_string, new_string, allow_multiple=False)` | Yes | 3-tier exact → whitespace-flexible → regex-like replacement. | Prefer small unique anchors; current community reports double-JSON escaping can cause `old_string not found`. |
| `write_file(path, content)` | Yes | Creates/overwrites under `/workspace`. | Avoid for scratch; `/workspace` untracked files enter patch. Use `/tmp` through `run_command` for repro scripts. |
| `get_code_neighbors(node, edge_type?, max_neighbors=50)` | Yes | Graph callers/callees/related symbol lookup. | Call only after identifying a real symbol; edge-type behavior must be verified per snapshot. |
| `search_similar_code(query, k=10)` | Yes | Symbol-key resolution + cosine search in precomputed vectors. | Not a free-form natural-language embedding search. Can return large code bodies; use carefully. |
| `get_code_subgraph(nodes)` | Yes | Induced subgraph for selected symbols. | Useful when a few confirmed symbols need structural context. |


When at least 20 ordinary calls have been used and 10 or fewer remain, the harness adds a budget warning to tool responses.

### Tool-call safety policy for our agent

- Prefer `grep -n`, `rg`, `find`, `sed -n`, `git diff --stat`, `pytest -q -x` and bounded output.
- Never dump an entire large file or repository tree.
- Do not invoke graph tools from free-form issue sentences. Extract symbols first.
- Keep `edit_file` payloads short enough that the tool-call JSON cannot be truncated.
- If `edit_file` fails, re-read the exact small region. Do not repeat the exact failing call indefinitely.
- Because current ADK may treat an undeclared tool call as fatal, do not casually remove tools from a toolset until Harness Certification tests missing-tool behavior.
---

## 9. Budgets, context and runtime

### 9.1 Per-task defaults in current README

`eval_config.yaml` may contain:

```yaml
evaluation:
  timeout_seconds: 300
  max_tool_calls: 10
  max_time_minutes: 1
  max_turns: 50
```

Current README/inference defaults when not overridden are approximately:

- agent wall time: **60 min**
- ordinary tool calls: **100**
- turns: **500**
- one shell command: **300 s**
- command/diff output: **5,000 chars**
- file read: **150 lines / 10,000 chars**

The official sample itself intentionally uses much tighter demo limits (1 minute / 10 calls / 50 turns), which are not a serious winning configuration by themselves.

Container/setup time is documented as excluded from the per-task **agent** timer. The competition page separately states a global runtime budget for all tasks (12 hours). Exact hidden-task concurrency and unfinished-task handling have been questioned on the forum and must be checked against current host behavior before choosing aggressive per-task caps.

### 9.2 32K context ceiling

The scorer vLLM maximum model length is **32,768 tokens** including prompt/history/reasoning/output reservation. Context overflow is therefore a real failure class.

### 9.3 Current README compaction/cache settings

- compaction interval: 5 events (current HARNESS README)
- overlap: 2
- token threshold: 14,336
- event retention: 5
- context cache minimum: 2,048 tokens
- cache TTL: 1,800 s

The official Getting Started notebook explicitly instantiated `compaction_interval=15`, illustrating that notebook examples and scorer defaults can differ by version/config. Never assume one historical notebook exactly matches today's scorer.

### 9.4 Retry behavior

The harness includes retries for transient model/proxy failures (e.g. selected HTTP 429/5xx/timeouts), with exponential backoff. This does not necessarily recover an ADK logic exception such as an undeclared tool call.
---

## 10. Patch hygiene and verification traps

### 10.1 `git add -N .` means scratch files matter

Patch extraction registers newly created files with intent-to-add. Therefore a scratch file left under `/workspace` can become part of the agent patch. Put repro scripts under `/tmp`, or delete any workspace scratch artifact before submission.

### 10.2 Do not edit tests or runner config

The verifier is intended to reset tests/configuration before hidden tests are applied. Regardless of any current verifier bug report, our agent should never rely on modifications to:

- `tests/`, `test/`, `testing/`
- `test_*.py`, `*_test.py`
- `conftest.py`
- `pytest.ini`, `.pytest.ini`
- `pyproject.toml`, `tox.ini`, `setup.cfg`
- `sitecustomize.py`, `usercustomize.py`, `_swegemma_stubs.py`, `*.pth`

Fix application/library code unless the task explicitly and legitimately requires a non-test configuration change and the verifier permits it. Our system prompt should strongly discourage test/config edits because they can invalidate an otherwise correct patch.

### 10.3 Patch application is resilient but not magical

Verifier application attempts several strategies (`git apply`, three-way/whitespace/recount variants, path-normalized/p0 fallbacks, GNU `patch`). A malformed diff can still fail immediately.

### 10.4 Minimum verification sequence we want the agent to learn

```text
make a small edit
    -> python -m py_compile target.py   (where applicable)
    -> minimal repro in /tmp
    -> nearest targeted tests, bounded output/time
    -> git status
    -> git diff / git diff --check
    -> ensure no scratch/test/config pollution
    -> submit_patch()
```
---

## 11. Official Getting Started notebook audit

The pinned Getting Started notebook is high-value because it demonstrates the intended current package flow, but it is still a notebook snapshot rather than an immutable scorer specification.

Observed in the supplied copy:

- loads all **129** public tasks;
- copies `sample_submission/` to writable working storage;
- overwrites `sampling.yaml` with:

```yaml
temperature: 0.2
top_p: 0.95
max_output_tokens: 16384
thinking_config:
  thinking_budget: 4096
  include_thoughts: true
```

- validates the single declared model;
- discovers LoRA adapters;
- starts vLLM at the competition model path with `max_model_len=32768`, Gemma4 tool/reasoning parsers, auto tool choice and LoRA support;
- selects TP=4 when four GPUs are available;
- its notebook vLLM config uses `gpu_memory_utilization=0.90`, while the current HARNESS README describes 0.80 for the scorer — another reminder not to equate notebook example and production scorer;
- demonstrates a FastAPI graph with 4,263 nodes / 2,430 edges;
- evaluates the first two tasks through the subprocess backend;
- reads the sample's very tight demo `eval_config` (1 min / 10 calls / 50 turns);
- the shown tasks time out/fail under that tiny budget, illustrating that the starter is primarily a mechanics example, not a leaderboard-optimized agent.

The notebook also instantiates compaction with interval 15, whereas current README says 5 for scoring defaults. Track version/config explicitly.
---

## 12. Public notebook audit: what each contributes and what not to copy blindly

Public notebooks are valuable because they expose complete bundles, traces and self-reported leaderboard outcomes. They are **not** guaranteed to match the current scorer version, and same-ZIP public scores can vary by several hundredths.

### 12.1 `gemma-4-dual-agent-reproducible-baseline.ipynb`

Core design:

- root coder + read-only `code_analyzer` as an `AgentTool`;
- root tools: shell/read/edit/write/status/submit + analyzer;
- analyzer tools: shell/read + three graph tools;
- shared sampling: temperature 0.2, top_p .95, top_k 40, max output 8192, thinking budget 4096, `include_thoughts:false`;
- no `eval_config.yaml`, so scorer defaults apply;
- deterministic five-file bundle with exact hashes;
- archive SHA-256: `f3534769cd8c7761b8ae83722b0f2fd381c0349587a10e84bd63e0554a4a7aa4`.

Strengths:

- concise architecture;
- separates exploration context from coder context;
- strong patch hygiene and budget guidance;
- deterministic artifact identity.

Current concerns:

- analyzer prompt says `search_similar_code` can be used for issue concepts without named code, but current official harness explicitly says symbol-style queries are appropriate; update this behavior in our own agent;
- `include_thoughts:false` semantics changed/are disputed in current `adk_submission 0.2.12`; do not assume hidden reasoning remains active;
- analyzer declares tools root does not declare. Current forum reports undeclared/hallucinated tool calls can crash a task when the harness advertises those tools. Certify first;
- child YAML uses `../` includes even though current README says traversal is blocked; verify compiler behavior.

### 12.2 `gemma-4-superagent.ipynb`

This is a synthesis/meta-notebook. Its most valuable contribution is **score evidence and ablation discipline**, not a new proven winning agent.

Self-reported public evidence in the notebook includes:

- Roman-style top bundle: **0.12**
- byte-identical Kozykappa copy: **0.15**
- Black Cat v2: 0.06
- Black Cat compact v5: 0.08
- Black Cat anchor dual v6: 0.10 twice
- Black Cat lab-tuned long-rule v8: 0.05
- Pathfinder hard-budget v1: 0.08

Takeaways:

- public score noise is nontrivial; same/near-same artifacts can differ by about 0.03;
- long repository-derived rule lists can generalize worse than a short generic SWE prompt;
- one change per submission is essential;
- graph-tool outputs can be very large; grep/symbol localization first is safer;
- deterministic ZIPs + SHA tracking should be mandatory.

Stale/unsafe claim to reject unless reproduced: the notebook says `include_thoughts:false` lets the model think privately while keeping reasoning out of context. A newer participant report says `adk_submission 0.2.12` instead maps this to `enable_thinking:false`. Our certification decides.

### 12.3 `black-cat-swe-agent-pack-instinct.ipynb`

Useful because it documents failed experimentation honestly.

- anchor design reportedly scored **0.10 twice** with byte-identical archive;
- a more elaborate v8 scored **0.05**;
- local 19-task lab: anchor 3/19, v8 3/19 despite very different public outcome;
- a proposed “Evidence Council” challenger was piloted on `rich_3468` and failed;
- the challenger produced a literal `\n` in a patch leading to SyntaxError, and the advisor was never called;
- conclusion: compiler success is not functional success; every candidate requires task-level smoke verification and rollback artifact.

### 12.4 `pathfinder-gemma-4-agent-eda-baseline.ipynb`

Reported v1 public score: **0.08**; later candidate pending in the notebook snapshot.

Useful ideas:

- full issue should be passed to analyzer;
- concise analyzer output structure (location/root cause/fix plan/tests/confidence);
- graph/embedding files should be checked for valid/nontrivial size rather than blindly selected by filename;
- deterministic packaging and round-trip validation;
- symbol-first use of code-intelligence tools;
- avoid hard budget decisions without scorer timing evidence.

### 12.5 `gemma-4-budget-aware-coding-agent.ipynb`

This notebook explicitly says its current candidate has **no measured leaderboard score**; a previous candidate scored 0.08.

Current candidate:

- single agent;
- unusual toolset omits `read_file`, relying on shell/edit/write/graph/status/submit;
- max output 4096, include_thoughts false;
- eval config roughly 4.5 minutes / 40 calls / 80 turns / 180s command timeout;
- contains sensible instructions about symbol-only graph search and safe edit fallback.

Do not treat its 4.5-minute arithmetic as proven optimal. Hidden concurrency/global handling is unresolved and platform speed varies.

### 12.6 `gemma-4-walkthrough-first-submission.ipynb`

A rich historical log of early competition behavior. Its own v2 is reported at **0.10**. It contains many version-sensitive community observations:

- historical LoRA wipe/fix discussions;
- reasoning persistence problems;
- very large `search_similar_code` outputs;
- graph/embedding quality observations;
- 4-minute vs 12-minute budget experiments;
- scorer/wheelhouse changes.

Use this notebook as a **bug/experiment inventory**, not a frozen spec. Its most durable lesson is to bound context/output, prefer simple architecture, and maintain exact submission identity.

### 12.7 `gemma-and-the-shape-of-doubt.ipynb`

This is one of the most useful evidence notebooks because it records a verified public score and detailed local task outcomes.

- claimed official public score: **0.08**, submission 56528080 (Sep 25 2026);
- exact ZIP hash reported: `c3f13174...` (see notebook for full value);
- architecture: root agent + optional read-only reviewer + a custom `repo-lens` skill;
- root gets all nine tools plus reviewer/skill;
- eval: 45 calls, 5 minutes, 80 turns, 60s command timeout;
- max output 2048, thinking budget 2048, include_thoughts false, temperature 1.0, top_p .95;
- custom skill does bounded lexical repository ranking and emits bounded JSON.

Most important empirical findings:

- on a 16-task public-dev set, 5/16 resolved in the recorded run;
- a Rich task hit a context-overflow condition near the 32K ceiling;
- some Requests failures were verifier/network-environment artifacts and the same patches passed after a bounded resolver replay;
- therefore separate **agent-quality failures** from **verification/environment failures**.

### 12.8 `gemma-develop-first-try.ipynb`

Builds a dual-agent candidate with strict budgets and official starter LoRA artifacts. Useful for package validation, but several claims are unsupported/generalized too strongly (for example that skills inherently cause crashes). Do not adopt those claims.

No trustworthy leaderboard score for its final candidate is established in the supplied snapshot.

### 12.9 `gemma-4-the-complete-guide-expanded.ipynb`

Primarily reproduces/packages the official sample submission. Useful as a control and for exact starter structure; not evidence of a superior agent.

### 12.10 `gemma-4-swe-agent-complete-eda-adk-starter-kit.ipynb`

Broad educational EDA/starter. Useful for task/graph exploration and code-building examples. Some derived patch-file statistics appear to rely on a simplistic diff parser, so do not treat every displayed statistic as authoritative without recomputation from `tasks.jsonl`.
---

## 13. Exact public dual-agent bundle preserved for reproducibility
The following is the exact five-file runtime mapping embedded in the supplied reproducible baseline notebook. **Do not automatically submit it as-is.** It is preserved so a new chat can reconstruct the reference artifact and then make controlled fixes after current-harness certification.

### `agent.yaml`

```yaml
name: swe_coder
model: gemma-4-31b-it-qat-w4a16-ct
description: Autonomous software engineer that fixes repository issues with minimal, verified patches.
instruction: !include prompts/system.md
generate_content_config: !include configs/sampling.yaml
tools:
  - run_command
  - read_file
  - edit_file
  - write_file
  - get_status
  - submit_patch
  - agent_tool:
      config_path: sub_agents/code_analyzer.yaml
      skip_summarization: true
```

### `configs/sampling.yaml`

```yaml
temperature: 0.2
top_p: 0.95
top_k: 40
max_output_tokens: 8192
thinking_config:
  thinking_budget: 4096
  include_thoughts: false
```

### `prompts/system.md`

```markdown
You are an autonomous senior Python engineer working inside a sandboxed checkout of a real open-source repository at /workspace.
Goal: resolve the issue in the user message with the smallest correct patch, then call `submit_patch`.

## Hard rules
- Never edit, add or delete tests, `conftest.py`, `pytest.ini`, CI or packaging files. Hidden tests are applied after you finish.
- Keep public APIs backward compatible unless the issue explicitly asks for a change.
- Scratch files go to /tmp only. Anything left in /workspace becomes part of your patch.
- The environment is pre-built: do not try to install packages.
- Always finish by calling `submit_patch`. A careful best-effort fix beats no patch.

## Workflow
1. **Understand**: state the expected vs. actual behaviour to yourself in one or two sentences.
2. **Localize**:
   - Call the `code_analyzer` tool with the full issue text first. It returns LOCATION / ROOT CAUSE / FIX PLAN. Verify its claim by reading those exact lines before editing.
   - Extract every identifier, error message and file name from the issue and search for them: `grep -rn "<identifier>" --include=*.py . | head -30`.
   - Read only the lines you need (`read_file` with a line range or `sed -n 'START,ENDp' FILE`).
3. **Reproduce**: write a minimal script to /tmp/repro.py that shows the bug and run it with `python /tmp/repro.py`.
4. **Fix**: edit source files with `edit_file`. Copy `old_string` verbatim from the file, *including leading indentation*, and strip any line-number prefixes. Keep `old_string` short but unique. One logical change per edit. Fix the root cause, not the symptom, and also handle the edge cases the issue mentions.
5. **Verify**: run `python -m py_compile <file>` after every edit, rerun /tmp/repro.py, then run the closest existing tests: `python -m pytest <tests/path> -x -q` (narrow with `-k`).
6. **Submit**: run `git status` and `git diff`, make sure only intended source changes remain, then call `submit_patch`.

## Budget discipline
- Call `get_status` every ~8 tool calls. When less than 25% of turns or time remain, stop exploring and go straight to Fix → Verify → Submit.
- Keep outputs short: pipe through `head`, use `grep -n`, `pytest -q`. Never print whole large files.
- If an edit fails twice, re-read the exact lines and retry with a smaller unique snippet.

## Quality bar
- Match the surrounding code style, type hints and naming.
- Prefer a small, targeted change over a refactor. Touch other files only when the fix requires it.
```

### `prompts/analyzer.md`

```markdown
You are `code_analyzer`, a read-only code navigation specialist. You never modify files.
Given an issue, find exactly where it must be fixed.

## Tools
- `run_command` for READ-ONLY commands only: `grep -rn`, `ls`, `sed -n`, `git log -p -S`
- `search_similar_code` for concepts the issue describes without naming code
- `get_code_neighbors` to walk callers and callees
- `get_code_subgraph` to see how a few candidate symbols connect
- `read_file` with tight line ranges to confirm

## Method
1. Extract identifiers from the issue: function/class names, error messages, file paths, options.
2. Search for each one, then follow the call chain until you reach the line where behaviour diverges from what the issue expects.
3. Confirm by reading the actual code. Never guess line numbers.

## Answer format (at most 250 words, nothing else)
LOCATION: <path>:<start>-<end> (<function or class>)
ROOT CAUSE: <one or two sentences>
FIX PLAN: <concrete change>
RELATED: <other call sites or files needing the same change, or "none">
TESTS: <existing test files that exercise this code>
CONFIDENCE: high | medium | low
```

### `sub_agents/code_analyzer.yaml`

```yaml
name: code_analyzer
model: gemma-4-31b-it-qat-w4a16-ct
description: Read-only code navigator. Given an issue, returns the exact location, root cause and a fix plan.
instruction: !include ../prompts/analyzer.md
generate_content_config: !include ../configs/sampling.yaml
tools:
  - run_command
  - read_file
  - search_similar_code
  - get_code_neighbors
  - get_code_subgraph
```


Known exact hashes for that public reference bundle:

- `agent.yaml`: `c8f4305df8091c679985b610d3e5195e12703c5989f61230939a938ef6afb2e4`
- `configs/sampling.yaml`: `ac857a0e8f76e01bfb7de0dcfb1d7d68d256ba1537109050127cf75476ee5012`
- `prompts/analyzer.md`: `771480aba97ca67110aec892a5107eb24ff544a9ef28a41fed5616228a0f4e58`
- `prompts/system.md`: `22b6a11eeed24a5f216abc2d3143a6dbb8e7ab5946d78df1d8856fb554feb3f6`
- `sub_agents/code_analyzer.yaml`: `07530d7f65c9654c3f074924cba10fb3e1fd22115885c3a948decc47fb055fc3`
- ZIP: `f3534769cd8c7761b8ae83722b0f2fd381c0349587a10e84bd63e0554a4a7aa4`

### Why we are NOT copying this blindly

Before using it we must resolve at least four current-version questions: `include_thoughts:false`, `../` include behavior, missing-tool fatal errors, and the overly broad analyzer advice for `search_similar_code`.
---

## 14. Community / forum issue ledger (2026-10-01)

Everything in this section is **COMMUNITY-EVIDENCE unless explicitly marked upstream-verified**. These reports are extremely useful because they define our certification tests, but they are not automatically scorer facts.

### C01 — `include_thoughts:false` may disable reasoning rather than only hide it

Participant report against `adk_submission 0.2.12`:

```text
thinking_budget: 4096
include_thoughts: false
        -> bridge sets enable_thinking:false
        -> no reasoning generated
```

If true, there is currently no supported mode “generate reasoning but omit it from retained ADK history.” This directly contradicts older public notebooks that describe `include_thoughts:false` as private reasoning. **Status: high-priority reproduction required.**

### C02 — thoughts may be dropped between tool calls (`reasoning_content` vs `reasoning`)

A participant showed ADK/LiteLLM sending prior thought as `reasoning_content` while the pinned vLLM path read only `reasoning`. They reported prompt-token evidence across many public runs.

**Upstream verification performed in the original chat:** vLLM GitHub issue **#38488** really exists and describes exactly this input-side compatibility bug. vLLM PR **#42664**, “Normalize reasoning_content to reasoning for client compatibility,” was merged on **2026-05-21** (merge commit `346cf163a11b55e069aa3143ae2878967393ddc2`). This proves an upstream fix exists. It does **not** prove Kaggle's pinned scorer build contains it. **Status: upstream-fixed / Kaggle-current unknown.**

### C03 — undeclared/hallucinated tool call can fatally end a task

Participant removed `search_similar_code` from an agent. The model still called it, and Google ADK `_get_tool` raised a `ValueError`, ending the task with zero patch. The harness task message may advertise graph tools based on task data even if a particular agent YAML omits them.

Impact: tool minimization intended to save context can become dangerous. **Status: reproduce before removing advertised tools.**

### C04 — same fatal path may discard an already-good working-tree patch

Related report says an undeclared tool exception exits through an exception path and can score 0 even after correct edits. Verify whether automatic patch recovery runs for this exception class in the current build. **Status: critical.**

### C05 — double-JSON encoded tool results

Participant analysis claims swegemma returns a JSON string; ADK wraps it in `{"result": "..."}`; LiteLLM serializes again for OpenAI tool-message content. Quotes, backslashes and newlines may therefore reach Gemma double-escaped.

Reported impact: 62/299 `edit_file` calls failed with `old_string not found`; 39 failures across 18 tasks were reportedly provably caused by escaping. Python docstrings and non-ASCII strings were frequent triggers.

**Status: extremely high-priority reproduction.** If confirmed, design edit strategy around short safe anchors or a `run_command`-based exact replacement fallback.

### C06 — `edit_file` old-string failures

Separate forum reports complain of systematic `old_string not found`. Could be user prompting, escaping, stale reads, or the double-JSON issue. Do not assume the tool's 3-tier matcher is sufficient until tested with quotes/backslashes/unicode.

### C07 — `read_file` line-range typing error

Forum title/report: `">" not supported between instances of 'int' and 'str'` when using line ranges. Could indicate schema/serialization regression in a current wheelhouse. **Status: reproduce current version.**

### C08 — test-file reset command may be a no-op when pathspec list contains nonexistent files

Participant points out `git checkout HEAD -- <files_to_reset>` aborts the entire checkout if one pathspec does not exist, while the error is swallowed. Since the reset list can include `sitecustomize.py`/other paths absent from repository `HEAD`, modifications to tracked tests might survive and then interfere with applying `test_patch`.

**Our policy regardless:** never modify tests/config; do not exploit verifier bugs. Certification only tells us whether accidental test edits can poison otherwise-correct patches.

### C09 — compaction may lose tool results

Forum report says compaction summaries keep text but can lose tool-call/result state, causing repetitive loops after compaction. Current README documents compaction, but not this claimed loss behavior. **Status: reproduce with a controlled long trajectory.**

### C10 — `search_similar_code` can explode context

Several notebooks report very large returned code bodies (100k+ chars before downstream truncation/serialization) and context pressure. Official tool docs are symbol-oriented. **Status: likely risk; measure current response cap.**

### C11 — LoRA may shrink available KV cache heavily

Community reports say enabling LoRA on the 4×L4 scorer can materially reduce available KV cache and make long contexts hang. Exact figures are version-specific. **Status: defer LoRA, then smoke-test memory/context.**

### C12 — historical LoRA adapter ineffectiveness / zeroing bug

Earlier community analysis reported a model-layer registration issue that could make LoRA ineffective; later wheelhouse updates were said to fix it. **Status: never assume adapter works—perform an output-delta test before training investment.**

### C13 — Sep 30 / Oct 1 scorer “Notebook Threw Exception” incidents

Multiple participant posts report scorer failures with no score after the Sep 30 wheelhouse update, including bundles without LoRA that compiled and ran locally on 4×L4. One exact same ZIP reportedly failed twice within minutes while an older notebook version had scored earlier.

This substantially increases the probability that a no-score/exception can be platform-side. Our submission ledger must distinguish:

- agent scored 0;
- agent timed out normally;
- ZIP/compile validation failure;
- scorer notebook exception;
- GPU/platform outage;
- unknown platform error.

Never treat a “Notebook Threw Exception” run as evidence that a prompt change reduced SWE capability.

### C14 — GPU outage reports

Participants reported scorer failures during a GPU outage. Same implication: submission provenance and retry policy matter.

### C15 — global 12-hour questions remain operationally important

Forum questions ask: hidden-task concurrency? exact `eval_config` keys? behavior at 12h if unfinished? Some historical notebook claims exist, but the user supplied no definitive current host answer in this chat. **Status: host/current scorer unknown; do not overfit time caps based on assumptions.**

### C16 — hidden repository composition unknown

Participant asked whether hidden set uses the same four public repos. No definitive answer was supplied. **Design for unknown/private repositories.**

### C17 — local smaller Gemma (E4B/E2B) / separate leaderboard suggestion

This is only a participant suggestion. Current official scoring contract still requires the 31B competition model. Do not plan around a hypothetical second leaderboard unless the host announces it.

### C18 — paper-track PHP-monolith benchmark discussion

Interesting research methodology (browser/E2E oracles, reward-hacking guards) but not directly part of the main agent architecture. Potential paper inspiration only.

### C19 — public code sharing / paper-track publication question

Rules already prohibit private sharing outside a team and govern public competition code. Before publication, re-check the exact competition ruling and use Kaggle-approved public channels if required.
---

## 15. Known contradictions that a new assistant MUST NOT gloss over
| Topic | Source A | Source B | Current stance |
|---|---|---|---|
| `include_thoughts:false` | Older public notebooks: model still reasons, thoughts not retained | Forum report on adk_submission 0.2.12: it maps to `enable_thinking:false` | Unknown until H02; do not claim private reasoning works. |
| Reasoning across tool calls | Gemma docs/historical intent: preserve thoughts in function-call turn | Pinned vLLM path may drop `reasoning_content` | Upstream PR merged, Kaggle current unknown; test H01. |
| Compaction interval | Current HARNESS README: 5 | Official Getting Started notebook: 15 | Configuration/version difference; inspect current scorer/wheels. |
| `..` in `!include` | README says path traversal blocked | Scored dual-agent public bundle uses `../` from sub-agent YAML | Compiler/version discrepancy; test before adopting. |
| Graph search query | Some public prompts say use concepts/free text | Current harness says query should be symbol/module/class/function name | Follow current harness: symbol-first. |
| Tool minimization | Public notebooks suggest removing `search_similar_code` to save context | Forum says model may still call advertised undeclared tool and fatally crash | Keep safe tool availability until missing-tool behavior certified. |
| LoRA readiness | Official format supports LoRA | Community reports adapter and KV-cache/runtime bugs across versions | Defer; prove adapter effect and stability first. |
| Hard per-task caps | Some notebooks use 4–5 min to fit 12h | Other evidence says hard caps lose solvable tasks; concurrency/12h semantics uncertain | Measure actual distribution before setting final caps. |
| Public dev score predicts LB | Public notebooks optimize on dev subsets | Same dev outcome/bundle can map to very different public-LB scores | Maintain locked holdout + use LB sparingly. |
---

## 16. STEP 0 — Competition Environment & Harness Certification

**This is the next phase. Do not jump to LoRA or complex agent design until these tests are classified.**

For every check record:

- current dataset/wheelhouse timestamp/version;
- Python package versions (`swegemma`, `adk-submission`, `adk-eval-core`, `google-adk`, `litellm`, `vllm`);
- machine/runtime (Mac subprocess, Docker, Colab GPU, 4×L4 if available);
- exact config/ZIP hash;
- result status: `PASS`, `FAIL`, `VERSION-SPECIFIC`, `HOST-UNKNOWN`, `NOT-REPRODUCED`;
- raw log/trace path;
- minimal reproduction command.

### Harness certification matrix

| ID | Test | Why it matters | Expected/decision rule |
|---|---|---|---|
| H01 | `reasoning_content` survives a tool-call continuation | Multi-turn reasoning integrity | Tokenized next prompt must contain sentinel thought when preservation is intended. |
| H02 | `include_thoughts` semantics | Prevent stale prompt assumptions | Separately measure generation enabled/disabled and retention behavior. |
| H03 | `thinking_budget` forwarding/effect | Confirm config reaches model | Controlled prompts should show measurable reasoning-token behavior difference. |
| H04 | undeclared tool call behavior | Can tool minimization kill tasks? | Determine whether ADK returns recoverable tool error or throws fatal exception. |
| H05 | declared tool call behavior | Basic control | Same call with tool declared must execute normally. |
| H06 | `read_file` line-range typing | Current forum regression | `start_line`/`end_line` integers must work through full ADK serialization path. |
| H07 | `read_file` quotes/backslashes/unicode | Tool serialization | Inspect exact model-visible content, not only Python return value. |
| H08 | `edit_file` simple exact replacement | Baseline edit | One plain replacement must pass. |
| H09 | `edit_file` triple-quoted/docstring replacement | Double-JSON stress | Model-visible text should support exact safe edit or we document fallback. |
| H10 | `edit_file` unicode replacement | Serialization stress | Arabic/non-ASCII fixture should edit correctly. |
| H11 | protected test/config reset | Verifier correctness | Modify a sacrificial public test, verify Container B actually restores it. Never exploit failure. |
| H12 | scratch-file contamination | Patch hygiene | `/workspace/repro.py` should enter diff; `/tmp/repro.py` should not. |
| H13 | timeout patch recovery | Best-effort safety | Make source edit, omit submit, hit timeout; see if patch is verified. |
| H14 | tool-budget exhaustion recovery | Same | Make edit, exhaust calls; see whether fallback diff is preserved. |
| H15 | compaction retains enough state | Prevent loops | Long controlled tool chain should not forget last critical result. |
| H16 | LoRA model-start smoke | Later phase risk | Server loads without crash on scorer-like GPU; do not train yet. |
| H17 | double-JSON encoding level | Root cause of edit failures | Capture raw ADK/vLLM model input around tool response and count escaping layers. |
| H18 | hallucinated tool recovery | Robustness | Deliberately request a nonexistent tool and observe whether task can continue. |
| H19 | compaction + tool-result retention | Stronger H15 | Place sentinel only in tool output, force compaction, check later model visibility. |
| H20 | `AgentTool(skip_summarization)` behavior | Architecture choice | Measure whether parent turn/history behaves as public notebooks assume. |
| H21 | graph-tool response size/context pressure | 32K safety | Quantify chars/tokens for top-k and worst public symbols; establish safe k/usage. |
| H22 | global scheduling/12h behavior | Budget policy | Host answer or controlled scorer evidence for concurrency/unstarted tasks. |
| H23 | scorer package/version fingerprint | Reproducibility | Record exact current wheel versions before each submission epoch. |
| H24 | LoRA effective-output delta | Prevent no-op training | Fixed prompt/seed should differ meaningfully with adapter vs base on current server. |
| H25 | LoRA KV-cache/context capacity | Runtime | Measure max stable prompt length/adapters on 4×L4-like setup. |
| H26 | `../` `!include` semantics | Bundle compatibility | Compile both same-dir and parent-relative include forms under current package. |
| H27 | no-LoRA empty adapter configuration | Sep30 scorer suspicion | Confirm discover/config path when zero adapters are present. |
| H28 | current public-sample submission notebook path | Catch scorer-only errors | Build a minimal CPU-only submission notebook and verify expected Kaggle upload artifact. |
| H29 | `get_status` and `submit_patch` free-call semantics | Budget model | Confirm they remain callable at ordinary tool-call limit. |
| H30 | automatic graph-tool advertisement vs actual agent tools | Tool hallucination | Inspect exact task prompt/tool schema when graph tools omitted from agent YAML. |

### Step 0 exit gate

Do not declare Step 0 complete until at minimum H01–H15, H17–H20, H23, H26–H30 are resolved sufficiently to design a safe baseline. LoRA-specific H16/H24/H25 can remain deferred if our first baseline is non-LoRA.
---

## 17. Proposed project repository layout
```text

gemma4-developer-agent/
├── README.md
├── pyproject.toml / requirements-locks as appropriate
├── .gitignore
├── docs/
│   ├── MASTER_HANDOFF.md                 # copy of this document
│   ├── competition/
│   │   ├── official_facts.md
│   │   ├── issue_ledger.md
│   │   └── decisions.md
│   └── experiments/
│       └── EXPERIMENT_PROTOCOL.md
├── vendor_meta/                          # metadata only, not duplicated Kaggle data
│   ├── source_hashes.json
│   └── wheelhouse_versions.txt
├── harness_cert/
│   ├── README.md
│   ├── fixtures/
│   ├── scripts/
│   └── results/                          # ignored/generated
├── agents/
│   ├── baseline_v0_official/
│   ├── baseline_v1_dual/
│   └── candidates/
├── tools/
│   ├── build_submission.py
│   ├── validate_submission.py
│   ├── hash_submission.py
│   ├── analyze_results.py
│   └── select_tasks.py
├── configs/
│   ├── local_mac.yaml
│   ├── gpu_smoke.yaml
│   └── scorer_like.yaml
├── eval/
│   ├── splits.json
│   ├── metrics.py
│   └── failure_taxonomy.py
├── experiments/
│   └── registry.csv or registry.jsonl
└── artifacts/                            # generated; do not commit large raw outputs
    ├── submissions/
    ├── local_runs/
    └── reports/
```

Repository rules:

- Do not copy the 22 GB Kaggle dataset into Git.
- Store local data path via config/env var.
- Generated run directories, traces and model artifacts should be ignored by Git unless a tiny curated fixture is intentionally committed.
- Each submission ZIP must be reproducibly buildable from committed source and have a SHA-256 recorded in the experiment registry.
- Preserve a known-good fallback ZIP after every scored milestone.
---

## 18. Experimental methodology designed for winning rather than guessing

### 18.1 Separate harness validation, agent development and leaderboard validation

We need three distinct layers:

**Layer A — Harness certification:** synthetic/public fixtures, focused on whether infrastructure behaves as documented.

**Layer B — Local agent benchmark:** public 129 tasks, with a locked holdout that humans do not inspect for gold patches/test patches while tuning.

**Layer C — Kaggle public leaderboard:** expensive/noisy external validation; one submission/day; use only after a candidate clears local gates.

### 18.2 Locked split

A simple starting point could be around 80 development / 49 locked holdout, but the final split should be generated after inspecting `instance_id`, repository and `base_commit` structure. Avoid leakage from near-duplicate issues/commits. Prefer grouping by commit/time/repo when appropriate rather than a naive random row split.

Never read the gold `patch`/`test_patch` for the locked holdout during agent design. The automated verifier may consume them to score the run, but the human/LLM developing the prompt should see only task inputs and aggregate failure classifications needed to avoid answer leakage.

### 18.3 Metrics to record per task

At minimum:

- `instance_id`, repo, base_commit
- resolved bool
- verification exit/error class
- patch produced? patch chars/files
- patch applies?
- syntax/compile result
- targeted-test result in Container A
- tool calls total and by type
- LLM turns
- elapsed agent time
- model token usage/context indicators if available
- analyzer invoked?
- invalid tool-call count
- edit-file failure count and reason
- context-compaction occurrence
- timeout/budget exhaustion
- automatic patch fallback used?
- platform/scorer error vs agent failure

Aggregate metrics:

- resolution rate overall and by repo/failure class;
- timeout rate;
- no-patch rate;
- invalid-tool fatal rate;
- edit failure rate;
- median/p90 wall time;
- median/p90 tool calls;
- patch size distribution;
- local holdout delta vs baseline with paired task-level table.

### 18.4 Experiment rule

Every candidate gets:

```text
experiment_id
parent_experiment_id
hypothesis
single intended change
Git commit
submission ZIP SHA256
agent file hashes
package/wheel versions
hardware/runtime
local split results
Kaggle submission ID (if submitted)
Kaggle timestamp
public score or scorer error
notes about platform incidents
keep/reject decision
```

Do not keep a candidate merely because one public score rose by 0.02 unless local paired evidence and/or repeated external evidence supports the change.
---

## 19. Baseline architecture after Step 0

The current preferred **starting hypothesis**, not yet a final submission, is deliberately simple:

```text
ROOT CODER (Gemma 4 31B)
  |
  |-- core shell/file/edit/status/submit tools
  |
  `-- CODE ANALYZER as AgentTool
          |-- read-only shell/file navigation
          `-- graph tools only under certified-safe usage
```

### Root coder responsibility

1. Read issue and state expected vs actual behavior internally/briefly.
2. Extract literal identifiers, error strings, paths, options.
3. Delegate bounded localization to analyzer if beneficial.
4. Independently confirm exact source lines.
5. Build a minimal `/tmp` reproduction when feasible.
6. Apply the smallest root-cause fix.
7. Compile/syntax-check.
8. Run the nearest targeted tests with strict output/time bounds.
9. Inspect Git diff/status.
10. Submit.

### Analyzer responsibility

Return a compact structured report only:

- LOCATION
- ROOT CAUSE
- FIX PLAN
- RELATED
- TESTS
- CONFIDENCE

Avoid large file dumps and avoid editing.

### Why not add a reviewer immediately?

Every additional LLM call consumes runtime/context and can introduce tool/transfer failure modes. A reviewer should be added only if paired local holdout evidence shows it converts enough failures to passes to justify its cost.

### Why no LoRA initially?

We first need to measure the capability ceiling of prompt/tool/context engineering on the same base model and establish stable harness behavior. Otherwise training investment cannot be attributed correctly.
---

## 20. Search/localization policy

Use a staged localization strategy:

```text
issue text
  -> literal identifiers / filenames / error messages
  -> grep/rg/find / workspace tree
  -> candidate source files and symbols
  -> tight read_file ranges
  -> optional graph neighbors/subgraph around CONFIRMED symbols
  -> implementation hypothesis
```

`search_similar_code` should not be the first action on a free-form issue sentence. The current harness documentation says its offline query resolution is symbol-oriented. If used, feed it a function/class/module symbol already found by lexical search.

Graph calls are useful when they reduce reading. They are harmful when they return huge code payloads or cause the agent to wander. Measure H21 before deciding default use.
---

## 21. Editing policy under current serialization risk

Preferred path:

1. `read_file` a narrow exact region.
2. `edit_file` using a short unique `old_string` copied from the model-visible content after current serializer behavior is understood.
3. Immediately syntax-check and inspect diff.

If H17 confirms double encoding materially damages exact copying, evaluate a controlled fallback using `run_command` and a tiny Python script stored in `/tmp` that:

- reads the real workspace file bytes/text;
- asserts the target occurrence count is exactly one;
- performs one deterministic replacement;
- writes atomically;
- exits nonzero on ambiguity.

This fallback must remain simple, auditable and root-cause-oriented. Do not use shell edits that silently rewrite formatting across a file.
---

## 22. Runtime / GPU plan with minimal cost

### Phase A — $0 / Mac-first

- complete dataset download;
- hash/inventory official files;
- install current wheels where feasible;
- inspect `tasks.jsonl`, graphs, embeddings;
- implement harness certification that does not require 31B;
- build deterministic submission builder/validator;
- establish local splits and analysis tooling.

### Phase B — small exact-model smoke tests

Use Colab/Kaggle/another GPU only after the bundle is statically valid. First inspect actual GPU/VRAM. Run 1–3 tasks, not all 129.

### Phase C — scorer-like batches

When a candidate is worth measuring, prefer an environment close to 4×L4. Run a selected development batch, then locked holdout. Monitor wall time/context/tool failures.

### Phase D — only then consider paid GPU or training

A paid A100/L40/H100 session may be justified if it materially accelerates repeated controlled experiments. Do not rent persistent infrastructure by default.

### Exact model on the M1 Pro

The Mac remains useful even if exact model inference is impractical. A smaller local model can be used only for **framework smoke tests**, never as a scientific substitute for the required 31B model when measuring agent quality.
---

## 23. Submission provenance and scorer-outage protocol

Given the Sep 30/Oct 1 “Notebook Threw Exception” reports, every Kaggle attempt needs a ledger row before clicking Submit.

Required fields:

```text
submission_id
notebook_slug/version
UTC timestamp
experiment_id
Git commit
ZIP SHA256
ZIP byte size
file manifest hashes
model config hash
prompt hash
LoRA present? adapter hashes
wheelhouse/package snapshot
expected per-task budget
local smoke result
Kaggle outcome: score / error / no-score
runtime duration if shown
known platform incident at that time?
```

If Kaggle returns a platform exception/no score:

1. do not mark candidate as worse;
2. save screenshot/API status;
3. compare with other forum outage reports;
4. verify the same ZIP locally without changing it;
5. if appropriate, request rerun/allowance restoration from host;
6. preserve submission allowance history.
---

## 24. What NOT to do

- Do not start LoRA training because the competition description mentions fine-tuning.
- Do not build 5–10 agents before a single-agent/dual-agent baseline is stable.
- Do not blindly copy `include_thoughts:false` reasoning explanations from old notebooks.
- Do not remove a tool merely to save schema tokens until invalid-tool behavior is certified.
- Do not call `search_similar_code` with a natural-language issue paragraph.
- Do not print entire files or huge graph results into model context.
- Do not use the public 129 gold patches to hand-author rules for the locked holdout.
- Do not change tests, pytest configuration or hidden-verifier-sensitive files.
- Do not leave `/workspace` scratch scripts behind.
- Do not run full repository test suites unless a task truly requires it and budget permits; use targeted tests.
- Do not trust one public leaderboard move as proof of causality.
- Do not assume a Kaggle “Notebook Threw Exception” is our fault.
- Do not assume a successful compiler means the agent works.
- Do not use a smaller Gemma as evidence that the 31B competition agent improved.
- Do not spend money on GPU before the experiment is ready.
- Do not claim hidden repositories are the same as public repos without host confirmation.
---

## 25. Immediate next actions once the 22.42 GB download finishes

### Gate A — dataset integrity/inventory

1. Confirm total size and 524-file structure against Kaggle page (recognizing compressed/local metadata can make byte totals differ slightly).
2. Record local path, download timestamp and key file hashes.
3. Verify presence of `HARNESS_README.md`, `tasks.jsonl`, `sample_submission`, `snapshots`, `graphs`, `embeddings`, `wheels`, `docker`, `sandbox`.
4. Parse all 129 tasks and recompute repository counts/stats ourselves.
5. Inspect zero/truncated graph/embedding files and hard-link behavior.

### Gate B — environment fingerprint

6. Inventory every relevant wheel and version from the downloaded `wheels/` directory.
7. Compare package versions with the notebook/forum reports (`adk-submission 0.2.12`, vLLM 0.19.1, etc.) rather than assuming they match.
8. Save `wheelhouse_manifest.csv/json` with filename, version, size, SHA256.

### Gate C — reproduce official sample mechanics

9. Copy `sample_submission/` to a clean working directory.
10. Validate/compile it with current packages without editing semantics.
11. Package deterministically and record ZIP SHA.
12. Run minimal public task(s) with CPU/subprocess only if possible; exact model calls can wait for GPU.

### Gate D — Harness Certification

13. Implement H01–H30 in order of risk, starting with serializer/tool/reasoning tests.
14. Create `HARNESS_CERT_REPORT.md` with evidence and current-version conclusions.

### Gate E — baseline

15. Reconstruct the exact public five-file dual-agent reference bundle.
16. Determine changes required only for current compatibility (not “improvements” yet).
17. Run 1–3 exact-model tasks.
18. Run a development batch.
19. Freeze a locked holdout and run it.
20. Only then consider first Kaggle submission.
---

## 26. Proposed experiment ladder

Advance only when the previous layer is stable:

### E0 — official sample control
Purpose: prove toolchain/package mechanics.

### E1 — exact public dual-agent reference
Purpose: reproduce a known public architecture under our current wheelhouse.

### E2 — current-harness compatibility fixes only
Examples: safe includes, corrected `search_similar_code` instructions, thinking setting supported by H02. No architecture expansion.

### E3 — tool/serialization robustness
Examples: short edit anchors, bounded outputs, fallback editing if H17 confirms escaping.

### E4 — localization ablation
Single vs analyzer; lexical-first vs graph-assisted; measure task-level paired changes.

### E5 — reasoning configuration sweep
Only after H01–H03. Compare no thinking vs retained thinking and any genuinely supported third mode.

### E6 — budget policy
Use empirical time/tool distributions; compare adaptive status-driven policy to fixed caps.

### E7 — reviewer / second-pass agent
Add only if failures show patch correctness would benefit and cost is acceptable.

### E8 — skills
Introduce a small deterministic navigation/edit skill only for a measured failure class.

### E9 — LoRA smoke + targeted PEFT
First H24/H25, then train only against a well-defined failure class. Evaluate on locked holdout and unseen repos/tasks.

### E10 — RL / learned routing
Only after a strong supervised/non-trained baseline and reliable evaluator exist. Otherwise reward signal is too noisy.
---

## 27. Failure taxonomy to use from the first run

Classify every unresolved task into the first applicable root cause:

1. `PLATFORM_SCORER_EXCEPTION`
2. `MODEL_SERVER_START_FAILURE`
3. `HARNESS_COMPILE_VALIDATION`
4. `INVALID_TOOL_FATAL`
5. `TOOL_SERIALIZATION`
6. `CONTEXT_OVERFLOW`
7. `AGENT_TIMEOUT`
8. `TOOL_BUDGET_EXHAUSTED`
9. `NO_PATCH`
10. `PATCH_MALFORMED_OR_APPLY_FAIL`
11. `SCRATCH_OR_TEST_POLLUTION`
12. `LOCALIZATION_WRONG`
13. `ROOT_CAUSE_WRONG`
14. `EDIT_FAILED`
15. `SYNTAX_ERROR`
16. `TARGETED_TEST_FAIL`
17. `HIDDEN_TEST_FAIL_BEHAVIORAL`
18. `OVERFIX_REGRESSION`
19. `ENVIRONMENT_VERIFICATION_ARTIFACT`
20. `UNKNOWN`

The point is to improve the largest **controllable** failure class, not whatever anecdote is most interesting.
---

## 28. Decision log already made in this chat

- **Decision:** target a serious winning attempt, but do not promise victory.
- **Decision:** treat this as agent engineering/research, not conventional tabular Kaggle.
- **Decision:** user Mac remains primary engineering machine; exact 31B inference moves to suitable GPU.
- **Decision:** do not buy a server yet.
- **Decision:** collect official information and public failure evidence before coding. This collection is now sufficient to begin Step 0.
- **Decision:** Step 0 is Harness Certification, not agent optimization.
- **Decision:** no initial LoRA.
- **Decision:** initial architecture hypothesis is root coder + one bounded read-only analyzer AgentTool.
- **Decision:** deterministic packaging and SHA tracking are mandatory.
- **Decision:** public LB is too noisy to be our only evaluator.
- **Decision:** maintain a locked public-data holdout with no human gold-patch leakage.
- **Decision:** symbol/grep-first localization; graph only after a real symbol is identified.
- **Decision:** current forum/harness bugs are first-class engineering variables and must be versioned/reproduced.
---

## 29. Open questions that remain unresolved

1. Does current Kaggle `adk_submission 0.2.12` still make `include_thoughts:false` disable reasoning?
2. Does current Kaggle vLLM include the merged upstream `reasoning_content` compatibility fix?
3. What exact wheelhouse/package versions are live in the scorer after Sep 30/Oct 1 changes?
4. Are hidden tasks processed sequentially or concurrently, and with what concurrency?
5. What exactly happens to unfinished tasks at the 12-hour global limit in the current scorer?
6. Does an undeclared tool call still abort the whole task without patch recovery?
7. Does the initial task prompt advertise graph tools even when the active agent omits them?
8. Is `read_file(start_line,end_line)` currently affected by an int/string schema bug?
9. Is double-JSON encoding still present in current tool responses?
10. Does protected-test reset still fail when nonexistent pathspecs are included?
11. How much context/KV cache is practically available with no LoRA vs one/multiple LoRA adapters?
12. Are hidden repos drawn from the public four, private versions of them, or broader private repositories?
13. Is `../` inside `!include` accepted or blocked in the exact current compiler?
14. Can a CPU-only Kaggle submission notebook that merely materializes `submission.zip` still be used reliably after the recent scorer update?
15. Will host re-score submissions affected by platform/scorer outages? This is policy/time dependent.
---

## 30. How a new ChatGPT/Claude/Codex session should continue

### First message/instruction for a new assistant

> Read `GEMMA4_COMPETITION_MASTER_HANDOFF_2026-10-01.md` completely before proposing changes. Treat OFFICIAL-RULE/OFFICIAL-HARNESS as authoritative, public notebooks/forum posts as version-sensitive evidence, and preserve unresolved contradictions. Our current phase is STEP 0 Harness Certification. Do not start LoRA or long GPU runs. The full Kaggle dataset was still downloading at the time of the handoff; first ask whether 22.42 GB finished, then inventory current files/wheels. Build reproducible tests H01–H30 and a versioned certification report. Long exact-Gemma runs must be explicit and justified. Maintain deterministic ZIP hashes and one-change experiments.

### What the new assistant must not “simplify away”

- the reasoning/include_thoughts contradiction;
- the vLLM `reasoning_content` issue and upstream merge-vs-Kaggle-version distinction;
- fatal undeclared-tool reports;
- double-JSON/edit reliability reports;
- test-reset bug report;
- Sep30/Oct1 scorer exception reports;
- public leaderboard noise / same-ZIP score variation;
- unknown hidden repo distribution;
- M1 Pro 16 GB constraint;
- need for a locked holdout and provenance.
---

## 31. Compact glossary

- **ADK** — Google Agent Development Kit used to compile/run the declarative agent.
- **AgentTool** — an agent wrapped as a callable tool for another agent; useful for isolating exploration context.
- **Container A** — agent's editable task sandbox.
- **Container B** — fresh verification sandbox.
- **Gold/reference patch** — solution patch included in public development tasks; should not be manually used for locked-holdout tuning.
- **`test_patch`** — task-specific verifier tests applied in Container B for public development evaluation; hidden equivalent used by scorer.
- **Resolution rate** — fraction of tasks fully verified as resolved.
- **W4A16** — weight-4-bit / activation-16-bit quantized model configuration.
- **LoRA/PEFT** — parameter-efficient adapters optionally submitted with the agent.
- **Graph tools** — precomputed symbol graph navigation functions.
- **Wheelhouse** — offline Python package wheels shipped for the evaluation environment.
- **Public LB** — score on public half/sample of hidden test data during competition.
- **Private LB** — final hidden split used for final placement.
- **Harness Certification** — our Step 0 suite that verifies the current runtime actually behaves as documented before optimization.
---

## 32. Final strategic summary

The competition is currently attractive because the public leaderboard is low and discrete, but the path to a strong result is not “write a huge prompt” or “train LoRA immediately.” The evidence collected so far shows three simultaneous problems to solve:

1. **SWE capability** — localize the right code, reason about the issue, make a minimal correct patch, verify it.
2. **Agent efficiency** — stay within 32K context, tool/runtime budget and global evaluation time.
3. **Harness reliability/compatibility** — survive current ADK/vLLM serialization, thinking, tool-declaration and scorer-version quirks.

The near-term competitive advantage is likely to come from eliminating systematic zeroes: invalid tool calls, context overflows, malformed edits, no-patch timeouts, unnecessary exploration and verifier pollution. Each additional reliably solved hidden task can materially move a low-score leaderboard.

The correct immediate action is therefore:

**finish the dataset download → fingerprint the exact wheelhouse → run Step 0 Harness Certification → reproduce a known dual-agent baseline → establish a locked holdout → make one measured change at a time → submit sparingly → only later evaluate LoRA/RL.**

This file should be updated, not replaced, as current host answers and our own reproductions resolve the open issues.

---

## 33. Exact official `sample_submission` control snapshot from the supplied notebook

The supplied `gemma-4-the-complete-guide-expanded.ipynb` copied the then-current official `sample_submission` byte-for-byte and created a deterministic ZIP. This is useful as a **control artifact**, not as a winning baseline.

At that notebook run:

- file count: **10**
- deterministic ZIP bytes: **175,156**
- deterministic ZIP SHA-256: `68daef7faebd4ab424888afbf0c1ab9d773a8e3b419f7428006cf80c23b1cfec`
- the notebook also verified that the `HARNESS_README.md` SHA was `3d6e57a13234cb4e783ba24eaab486459af76e0923ea4c6607cd41cc8961bbbb`, matching the HARNESS file used in this handoff.

### 33.1 Official sample file hashes observed in that notebook

| File | Bytes | SHA-256 |
|---|---:|---|
| `adapters/main_lora/adapter_config.json` | 664 | `75a46da2db7f3c70442e5c728f64059aff52cf64174cee7aeb3a4ec37f6e78fb` |
| `adapters/main_lora/adapter_model.safetensors` | 217,672 | `dcbedd989af34f5201a39606e0bd4014d351a29162dd96da418782cbbe487ad9` |
| `adapters/tool_lora/adapter_config.json` | 664 | `75a46da2db7f3c70442e5c728f64059aff52cf64174cee7aeb3a4ec37f6e78fb` |
| `adapters/tool_lora/adapter_model.safetensors` | 217,672 | `dcbedd989af34f5201a39606e0bd4014d351a29162dd96da418782cbbe487ad9` |
| `agent.yaml` | 438 | `c7fbbbbdc44778419be8e9f53a85800eeb17d0cf151e0846c46e107401b72034` |
| `configs/sampling.yaml` | 120 | `3dab0b2506dae34ce92fef7b380d6073729104b23fe2cf66d44ac1d2f213aa6a` |
| `eval_config.yaml` | 232 | `adb486b67535fa59b427b79d44dafc131176f1f1964d184e16046ed520ede02d` |
| `prompts/analyzer.md` | 527 | `c762b062032cc8463015e589ad8b4f0e17296062e1b0ff1e78712eef9f5e2d43` |
| `prompts/system.md` | 3,888 | `f1cbf7943ee9687382321b1dfd9cd88581d608c1c8b8e6ea61a763bd1a3234af` |
| `sub_agents/code_analyzer.yaml` | 361 | `7a764798e36cc68aa38900256245e4aed969b5439b992c7da4105b6ebcb7e62b` |

### 33.2 Official sample root agent

```yaml
name: swe_baseline_agent
model: gemma-4-31b-it-qat-w4a16-ct
adapter: main_lora
instruction: !include prompts/system.md
tools:
  - run_command
  - read_file
  - edit_file
  - write_file
  - get_status
  - submit_patch
  - get_code_neighbors
  - search_similar_code
  - get_code_subgraph
  - agent_tool:
      config_path: sub_agents/code_analyzer.yaml
      skip_summarization: true
generate_content_config: !include configs/sampling.yaml
```

### 33.3 Official sample sampling

```yaml
temperature: 0.2
top_p: 0.95
max_output_tokens: 16384
thinking_config:
  thinking_budget: 4096
  include_thoughts: true
```

### 33.4 Official sample analyzer

```yaml
name: code_analyzer_agent
description: Analyzes repository source files and symbol graphs to locate root causes.
model: gemma-4-31b-it-qat-w4a16-ct
adapter: tool_lora
instruction: !include ../prompts/analyzer.md
tools:
  - read_file
  - search_similar_code
  - get_code_neighbors
  - get_code_subgraph
generate_content_config: !include ../configs/sampling.yaml
```

### 33.5 Official sample `eval_config.yaml`

```yaml
# Optional participant evaluation configuration for Stage 1 inference.
# Controls per-task execution budgets and sandbox command timeouts.
evaluation:
  timeout_seconds: 60
  max_tool_calls: 10
  max_time_minutes: 1
  max_turns: 50
```

Important: these are **starter/demo budgets**. The official Getting Started notebook itself demonstrated tasks timing out under them. Do not interpret them as Google’s recommendation for a competitive final agent.

The sample also demonstrates that the official bundle itself used `../` includes from `sub_agents/` despite the current HARNESS prose saying path traversal components are blocked. This strengthens H26: test the exact compiler semantics rather than inferring from prose alone.

---

## 34. Useful official/local CLI patterns to preserve

### 34.1 Local evaluation pattern from `HARNESS_README.md`

```bash
swegemma eval \
  --tasks tasks.jsonl \
  --snapshots-dir snapshots \
  --submission-dir sample_submission \
  --results-dir results/run_01 \
  --sandbox docker \
  --max-tool-calls 50 \
  --max-time-minutes 30 \
  --concurrency 2 \
  --display auto
```

Useful flags:

- `--task-id` / `--task-ids` for focused debugging;
- `--sandbox docker|subprocess`;
- `--concurrency N`;
- deterministic `--shard-index` / `--num-shards`;
- `--models-yaml` for local model aliases;
- `--skip-agent-patch` for baseline verifier behavior.

### 34.2 Expected results tree

```text
results/run_01/
├── summary.json
├── task_results.jsonl
├── patches/
│   └── <instance_id>.patch
├── test_outputs/
│   └── <instance_id>.log
├── traces/
│   └── trace_<instance_id>.json
└── logs/
    └── <instance_id>.log
```

These artifacts are enough to build a serious failure-analysis pipeline. Do not rely only on terminal output.

### 34.3 Deterministic ZIP recommendation

Use sorted file order and a fixed ZIP timestamp/permissions when packaging. The public control notebook used `ZipInfo(..., date_time=(1980,1,1,0,0,0))`, DEFLATE, and fixed external file mode. This avoids different SHA hashes from filesystem mtimes and makes “same artifact” scientifically meaningful.

---

## 35. Versioned interpretation of the newest forum posts supplied immediately before this handoff

These are important because they were posted around **Sep 30–Oct 1, 2026**, i.e. close to this snapshot rather than weeks earlier.

### 35.1 Submission 56722466 — no score / `Notebook Threw Exception`

A participant reports that submission **56722466**, timestamped 2026-10-01 00:00 UTC, ended with `Notebook Threw Exception` and no score. Their agent had **no LoRA adapters**, passed the official compiler, and had run cleanly in a 4×L4 lab under the official harness before the prior day’s update.

Interpretation: this weakens any simplistic theory that all recent scorer crashes are LoRA-only. It supports our decision to classify scorer exceptions separately from agent capability.

### 35.2 Same exact ZIP failed twice within minutes after the Sep 30 update

Another participant reports an exact same ZIP/notebook pair failing around 3 minutes and 1 minute respectively, both as COMPLETE with no score, after Sep 30/Oct 1 changes. They state:

- notebook only decodes an embedded ZIP, checks SHA, writes `/kaggle/working/submission.zip`;
- notebook runs CPU-only/no internet;
- bundle differs from a previously scored one only in prompt text/comments;
- no adapters;
- all four supported `eval_config` keys only;
- same ZIP ran 40 public tasks end-to-end on a 4×L4 lab before submission.

They speculate about scorer changes but do not prove a cause. **Do not adopt their speculation as fact.** The valuable evidence is repeatability of a scorer-side/no-score failure on an otherwise locally working artifact.

### 35.3 Smaller local E4B/E2B leaderboard suggestion

A participant asked whether smaller Gemma models could get a separate leaderboard. This is only a suggestion. The official single-model rule remains the source of truth until organizers announce a change.

---

## 36. Dataset-download completion checklist for the user’s Mac

When the download reaches 22.42 GB, do not immediately start a giant run. First execute a read-only inventory. A future assistant can prepare commands adapted to the actual download path, but the logical checklist is:

```text
[ ] identify root path
[ ] du -sh dataset_root
[ ] find dataset_root -type f | wc -l
[ ] list top-level entries
[ ] hash HARNESS_README.md and tasks.jsonl
[ ] count tasks.jsonl lines / parse JSON safely
[ ] count snapshots / graphs / embeddings / wheels
[ ] list wheel versions
[ ] inspect sample_submission file tree and hashes
[ ] check graph/embedding zero-byte or <=100-byte files
[ ] check free disk space after download
[ ] DO NOT duplicate 22 GB into the Git repo
```

Because the Mac has a 1 TB SSD, storage is not the primary constraint. Maintain enough free space for extracted snapshots, Docker images, run traces and temporary environments; those can consume substantially more than the original compressed dataset.

---

## 37. Proposed artifact naming/versioning convention

Use names that make lineage obvious:

```text
agent-b000-official-control.zip
agent-b010-public-dual-exact.zip
agent-b011-current-compat.zip
agent-e020-edit-robustness.zip
agent-e030-localization-graph-ablation.zip
...
```

For each artifact write a sidecar manifest:

```json
{
  "artifact": "agent-b011-current-compat.zip",
  "parent": "agent-b010-public-dual-exact.zip",
  "git_commit": "...",
  "zip_sha256": "...",
  "created_utc": "...",
  "hypothesis": "...",
  "changed_files": ["..."],
  "wheelhouse_fingerprint": "...",
  "model": "gemma-4-31b-it-qat-w4a16-ct"
}
```

A score with no artifact identity is not a useful experiment.

---

## 38. Proposed first safe GPU smoke sequence

Once Step 0 static work is ready and a GPU is available:

1. Verify GPU model and VRAM with `nvidia-smi`.
2. Record CUDA/driver/PyTorch/vLLM versions.
3. Start the exact model with the official parser/template settings.
4. Run a simple text-only generation to prove server health.
5. Run one declared tool-call fixture.
6. Run one reasoning-continuation fixture (H01/H02).
7. Run one public task with a known simple profile.
8. Stop and inspect trace before launching multiple tasks.
9. Only after clean traces run 3–5 tasks.
10. Do not burn Colab compute units on 129 tasks until the candidate is worth it.

This sequence is more valuable than immediately benchmarking the entire training set, because a systemic tool/reasoning bug would otherwise waste all compute.

---

## 39. Public leaderboard interpretation guardrails

At this snapshot the visible top score was 0.17. A low, discrete resolution-rate leaderboard creates several traps:

- one additional solved task can visibly move rank;
- the public split is only part of the hidden test set;
- a change can improve public score through split-specific luck and hurt private performance;
- scorer outages can yield no score independent of agent quality;
- same/byte-identical artifacts have been reported at different public scores in public notebook evidence.

Therefore the target is **not** “copy whatever is currently #1.” The target is a robust general agent that systematically turns common failure classes into resolved tasks on a locked local holdout, then validates that capability on Kaggle.

---

## 40. Handoff integrity note

This handoff was generated from the user-supplied official rules, current `HARNESS_README.md`, screenshots, forum text pasted into the chat, and the set of public Kaggle notebooks uploaded by the user. Where a statement came only from a participant notebook/forum post, it is labeled as community evidence or an unresolved issue. Where two sources conflict, the conflict is intentionally preserved.

The document should be treated as a **living engineering record**. When the completed local dataset or a new wheelhouse changes facts, append a dated correction with evidence rather than silently deleting the old observation.
