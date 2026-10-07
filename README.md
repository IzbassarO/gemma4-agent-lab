# gemma4-agent-lab

Research and engineering workspace for **Google – The Gemma 4 Developer Agent Competition**.

**Current phase: competitive optimization, DEV forensics.** H28 E0 scored 0.05 on Kaggle (D014). Next run: `EXP-20261007-001` DEV-E0-FORENSICS-V1-S1 ([preregistration](docs/experiments/DEV_E0_FORENSICS_V1_S1_PREREG.md)), blocked on the [real-runtime admission tranche](docs/experiments/REAL_RUNTIME_TRANCHE_2026-10-07.md). No LoRA yet; the operator launches every model/GPU run.

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

### Submission pipeline (see `docs/competition/SUBMISSION_FORMAT.md`)

```bash
uv run python -m tools.validate_submission agents/candidates/<id>                 # static checks, --json for machine output
uv run python -m tools.build_submission agents/candidates/<id> --experiment-id EXP-... --require-clean
uv run python -m tools.inspect_submission artifacts/submissions/<id>/submission.zip
uv run python -m tools.hash_submission artifacts/submissions/<id>                 # VERIFIED / VERIFIED_WITH_UNVERIFIABLE_EVIDENCE / FAILED
uv run python -m tools.package_official_control --dataset-root "$GEMMA4_DATASET_ROOT"  # E0: restore + validate + package the official sample
```

Builds go to `artifacts/submissions/<id>/` (gitignored) and are byte-identical for identical sources. Harness compatibility stays `CURRENT_HARNESS_COMPATIBILITY_UNKNOWN` until H23/H26 are resolved.

Existing inventory/submission tools are deterministic (stdlib only, except PyYAML for the submission validator): a rerun gives byte-identical JSON. Their writing steps use `tools.common.WriteGuard`, which refuses destinations at or below the dataset root (symlinks and aliases included). Traversal never follows symlinks.

### H23 protocol v4 capture checkpoint

[H23_PROTOCOL_V4.md](docs/competition/H23_PROTOCOL_V4.md) defines the new untrusted
observation archive, technical importer, external receipt and separate human
attestation. Schema 3 is rejected/historical; there is no compatibility reader.
The importer publishes only beneath an existing private external evidence root,
using descriptor-relative protected-root checks and atomic no-replace storage.
Its results are `CAPTURE_VALIDATED` / `CAPTURE_REJECTED`, without origin or
installation promotion. The [2026-10-03 real Kaggle Version 3 capture](harness_cert/reports/H23_KAGGLE_CAPTURE_2026-10-03.md)
(`scriptVersionId=354974356`, wheelhouse v28) was `CAPTURE_VALIDATED`, with
`CONFIRMED` durability and no warnings. Bootstrap returned `EXIT_NONZERO`
(code 1), so successful capture does not establish successful bootstrap or
reproduction of the official evaluation stack. H23 remains **HOST-UNKNOWN**;
the hidden scorer remains **SCORER_ONLY_UNKNOWN**. The bootstrap cause is
confirmed: the interactive CPython 3.13.15 runtime rejected the cp312
`apache_tvm_ffi` wheel. The local Python 3.12.14 compiler subset is now verified
and [H26 include-path checks](harness_cert/reports/H26_INCLUDE_PATHS_2026-10-03.md)
are **PASS**. Step 0 remains incomplete pending acquisition and verification
of the remaining CPU harness subset and its scripted-mock certification checks.

```bash
python -m tools.build_h23_v4_notebook --check   # exact reviewed source embedding, no execution
python -m pytest                             # existing suite plus literal v4 fixtures
```

## Map

- `docs/MASTER_HANDOFF.md`: full background (copy of the 2026-10-01 handoff)
- `docs/competition/`: `official_facts.md`, `issue_ledger.md`, `decisions.md`, `H23_WHEELHOUSE_FINGERPRINT.md`, `STATIC_HARNESS_AUDIT.md`, `SUBMISSION_FORMAT.md`, `SUBMISSION_READINESS.md`
- `docs/experiments/EXPERIMENT_PROTOCOL.md`: what every experiment must record
- `harness_cert/matrix.yaml`: H01–H30 status
- `agents/baseline_v0_official/`: byte-exact official sample (control; never edit)
- `eval/failure_taxonomy.py`: failure categories, separating platform from agent failures
- `experiments/registry.jsonl`: experiment ledger (line 1 is a schema example)
- `eval/splits/`: frozen v1 dev/holdout split + `SPLIT_POLICY.md`
- `harness_cert/reports/`: curated certification summaries (raw output in `results/` is never committed)
