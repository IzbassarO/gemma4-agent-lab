# H28 E0 technical-control operator runbook

**Historical preparation — superseded for the real E0 flow on 2026-10-06.**
The operator observed **Submit to Competition → File Upload**, with the explicit
requirement **Your submission should be named submission.zip.** The frozen ZIP
was uploaded directly, reached **Succeeded / score 0.05**, and the downloaded
copy was independently verified as byte-identical. Notebook materialization was
unnecessary for that real current flow. This runbook does not authorize another
submission. See [H28 certification](H28_KAGGLE_E0_CERTIFICATION_2026-10-06.md)
for attributed evidence, hashes, current matrix decision and unknowns.

The retained sections below describe the **historical 2026-10-05 proposed
notebook route**, not the preferred current upload procedure. They preserve
useful preparation and failure-handling history. The notebook has not been
established as the executed E0 route. Labels such as "required notebook output"
and "STOP" below apply to that historical proposal only.

This was a proposed, instrumented experiment for the first real Kaggle technical-control attempt. It is not a recovered official cell-by-cell procedure. Notebook preparation alone did not establish submission or scoring success.

## Frozen identity

| Item | Required value |
|---|---|
| Git HEAD and `origin/main` | `3c6133ea9a1d9440183bfacc72b9f6e823dc6208` |
| Local artifact | `artifacts/submissions/e0_official_control/submission.zip` |
| Transport filename | `submission.zip.bytes` (unchanged ZIP bytes under an opaque input name) |
| Size | `443572` bytes |
| SHA256 | `25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3` |
| Operator notebook | `kaggle/e0_submission.ipynb` |
| Required notebook output | `/kaggle/working/submission.zip` |

The existing ZIP must remain unchanged. Its ten members are:

```text
adapters/main_lora/adapter_config.json
adapters/main_lora/adapter_model.safetensors
adapters/tool_lora/adapter_config.json
adapters/tool_lora/adapter_model.safetensors
agent.yaml
configs/sampling.yaml
eval_config.yaml
prompts/analyzer.md
prompts/system.md
sub_agents/code_analyzer.yaml
```

## Evidence and experiment boundary

