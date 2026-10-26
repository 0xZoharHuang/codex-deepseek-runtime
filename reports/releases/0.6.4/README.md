# 0.6.4 Windows critical-read readiness qualification

The fixed runtime scope passes the composed evidence below. This release updates
only the Windows critical-read readiness path and retains v0.6.3 Python response
identity validation. OpenAI model capabilities and cloud/account entitlements
remain separate from the local-runtime contract. App qualification belongs to the
independent gateway project.

## Problem and changed behavior

The provisioning helper previously treated a matching ALLOW ACE as sufficient
readiness evidence even when an effective DENY applied. Windows still enforced
its denial; this was a false readiness report, not an OS permission bypass.

The critical-read path now checks applicable DENY entries for the provisioner's
actual offline/online accounts and known normal groups before accepting a
read/execute grant. It preserves the DACL and returns the native error. Unrelated
SIDs, write-only denial and inherit-only entries do not become false read denials.
The global ACL grant algorithm, command execution, approval and agent loop were
not replaced. This check is not a claim to predict every future token or ACL change.

The source delta is Windows-only. Both complete source trees and exact patches
were independently reviewed; the final six registered native tests include
eighteen DENY branches and four negative controls. The original four critical
read/grant tests retain their file, sibling and write assertions. Original fixture
and collector failures remain separate evidence, not erased reruns.

## Actual current checks

Implementation qualification commit: 530627a59a59da57922981d7f863e43526fc7771.
Product source ID: 3ae81092a494b2abc1e1, 100 product inputs.
Later report-only commits do not change those product bytes.

| Check | Result | Evidence |
| --- | --- | --- |
| Windows normal candidate installation | Exact new archive, all 2991 files, immutable entry and installed adapter verified; no daily activation | [Windows qualification](windows-qualification.json) |
| Windows full current collection | 2286 passed / 4 platform skips, 2290 collected, no errors or deselection; strict public consumer passed | [Windows qualification](windows-qualification.json) |
| New Windows native transport | 21/21 separately, also included in the full Windows suite; results retained separately | [Windows qualification](windows-qualification.json) |
| Actual target-account readiness | Low client, real dedicated offline account, normal Full refresh: read succeeds; target RX DENY fails on the exact critical path without changing the canary ACL/content; restoring its exact ACL makes reading succeed | [Windows qualification](windows-qualification.json) |
| Actual workspace permissions | Complete effective write roots checked before effects; restricted non-admin user writes inside and is denied outside | [Windows qualification](windows-qualification.json) |
| Actual new CLI model turn | Three requests independently confirmed as DeepSeek/fixed canonical model; actual native file read and exact answer for a nonce absent from the prompt | [Live native read](windows-live-native-read.json) |
| Linux installed adapter | 2229 passed / 40 platform skips, plus 21 native transport and 171 controller checks | [Hosted readback](unix-installed-regression.json) |
| macOS installed adapter | 2228 passed / 41 platform skips, plus 21 native transport and 171 controller checks | [Hosted readback](unix-installed-regression.json) |

[Hosted CI 40266787792](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40266787792)
uses the exact published v0.6.2 Unix native assets. Original build recipes,
complete file maps, installed Python hashes, JUnit partition and process outcomes
were independently read back. No Rust rebuild or live model request ran in those
hosted jobs.

The Windows full suite uses an isolated QA installation with the same 29 Python
runtime files as production. Its trusted-root isolated pytest entry does not load
the source/src tree. High privilege is restricted to offline platform contracts
and one new owned namespace's normal ProvisionOnly setup. Actual runtime/model
work runs in the Low interactive session and real native sandbox.

## Retained failures and interpretation

This is composed qualification: single_run_all_green=false. A task-created
ancestor initially had the SSH elevated creator's group owner; the native owner
guard rejected it correctly. Only that proven new fixture root's owner was
corrected, with its DACL and FileID unchanged. An in-memory ACL-copy API mistake
occurred before writing any DENY. A collector initially searched for an inner
error in the RPC envelope; the final assertion pairs the outer RPC/path with the
newly appended native log cause. Both original failures remain.

A first outside-write fixture was actually inside the legacy API's implicit
server working-directory root. Its write remains recorded and is not labeled a
sandbox escape. The corrected test observes the complete effective root set and
matching native root count before any write. See the usage note below.

The actual live model turn first explicitly selected Bash, which the restricted
Windows environment rejected. The same model turn selected PowerShell and read
the file successfully. All two tool calls and all three model requests remain.
The controller did not replay the task. A display-path escaping mismatch in the
first collector was corrected using the structured read arguments, Windows path
normalization and actual FileID; it did not require another model call.

The independent complex Jinja/HTTPcore and full native mechanism evidence from
[0.6.2](../0.6.2/README.md) remains attached to the unchanged core source and its
original executions. In particular, the HTTPcore business-code failure remains
a failure even where its runtime recovery checks passed. This release does not
claim new complex-task success from a short file-read probe.

## Versions, delivery and cleanup

- Python distribution: 0.6.4; tested Windows CPython: 3.13.3.
- Windows native asset: 0.6.4, archive SHA
  aa9e3b2905c145ee79817c627f141f51bea7399e0911d28a56bdcaaa139707eb.
- Unix native assets: unchanged 0.6.2 bytes and original receipts.
- Upstream native runtime: 0.164.0-alpha.7 / fe136d4e70ef2b5bbed40a2d22b3aabf267796d3.
- Model: deepseek/deepseek-v4.1-flash-1017, with the verified dated canonical alias.

The original archive's build receipt records when build/focused tests completed
and installed qualification was still pending; it is not rewritten after testing.
The external installer manifest's ready field means the immutable package can be
installed and verified. Stable publication is a separate GitHub release action.

All observed qualification actors retired with no remaining or unknown-live
identities. One owned namespace and audit fixtures remain in the final cleanup
ledger; no completed uninstall is claimed here. Existing daily CDS and other
users' work were not stopped. The qualification used an explicit local archive;
public download readback is recorded separately after assets are published.
