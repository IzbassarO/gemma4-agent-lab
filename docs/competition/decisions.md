# Decisions

Append-only. Each entry: date, decision, reason, and what would reverse it.

### D001 — 2026-10-02 — External data via `GEMMA4_DATASET_ROOT` only
The dataset stays outside the repo and is read-only. Code resolves it via `tools.common.dataset_root()`, and generated metadata records the env-var name, never the absolute path. Local default:
`/Users/izbassar/Documents/Projects/gemma competition/gemma-4-developer-agent`.
*Reverse if:* never.

### D002 — 2026-10-02 — Snapshots get a partial hash, not a full hash
The 129 snapshots (21.5 GB) are recorded with size + sha256(size, first 1 MiB, last 1 MiB). Graphs and embeddings (0.89 GB) and every small file are fully hashed.
*Reverse if:* a snapshot integrity question arises. Full hashing is ~20 s of SSD I/O and easy to add.

### D003 — 2026-10-02 (rev. same day) — Official sample preserved byte-exact; adapters stay gitignored
`agents/baseline_v0_official/` is a byte-exact copy of `sample_submission/` (tree sha256 `4557fcf4…`). The two adapter `.safetensors` (217,672 B each, identical to the dataset source, sha256 `dcbedd98…`) and their `adapter_config.json` exist on disk but stay **gitignored**. Redistribution terms for competition data were not verified locally: the handoff's "Competition Use and Commercial Apache 2.0" summary was not checked against the rules text, and the repo's eventual visibility is unknown.
**Restoration requirement:** after cloning, run `python -m tools.preserve_sample` with `GEMMA4_DATASET_ROOT` set. It refills missing files from the dataset (never overwrites) and fails unless the tree hash matches. A narrow, tested allowlist for exactly the four adapter files is prepared, commented out, in `.gitignore`.
The same redistribution question applies to the six non-weight sample files already git-eligible, and to sample excerpts in `docs/MASTER_HANDOFF.md`.
*Reverse if:* the user confirms the data terms permit committing competition files to this repository.

### D004 — 2026-10-02 — Static audit does not substitute community snippets for source
Harness source is absent locally (L01/L02). We record NOT AVAILABLE rather than reconstruct implementations from notebook excerpts.
*Reverse if:* harness distributions are captured from a Kaggle runtime (H23 next step).

### D005 — 2026-10-02 — Repo tooling is stdlib-only (amended by D010: PyYAML is a runtime dependency of the submission validator)
`pytest` and `pyyaml` are dev-only. The harness is not a dependency of this repo. It will be referenced by a separate env var once captured.
*Reverse if:* a certification script needs the harness importable, in which case it goes in its own venv.

### D006 — 2026-10-02 — Locked holdout split v1 frozen
`eval/splits/v1.json` (sha256 `420e35ef…`): 80 dev / 49 holdout, grouped by base_commit, repo-proportional, seeded-hash order, inputs = instance_id/repo/base_commit only. HTTPX's single task is in dev, so the holdout has no unseen-repo stratum. Rules: `eval/splits/SPLIT_POLICY.md`, `EXPERIMENT_PROTOCOL.md`.
*Reverse if:* only through a v2 with a methodological reason; never for score.

### D007 — 2026-10-02 — Canonical manifests are content-only
`vendor_meta/*.json` contain logical sizes and content hashes only. Allocation sizes, `.DS_Store` counts, mtimes and absolute paths were removed, so identical bytes give identical JSON on any filesystem. No machine-specific diagnostics file is kept (nothing needed it).
*Reverse if:* a real need for physical stats appears; then add a separate, non-canonical, gitignored diagnostics file.

### D008 — 2026-10-02 — One output guard for every writing tool
`tools.common.guard_output` (resolve + samefile on every existing ancestor; always also protects `$GEMMA4_DATASET_ROOT`) runs before any directory creation or file open. Writes are atomic (temp file + `os.replace`). Traversal never follows symlinks: they are recorded as metadata (inventory) or rejected (sample copy, tree hashing).
*Reverse if:* never.

### D009 — 2026-10-02 — Raw certification results are never committable
`harness_cert/results/` is ignored entirely, whatever the extension. Curated summaries live in `harness_cert/reports/`.
*Reverse if:* never.

### D010 — 2026-10-02 — Submission pipeline policies
- ZIPs are `ZIP_STORED`. Deflate output depends on the zlib build, and byte identity across machines matters more than archive size. The ZIP limit that matters (< 3 GiB) is on *unpacked* bytes.
- Packaging is an allowlist: root config, `eval_config.yaml`, the five contract dirs, plus referenced files. Other files are excluded with a warning, so READMEs/notes never ship.
- Certification issues never block a build; they are recorded in the manifest and provenance. Structural errors always block.
- `!include` / `config_path` are resolved file-relative first, then root-relative, and every divergence is reported under H26. New candidates keep referencing YAML at the bundle root and avoid `../`.
- PyYAML is now a runtime dependency (`pyproject.toml`).
*Reverse if:* H23/H26 capture shows the compiler differs; then align the validator to observed behavior.

### D011 — 2026-10-02 — Submission re-audit corrections
- Write safety is an explicit immutable `WriteGuard` passed to every writing step. Writing CLIs fail closed without a dataset root; `$GEMMA4_DATASET_ROOT` is additive protection, never the only one.
- `--require-clean` is a per-file proof (tracked, in HEAD, index = HEAD, working-tree bytes = HEAD blob) plus a clean repository, with no override. E0's gitignored official adapters are labelled `external-official-control`, and E0's `source_in_git` is false.
- Provenance verification recomputes facts from the ZIP, manifest, repository metadata and git objects. Unavailable environments are UNVERIFIABLE, not failures.
- The split identity is sha256(`eval/splits/v1.json`). The sidecar is only cross-checked, and a stale sidecar fails generation and verification.
- Archive policy also forbids comments, extra fields, non-canonical paths and normalized (path/Unicode/case) duplicates.
- `.safetensors` must pass a container sanity check, which proves SAFETENSORS_CONTAINER_VALID only.
- Static validation implements the documented agent/generation schema and limits; undocumented fields are warnings.
*Reverse if:* H23/H26 capture shows the compiler differs; align to observed behavior and record it here.

### D012 — 2026-10-02 — Verification evidence is tri-state
Every verification fact is `ok`, `mismatch` or `unverifiable`, and git evidence aggregates to VERIFIED / FAILED / UNVERIFIABLE (or NOT_CLAIMED). Unavailable commits or tree objects are never coerced into a failure, and observable contradictions are never downgraded to unverifiable. Overall: FAILED / VERIFIED_WITH_UNVERIFIABLE_EVIDENCE / VERIFIED (exit 1 / 0 / 0; `--strict` makes the middle state exit 2).
*Reverse if:* never.
