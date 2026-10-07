# DEV data-boundary and manifest admission — 2026-10-06

**DATA BOUNDARY / MANIFEST LAYER READY FOR INDEPENDENT AUDIT.**
This tranche establishes typed data admission and screening membership. It does
not admit a runnable evaluator or authorize a real DEV solver run.

Baseline admission began with a clean repository. HEAD and origin/main were both
`9cfe1b0da77bdef035a136cafe530f369b060c06`. The committed competitive program
supplies the screening algorithm and exact memberships; the current request
narrows implementation to this layer and defers numerical promotion thresholds
until baseline variance is measured. Existing agents, H28/H30 certification,
experiment history and frozen split bytes are preserved.

## Contracts and trusted intake

`eval/solver_task.py` defines frozen, slotted `PublicAssetRef` and `SolverTask`.
An asset contains only kind, asset ID, safe source-relative locator, full SHA256
and byte size. A solver task contains schema version 1, instance ID, repository,
base commit, exact problem/hint text, required snapshot and optional graph and
embedding references. It has no reference fix, test patch, verifier outcome,
private locator or partition label. Direct construction and JSON decoding are
strict: unknown/duplicate keys, invalid identifiers, wrong types (including bool
integers), non-finite JSON and traversal paths fail. Serializers revalidate the
exact contract type and reject verifier objects, subclasses and malformed values.

`eval/verifier_task.py` is physically separate. `VerifierTask` contains schema
version, ID/repository/base commit, snapshot, `PrivateTestPatchRef(sha256,
size_bytes)`, verification-config SHA256 and optional immutable fail-to-pass and
pass-to-pass tuples. The current public source has neither test-node field, so
their absence is represented by `None`, never invented nodes. Reference solution
`patch` is unnecessary here. The private reference contains no storage path.

`eval/verifier_data.py` admits a caller-specified test-patch file from an explicit
private root only, matching its full hash/size and UTF-8 bytes to the verifier
reference. It requires a root owned by the current user with no group/other
permissions and a regular, singly linked 0600 evidence file. Its immutable
`VerifierMaterial` remains verifier-only. No inline public test-patch extraction,
private storage publisher or verification execution is implemented. This small
separate reader makes private file access reviewable without placing it in solver
intake. No reference-fix file is read.

`eval/public_data.py` is trusted coordinator intake, **not solver-worker code**.
It reads exactly `tasks.jsonl` beneath an explicit absolute public root and
projects each parsed row onto ID/repository/base commit/problem/hints. The public
source itself contains inline gold; parsing those mixed rows transiently in
trusted intake does not make the raw file safe to mount in an actor environment.
No raw row, gold field, private reference or open descriptor survives in a solver
view. Known source-only fields are discarded; unknown fields, duplicate IDs,
invalid values and missing requested assets fail closed. A directly supplied row
can be projected by the trusted admission primitive; routine screening uses
`load_solver_tasks`, which requires an admitted screen and defaults to S1.

The intake never imports the vendor evaluator or verifier modules, uses no
environment source fallback, searches no parent/sibling directories, and never
hydrates secret bundles. Selected asset names follow the existing inventory:
`snapshots/{instance_id}.tgz`, `graphs/{repo_short}_{base_commit}.json` and
`embeddings/{repo_short}_{base_commit}.npz`. Snapshot admission is mandatory;
requested graph/embedding assets must both exist and exceed the public code-intel
threshold of 100 bytes. Omitting code intelligence requires an explicit option;
missing requested assets never trigger fallback discovery.

## Identity and filesystem reuse

Canonical contract JSON uses the repository's sorted, indented UTF-8 encoding
with a trailing newline, after closed validation. Task identity is SHA256 of
those exact bytes. No host location or generation timestamp is added; legitimate
problem/hint text is preserved verbatim, including any paths in the issue text.
`eval/_contracts.py` shares these pure validators. A second JSON schema would
duplicate the executable closed schema, so none is added.