The user-supplied official competition facts require a ZIP with root `agent.yaml` and the supported model `gemma-4-31b-it-qat-w4a16-ct`. [Kaggle Staff's output-file explanation](https://www.kaggle.com/competitions/gemma-4-developer-agent/discussion/743683) establishes that the submitted notebook must generate `submission.zip` before scoring. The evaluator later produces task-level `submission.parquet` containing `id` and diff/`NO_PATCH` predictions; this operator notebook does not create parquet.

The local evidence set has 41 notebooks and a generic `12.py`, with no authenticated current official operator export. Community instructions support a completed notebook version with ZIP output, but exact current save/submission controls require live confirmation. [Kaggle's dataset documentation](https://www.kaggle.com/docs/datasets) says uploaded archives are automatically unpacked and arbitrary file formats are supported; [notebook documentation](https://www.kaggle.com/docs/notebooks) describes dataset attachment. Therefore this experiment uploads the frozen bytes under the opaque transport name `submission.zip.bytes` and copies them into the required ZIP output. This is a designed experiment, not a claimed organizer requirement or a guarantee that Kaggle will preserve the transport file. The mounted filename, size, and hash must prove preservation before proceeding.

## A. Local preparation

1. From the repository root, run these read-only checks:

   ```bash
   git rev-parse HEAD
   git rev-parse origin/main
   shasum -a 256 artifacts/submissions/e0_official_control/submission.zip
   stat -f "%z" artifacts/submissions/e0_official_control/submission.zip
   git status --short
   ```

2. Require the frozen HEAD, hash, and size above. The preparation may leave the two new operator files uncommitted. Stop if the ZIP identity differs; classify this as `E0-INPUT-MATERIALIZATION` and do not rebuild it.
3. The prepared upload payload is `/private/tmp/gemma-h28-e0-input/submission.zip.bytes`. It contains exactly the frozen ZIP bytes. Verify it before uploading:

   ```bash
   shasum -a 256 /private/tmp/gemma-h28-e0-input/submission.zip.bytes
   stat -f "%z" /private/tmp/gemma-h28-e0-input/submission.zip.bytes
   ```

   To recreate a transport copy on the operator's machine, use a fresh directory outside the repository:

   ```bash
   E0_INPUT_DIR=$(mktemp -d /private/tmp/gemma-h28-e0-input.XXXXXX)
   cp artifacts/submissions/e0_official_control/submission.zip "$E0_INPUT_DIR/submission.zip.bytes"
   shasum -a 256 "$E0_INPUT_DIR/submission.zip.bytes"
   stat -f "%z" "$E0_INPUT_DIR/submission.zip.bytes"
   printf '%s\n' "$E0_INPUT_DIR"
   ```

   Require the same hash and size. This copies the existing archive without extraction or rebuilding; its local source filename and bytes remain unchanged. Preserve the command output as the E0 receipt.

## B. Kaggle notebook preparation

1. Create/open an E0 notebook associated with **Google — The Gemma 4 Developer Agent Competition**, slug `gemma-4-developer-agent`, and import `kaggle/e0_submission.ipynb`. **LIVE UI LABEL — CONFIRM IN KAGGLE** for notebook creation/import and competition attachment.
2. Create a dedicated Kaggle Dataset, preferably **private**, by uploading **only `submission.zip.bytes`** as an individual file. Do not upload `submission.zip`, a wrapping archive, the whole repository, or the staging directory as an archive. **LIVE UI LABEL — CONFIRM IN KAGGLE** for dataset creation/file upload. Require the stored file to retain the literal name `submission.zip.bytes` and size `443572` bytes.
3. Attach that dataset as exactly one E0 notebook input. **LIVE UI LABEL — CONFIRM IN KAGGLE** for dataset/input attachment. The illustrative path is `/kaggle/input/<E0_INPUT_DATASET>/submission.zip.bytes`; modern mounts may add owner/dataset prefixes. The notebook discovers the exact basename beneath `/kaggle/input` and fails unless exactly one file matches. Record its owner/slug, dataset version, uploaded filename, and resolved mounted path; do not invent a Kaggle-generated slug. If Kaggle unpacks/sniffs the payload, changes its name/bytes, or exposes no literal file, stop with `E0-INPUT-MATERIALIZATION` before submission. Do not reconstruct an archive from extracted members.
4. Recommend **CPU/no accelerator** for this file-copy notebook and **Internet OFF**. It uses no model or GPU. Competition scoring infrastructure is separate and has the supplied 12-hour budget and L4/no-Internet rule. If the live submission UI requires a supported notebook accelerator, **LIVE UI CONFIRMATION REQUIRED**: record that requirement before choosing the supported setting.
5. Run all seven prepared cells in order without edits: experiment identity; standard-library configuration/output cleanup; exact input location; source size/hash verification; byte-preserving copy; output verification and ready marker; final stop reminder. Only `pathlib`, `hashlib`, `shutil`, and `zipfile` are used. The notebook verifies output size/hash, ZIP integrity, root entrypoint, and all ten members. It accesses no task contents, imports no agent, and runs no inference. It does not unzip/re-zip the artifact or use a network connection.
6. Require the final output:

   ```text
   H28_E0_ARTIFACT_READY
   path=/kaggle/working/submission.zip
   size=443572
   sha256=25d07b6484d3c4fb4683170ed85b7a3adfe2ff0c1962f7949e43bc3cf87e4fc3
   ```

   A marker from an interactive session is insufficient; require the same checks in the completed saved version below.

## C. Save a completed version

1. Use the current Kaggle control that runs and saves a completed notebook version; likely **Save Version → Save & Run All**. **LIVE UI LABEL — CONFIRM IN KAGGLE**. A saved source-only snapshot does not demonstrate successful execution or preserved ZIP output.
2. Wait for that version to finish successfully. Record notebook title/slug, version number, save/run timestamps with timezone, runtime, accelerator, Internet setting, and input identity/version.
3. Inspect that completed version's outputs and logs. Require `submission.zip`, size `443572`, matching printed source/output SHA256, all archive assertions passing, and `H28_E0_ARTIFACT_READY` with no exception. Download the output and verify its SHA256 locally if available; record any inability to do so.

## D. Stop before submission

At this checkpoint, **STOP before clicking Submit or its final confirmation**. The operator checks:

- [ ] Frozen local HEAD, ZIP size, and hash match.
- [ ] The intended competition is attached and selected.
- [ ] The completed notebook version used the intended frozen input/version.
- [ ] That saved run succeeded with no exception and printed the ready marker.
- [ ] Its output contains `submission.zip`, size `443572`, and the matching hash.
- [ ] Accelerator and Internet settings are recorded.
- [ ] The live controls identify the intended completed version and ZIP output.

Capture the live submission controls before consuming the first attempt. If they demand a parquet or a different object instead of the saved ZIP output, stop and return the visible requirement for diagnosis as `E0-SUBMISSION-UI`.

## E. Separate manual submission step

After reviewing checkpoint D, the operator may make **one E0 attempt** using the completed notebook version/output containing `submission.zip`. **LIVE UI LABEL — CONFIRM IN KAGGLE** for the submission action, likely **Submit to Competition**. If a file selector is exposed, choose `submission.zip`; if Kaggle detects the output automatically, record that behavior and the selected notebook/version.

**LIVE UI STEP — record screenshot/text of available submission controls before final confirmation if they differ from this runbook.** Do not guess a control, substitute `submission.parquet`, or make speculative repeated submissions. Capture the confirmation and new submission row immediately. No automated submission command is part of this runbook.

## Evidence to return

| Stage | Required evidence |
|---|---|
| Before submission | Git HEAD; local ZIP size/SHA256; notebook title/slug/version; save/run timestamps and timezone; input owner/slug/version/filename/mounted path; accelerator; Internet setting; saved-run status/runtime; printed source and output size/hash; integrity/member checks and ready marker; ZIP output existence; live output selection/control screenshot or text. |
| Immediately after submission | Kaggle submission ID, timestamp/timezone, visible status, associated notebook/version, selected output or automatic-detection behavior, and any immediate validation error. |
| At completion/failure | Final status, score if any including zero, runtime, full visible error/log, organizer diagnostics, whether task-level scoring started, and evaluator `submission.parquet` existence/row count if exposed. Record unavailable evidence as unavailable, not as success. |

Preserve the original frozen ZIP and preparation files. Do not tune the agent based on this score.

## Failure classification

| Label | Observed failure layer |
|---|---|
| `E0-PACKAGE` | ZIP rejected structurally. |
| `E0-INPUT-MATERIALIZATION` | Frozen bytes cannot be introduced, found, copied, or verified correctly. |
| `E0-NOTEBOOK` | Notebook execution, saved version, or output preservation fails. |
| `E0-IMPORT` | Evaluator cannot load agent dependencies/imports. |
| `E0-DEPENDENCY` | Required runtime package is unavailable. |
| `E0-MODEL-SERVING` | Model endpoint or supported base model cannot initialize. |
| `E0-ADAPTER` | Adapter load/runtime failure. |
| `E0-HARNESS` | Harness, tool, or session failure. |
| `E0-INFERENCE` | Agent inference runtime failure. |
| `E0-OUTPUT-PARQUET` | Evaluator task-prediction artifact fails. |
| `E0-SUBMISSION-UI` | Kaggle refuses or does not expose the intended notebook submission path. |
| `E0-EVALUATOR` | Accepted submission fails in external evaluation/scoring. |
| `E0-SUCCESS-LOW-SCORE` | Technical path succeeds and returns a score, including zero or a poor score. |

Classify from the visible failing operation and logs; retain uncertainty if the failure layer is not established. Diagnose before a second attempt. Submission allowance may be limited; no exact daily quota is established here.

## Preparation validation

Local JSON and Python syntax checks passed for seven cells, including five code cells. Frozen constants, the ten-member inventory, standard-library-only imports, empty saved outputs, CPU/offline metadata, and absence of network/model/task/local-path references passed static checks.

An isolated simulation remapped only the two Kaggle filesystem paths to temporary directories outside the repository. Exact byte-copy success and nine failure/cleanup cases passed: missing input, ambiguous inputs, wrong size, wrong hash, symlink input, source alteration before copy, partial copy failure, output alteration before final verification, and archive CRC corruption. Failed full runs left no submission ZIP or ready marker. The frozen source remained byte-identical. This validates file handling, not Kaggle execution or scoring.

The local validation script and receipt are `/private/tmp/gemma-h28-e0-validate.py` and `/private/tmp/gemma-h28-e0-validation.json`. They are outside the repository and are not notebook inputs. The prepared transport file was separately verified against the frozen size and SHA256.

## Historical H28 preparation state (2026-10-05)

`NOT-REPRODUCED — awaiting real Kaggle E0 execution`

At preparation time, the notebook and this runbook were not promotion evidence:
the matrix was to remain unchanged until the real run was reviewed. An interactive
ready marker or ZIP-output existence alone could not establish PASS. That historical
restriction is now superseded by the separate real-run certification report above,
using the existing OWN-KAGGLE-RUN authority. No competitive-agent, prompt, sampling,
tool, budget, adapter, or frozen-ZIP changes are part of this history update.
Do not commit or push.
