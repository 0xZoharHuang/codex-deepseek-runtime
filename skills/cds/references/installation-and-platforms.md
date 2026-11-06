# Installation and platforms

Use this reference for setup, credentials, updates, platform failures or cleanup. Preserve the selected product and host; using CDS is not a request to alter ordinary Codex/App installation or login.

## Establish what is installed

Locate a real executable, not only a shell alias. Read the distribution's installation metadata and check the version. `scripts/inspect_cds.py` can perform a bounded offline preflight; it does not call a model, inspect secret stores, run doctor, or establish end-to-end connectivity.

The published project is `https://github.com/0xZoharHuang/codex-deepseek-runtime`. Release manifests identify the qualified native prerelease and platform artifacts. Historical version notes in this skill are not a current release selector. If the user wants an update, inspect the selected release/source provenance and compatibility rather than silently selecting `latest`.

Install with an independent supported Python and the matching source/native archive. Use the release's installer, exact archive SHA and dependency lock. A source tree, Python wrapper and arbitrary native Codex binary are not interchangeable pieces. Do not use a Codex App-bundled Python as a durable runtime base.

The normal installer supports a separate root and `--no-activate` for testing a candidate. Inspect its current `--help` before choosing options. Verify the candidate before switching an active entry. Preserve an unrelated existing executable or an installation you cannot identify as this distribution.

## State and identity

Typical roots are `~/.local/share/codex-deepseek` on macOS/Linux and `%LOCALAPPDATA%\codex-deepseek` on Windows. Runtime state uses the distribution's `codex-home`; use `CODEX_DEEPSEEK_HOME` to choose another independent profile. Never point it at Codex App's `~/.codex` state.

OS HOME, installation root, runtime profile, work directory and thread identity serve different purposes. Set `CODEX_DEEPSEEK_HOME` in the process environment of the selected CDS launcher before it starts; setting only `CODEX_HOME` or a later shell tool's environment does not select that wrapper's profile. Prefer invocation-local settings for separate workers. Changing a variable after an owner has started does not reconfigure its in-memory turn. For recovery, retain the original profile; for a genuinely separate new worker, select new state deliberately.

The adapter removes provider credentials and inherited App IPC identity before native tool execution. Project instructions and shared user skills retain native discovery behavior; profile isolation is not a claim that every file on the machine is inaccessible.

## Credentials and connectivity

Use the user's already authorized credential mechanism. `OPENROUTER_API_KEY` in the launching process takes precedence over the runtime's OS store. Do not place its value in command arguments, prompts, reports, source control or persistent shell settings.

`runtime setup-key` uses the supported store/input flow. Native `login`/`logout` are Codex account operations, not substitutes for OpenRouter setup. `runtime doctor` performs a real model request and provider verification; use it when connectivity verification is needed and within the task's authorized API usage. `--version` is not a connectivity test.

Do not read or display a key to “check” whether it works. If a user forbids a particular store or password prompt, respect that constraint; use another already authorized mechanism or ask for the missing choice. Do not extract secrets from unrelated apps, profiles, process environments or chat logs.

Keep model-request completion separate from later provider billing metadata. If metadata is temporarily unavailable, reconcile the original response ID with idempotent reads; do not repeat inference merely to obtain a fresh receipt.

## Platform-specific decisions

| Platform | Relevant differences |
| --- | --- |
| macOS | Keychain access may require user interaction; honor the user's preference. Seatbelt separately controls filesystem and Unix socket access. Login shells may change PATH, so pin required external tools. |
| Windows | Use the verified `.exe` launcher and argv arrays. Credential Manager is scoped to the actual user/logon context; SSH network logon may return 1312 even when an interactive user's store works. Use that user's authorized interactive context or secure process environment, not fabricated evidence from a different SID. Native sandbox setup may have OS/user requirements. Do not alter pre-existing sandbox accounts just to make a test pass. |
| Linux | GNU/libc and runtime libraries must match the published target. The package includes its qualified `bwrap` resource. Desktop Secret Service availability is distinct from headless SSH; use an authorized process environment when no session store is available. Do not replace the sandbox with an unqualified helper. |

The skill does not create administrator launchers, users, services, SSH access or global policy changes as an automatic fix. Those are separate actions only when the actual task requires and authorizes them.

