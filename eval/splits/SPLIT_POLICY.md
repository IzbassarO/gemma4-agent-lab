# Split Policy — v1 (FROZEN)

| | |
|---|---|
| File | `eval/splits/v1.json` |
| Identity | `eval/splits/v1.sha256` (`shasum -a 256 -c v1.sha256` from this directory) |
| sha256 | `420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12` |
| Generator | `python -m tools.build_split` (verify only: `--check`) |
| Frozen | 2026-10-02 |

## Inputs (non-gold only)

The generator parses each `tasks.jsonl` line and **immediately projects it onto `instance_id`, `repo`, `base_commit`**. Nothing else reaches the algorithm: no `patch`, `test_patch`, `problem_statement`, `hints_text` or `created_at`, and nothing derived from them, including any notion of difficulty.

Tests prove the output is byte-identical when gold fields are changed or removed, and when other non-gold fields change (`tests/test_split.py`). `created_at` was allowed but is not used; no temporal claim is made.

`v1.json` contains only `instance_id`, `repo`, `base_commit` and `split` per task, plus aggregate counts and the algorithm description. `input_projection_sha256` identifies the exact projected input.

## Algorithm (fixed)

1. **Group** tasks by `base_commit`. A group never crosses dev/holdout.
2. **Per-repo holdout quota**: largest-remainder apportionment of `round(129 × 49/129) = 49` holdout slots, in proportion to repo size. Ties are broken by repo name.
3. **Order** each repo's groups by `sha256("gemma4-agent-lab/split/v1:" + base_commit)` (commit hex as the tiebreak). The order is version-independent and needs no RNG.
4. **Greedy fill**: in that order, a group goes to holdout if it fits in the repo's remaining quota; otherwise it goes to dev.

## Result

| | dev | holdout | quota |
|---|---:|---:|---:|
| fastapi/fastapi (67) | 41 | 26 | 26 |
| Textualize/rich (48) | 30 | 18 | 18 |
| psf/requests (13) | 8 | 5 | 5 |
| encode/httpx (1) | 1 | 0 | 0 |
| **total (129)** | **80** | **49** | 49 |
| unique base commits (127) | 79 | 48 | |

The exact 80/49 target was reachable without breaking any group. Both shared-commit groups were kept together:

- `requests_6589` + `requests_6629` → dev
- `rich_3882` + `rich_3894` → holdout

`base_commits_crossing_split` is `[]`; this is asserted by the tests.

### HTTPX (single task)

`httpx_3672` is the only `encode/httpx` task. Its proportional holdout share is 0.38, so apportionment gives httpx 0 holdout slots and the task is in **dev**. A one-task holdout stratum could not estimate anything, and in dev it at least exercises the harness on a fourth repository (e.g. its `src/` layout).

Consequence: the holdout has **no** unseen-repository stratum. It measures generalization to new tasks within fastapi, rich and requests only. Generalization to hidden or private repositories is **not** measured by v1.

### Known limitations

- Grouping uses `base_commit` only. Different commits touching the same code are not grouped, because detecting that would need gold or problem-text signals that are excluded by design.
- Repo proportions are preserved; nothing else is stratified (no difficulty, no file area).

## Freeze rules

- `tools.build_split` refuses to overwrite an existing `v1` with different content (`FrozenSplitError`).
- v1 is never reshuffled because results are disappointing.
- A `v2` needs a documented **methodological** reason, for example a dataset change detected by the inventory, or a leakage channel found. Score improvement is never a reason. v2 gets a new file, a new sha256, a new seed string and a `decisions.md` entry. Results on different split versions are not compared directly.
