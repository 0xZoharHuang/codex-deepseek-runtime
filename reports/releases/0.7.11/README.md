# CDS 0.7.11 Files capacity candidate

Responses HTTP 413 stays on the existing native Codex capacity path. Files
HTTP 413 now surfaces a file-operation error while retaining the upstream
status as diagnostic data. The adapter distinguishes the file call from
model inference before the native client can treat it as a context limit.

An upload's HTTP error can leave its outcome unknown. CDS retains the
original journal entry and nonce, blocks a blind upload retry, and uses
existing reconciliation to verify and clean up the remote object. An
identity-bound journal made by an older CDS can still hold a status-only
rejection. It is reclassified in the active cache transaction without
contacting Files or changing its nonce. Independently proven rejections
remain retryable.

The production install source ID is `b233186048b9af5cdaf2`. On an isolated
macOS ARM64 install using P35, an actual official DeepSeek task sent 96 image
occurrences from 16 distinct originals. Its 51,922,155-byte Responses request
returned HTTP 413. Native Codex summarized the exact 80-image prefix,
reissued the current 16-image suffix in order and completed the turn.
The engineering changes passed a held-out oracle and nine workspace tests.
The native CLI explicitly compacted the resulting state and a new
process resumed the same thread. The cold turn kept the saved summary
and used one new image. Sixteen successful generations were matched to
official DeepSeek read-only metadata.

The original engineering run had a controller assertion that incorrectly
expected the later explicit compaction to include the already summarized
prefix. The initial stop remains in the private evidence. A separate,
single-turn cold continuation on the same durable thread closed the check
without reissuing prior tasks. The Files fault boundaries were exercised
through actual native CLI and synthetic local upstream events.
