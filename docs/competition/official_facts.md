# Official Facts

Only facts with an authority label. When sources disagree, the higher label wins, and the conflict is logged in `issue_ledger.md`.

## Dataset (REPRODUCED-LOCAL, `vendor_meta/dataset_manifest.json`, 2026-10-02)

| Fact | Value |
|---|---|
| Files (excl. `.DS_Store`) | 524 regular files, 0 symlinks/special files |
| Logical bytes | 22,416,766,904 (= Kaggle's "22.42 GB", decimal) |
| `HARNESS_README.md` sha256 | `3d6e57a13234cb4e783ba24eaab486459af76e0923ea4c6607cd41cc8961bbbb` (matches the handoff snapshot) |
| `tasks.jsonl` sha256 | `e4b3fd60f69dbc2b9213e54eeb9636db78aefe92c1d06269d73d9f5f8f3c8ad6` |
| Tasks | 129 rows, 129 unique IDs, 127 unique base commits, 0 bad lines |
| Repos | fastapi/fastapi 67 · Textualize/rich 48 · psf/requests 13 · encode/httpx 1 |
| Task keys | `instance_id, repo, base_commit, problem_statement, hints_text, patch, test_patch, created_at` (no F2P/P2P fields) |
| Non-empty hints | 0 |
| `created_at` range | 2023-07-29 → 2026-06-20 |
| Snapshots | 129 `.tgz` (one per `instance_id`), 1.58 MB – 334.9 MB, 21.50 GB total |
| Graphs / embeddings | 127 / 127, keyed `{repo_short}_{base_commit}`; 0 zero-byte; all > 100 B; every task covered |
| Wheels | 124, sandbox test deps only (see H23) |
| Official sample bundle | 10 files, 442,238 B, tree sha256 `4557fcf418a85aa41c0ad560a9951f05a2726e69b249850aa9607e52c3736e2c` |

## Canonical identities (REPRODUCED-LOCAL; regenerate and compare before trusting)

| Artifact | sha256 of file |
|---|---|
| `vendor_meta/dataset_manifest.json` (schema v2) | `d23eaa3ef26c53da1b48955547147d6f91625463629eafc451881299b2d51d6f` |
| `vendor_meta/source_hashes.json` | `a863961bb93f38d424bad628fd999e3775f5990a12c97999cf9fdc1199af09c2` |
| wheelhouse fingerprint (field inside `wheelhouse_manifest.json`) | `acb7649a9bfa1e5c5f8843423dee434d45f51df093070eeb7428182cf3ec5709` |
| `eval/splits/v1.json` | `420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12` |

Expected to reproduce on any machine with byte-identical data: all of the above. Not recorded anywhere, by design: allocation sizes, mtimes, owners, inodes, absolute paths, `.DS_Store` files.

## Harness contract (OFFICIAL-HARNESS = `HARNESS_README.md` unless noted)

- Competition model: `gemma-4-31b-it-qat-w4a16-ct`. All agents share one base model.
- Scorer: 4×L4, TP=4, `gpu_memory_utilization 0.80`, `max_model_len 32768`, `tool_call_parser gemma4`, `reasoning_parser gemma4`, `enable_thinking: True` by default, LoRA enabled (max 8, rank ≤128).
- Submission: declarative YAML only. Root config is exactly one of `agent.yaml` / `agent.yml` / `root_agent.yaml` / `root_agent.yml`. Unpacked size < 3 GiB. Allowed extensions `.yaml .yml .md .txt .py .json .safetensors`.
- `generate_content_config`: `max_output_tokens` 1–32768 (default 16384), `thinking_budget` 0–32768 (default 4096). The README says "use `0` or `include_thoughts: false` to disable thinking".
- 9 tools: `run_command, submit_patch, get_status, read_file, edit_file, write_file, get_code_neighbors, search_similar_code, get_code_subgraph`. `submit_patch` and `get_status` do not count toward `tool_calls`.
- Budgets (`eval_config.yaml` → `evaluation:`): `timeout_seconds`, `max_tool_calls`, `max_time_minutes`, `max_turns`. `inference.py` defaults: 60 min / 100 calls / 500 turns / 300 s.
- Limits: 5,000-char command output; `read_file` 150 lines / 10,000 chars.
- Patch = `git add -N . && git diff --binary _swegemma_baseline || git diff --binary HEAD`. A fallback runs if `submit_patch` is never called.
- Verification: fresh Container B, 4-pass apply, protected test/config reset, `test_patch`, pytest + JUnit. Resolved iff exit 0 and every required test explicitly passed.
- Metric: resolution rate over the evaluation split.
- Compaction (scorer-side): interval 5, overlap 2, threshold 14,336, retention 5. Context cache min 2,048 tokens. ModelRetryPlugin ×5.

## Sandbox image (OFFICIAL-HARNESS, `docker/Dockerfile.*`)

`python:3.13-slim` + git, patch, protobuf-compiler, pigz; pip `pytest`, `pytest-timeout==2.1.0`, `typer`, build backends; `imp.py` / `telnetlib.py` shims; `/wheels` ← dataset `wheels/` (public image).

## Rules (OFFICIAL-RULE; from the handoff, not re-verified in this step)

Team ≤ 5 · 1 submission/day · 2 final selections · Apache-2.0 winner license · final deadline 2026-12-02 23:59 UTC. Re-check the Kaggle rules page before relying on any of these.

## Unknown (do not fill from forums)

Scorer harness package versions (H23) · global time/concurrency semantics (H22) · hidden repo composition · whether the scorer's `/wheels` and cached site-packages equal the local `wheels/`.
