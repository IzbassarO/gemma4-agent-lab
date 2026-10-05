Local H04/H05/H18 scripted probe
==============================

This tooling prepares REPRODUCED-LOCAL evidence using an entirely synthetic
repository. It changes no production agent and makes no hidden-scorer claim.
Run the certification experiment yourself; unit tests use fakes and do not run
the installed harness.

The H30 observation gate is specified in [H30_DESIGN.md](H30_DESIGN.md). Its
separate graph profile pins `81ac5b150c719cafc671b884dce7f525219e4ea0`; both older
profiles retain their historical pins. From the repository root:

```sh
H23_CPU_TMP=/private/tmp/h23-cpu.UA1C8T \
  /private/tmp/h23-cpu.UA1C8T/harness/bin/python -B -m tools.harness_cert.run_h30
```

This captures actual official-runner ADK/LiteLLM requests through an in-process
HTTP transport, with no listener or network connection. Six synthetic controls
distinguish recognized file presence/size from agent tool declarations. Graph
fixtures are stat-only; no graph-tool functionality or real model is tested.
Raw evidence remains under the ignored H30 results directory. No prediction
flag promotes the matrix. Separate package/import readiness cannot certify the
current Kaggle submission wrapper or hidden scorer.

The next tranche is documented in [H13_H14_H29_DESIGN.md](H13_H14_H29_DESIGN.md).
Its separate fixed baseline is `12c319fa8bcf5b313175f80cee3ce06a77a59619`.
The historical dispatch probe below retains its original pin and refuses the
new HEAD. From this repository root, the operator runs the next tranche with:

```sh
H23_CPU_TMP=/private/tmp/h23-cpu.UA1C8T \
  /private/tmp/h23-cpu.UA1C8T/harness/bin/python -B -m tools.harness_cert.run_h13_h14_h29
```

This runs a submission/verification control, H13, H14, H29, then an H29 final-text
boundary control. The control uses official `verify_task` twice: the unchanged
synthetic baseline must fail and the submitted patch must pass. H13/H14 also
verify their returned fallback patches in fresh isolated sandboxes. Evaluator
task hydration is never called. H13 deliberately withholds the second HTTP
response while the real session timer runs; it does not certify cancellation
during a synchronous tool.

The exact pure Python pytest support closure already present in `.venv` is
staged before the audit hook, hash-checked, and copied only to synthetic
verification venvs. There is no package installation, host `.pth`, general
site-packages access, wheel discovery or `system_site_packages=True`.
Inherited pytest options/plugins are cleared and plugin autoload is disabled.
A changed closure refuses preflight rather than silently accepting new code.

All cases share one run ID. Primary evidence goes under
`harness_cert/results/{H13,H14,H29}/<run-id>/`; controls are under
`H13/<run-id>/CONTROL/` and `H29/<run-id>/H29_BOUNDARY/`. Each preserves
preflight, script, HTTP, trace, exceptions, returned/submitted/workspace patches,
workspace content, verification/JUnit evidence where exercised, and an artifact
SHA256 inventory. `prediction_matches` checks the source-derived design; it is
separate from `infrastructure_ok` and never promotes the matrix.

Do not change matrix/report evidence before reconstructing a valid operator run.
Synthetic local results establish no Kaggle hidden-scorer behavior. No H04/H05/H18
certification run is repeated by this command.

From the repository root, using the already acquired environment:

```sh
export H23_CPU_TMP=/private/tmp/h23-cpu.UA1C8T
"$H23_CPU_TMP/harness/bin/python" -B -m tools.harness_cert.run_h04_h05_h18
```

Admission requires the reviewed baseline
`ea5b857487ae9e146e94a88e108962742fcbdef8`, a clean repository except the exact
probe/source/test/documentation allowlist, Python 3.12.14, the audited package
versions, byte-identical installed v28 Python sources and bundled tokenizers.
Neither an unrelated dirty tree nor a new HEAD is silently accepted.

The sole additional tracked-file exception is the reviewed support-pin update
in `harness_cert/reports/CPU_HARNESS_ACQUISITION_PLAN_2026-10-03.md`, gated by its
full SHA256 and regular-file/canonical-path checks. Its digest is recorded with
the probe sources. Other reports and status/matrix changes remain refused.

The local CPU support closure adds only `authlib==1.6.6`, required by the exact
Google ADK wheel (`authlib>=1.6.6,<2`). Cryptography and the Requests integration
dependencies are already installed/pinned. Keep 1.6.6 for this local offline
certification experiment; this pin makes no hidden-scorer version claim.
From a fresh checkout, acquire and verify the exact wheel and recreate the
one-package hash lock below. Generated files remain gitignored. These incremental
commands require the existing CPU harness environment but no pre-existing
`artifacts/harness_support/authlib166/` files:

