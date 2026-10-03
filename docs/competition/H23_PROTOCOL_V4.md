# H23 capture protocol v4 — candidate for independent audit

This is a clean architecture reset on `feat/h23-protocol-v4`. Schema 3 was
rejected and is historical on `archive/h23-schema3-rejected`. V4 has no legacy
reader, conversion, compatibility mode, source-shaped extraction, installation
attribution engine, or automatic origin promotion. H23 remains **HOST-UNKNOWN**.
No real Kaggle capture, model run, GPU execution, or H26 certification occurred.

## Three separate layers

1. **Capture** produces an untrusted observation archive. A hash commits to bytes;
   it does not establish that the reported remote observation happened.
2. **Import** returns only `CAPTURE_VALIDATED` or `CAPTURE_REJECTED`.
   `CAPTURE_VALIDATED` means technical validation plus committed publication:
   this exact archive conforms to policy, required included bytes were verified,
   supplied relationships are internally consistent, and its complete evidence
   bundle was atomically published or exactly matched existing committed bytes.
   A fabricated local archive can pass. Faithfully represented pip failure can
   pass. Power-loss durability is a separate confirmation, described below.
3. **Project review** may later associate a receipt with independently checked
   external run evidence and label that specific run
   `INTERACTIVE_KAGGLE_VERSION_SPECIFIC`. The importer has no such label or logic.
   The hidden scorer remains `SCORER_ONLY_UNKNOWN` regardless of that review.

## Archive and authoritative manifest

`h23_capture_v4_<YYYYMMDDTHHMMSSZ>.zip` contains exactly one `manifest.json` and
zero or more `blobs/<64 lowercase hex SHA256>`. Every blob is independently
hashed. All members must be regular files; no directories, links, arbitrary
paths, README, index, logs, raw METADATA, raw direct URLs, or environment dumps
are allowed. Only stored/deflated, unencrypted members are accepted. Producer
permissions are ignored when storing evidence.

The supported canonical **structural** envelope is non-ZIP64 and single-disk:
contiguous local file headers and their exact compressed payload extents start
at byte zero, followed immediately by a contiguous central directory in the
same member order, followed by exactly one final 22-byte EOCD. The EOCD has zero
comment length, disk numbers zero, equal per-disk/total entry counts, and exact
central-directory offset/size. There are no gaps, preamble or trailing bytes.
Every local filename matches its central filename byte-for-byte; extraction
version, flags, compression, timestamps, CRC32 and both sizes must agree.
Flags are zero; stored members use extraction version 10 or 20, and deflated
members use version 20. Stored compressed/expanded sizes are equal. A deflated
payload is exactly one complete raw-DEFLATE stream: truncation, concatenated
streams and unused compressed suffix bytes reject. Actual expanded size and
CRC32 are checked, and every blob is independently SHA256-hashed.

Archive/member comments, central **and raw local** extra fields, encryption,
data descriptors, multi-disk forms, ZIP64 records/extra fields and unsupported
flags/versions/compression reject. Forbidden bytes are never sanitized. A
bounded raw EOCD/central/local preflight checks the declared member limit and
the actual bounded walk **before constructing ZipFile**; declared counts alone
cannot authorize malformed structure. Normal ZIP parsing must agree with the
preflight count, and bounded exact payload decoding verifies the remaining facts.

The capture builder emits a deterministic instance of this profile: manifest
first, sorted content-addressed blobs, fixed 1980 timestamp, Unix regular mode
0600, version 20, deflate level 9, zero flags/comments/extras and ZIP64 disabled.
The importer also permits structurally conforming stored members; producer
timestamp/permission values confer no trust or stored execution permission.

The exact manifest fields are:

