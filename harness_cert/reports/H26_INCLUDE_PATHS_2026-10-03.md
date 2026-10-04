# H26 include paths — 2026-10-03

**H26: PASS. Authority: REPRODUCED-LOCAL. Evidence class: RUNTIME-REPRODUCED, scoped to the local CPU compiler.**

`..` is not categorically prohibited in `!include`. An include resolves relative
to the containing YAML file and is allowed when it remains inside the submission
root. The reproduced checks accept the official parent include and reject
resolved traversal escapes, absolute external paths and escaping symlinks.

## Environment and evidence

The operator supplied the reproduced local execution outcomes recorded below.
The isolated environment was successfully created with **41 support packages**
and these three exact **wheelhouse v28 source wheels**:

| Component | Exact version |
|---|---|
| Python | 3.12.14 |
| adk-submission | 0.2.12 |
| google-adk | 1.36.1 |
| google-genai | 2.11.0 |

Compiler import returned **COMPILER_IMPORT_OK**. `validate_directory` on the
official baseline returned **PASS**. Compilation used a **no-LoRA copy** of that
baseline with explicit tool and model registries; the original agent behavior
was not changed.

The exercised APIs were `validate_directory(...)`,
`load_yaml(..., root_dir=...)` and
`compile_submission(..., tool_registry=..., model_registry=...)`.
The evidence supplied for this checkpoint is the operator's outcome summary;
an exact shell invocation and raw transcript location were not supplied.
`harness_cert/results/H26/` remains the reserved, gitignored raw-results path.
This report does not claim a raw transcript exists there.

## Reproduced outcomes

| Include case | `load_yaml` | `compile_submission` on the no-LoRA copy |
|---|---|---|
| Official `../prompts/analyzer.md` / `OFFICIAL_PARENT_INCLUDE` | PASS | COMPILE_PASS; `agent_type=LlmAgent`, `agent_name=swe_baseline_agent` |
| `../../outside.md` escaping submission / `TRAVERSAL_ESCAPE` | PathTraversalError | COMPILE_REJECT / PathTraversalError |
| Absolute external path / `ABSOLUTE_ESCAPE` | PathTraversalError | COMPILE_REJECT / PathTraversalError |
| In-root symlink targeting outside / `SYMLINK_ESCAPE` | PathTraversalError | COMPILE_REJECT / SubmissionValidationError |

`validate_directory` performs structural filesystem validation; its baseline
PASS alone does not establish include resolution. `load_yaml` and the compiler
exercise the YAML boundary checks. Compilation performs directory validation
first, explaining why its symlink case raises `SubmissionValidationError`
before the loader's `PathTraversalError` can occur.

The exact source agrees with these results: `adk_submission/yaml_loader.py:47–78`
resolves includes relative to the containing YAML; `paths.py:102–147` checks
symlinks and resolved containment; `discovery.py:98–105` rejects symlinks during
directory validation. These references identify members of the inspected wheel.
The separate `config_path` policy is not generalized from these `!include`
results.

## Scope

H26's `harness_source` blocker is removed for this exact local compiler.
The result does not certify model execution, LoRA serving, the remaining CPU
harness tests, or the hidden scorer's installed stack. No other H-status is
promoted. **H23 remains HOST-UNKNOWN; the hidden scorer remains
SCORER_ONLY_UNKNOWN. Step 0 remains incomplete.**