```sh
export H23_CPU_TMP=/private/tmp/h23-cpu.UA1C8T
export UV_CACHE_DIR="$H23_CPU_TMP/uv-cache"
mkdir -p artifacts/harness_support/authlib166

curl -fL -o artifacts/harness_support/authlib166/authlib-1.6.6-py2.py3-none-any.whl \
  https://files.pythonhosted.org/packages/54/51/321e821856452f7386c4e9df866f196720b1ad0c5ea1623ea7399969ae3b/authlib-1.6.6-py2.py3-none-any.whl

echo "7d9e9bc535c13974313a87f53e8430eb6ea3d1cf6ae4f6efcd793f2e949143fd  artifacts/harness_support/authlib166/authlib-1.6.6-py2.py3-none-any.whl" \
  | shasum -a 256 -c

cat > artifacts/harness_support/authlib166/authlib.lock <<'LOCK'
authlib==1.6.6 \
    --hash=sha256:7d9e9bc535c13974313a87f53e8430eb6ea3d1cf6ae4f6efcd793f2e949143fd
LOCK
uv pip install --python "$H23_CPU_TMP/harness/bin/python" \
  --no-deps --no-index --find-links artifacts/harness_support/authlib166 \
  --only-binary :all: --require-hashes \
  -r artifacts/harness_support/authlib166/authlib.lock
export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_MODE=PRODUCTION
export HF_HUB_OFFLINE=1
export PYTHON_DOTENV_DISABLED=1
"$H23_CPU_TMP/harness/bin/python" -I -B -c 'from importlib.metadata import version; assert version("google-adk") == "1.36.1"; assert version("authlib") == "1.6.6"; import google.adk.auth.oauth2_credential_util; from google.adk.flows.llm_flows.auto_flow import AutoFlow; AutoFlow(); print("AUTHLIB_IMPORT_OK")'
```

The complete 83-package `harness.in` / `harness.lock` can be regenerated from the
tracked acquisition plan's pin blocks using its existing extraction/compile
recipe. Compiler pins and the other 82 support versions remain unchanged.

This checkpoint imports the exact previously failing module and constructs
`AutoFlow`, reaching `SingleFlow`'s authentication preprocessor path. It does not
instantiate OAuth sessions, exchange/refresh tokens or execute any H05/H04/H18
case. The probe also checks the exact Authlib version before fixture admission
and performs the AutoFlow smoke under its existing guards before fixture/server
creation. Package installation and a successful real smoke remain operator
steps; the prepared closure alone does not establish harness execution.

The probe creates a fresh working directory beneath `$H23_CPU_TMP`. It checks
both wheel resolver sets against the original and isolated working directories,
including synthetic task/snapshot sibling paths. A top-level `.whl`, a candidate
symlink/special file, or an inspection error refuses launch with the offending
path. It checks again before sandbox commands. Wheel candidate checks read only
directory metadata; competition tasks, snapshots and gold are never opened.
The exact `SubprocessManager` uses `system_site_packages=False`; its name remains
unchanged so the official subprocess package-installation skips still apply.
There are no fixture wheels and no task package installation.

The tiny snapshot contains `app.py` and its local Git repository. No remotes or
hooks are present. The tools use a fixed change from `MARKER = "before"` to
`MARKER = "after"`. H05 declares `write_file` and `submit_patch`; H04 declares
only `submit_patch` but retains `write_file` in the registry. H18 performs the
same write before emitting the nonexistent `bash` tool. No scripted response
intentionally runs a networking command, or any arbitrary shell command.

The loopback server binds only `127.0.0.1` at an ephemeral port. Offline flags and
dotenv suppression precede optional imports. An explicitly owned OpenAI client
uses a dummy key, finite timeout, zero SDK/LiteLLM retries, disabled environment
proxies and redirects, and an exact-origin request guard. A parent audit hook
rejects non-probe TCP connections, DNS/datagram operations, unapproved process
launches and filesystem access outside admitted synthetic/library paths. The
SDK resource shares identity across the compiler's deep copies, preserving its
guarded transport. No remote callbacks, model fallbacks, cache exporters or
context compaction are configured.

The sole IPv6 bind exception is urllib3 2.8.0's import-time capability check:
`urllib3/util/connection.py:127` binds an IPv6 stream socket to `(::1, 0)` and
closes it, without listening or connecting. Preflight verifies that module's
reviewed source SHA256 and compiles it for code identity checks without executing
it. Admission requires the immediate `_has_ipv6` frame, the same module's
initialization frame/globals/spec, and its actual local socket. The address is
parsed with `ipaddress`; wildcard, mapped, scoped, hostname and non-loopback
addresses receive no exception. This operation is recorded separately as
`urllib3_ipv6_detection`. General IPv6 binds and all IPv6 outbound connections
remain refused. The HTTP server and its exact allowed client origin remain
`127.0.0.1` on the selected ephemeral port; other `127/8` addresses and
`localhost` receive no permission. Redirects and environment proxies remain
disabled.