The read path reuses `tools.h23_v4.filesystem.anchor_directory/read_regular`:
component-wise nofollow directory opens, nonblocking regular-file admission,
hard-link rejection, same-descriptor hashing/size, change detection and scoped
descriptor cleanup. Explicit roots stay lexical; intake does not resolve away
symlink evidence. Public assets are streamed without retaining their payloads.
Source/test-patch reads are bounded to 32 MiB, public assets to 8 GiB, manifest
JSON to 512 KiB and sidecars to 512 bytes.

Every `PublicAssetRef.sha256` is a **full** content hash produced from every
byte. Snapshot inventory `partial_sha256(size+first/last 1MiB)` remains a separate
historical inventory identity and is never substituted. Tests mutate a snapshot's
middle bytes while its partial identity remains unchanged and show that admitted
full identity changes. `tools.common` supplies canonical JSON/hash helpers and
`WriteGuard` for screening publication; the inventory's code-intel threshold is
reused. The split generator was inspected but is not invoked to regenerate v1.

## Frozen split and screens

Read-only admission checks the pinned v1 bytes and exact sidecar, closed metadata
schema, all 129 unique IDs, complete source membership/projection, 80 tune and 49
holdout counts, repo counts, 127 unique base commits, and absence of cross-repo
or cross-partition base-commit groups. `dev` in frozen v1 means tune here; v1 is
never rewritten. Admission handles are immutable and obtained through validating
factories. The normal screen API exports only tune metadata and rejects S4.

| Identity | SHA256 |
|---|---|
| Published `tasks.jsonl` | `e4b3fd60f69dbc2b9213e54eeb9636db78aefe92c1d06269d73d9f5f8f3c8ad6` |
| Frozen `eval/splits/v1.json` | `420e35ef6c6a249527bded93f4eee4d47aa8d1453b7a9a14aa80367f9cd05a12` |
| Non-gold input projection | `5a6188365392b2febde6d3067f5fea9e8c5dddcd7389a6e02a87e4333245df30` |
| `eval/splits/screens_v1.json` | `a6bdb7e9f99c3cbfd59be4130884c3dadcd99d4cfb6a91260bbfcdb1f71832f7` |

Split seed: `gemma4-agent-lab/split/v1`. Screening seed:
`gemma4-agent-lab/screen/v1`, algorithm version 1. Screens group existing tune
tasks by base commit within each repo, order by SHA256(seed:repo:base_commit),
greedily fit whole groups to exact quotas, then sort selected task IDs. The
result exactly matches the committed competitive plan's Appendix A.

| Stage | FastAPI | Rich | Requests | HTTPX | Total | Newly added tasks |
|---|---:|---:|---:|---:|---:|---:|
| S1 | 6 | 4 | 1 | 1 | 12 | 12 |
| S2 | 20 | 15 | 4 | 1 | 40 | 28 |
| S3 | 41 | 30 | 8 | 1 | 80 | 40 |
| Existing holdout, not a screen | 26 | 18 | 5 | 0 | 49 | — |

The shared canonical manifest and sidecar bind all three screens to the parent
split, source identity, seed and algorithm. Each task record contains ID, repo,
base commit, `partition: tune` and group key. No outcomes, issue text, generation
timestamp or S4 membership is added. This is coordinator selection metadata;
it is not passed into a solving agent. Solver-facing selection metadata is only
ID/repo/base commit, and solver task serialization omits partition and source
file hashes.

For optional independent checks, SHA256 of canonical JSON for each
`stages[stage]` selection record (IDs/count/repo counts, not a separate file or
asset bundle) is:

- S1: `7c74e4b92ae762f6e6929cf046d5876d6b8dc4fc64c488d1ccc072df34dff976`
- S2: `49a6d3fc5d226a6c17c7741b435ba0c0a49f3bffd61123a4765f96d586e33b96`
- S3: `9d40cc5614e5e55810502d026344853e10e7b53b7754b832e688f5c5f42810b1`

