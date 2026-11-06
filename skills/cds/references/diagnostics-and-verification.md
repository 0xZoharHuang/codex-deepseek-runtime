# Diagnostics and verification

Use this reference for an actual failure or when the user requests acceptance/audit. Do not turn every ordinary CDS task into a release qualification project.

## Locate the failing layer

Collect the smallest relevant non-sensitive evidence: actual executable/version, argv, cwd, profile identity, native permission readback, thread/turn/process identity, exact error and observable effects. Do not dump broad environment values, credentials, unrelated prompts or private research data.

| Observation | First distinction to make |
| --- | --- |
| Command not found, unsupported Node/JDK, helper fails before analysis | Toolchain/launcher selection versus runtime failure |
| Cache write denied or Unix socket bind EPERM | Actual child filesystem and IPC policy, independently |
| `Queued`, but current behavior is unchanged | Persisted next-turn input versus active steer |
| Steer accepted, but an old command still runs | Sampling input versus already-running tool lifecycle |
| Nonzero wrapper exit after provider output | Wrapper bookkeeping versus provider completion/effect |
| CLI exit 0 with unusable output | Process success versus actual requested result |
| HTTP/model error with unknown side effects | Request/receipt state versus retry safety |
| Works on host but fails when delegated | Different user, permissions, cwd, HOME, PATH, tool versions or transport |
| Shell DNS failure, `curl` exit 6 / HTTP 000 / zero bytes | Child network policy versus DNS/route/proxy/TLS/server availability; no HTTP response was observed |
| App-server cannot find the active turn | Different in-memory owner, wrong profile or stale expected ID |

Do not infer a CDS defect solely from a tool failure, and do not dismiss a reproducible adapter error as “the model.” Preserve the original outcome. Correct the demonstrated layer, then retest that affected behavior.

## Passive startup and state inspection

When the task permits only read-only diagnosis, keep the diagnostic passive. The default `scripts/inspect_cds.py --entry <actual entry>` reads local installation metadata and launcher consistency; omit `--offline-native`. Help/version still execute a native process. `runtime status` enters Store/provider lifecycle, `runtime provider status` takes its configuration lock, and `runtime doctor` performs inference. With image transport enabled, `runtime image-status` also resolves the credential scope and opens its journal. None is a substitute for a passive state sample.

Use the original owner's already authorized observation channel. Read only the selected installation/profile's bounded non-secret projections: entry/version and effective home, PID/birth ancestry, relevant file metadata, and the matching stderr/terminal or operation receipt. Do not dump full gateway descriptors (they can contain local authentication tokens), full environments, prompts or unrelated history. A different SID/logon context succeeding is a contrast, not recovery of the original owner.

For a schema that exposes `runtime_owners`, an existing SQLite database opened in read-only mode can provide a bounded phase witness without constructing `Store` or invoking its reconciliation:

~~~sql
SELECT rowid,pid,birth,supervisor_pid,supervisor_birth,
       native_pid,native_birth,closed
FROM runtime_owners
ORDER BY rowid DESC LIMIT 12;
~~~

Check the actual schema first; report missing data rather than create or repair it. Close the read transaction promptly. If the user forbids even SQLite sidecar activity, use an approved consistent snapshot supplied by the owner. File mtime, a stale descriptor or a numeric PID alone cannot identify the blocked startup call or prove retirement.

Observe local launcher/import/verification, provider readiness, actual native creation and RPC initialize separately. Record when the initialize timer was armed, the SDK's effective deadline and actual EOF/exit alongside stderr; a host's total launch interval is not the configured RPC timeout. Windows venv redirectors can make `Popen.pid` differ from the Python IPC owner, so verify the stable parent edge and birth instead of guessing by process name. Preserve an early process exit as that outcome rather than relabeling it as a model failure or extending a timeout before locating the wait.

## Shell network errors do not prove a remote outage

Record the failing child command's exact exit code, stderr, bytes received and current thread/turn. `curl` exit 6 means that this invocation could not resolve its destination. HTTP 000 is not an HTTP response, and zero bytes do not prove that a remote service is down or that content does not exist.

Check the **child's effective native policy** before another attempt. A parent host resolving the same name is a useful contrast, but it does not share the child's sandbox, proxy, resolver or network identity. A host DNS success alone neither proves nor rules out a CDS defect. State an unproven diagnosis as “resolution failed in the child; sandbox/network policy has not yet been excluded.”