For the recorded Windows SSH network-logon environment, PowerShell initialization returned `0xC0000142` under the native unelevated workspace sandbox. A no-model comparison reproduced the same failure when bypassing CDS and calling the same native binary directly; CMD worked under the same policy, and direct unsandboxed SSH PowerShell also worked. Treat this as an observed native/environment interaction, not proof of a CDS protocol defect or a guarantee about other hosts. If encountered, retain the policy and use a working native shell/tool within the existing authorization; do not automatically widen to full access or alter sandbox accounts. A future claim that PowerShell itself is fixed needs an actual PowerShell retest.

## Image-cache format maintenance

Use this section only for an upgrade, rollback or diagnosed image-cache format mismatch in a release that supports format 3 (the 0.7.0 maintenance interface). It is local protocol-resource maintenance, not native thread recovery. A fresh independent home initializes its own current format when the optional image cache is first used.

First let the selected home's work drain through its original controllers and quiesce that profile's launch/restart loop. Confirm the native clients, daemon and provider owners have actually retired using their PID/birth identities and lifecycle receipts. A controller reporting “stopped,” an old descriptor PID disappearing, or a free cache lock alone is insufficient. Do not stop another home's work or widen permissions to pass this check.

With the **new compatible absolute entry** and the original `CODEX_DEEPSEEK_HOME`, the current maintenance forms are:

~~~text
[cds_executable, "runtime", "image-cache-format", "--to", "3"]
[cds_executable, "runtime", "image-cache-format", "--to", "2"]
~~~

Use `--to 3` before starting the new provider against an existing supported old cache. For rollback, first drain the new owners, use the new entry's `--to 2`, verify completion, then select the retained old entry. Check the selected release's adapter documentation/source before using these forms; native top-level help does not list every adapter maintenance verb.

This operation reads no provider key, sends no network request and does not start the native runtime. It still mutates format metadata. The CLI checks registered provider/native lifetimes and leases. For an unregistered external controller, independently verify and retain its full owner-family retirement before using the supported controlled metadata API. That API's cache-level `complete` is not a provider-retirement witness.

Active or unknown owners, busy operations and unsupported/dirty ownership metadata must remain explicit refusals. Do not mix old and new readers of the same cache. Preserve assets, receipts and unknown pins, including those whose process has exited; elapsed time alone does not establish remote consumption. Never delete locks/salt/pins, hand-edit schema versions, or restore an older database snapshot over newer knowledge.

An interrupted transition needs its original output and process outcome inspected first. Once the original maintenance process has retired, use the same compatible entry and target format for an explicit resume. It resumes metadata publication, not uploads, deletion or inference. Require both exit code 0 and `complete=true`; then verify the intended runtime/task separately. Format completion is not model connectivity or whole-task acceptance.

## Update, rollback and cleanup

Use the verified distribution installer for updates. The upstream native updater could replace the provider integration, so CDS may reject that path. A native daemon update must retain this distribution's package, using supported `--from-cli` behavior when applicable.

Do not change code, provider service configuration or active pointers underneath running work. Prefer an isolated candidate profile and installation, then deliberately switch when its relevant checks pass. Keep the old release and metadata needed for rollback.

For cleanup, identify ownership and live processes first. Remove owned rebuildable caches or closed temporary environments; preserve user sessions, work, meaningful failure evidence and required rollback material. If compressing evidence, verify archive contents before deleting originals. Measure actual free-space changes without double-counting nested roots, hard links or sparse VM disks.


### Isolated profiles and the native temporary-directory guard

Keep `CODEX_DEEPSEEK_HOME` outside the launching process's
`TEMP/TMP/TMPDIR`. Native Codex intentionally refuses to create its
PATH helper aliases when the home is inside that temporary tree. A zero exit from
initialize or `--version` does not establish full helper/tool coverage.

Use an existing user data base with sibling `home` and `tmp`
directories. On Windows a suitable base is under `%LOCALAPPDATA%\cds-cases\<id>`,
not under `%TEMP%`. Select `<base>\home` with
`CODEX_DEEPSEEK_HOME` and, only in the worker process, set
`TEMP=TMP` to `<base>\tmp`. On POSIX use a user-owned
data base and a sibling `TMPDIR=<base>/tmp`. Preserve OS HOME and the
normal runtime sandbox/approval policy. Do not disable the native guard or treat
the warning as successful tool qualification.