`S1 ⊂ S2 ⊂ S3`. Future stage reports must distinguish `S2-new = S2 - S1`
and `S3-new = S3 - S2`; reused tasks are not independent new confirmation.
Only membership is implemented. There are no +1/+2/+3 promotion rules or
candidate decisions. Baseline variance measurement must precede fixed gates.

## Threat model and validation

Synthetic tests exercise wrong-type interchange, unknown/duplicate keys,
gold/test-node/private-path sentinels, canonical identities, traversal, missing
assets, symlinks/hardlinks/FIFOs, split/source/hash mutation, group crossing,
holdout injection, nesting and seeded-membership mismatch. File-open/read spies
fail if intake attempts planted sibling/ancestor secret or reference-fix paths,
or consumes an existing private descriptor. Internally opened descriptors are
noninheritable and close on success/failure. Import tests confirm no vendor or
verifier import on the solver intake path.

An integrated synthetic 129-row source uses v1's non-gold IDs and harmless assets
to exercise the real admission factories and all three screens. Only selected
tune assets are opened, sentinel values never reach solver views or selection
metadata, and source drift is rejected before asset reads. Its source-hash pin
is fixture-local; production source pins are unchanged. Actual public-source
metadata/hash admission independently passed with the identities above. No real
solver task was executed and no real verifier evidence was loaded.

Validation environment: Python 3.14.7, pytest 9.1.1, CPU-only. New targeted tests:
**240 passed in 0.57 s**. Full short CPU suite: **1919 passed in 21.32 s**
(previous 1679 plus 240 new tests). Both runs used
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p no:cacheprovider`,
with the four new test paths supplied for the targeted run. `git diff --check`
passed; whitespace checks also covered every new untracked file. All 118
pre-existing tracked files remained byte-identical. No benchmark/result artifacts
were created, and the registry still contains only its existing schema example.

Read-only metadata verification (no asset admission or solver execution):

```bash
.venv/bin/python -B - <<'PY'
from pathlib import Path
from eval.public_data import load_public_metadata, tasks_source_sha256
from eval.manifests import verify_frozen_split, load_screen_manifest, select_screen
root = Path('/Users/izbassar/Documents/Projects/gemma competition/gemma-4-developer-agent')
split = verify_frozen_split(load_public_metadata(root), tasks_sha256=tasks_source_sha256(root))
screen = load_screen_manifest(split)
print(split.split_sha256, screen.sha256)
print({stage: len(select_screen(screen, stage)) for stage in ('S1', 'S2', 'S3')})
PY
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -p no:cacheprovider tests/test_dev_task_contracts.py tests/test_dev_public_data.py tests/test_dev_manifests.py tests/test_dev_leakage.py
```

## Remaining admission and scope

Typed contracts are an API boundary, not an operating-system security boundary.
Future solver workers must receive only serialized `SolverTask` plus staged,
hash-verified public assets. They must have no raw dataset, verifier storage,
secret environment, inherited private descriptor, shared private cache or
coordinator-memory access. Workers must start separately rather than inherit
trusted mixed/gold memory. Shell and analyst access need the same confinement.
This tranche does not implement or claim those process-level controls. Snapshot
extraction safety and real selected-asset staging/hash receipts also require
future runtime admission; the full-hash admission primitive is tested here on
synthetic assets only.

No solver/verifier runner, model endpoint/inference, registry migration/backfill,
scheduling/resume, result artifacts, failure automation, graph metrics, holdout
execution/credits, agent/prompt/budget changes, training/LoRA or Kaggle submission
is implemented. No dataset mutation, commit or push is performed.

Next gate: **Claude Code — independent read-only audit of DEV data-boundary /
manifest tranche**. After approval, separately scope worker confinement, faithful
fresh-sandbox verification and runtime admission with synthetic tests. A later
real DEV run additionally needs explicit authorization, exact model/runtime
admission and preregistration. Holdout execution remains behind its future
explicit admission/credits system.