| Field | Typed observation |
|---|---|
| `protocol` | Literal `H23_CAPTURE_PROTOCOL_V4`; any other value rejects |
| `captured_utc` | Valid UTC date/time, `YYYYMMDDTHHMMSSZ` |
| `capture_program` | SHA256 of the exact capture runtime source commitments; nullable reported executed-notebook SHA256 |
| `interpreter` | Python version triple; supported scheme; prefix; unique site roots; one scripts directory; command executable |
| `bootstrap` | Recipe ID, ordered wheel references, argv array, executed boolean, nullable return code, output counts/hashes, fixed diagnostic |
| `wheels` | Dataset filename, installation filename, size, SHA256, nullable allowlisted metadata projection |
| `distributions` | Present target name/version, installation root, exact own dist-info name, metadata observation, direct-URL presence commitment, canonical console declarations |
| `record_evidence` | Exactly one original RECORD blob reference for each present target distribution |
| `file_observations` | Distribution, original RECORD path, regular-file kind, byte size, SHA256, link count, nullable payload reference |
| `payload_references` | Sorted unique blob IDs; importer independently derives the reference graph and requires exact agreement |

Missing targets are absent observations, not an asserted successful installation.
An archive with no present distributions can technically validate and reports
zero rows/payloads. It cannot establish source availability or resolve H23.
No producer classifications, completeness booleans, match counters, fingerprints
with unspecified scope, installation causality, human attestations, or origin
claims are accepted. Unknown fields and invalid types reject at every level.
JSON must be UTF-8; duplicate keys, nonfinite/floating values, NUL, unpaired
surrogates and excessive structure reject.

## Bootstrap and wheel commitments

The supported recipe `OFFICIAL_NOTEBOOK_CELL_2` denotes the package-bootstrap
invocation: all observed non-cutlass wheels, sorted by installation filename,
`[executable, "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall", ...]`.
Wheel arguments share one canonical absolute staging directory. The notebook
restores the stripped `+` before `cu128` in the version component. It supplies
the documented fixed bootstrap environment to that subprocess without
serializing environment values or changing the capture parent environment.
This recipe ID is not proof of an official notebook execution. V4 does not
delete arbitrary cutlass `.pth` files or reproduce subsequent model cells.

`NOT_RUN` requires executed=false, empty argv/references, null return code and
output hashes, zero output sizes and `NOT_RUN` diagnostic. An attempted recipe
requires executed=true; `EXIT_ZERO` means reported code=0, `EXIT_NONZERO` means
reported nonzero code, and `EXECUTION_ERROR` means reported code=null.
Attempted output hashes are required; zero-size output must commit to empty
bytes. Output is held only in anonymous local temporary files and hashed; no
output content, exception message, or tail enters the archive or receipt.

Wheel bytes are omitted. Their hashes, sizes and optional metadata projections
remain **reported commitments**. Each filename has five components, or six with
a digit-leading build tag: distribution, bounded version, optional build tag,
Python tag, ABI tag and platform tag. Tag alternatives are dot-separated ASCII
identifiers. Distribution identity uses the same lowercase/hyphen normalization
as metadata; versions use the bounded package-version grammar. The sole
supported source alias is a stripped final `cu128` local suffix restored to
`+cu128` in the installation filename. The exact supported rename is required;
invalid versions reject even when metadata is absent. A present projection has
required Name/Version matching the effective filename identity.

Capture checks every observed top-level wheel dist-info directory against the
same normalized distribution and exact bounded version, including when
METADATA is absent. A selected METADATA projection must agree with that identity;
`other-9.dist-info` cannot stand in for a swegemma wheel. Internal wheel paths
are not stored as a new manifest identity field. Since wheel bytes are omitted,
the importer cannot independently prove capture's reported wheel parsing was
truthful. These checks do not claim installation, independently rehash omitted
wheels, or recreate wheel-to-install causality from pip results or observations.

## Metadata projection and confidentiality boundary

Capture strictly parses original metadata but stores only raw SHA256/size and
the exact identity projection `Metadata-Version`, canonical `Name`, and `Version`.
Supported metadata versions: **1.0, 1.1, 1.2, 2.1, 2.2, 2.3, 2.4**. Duplicate known
singleton headers, malformed headers/continuations, unsupported versions and
identity contradictions reject. Package versions use a bounded ASCII grammar;
future unsupported syntax fails closed. `Requires-Python` has no H23 purpose and
is omitted without parsing its specifier value; duplicate raw singleton headers
still reject. Injecting `Requires-Python` or any other unsupported projection
field rejects under exact schema semantics. Description, URLs, authors,
dependencies and other arbitrary text are omitted. This is an allowlist
projection, not regex secrecy redaction.