System runtime reads have a separate, read-only policy. Before the audit hook,
the probe captures the active `/usr/share/zoneinfo` tree and checks OS ownership
and directory permissions. On macOS its resolved location must be exactly
`/private/var/db/timezone/tz/<version>/zoneinfo`; other systems admit only the
physical `/usr/share/zoneinfo` tree. Only regular-file reads resolving inside
that frozen tree are allowed. `/etc/localtime` is allowed only when its resolved
target lies inside the same tree. Traversal, symlink escapes, other timezone
versions, directory listings and all mutations gain no exception. The Python
environment prefixes remain library roots; `/usr`, `/etc`, `/private`, `/var`,
user home and other system directories are not general read roots.

The null sink has an independent, exact-open policy. Only the raw spelling
`/dev/null` (string, bytes or Path) is admitted, and strict resolution must keep
that identity. Each admission checks a root-owned character device with the OS
null device number (Darwin 3:2, Linux 1:3) and a root-owned `/dev` directory that
is not group/world writable. Other platforms or failed metadata checks refuse
access. Write-only opens, including append and Python's create/truncate flags
on this existing device, discard bytes. General reads and read/write opens are
refused. The sole `O_RDWR` exception requires the frozen stdlib
`Popen._get_devnull` / `_get_handles` / constructor code and globals, the same
local Popen instance, DEVNULL stdout/stderr (never DEVNULL stdin), and an
admitted process scope. The separate subprocess argv check still applies.
Stat/lstat metadata inspection is allowed; unlink, rename/replace, chmod,
chown, hardlink, symlink creation at `/dev/null`, truncate and other filesystem
mutations gain no permission. A symlink to the sink gains no open permission,
even inside an output root. No `/dev` tree, prefix or other device is admitted.

The latest recorded H05 refusal originated in optional SDK platform metadata.
The source-reconstructed chain is OpenAI `get_platform()` ->
`platform.platform()` -> `uname_result.processor` / `_Processor.get()` ->
`_Processor.from_subprocess()` -> `check_output(['uname', '-p'], stderr=DEVNULL)`
-> `run()` -> `Popen.__init__()` -> `_get_handles()` -> `_get_devnull()` ->
`os.open('/dev/null', O_RDWR)` (the `open` audit flags also include `O_CLOEXEC`).
The recorded HTTP requests all used `X-Stainless-OS: Unknown`, consistent with
the SDK catching the refusal; the historical result/log did not save its stack.
This parent operation is separate from child-shell `2>/dev/null` redirections.
Admitting the open alone would merely expose the unapproved `uname` launch.
The owned SDK client therefore sets its optional `_platform` metadata to the
same `Unknown` value before requests, preserving that header without discovery.
This affects only the shared probe client, never global SDK state, harness
tool dispatch, H05 assertions or subprocess permissions. Regression tests
exercise the real stdlib stdio caller chain without launching a process.

**The process tree is not globally firewalled.** Python audit hooks do not impose
an OS firewall, intercept every native-library operation, or propagate into
child processes. Child work is limited to the exact audited setup commands,
bundled venv/ensurepip bootstrap and fixed file/patch operations. Unexpected
commands, copies, network refusals or observer failures invalidate evidence;
they cannot qualify as H04/H18 fatal-tool observations.

H05 must return a successful real `write_file` result, the deterministic patch,
submitted context state, and the three write/submit/final model turns. Failure
stops the probe before H04/H18. H04/H18 have continuation responses available;
their fatality or recovery is recorded without an expected-fatal assertion.

Each invocation creates a unique run ID. Raw outputs stay in these gitignored
locations, with the exact paths printed at completion:

```text
harness_cert/results/H05/<run-id>/
harness_cert/results/H04/<run-id>/
harness_cert/results/H18/<run-id>/
```

Each executed case contains `result.json`, `http.json`, `trace.json`,
`exceptions.json`, `agent.patch`, the pre-cleanup `workspace.patch` and
`workspace_app.py`, plus the harness's `logs/`. H05 also holds `preflight.json`
and, if startup fails after artifact admission, `launch_error.json`. H04/H18
directories can be empty when H05 fails. Workspace observations occur before
normal sandbox deletion; observation does not feed edits back into the returned
patch. Setup files and ephemeral caches remain beneath `$H23_CPU_TMP`.

Exit 0 means the infrastructure/control completed and observations were saved;
it does not promote H04/H18 to PASS. Exit 2 means refusal or an invalid/control
failure. Do not update curated reports or the matrix until reviewing the actual
execution artifacts. Do not commit raw outputs.
