#!/usr/bin/env bash
# Git-level semantics behind H11 and H12, reproduced in a throwaway repo. No harness code is involved:
# this proves what the README's git commands do, NOT what the scorer passes to them.
# Usage: bash harness_cert/scripts/git_semantics_probe.sh
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
git init -q
mkdir tests && echo orig > tests/test_a.py
printf '__pycache__/\n*.pyc\n.pytest_cache/\n*.egg-info/\nbuild/\ndist/\n.coverage\n' > .git/info/exclude  # setup_git_exclude rules
git add -A && git -c user.email=a@b -c user.name=a commit -qm baseline

echo "git: $(git --version)"
# H11: README section 8.2 reset, with one existing tracked test and one path absent from HEAD.
echo hacked > tests/test_a.py
echo new > sitecustomize.py
git checkout HEAD -- tests/test_a.py sitecustomize.py 2>/dev/null || true
echo "H11 tracked test after mixed-pathspec checkout: $(cat tests/test_a.py)  (orig = reset worked, hacked = reset skipped)"
git checkout HEAD -- tests/test_a.py 2>/dev/null || true
echo "H11 tracked test after existing-only checkout:  $(cat tests/test_a.py)"

# H12: git add -N . + diff against HEAD, README section 8.1.
rm -f sitecustomize.py
echo x > repro.py
mkdir -p build __pycache__ && echo y > build/x.py && echo z > __pycache__/m.pyc
git add -N .
echo "H12 files in diff: $(git diff --binary HEAD --name-only | tr '\n' ' ')"