`workspace-write` and tool network access are separate settings. Search and the bundled web reader also have their own execution paths; a working `--search` does not grant shell commands network access. `-a never` prevents requesting an approval; it does not authorize outbound networking.

When the task already authorizes the required public-network operation, configure the supported **invocation-local** network policy and read back the effective setting. See [Launch and permissions](launch-and-permissions.md#outbound-network-is-separate-from-workspace-write). Preserve the original failed result and then make only a bounded, appropriate continuation. Do not silently switch to full access, another endpoint, a host-side download, or a different transport to manufacture success. Unknown outcomes or side effects still require reconciliation.

## Active lifecycle probes can time out before CDS runs

When an active-only status probe times out, preserve its exact argv, original deadline, process birth and outcome. Record import and function boundaries, plus whether the parent received the result and EOF. A fast function phase is not the complete subprocess round trip; a timeout in `communicate()` alone does not locate the blocked layer. SQLite's connection timeout bounds busy-lock waiting, not interpreter startup or the entire probe.

Do not substitute `runtime status` for a passive probe: it opens Store and enters provider lifecycle handling. `runtime provider status` also takes the service configuration lock. A lease check's nonblocking lock does not bound its earlier file open.

On Linux, correlate a real stack with guest `MemTotal`/`MemAvailable`, memory and IO PSI, and actual overlapping tool processes. Free Windows RAM does not override a WSL guest limit; no OOM event does not prove there was no reclaim pressure. Treat unavailable metrics as unavailable. WSL memory settings apply to the actual Windows user's WSL2 VM, so confirm all consumers and existing authority before changing them. A particular memory limit from one workload is not a universal CDS requirement.

Fix the demonstrated layer, preserve the original failure and recheck the same affected concurrency and resource peak under the original deadline. Unknown ownership does not authorize forced replacement; the confirmed original owner may still perform its normal failure cleanup and verify retirement.

## A useful integration acceptance

When needed, use a small, owned, non-sensitive fixture and the user's original unmodified tool. Let CDS launch the actual tool when the requested workflow is CDS-owned. Verify the returned provider identity/result against the real input and effects; do not substitute a host execution and call it CDS acceptance.

For permissions, include an authorized positive path and a nearby denied path. Verify actual bytes/process/socket outcomes in addition to effective configuration. For control, bind receipt, thread, turn and process identities, and distinguish admission from later execution. For native schema comparisons, report schema equivalence only; it does not itself demonstrate all tools work.

Choose a finite stopping condition appropriate to the task. After a deterministic denial or a completed-but-misrecorded operation, inspect the evidence before another launch. Do not repeatedly start a heavy analyzer or model run while an earlier one is still alive.

## Claims must match evidence

Separate:

- Static source inspection.
- Offline unit/protocol tests, including mocks.
- Real native API calls with observable effects.
- Model-driven tool workflows.
- Complex application/business outcomes.

Native RPC counts are not counts of independent model tools. A passing runtime mechanism does not guarantee the model produced correct application code. No finite test suite proves every input, failure case or future version.

Use historical evidence only with an explicit unchanged-component/impact argument. Preserve the original source/build, verifier identity, failures and actual scope. Do not relabel old runs with a new source ID or present original failures plus supplements as one clean run.

For the published v0.5.0 record, Windows has a composed standard suite and 131 local RPC witnesses; Mac/Linux include explicit component lineage. This is background provenance, not a requirement to rerun that suite for every task. Current feature/version changes must be assessed on their actual impact.

## Diagnose without taking over business work

If the user requested CDS to do the work, the controller may inspect operational evidence and fix its authorized launch/configuration. It should not secretly perform the business operation with host tools or another agent. If CDS cannot finish, report the concrete obstacle and continue only the independent work still authorized.

Keep original tool transport and validation when those are part of the requirement. Replacing a Unix socket with TCP/files, disabling validation, or patching a third-party analyzer changes the experiment; it is not a valid acceptance of the original tool.

A focused fix may be documentation or a supported configuration. Change adapter/native code only when a demonstrated implementation defect warrants it. Preserve native scheduling, approvals, sandboxing, recovery and lifecycle logic; do not build a second runtime to work around uncertain behavior.