`direct_url.json` stores only presence/type, original SHA256 and size. Raw bytes
are prohibited as payload. The metadata projection and entry-point declaration
are reported observations: since their original bytes are omitted, the importer
cannot independently prove their remote parsing was truthful. It verifies
their structure and linkage to supplied RECORD/file commitments.

Original RECORD and required source/resource/script bytes are retained for
review. Arbitrary source can contain secrets or unsafe code; validation does
not certify secret-freedom, importability, runtime behavior, or code safety.
No payload or target package is imported or executed by the importer.

## RECORD, path, ownership and payload policy

Every present target supplies original RECORD bytes. Strict UTF-8 CSV has
exactly three fields per nonblank row, comma/double-quote grammar, no malformed
quoting or NUL, no repair, no truncation and no skipped rows.

Ordinary paths are relative POSIX component sequences. Components match
`[A-Za-z0-9_][A-Za-z0-9_.+-]*`. Empty components, `.`, `..`, leading slash,
backslash, Windows drive, controls and unsupported syntax reject before
classification. Duplicate paths, casefold collisions and every file/directory
prefix conflict reject, including conflicts hidden by an intervening sorted
name. Cross-distribution physical paths/casefold ownership must also be unique.

| Target | Exact owned package prefix |
|---|---|
| swegemma | `swegemma/` |
| adk-submission | `adk_submission/` |
| adk-eval-core | `adk_eval_core/` |
| google-adk | `google/adk/` |
| google-genai | `google/genai/` |
| litellm | `litellm/` |
| vllm | `vllm/` |
| transformers | `transformers/` |

Each also owns only its exact `<name_with_underscores>-<version>.dist-info/`.
Component containment is used; RECORD never authorizes unrelated neighbors.
One own METADATA row and one own RECORD row are mandatory. Every supplied row
has exactly one regular-file observation; extra observations reject.

Importer derives only `owned_regular`, `excluded_volatile`, or
`verified_console_script`; invalid rows reject. Both hash and size are required
for regular/script rows. Hash is exactly canonical unpadded URL-safe base64 of
32 SHA256 bytes, prefixed `sha256=`. Size is canonical bounded ASCII decimal.
Actual included bytes are rehashed/recounted independently; omitted bytes have
only reported hash/size consistency checks.

Only these volatile rows are allowed: exact own RECORD (self hash/size empty),
own INSTALLER/REQUESTED/direct_url.json, and owned Python bytecode linked to a
supplied owned `.py` row under `__pycache__/<identifier>.cpython-<major><minor>`
`[.opt-1|.opt-2].pyc`. Unsafe paths, arbitrary caches, orphan bytecode, `.pyo`,
neighbor dist-info, and nonregular observations reject. Supplied volatile
hash/size must both be present or absent and, when present, agree with the
observation. Volatile exclusions never bypass safety or ownership checks.

Payload is mandatory for original RECORD, every verified script, and source/
resource files under the first three harness prefixes with suffixes `.py`,
`.pyi`, `.json`, `.yaml`, `.yml`, `.toml`, `.txt`, `.md`, `.cfg`, `.ini`, `.typed`,
`.j2`, `.jinja`. All dist-info payloads except original RECORD and all other
volatile payloads are prohibited. Other owned bytes may be omitted or included;
receipts distinguish the two. Content deduplication is allowed across different
logical files, while duplicate logical observations are rejected. Blob set,
explicit reference set and independently derived reference graph must agree.

## Single console-script exception and supported platforms

