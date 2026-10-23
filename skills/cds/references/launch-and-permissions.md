# Launch and permissions

Use this reference when starting work or integrating a local tool. Resolve the host and executable from the user's request or existing job; do not move work to another machine merely to avoid a configuration problem.

## Direct CLI use and delegated work

Users can run CDS directly like Codex CLI; ordinary editing, tests and native tools do not depend on this skill. Inside CDS, use this skill when its operational guidance is relevant and keep work in the current session.

An outer agent may launch CDS as a separately owned worker through exec or app-server. It must retain the child's process/connection, cwd, CDS profile, task identity and outputs. This is external delegation: it does not automatically register the worker in the parent's native `spawn_agent`/wait/close lifecycle. Use those native collaboration tools for subagents inside a CDS session. Do not claim the two lifecycles are interchangeable or build another scheduler to hide the distinction.

## Pick the native mode

- Interactive work: invoke the verified CDS executable in the chosen project directory. The native TUI owns input, approvals and session interactions.
- Bounded headless work: use `exec --json`, send the prompt via stdin, and retain the process handle and JSONL output.
- Work requiring reliable live control: hold the original `app-server` connection from thread creation. Read `control-and-recovery.md` for the ownership and event contracts.
- Existing work: resume the exact thread in its original CDS profile. Do not use `--last` when other tasks may be concurrent.

A typical headless invocation is an argument array, not a shell command assembled from untrusted strings:

```text
[cds_executable, "-s", selected_sandbox,
 "exec", "--json", "-C", project_directory, "-"]
```

Supply stdin with the user's concrete objective, constraints, expected result and stopping conditions. Add `--skip-git-repo-check` when the intended workspace is not a Git repository. Do not create a repository merely to satisfy a launch check unless the task calls for one.

On the pinned `0.164.0-alpha.7` baseline, headless exec defaults to Never approvals; root `-a` is not a separate `exec resume` option. For interactive use, native approval choices apply. Check current help rather than assuming a parent app can answer a child CLI's approval requests.

## Permissions are an explicit choice

Use the task's approved scope. Ordinary work can retain the native workspace boundary; read-only work should not receive broader write access. A specifically authorized trusted local tool task may use invocation-local `-s danger-full-access`. Do not infer authorization solely from the parent process's sandbox mode, and do not quietly change global defaults.

`workspace-write` plus no approvals can legitimately reject external caches, child tools or local IPC. Repeating the same denied operation will not fix it. Determine the actual cwd, writable roots, network/IPC policy and native effective configuration.

The app-server's initial cwd can be a writable root in legacy workspace-write mode. A denial fixture must be outside **all** writable roots, including implicit temporary/state locations. For named profiles, verify `default_permissions`, the selected profile, and the `config/read` result; merely writing a configuration file is insufficient.

If the user's restriction literally includes every runtime/state/cache write, `workspace-write` alone is not proof of compliance. Identify all effective writable roots and place owned state/temp/cache directories within the permitted boundary when supported, preserving OS HOME. If that literal boundary cannot be enforced, resolve the specific mismatch before launching dependent work; do not silently reinterpret it as only project-file edits. Ordinary project-editing scope does not require this extra audit unless the user actually imposes that stronger boundary.

Unix socket permission is separate from directory write permission. On the pinned version, selected named profiles use `permissions.<name>.network.unix_sockets` and the applicable native network-policy configuration. Do not reuse obsolete `allow_unix_sockets` keys or open all sockets as a speculative fix. Also do not assume a no-model OS probe proves the full model/tool workflow under that profile.

## Outbound network is separate from workspace-write

For the pinned native baseline's **legacy workspace-write profile**, an already authorized network task can retain the filesystem sandbox and enable networking for that invocation:

```text
[cds_executable, "-s", "workspace-write",
 "-c", "sandbox_workspace_write.network_access=true",
 "exec", "resume", exact_thread_id, "-"]
```

Use this only for an existing task in its original profile; a fresh task uses the normal `exec` form. Supply the prompt through stdin. This is a supported configuration example, not a promise that every installed version/profile accepts the same syntax or that every destination is allowed. Read back the active configuration and actual tool result. Named permission profiles and managed network rules must use their own documented configuration; do not assume a legacy flag overrides a selected named profile or an administrative restriction.

On Windows, complete the native sandbox setup for the selected backend before relying on command effects. The fixed baseline can return an effective `readOnly` thread policy when the Windows backend is unset, even if configuration text says `workspace-write`. Inspect the native `thread/start` or `thread/resume` policy response as well as `config/read`. Declaring `windows.sandbox="elevated"` alone does not prove setup has completed; use the native setup/readiness flow. The setup elevation prepares restricted execution, not permission for tools to run with unrestricted administrator access.

Keep approval policy and network policy distinct. Headless Never approvals cannot satisfy a later interactive escalation request. If the task does not authorize network access, preserve the denial. If it does, use the supported narrow network configuration rather than `danger-full-access` or a global default change.

On a delegated failure, compare policy and identity before comparing availability: parent-host DNS success plus child `curl` exit 6 is not evidence of an upstream outage. Preserve HTTP 000/zero-byte outcomes as “no HTTP response observed,” and follow [network diagnostics](diagnostics-and-verification.md#shell-network-errors-do-not-prove-a-remote-outage).

## Pin the actual toolchain

Use compatible, explicit tool executable paths when PATH may differ between the controller, login shell and child. A configured PATH prefix alone may be replaced by shell startup files. Preserve OS `HOME`; configure tool-supported cache/temp variables or approved writable roots instead of masking failures with a different home.

For platform-specific syntax, use argv arrays. Avoid Python or shell source embedded through multiple quoting layers. A script's bookkeeping may fail after the underlying tool succeeds; retain both the raw provider/tool result and the wrapper exit code before considering a retry.

On Windows, distinguish the controller's argv from command text passed to a native shell tool. Inspect the actual shell: CMD, Windows PowerShell and PowerShell 7 do not share quoting rules. CMD does not treat single quotes or `\"` as POSIX-style quoting. Prefer writing a small script with native file/patch tools and invoking its path over repeatedly embedding Python/source through nested `-c` or `/c` strings. Verify output encoding when Unicode paths/data matter. An OS shell initialization failure must be distinguished from a syntax error before retrying; preserve its exit code and the effective sandbox/logon context.

## REA as a verified example, not a universal dependency

The recorded integration used original REA 6.4.0, supported Node 24.19.0, Ghidra 12.1.4 and the caller's compatible JDK. Its authorized full-access launch preserved HOME and used:

- Absolute Node and original `scripts/rea.mjs` paths.
- `JAVA_HOME`, `GHIDRA_INSTALL_DIR`.
- `GHIDRA_HEADLESS_MAXMEM=512M` and `REA_GHIDRA_STARTUP_TIMEOUT_MS=180000`.
- A private, short, dot-free temporary directory.

CDS compiled a synthetic target and invoked the original Ghidra provider once, obtaining matching binary identity and recovered function evidence. REA remained unchanged. This demonstrates one supported integration, not a reason to require these versions or full access for every CDS task.

REA 6.4's ownership helper deletes inherited `CLANG_MODULE_CACHE_PATH`; blindly setting it does not redirect that helper's default Swift cache. Ghidra also rejects dot-prefixed project path components. Keep these requirements conditional on this tool/version. Do not substitute TCP/file bridges or disable checks when the user's request is to use original REA.
