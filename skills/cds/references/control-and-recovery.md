# Control and recovery

Use this reference before steering, queuing, stopping, resuming, forking or cleaning up a running task. Preserve thread, turn, item and process identities separately. “Accepted,” “consumed,” “applied” and “completed” are different observations.

## Choose the right control surface

| Intent | Native mechanism | Required observation |
| --- | --- | --- |
| Add work for a later turn | `queue` / `thread/queue/add` | Queue ID; later actual new-turn execution |
| Add input to the current turn | `turn/steer` on the original app-server | Correct active turn ID accepted; subsequent effect |
| Cancel an active turn | `turn/interrupt` | Matching native interrupted terminal event |
| Stop an owned headless exec | Ctrl-C/SIGINT to its verified process | Actual exit; inspect surviving owned work |
| Stop a yielded terminal/tool process | Native background-terminal/process control | Exact process/session stopped |
| Continue persistent work | Exact-ID resume in the original profile | Same session/history and new requested result |

The pinned CLI `queue` persists input for an idle-scheduled **new** turn. It does not inject that input into the active turn. Interrupted threads are not automatically restarted just because a queue exists. Do not repeat a queued stop instruction until it “takes”; inspect only queue items you own and preserve other senders' work.

Before resuming with revised instructions, check whether your earlier queued stop/correction is still pending or has been consumed. If superseded and still pending, use the owner's supported queue update/delete operation for that exact item, then read back its state. On this baseline, `thread/queue/update` and `thread/queue/delete` are experimental; obtain their IDs/fields from the actual queue and schema. Do not clear unrelated items or create a second active owner merely to edit the queue. Without a safe supported control surface, make the pending-item conflict explicit before continuation.

## Existing exec --json task

Exec's stdin/stdout are not a JSON-RPC control channel. On the pinned baseline, `thread.started` exposes a thread ID but exec's `turn.started` does not expose an active turn ID. Do not invent one or use a new unrelated server to steer it.

The original controller should verify its process identity, send Ctrl-C/SIGINT, and wait for exit. Exec JSONL is not the raw app-server event stream; do not wait for an object it does not emit. Intentional interruption may return a nonzero code. If the controller sent a signal to an entire process group, that scope must be recorded rather than conflated with a single-PID signal.

A controller-owned exec is not a detached service. Keep the outer tool handle and the controller/native PID plus creation identity, and persist events incrementally with the real process exit status. A terminal tool reporting an unknown handle, an EOF, or the disappearance of both controller and CLI without a native terminal event is an **unknown outcome**, not proof of native completion or a CDS crash. Freeze the partial events and stderr, then reconcile the original thread and owned effects before exact-ID resume. Never replay the whole prompt simply because the outer handle was lost.

POSIX start_new_session changes session/process-group ownership; it does not by itself establish a surviving controller, continuously drained pipes, durable output, or freedom from an outer broker's cleanup policy. Windows process/job ownership is a separate boundary. Do not silently detach every task or replace native cancellation to mask an unobserved controller exit. For sustained work with live control, use the original supported app-server/daemon lifecycle and verify its actual owner/connection and transport; merely starting a second server against the same home cannot recover an in-memory turn.

A yielded tool may outlive turn interruption. Check its exact owned process/session before resuming or launching the same operation again. Do not kill by executable name or assume that a stopped model proves a stopped child tool.

Resume using the original CDS home and exact UUID:

```text
[cds_executable, "-s", authorized_sandbox, "-C", original_cwd,
 "exec", "--json", "resume", exact_thread_uuid, "-"]
```

Supply the newest instructions on stdin. On the pinned version, `-s/-C` must precede `resume`; root `-a` must precede `exec`. `--json` is global within exec. Check help for a different installed version. Changing `CODEX_DEEPSEEK_HOME` can make the original session unavailable; changing OS HOME is not a recovery technique.

## Long-lived app-server owner

Keep the original connection, or reconnect to the same owned server using its supported native transport. The same filesystem home does not make two servers share an in-memory active turn. Use `initialize` and `initialized`; opt into experimental APIs only when using the corresponding features.

Save `turn.id` from `turn/start`, or the ID from the original `turn/started` event. Steer that turn explicitly:

```json
{"jsonrpc":"2.0","id":21,"method":"turn/steer","params":{"threadId":"<thread-id>","expectedTurnId":"<active-turn-id>","input":[{"type":"text","text":"<new instruction>"}]}}
```

A wrong expected ID should be rejected. Do not remove that guard to get a success response. A successful response acknowledges input acceptance; verify its subsequent use before claiming that behavior changed.

For cancellation:

```json
{"jsonrpc":"2.0","id":22,"method":"turn/interrupt","params":{"threadId":"<thread-id>","turnId":"<active-turn-id>"}}
```

Wait for the matching `turn/completed` with `status="interrupted"`. A `{}` response is the request acknowledgement. Use `thread/backgroundTerminals/list` and precise `terminate`, or authorized `clean`, for separate background cleanup. A `process/spawn` session has its own handle and `process/kill`; it is not automatically the same thing as a model tool's background terminal.

`features.instant_interrupt` affects active-input preemption. On the pinned source it is under-development, default true; a profile may override it. It does not change queue semantics or guarantee termination of existing tools. The pinned CLI has no top-level `cds steer` command and no registered `thread/input/append` API.

## Recovery after unknown outcomes

Before retrying, reconcile receipts, file effects, operation IDs and process state. Provider metadata delay does not justify replaying a completed model request. A failed wrapper after a successful provider operation must not erase the original result. Resume native state when it exists; do not synthesize replacement history or manually edit its database to force success.

Fork when the task requires a separate branch of history; otherwise preserve the original thread. Archive/delete and queue-removal operations require their actual intended scope. Skills do not authorize discarding a user's history.