Supported POSIX profiles are `posix_prefix` and `posix_venv` (the sysconfig
`venv` alias is normalized explicitly) and Debian's `posix_local`. Each profile
is that scheme's own sysconfig templates anchored at the observed prefix; no
absolute path is special. For `posix_prefix`/`posix_venv`, prefix-relative site
roots are only `lib` or `lib64` `/python<major>.<minor>/{site-packages,dist-packages}`,
scripts must be `<prefix>/bin` and the command executable must be directly in
that directory. For `posix_local`, the only site root is
`<prefix>/local/lib/python<major>.<minor>/dist-packages`, scripts must be
`<prefix>/local/bin`, and the command executable must be directly in
`<prefix>/bin` or in that scripts directory. Recorded Kaggle output shows this
layout with prefix `/usr` and Python 3.12. Every installation root must be an
observed site root, and the console-script path is computed from the same
accepted scripts directory. Mixed observations, neighbouring roots, a wrong
minor version, other schemes (`deb_system`, `posix_home`, `posix_user`), macOS
framework layouts outside these profiles and Windows fail closed; v4 has not
yet been exercised in a real Kaggle image.

The sole external RECORD exception is a console basename matching
`[A-Za-z0-9_][A-Za-z0-9_.-]*`, declared exactly once with exact case under that
same distribution's canonical console entry points. Target is syntactic
`module:callable` (dotted identifiers), never imported. Own entry_points.txt
must have a RECORD row. GUI declarations, extras-style targets, Windows
launchers, undeclared names and duplicate/casefold owners reject.

Importer computes the exact relative path from installation root to
`scripts_directory/basename`; it must contain only the necessary leading `../`
components followed by `bin/basename`. Wrong depth, nested paths and any other
external category reject. Capture does not traverse the RECORD path: it opens
the basename directly through a scripts-directory descriptor anchored
component-by-component without following symlinks. Script must be regular,
have exactly one hard link, include payload bytes, and match RECORD hash/size.
Script-root plus casefold basename ownership is unique across all targets.

## Trusted root and single publication transaction

The local operator supplies an **existing**, dedicated absolute private
`GEMMA4_HARNESS_ROOT`. Import never creates it. A missing root creates nothing.
The directory must belong to the current UID and grant no group/other access;
all path components must be nonsymlink directories. Root cannot contain or be
contained by the repository, the competition dataset, Python installation
roots, system/user site roots, or additional trusted local exclusions. Other
Git repositories are excluded by checking `.git` along anchored ancestors
(including worktree marker files) and conventional bare-repository
`HEAD`/`objects`/`refs` markers. Unusual externally configured Git directories
must be supplied as trusted additional exclusions. Dedicated roots reject foreign
child directories, so a nested repository cannot hide under the root; only
recognized staging directories with the fixed staged-file profile are allowed.
These roots come from local configuration, never capture observations. An explicit
dataset root is required; the environment dataset root is additive protection.

The local-account model trusts the owner and excludes malicious same-UID
writers. It does not claim protection from a privileged adversary relocating
an already anchored directory. Root descriptors pin the acquired inode;
ancestor symlink replacement cannot redirect subsequent descriptor writes.

One scope anchors the root, reads a bounded private immutable byte snapshot,
hashes/parses/validates that same snapshot, constructs a receipt, stages a
complete evidence bundle, and publishes it. The snapshot is the same `bytes`
object throughout; the input path is not reread for storage. Source ZIP paths
also use no-follow reads. On macOS use actual canonical `/private/tmp/...`, not
the symlink alias `/tmp/...`; the trusted evidence root itself is never resolved
to bless a symlink path.

All writes are descriptor-relative, exclusive, regular and mode 0600 within
mode 0700 staging. Authorized publishers serialize with a private flock. A
staged bundle is prepare-renamed, then hard-linked into the committed namespace
atomically without replacement. Full capture ZIP SHA256 names the committed
`<SHA256>.evidence.zip`. It contains exactly `capture.zip` (the exact snapshot)
and `receipt.json`; blobs remain content-addressed inside the capture archive.
The publication link temporarily shares the staged inode; finally cleanup
leaves one link when cleanup succeeds. No remote source-shaped tree is recreated.

**The successful atomic no-replace publication is the transaction commit point.**
Before it, errors reject and do not create committed evidence. After it, no
durability or cleanup operation can change the terminal result back to
`CAPTURE_REJECTED`; already committed evidence is never rolled back or mutated.
Exact matching existing evidence is already committed and follows the same
rule once its bytes have been verified.

