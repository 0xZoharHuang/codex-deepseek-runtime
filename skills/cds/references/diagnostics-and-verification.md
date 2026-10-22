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
| App-server cannot find the active turn | Different in-memory owner, wrong profile or stale expected ID |

Do not infer a CDS defect solely from a tool failure, and do not dismiss a reproducible adapter error as “the model.” Preserve the original outcome. Correct the demonstrated layer, then retest that affected behavior.

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
