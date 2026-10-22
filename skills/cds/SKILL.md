---
name: cds
description: "Use codex-deepseek (CDS), the native Codex CLI runtime for DeepSeek, when the user chooses it to perform work or asks to install, configure, control, recover, or diagnose a CDS task. Do not switch ordinary coding or generic Codex questions to CDS, or confuse it with unrelated tools named cds."
---

# CDS

This is primarily an operating skill for an agent that launches or controls CDS. Inside an existing CDS session, use only the relevant native-tool and diagnostic guidance; this skill does not add a second controller or agent loop.

Operate the user's selected CDS installation. CDS preserves native Codex execution, approvals, sandboxing, sessions, tools and agent scheduling; its adapter supplies the model protocol, provider routing and platform integration. Treat model quality, runtime behavior and provider/cloud availability as separate questions.

## Establish the execution boundary

Identify the requested host, actual executable, work directory, new or existing session, and authorized scope. Reuse known answers from the current task. A parent's permissions do not automatically configure a child CLI, and the ability to launch full access is not itself authorization to do so.

When installation identity is uncertain, use `scripts/inspect_cds.py --entry <actual executable>` or native help/version from a previously verified entry. The helper is offline and does not prove model connectivity. Do not resolve an unrelated `cds` program or vanilla `codex` as the DeepSeek runtime merely because the name/version looks plausible.

If already executing inside CDS, use the current native tools and session; do not recursively launch another CDS merely because this skill is loaded. A separate worker or session requires a task reason and the user's intended scope.

When the user has delegated execution to CDS, use the host to prepare the bounded launch, observe it and verify results. Let CDS perform the requested business/tool work. Do not silently take over that work with host commands or another agent after a failure.

## Choose only the needed reference

| Task | Read |
| --- | --- |
| Start interactive or headless work; set permissions and toolchain | [Launch and permissions](references/launch-and-permissions.md) |
| Steer, queue, stop, resume, fork or manage background work | [Control and recovery](references/control-and-recovery.md) |
| Use native tools, collaboration, Code Mode, MCP, plugins, skills, hooks, memory or web | [Native capabilities](references/native-tools.md) |
| Install, supply credentials, update, roll back or work across Windows/Linux/macOS | [Installation and platforms](references/installation-and-platforms.md) |
| Diagnose failure, test integration or assess a completion claim | [Diagnostics and verification](references/diagnostics-and-verification.md) |
| Check version-sensitive syntax, sources or compatibility limits | [Sources and compatibility](references/sources-and-compatibility.md) |

Read the selected reference rather than every file. Use the installed version's help or schemas for exact flags and API fields; these references explain decisions that help avoid known misuse, not a replacement command catalog.

## Execute and retain control

When launching CDS from a controller, use an argument array and bounded process interface. Inside CDS, use each native tool's documented schema; a tool accepting shell text still requires that shell's syntax. Keep credentials out of argv, prompts and logs. Use the already authorized credential mechanism; skill activation does not authorize reading secret values, changing stores or widening permissions. Preserve OS `HOME`; select CDS state with `CODEX_DEEPSEEK_HOME`, keeping it separate from Codex App state.

For a fresh task, select one appropriate native mode. For an existing task, preserve its executable, profile and identity. Keep the original process handle or app-server connection so later control reaches the actual owner. Record the thread ID and, for app-server control, the active turn ID. Retain a running tool's session handle instead of starting duplicate work.

`queue` acknowledges persistence for a later turn. `turn/steer` targets an active turn on its original app-server. `turn/interrupt`, exec process interruption, and termination of a yielded background tool are distinct operations. Follow [control and recovery](references/control-and-recovery.md) before using them; never label `Queued` as “applied.”

On failure, locate the failing layer before retrying. Preserve completed side effects, the original error and the relevant identity. Correct a demonstrated configuration or protocol problem within the user's scope; do not modify a third-party tool's transport, weaken validation, or change the native runtime's business logic to manufacture a pass.

## Verify the requested result

Check the result that matters: actual file changes, program tests, tool/provider output, session continuity, permission denial, or process termination. A successful launch, queued message, schema export, zero wrapper exit or model's prose alone may not establish that result.

Report what CDS actually did, where its evidence/output is, and any remaining limitation. Distinguish current execution from reused evidence, runtime success from model-generated business correctness, and accepted control input from its eventual effect. Use focused checks for the change; a routine task does not require the full release qualification suite.

Clean only owned, rebuildable temporary artifacts after their processes have stopped. Keep the user's work, sessions, meaningful evidence and needed rollback state. Do not rewrite an active profile or upgrade an installation out from under its running tasks.
