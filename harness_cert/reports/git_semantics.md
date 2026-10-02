# git_semantics probe — 2026-10-02

**Class: RUNTIME-REPRODUCED, git only.** No harness code was involved. This shows what the README's git commands do; it does not show what the scorer passes to them.

Command: `bash harness_cert/scripts/git_semantics_probe.sh` · git 2.50.1 (Apple Git-155), macOS · raw output: `harness_cert/results/git_semantics/` (gitignored)

| Probe | Observed |
|---|---|
| H11: `git checkout HEAD -- tests/test_a.py sitecustomize.py 2>/dev/null \|\| true` (second path absent from HEAD) | tracked test still `hacked`: the whole checkout aborted and the error was swallowed |
| H11: same with only the existing path | restored to `orig` |
| H12: `git add -N .` then `git diff --binary HEAD --name-only` | `repro.py` only; `build/x.py` and `__pycache__/m.pyc` excluded by the `setup_git_exclude` rules |

Still HOST-UNKNOWN: the pathspec list the harness builds (H11), harness-level `submit_patch` (H12), and the container's git version. H11/H12 stay NOT-REPRODUCED in `matrix.yaml`.