Returned `publication` and `evidence_path` are the real committed evidence path;
`publication_status` is `CREATED` or `IDEMPOTENT`. `durability=CONFIRMED` means the
required file/directory fsync operations succeeded. Any post-commit durability
failure leaves `CAPTURE_VALIDATED`, the committed path,
`durability=UNCONFIRMED`, and bounded warning
`PUBLICATION_DURABILITY_UNCONFIRMED`. A later successful fsync in the same import
does not erase an earlier failure. Cleanup/descriptor teardown failures add
`PUBLICATION_CLEANUP_UNCONFIRMED` and may leave recognized staging; they cannot
change validation or delete committed evidence. Durability can remain confirmed
when cleanup alone failed and the durability operations succeeded. Warnings
are returned separately from rejection reason codes, without exception text.
Validation does not guarantee power-loss durability when confirmation failed.

Exact existing bundle bytes reimport idempotently. Different receipt/policy
bytes for the same archive identity are an explicit conflict, not an overwrite.
The importer never writes repository/docs/vendor_meta/experiments paths.

Before creating even the lock, a read-only capacity check requires
`existing_entries + missing_lock_slot + stage_slot + final_slot <= 8192`.
New publication reserves one staging slot and one final slot; the missing lock
adds one more. Thus the highest initial new-import count is 8189 without a lock,
or 8190 with a lock. Peak root occupancy is at most 8192; ordinary cleanup frees
the staging slot. For an existing final entry, stage/final reservation is zero;
its bytes are still checked under the lock, and an exact reimport at 8192 entries
with an existing lock succeeds. Stale staging is not credited as free capacity
before it has actually been recovered. A second reservation under the flock
protects creation after recovery and serialized competing imports. Capacity
rejection before admission creates no importer-owned root entries.

Finally cleanup covers staging creation, write, receipt bundle preparation,
prepare-rename and publication. Tests inject write/rename/link failure and
descriptor alias races. Bounded recovery removes only recognized importer
staging names and the two fixed regular files; unknown files, links and
committed evidence are never deleted. No cleanup guarantee is made for
SIGKILL, power loss, cleanup I/O failure, or malicious local-owner intervention.
An exact retry after durability-unconfirmed publication recovers recognized
staging if needed, verifies the same immutable bundle bytes, and independently
fsyncs its file and root. It reports `IDEMPOTENT` and current durability without
rewriting the receipt or capture. No guarantees are made for power loss,
SIGKILL, or cleanup I/O failure beyond these bounded returned observations.

## External receipt and separate human ledger

Validated receipts have schema `H23_VALIDATION_RECEIPT_V4`, policy revision
`h23-v4.1`, archive SHA,
protocol/policy revision, available local Git revision, exact importer program
source-commitment SHA, literal technical result/reasons, bootstrap summary,
distribution identities, reported wheel count, payload count/set SHA,
record-row/category counts, byte-verified versus digest-only counts, and
explicit technical limitations. No free-form subprocess errors appear.
Unavailable local Git revision is null, not fabricated. Git revision alone
does not identify uncommitted code; the separate program hash binds source
commitments. Receipts have no new timestamp so identical imports are idempotent.
They contain fixed technical publication semantics: validation requires committed
atomic no-replace publication, and durability is reported separately. They do
not embed a mutable final durability/warning claim. Committed receipt bytes are
never changed after publication or retry. Rejected imports return a bounded
result/reason and create no new committed capture or receipt.

`record_coverage_complete` means **every supplied RECORD row passed policy**,
including exclusions. It does not mean RECORD enumerated every installed file.
`byte_verified_observation_count` counts logical file observations with included
payload verified by importer; `digest_only_observation_count` counts observations
whose bytes were omitted. Original RECORD counts as one byte-verified observation.
`payload_set_sha256` hashes sorted unique lowercase blob IDs, each followed by
ASCII newline (empty set hashes empty bytes); it has no installation meaning.

