# gemma4-agent-lab

Research and engineering workspace for **Google – The Gemma 4 Developer Agent Competition**.

**Current phase: STEP 0, Competition Environment & Harness Certification.** No agent optimization, no Gemma runs, no LoRA.

## Data boundary

| Kind | Where | In git? |
|---|---|---|
| Immutable competition data (21 GB) | `$GEMMA4_DATASET_ROOT` (outside this repo, read-only) | never |
| Research, config, code, metadata | this repo | yes |
| Generated runs, traces, ZIPs, raw cert output | `artifacts/`, `harness_cert/results/` | never (any extension) |
| Curated certification summaries | `harness_cert/reports/` | yes |

```bash
export GEMMA4_DATASET_ROOT="/Users/izbassar/Documents/Projects/gemma competition/gemma-4-developer-agent"
```

## Commands

```bash
python3 -m tools.inventory_dataset   # -> vendor_meta/dataset_manifest.json, source_hashes.json   (~1 s)
python3 -m tools.wheelhouse          # -> vendor_meta/wheelhouse_manifest.json (H23)
python3 -m tools.preserve_sample     # agents/baseline_v0_official <- sample_submission (refills gitignored adapters), tree-hash verified
python3 -m tools.build_split --check # verify the frozen locked-holdout split eval/splits/v1.json
bash harness_cert/scripts/git_semantics_probe.sh
uv run --group dev pytest            # fast tests; temp fixtures only, never touch the dataset
```

All tools are stdlib-only and deterministic: a rerun gives byte-identical JSON. Every writing tool goes through `tools.common.guard_output`, which refuses any destination at or below the dataset root (symlinks and aliases included). Traversal never follows symlinks.

## Map

- `docs/MASTER_HANDOFF.md`: full background (copy of the 2026-10-01 handoff)
- `docs/competition/`: `official_facts.md`, `issue_ledger.md`, `decisions.md`, `H23_WHEELHOUSE_FINGERPRINT.md`, `STATIC_HARNESS_AUDIT.md`
- `docs/experiments/EXPERIMENT_PROTOCOL.md`: what every experiment must record
- `harness_cert/matrix.yaml`: H01–H30 status
- `agents/baseline_v0_official/`: byte-exact official sample (control; never edit)
- `eval/failure_taxonomy.py`: failure categories, separating platform from agent failures
- `experiments/registry.jsonl`: experiment ledger (line 1 is a schema example)
- `eval/splits/`: frozen v1 dev/holdout split + `SPLIT_POLICY.md`
- `harness_cert/reports/`: curated certification summaries (raw output in `results/` is never committed)
