# Installation and platforms

Use this reference for setup, credentials, updates, platform failures or cleanup. Preserve the selected product and host; using CDS is not a request to alter ordinary Codex/App installation or login.

## Establish what is installed

Locate a real executable, not only a shell alias. Read the distribution's installation metadata and check the version. `scripts/inspect_cds.py` can perform a bounded offline preflight; it does not call a model, inspect secret stores, run doctor, or establish end-to-end connectivity.

The published project is `https://github.com/0xZoharHuang/codex-deepseek-runtime`. The documented v0.5.0 baseline uses native `0.164.0-alpha.7`; treat it as a qualified fixed prerelease, not an automatically current upstream version. If the user wants an update, inspect current release/source provenance and compatibility rather than silently selecting `latest`.

Install with an independent supported Python and the matching source/native archive. Use the release's installer, exact archive SHA and dependency lock. A source tree, Python wrapper and arbitrary native Codex binary are not interchangeable pieces. Do not use a Codex App-bundled Python as a durable runtime base.

The normal installer supports a separate root and `--no-activate` for testing a candidate. Inspect its current `--help` before choosing options. Verify the candidate before switching an active entry. Preserve an unrelated existing executable or an installation you cannot identify as this distribution.

## State and identity

Typical roots are `~/.local/share/codex-deepseek` on macOS/Linux and `%LOCALAPPDATA%\codex-deepseek` on Windows. Runtime state uses the distribution's `codex-home`; use `CODEX_DEEPSEEK_HOME` to choose another independent profile. Never point it at Codex App's `~/.codex` state.

OS HOME, installation root, runtime profile, work directory and thread identity serve different purposes. Keep them explicit when moving between SSH, interactive terminals, users or machines. For recovery, retain the original profile; for a genuinely separate new worker, select new state deliberately.

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