Receipts establish archive identity, policy conformity, included-byte hashes/
sizes, required payload coverage, internal identity/RECORD relationships,
supplied path safety and complete atomic publication when publication succeeds.
They do not guarantee confirmed power-loss durability; consult the separately
returned durability and bounded warnings for that import attempt.
They cannot establish origin, truthful command execution, pip causality,
omitted-byte hashes, exhaustive installation, source secret-freedom,
runtime/importability/code safety, or hidden scorer version.

Humans may later author a **separate** `H23_HUMAN_ATTESTATION_V1` ledger with
label `HUMAN-ATTESTED`. Required fields: Git commit SHA; executed notebook SHA;
Kaggle owner/name, immutable notebook version and matching permalink;
wheelhouse dataset owner/name and immutable version; capture ZIP SHA; receipt
SHA/reference; attester; UTC timestamp; and all five explicit personally
checked checklist entries defined in `attestation.py`. No real attestation is
created by this reset. The read-only checker validates format only, neither
authenticates a human nor promotes a run. Later deliberate project review may
commit a tiny ledger reference. It is not part of importer operation.

## Independently documented limits

All MiB/GiB here are binary units. These are acceptance-policy values, not
untrusted manifest configuration.

| Resource | Maximum |
|---|---:|
| Capture ZIP/snapshot bytes | 128 MiB |
| Sum expanded archive members | 256 MiB |
| Blob member | 32 MiB |
| Manifest bytes | 8 MiB |
| ZIP members / blob count | 8193 / 8192 |
| JSON nesting depth / nodes | 12 / 500,000 |
| JSON string length / generic list length | 4096 characters / 100,000 |
| Present distributions / original RECORD references | 8 / 8 |
| Wheel observations / wheel references / argv items | 256 / 256 / 300 |
| Site roots / console declarations per distribution | 4 / 128 |
| RECORD bytes / rows / field length | 20 MiB / 100,000 / 2048 characters |
| Raw metadata / direct-URL observation size | 512 KiB / 512 KiB |
| Omitted file reported size / wheel reported size | 2 GiB / 16 GiB |
| Receipt bytes | 4 MiB |
| Recovery staging directories / evidence-root entries | 16 / 8192 |

The aggregate manifest/node/expanded limits can constrain a capture before an
individual per-distribution bound is reached. Capture wheel hashing is streamed;
pip time/output disk consumption is not an importer archive-resource guarantee.

## Local review commands

Use the existing development environment; none of these executes capture/pip:

```bash
python -m tools.build_h23_v4_notebook --check
python -m pytest
python -m tools.h23_v4.importer /absolute/path/capture.zip \
  --harness-root /absolute/private/existing/evidence \
  --dataset-root "$GEMMA4_DATASET_ROOT"
python -m tools.h23_v4.attestation /absolute/path/manually-authored-ledger.json
```

To regenerate a reviewed notebook, use
`python -m tools.build_h23_v4_notebook --dataset-root "$GEMMA4_DATASET_ROOT"`.
The builder embeds exact reviewed source files and checks byte-for-byte drift.
The notebook uses normal static imports from its private temporary module tree,
requires a fresh kernel to avoid a previously loaded `tools` namespace, and
contains no second hand-copied implementation. Notebook execution identity is
a nullable reported observation; do not fill it with the builder's notebook
hash and pretend that proves a specific remote notebook executed.

Independent literal fixtures cover parser/ZIP attacks, RECORD shape/path/
ownership/collisions/hash/size, volatile rules, console symlinks/hardlinks/
declarations/ownership, metadata projections, protected-root races, cleanup/
recovery/conflicts/idempotence, failed bootstrap, fabricated origin, and exact
notebook/core drift. Existing submission/split/data-boundary tests must also
remain green. Recommendation for this candidate: **AUDIT V4 only**.
This final narrow corrective pass requires a **FINAL NARROW RE-AUDIT** before
any commit or first real Kaggle capture. H23 remains HOST-UNKNOWN and the hidden
scorer remains SCORER_ONLY_UNKNOWN.
