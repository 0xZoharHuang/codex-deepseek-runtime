# 0.6.6 Windows qualification and release provenance

This directory records the measured Windows 0.6.6 qualification. The complete
installed SDK and real DeepSeek task passed in the explicit scopes below.
Publication is a separate step: at the final local signoff no GitHub release or
main merge had occurred. The release URL becomes usable only after the exact
matching assets and their hashes are published and read back.

Earlier component and regression sections preserve their original scope and
failures. Historical pending-gate statements are superseded only by the explicit
final qualification and README-identity reconciliation sections below.

## Changes

- Custom tool calls retain final-only input tails. Native completed tool items
  are authoritative over uncommitted argument previews; invalid or revised
  previews cannot dispatch a tool early. Bounded diagnostics retain hashes and
  reason codes rather than raw code, call IDs or user text.
- The Windows-only metadata layer uses current authorized paths and concrete
  local namespace subjects. It removes the historical metadata journal, raw
  NTFS deleted-object proof and extra exact-revocation engine. Native uninstall
  continues to preserve user files and ordinary filesystem permissions.
- The native version, CDS namespace domains, background-helper handle whitelist,
  WFP readiness, managed deny-read revocation and native runtime loops remain.

## Artifact identity

| Item | Value |
|---|---|
| Python adapter candidate | 0.6.6 |
| Native upstream | 0.164.0-alpha.7 / fe136d4e70ef2b5bbed40a2d22b3aabf267796d3 |
| Five-binary producer commit | 37787ecf1882521b168ec8cc16d5dcd5894b2572 |
| Prepared native source | 9c33e0369dd955f2715d52fbdd3d19265d6ff49a29bf77ad9d777685b308a6ff |
| Windows recipe | 410a7343667baecf2a8de222e32a09a858eaa626706ba9b65222fc5135dc1ef2 |
| Raw recipe | 4729f350664964b19fd6b8876e40a2e4fa4166b388b4a18bf3b438bab554f4ff |
| Native compiler | 1.99.1, original release flags, two jobs |
| Windows archive | codex-deepseek-native-0.6.6-x86_64-pc-windows-msvc.tar.gz |
| Windows archive SHA256 | 59fbae5093423746d3625876e82d7a4106a1dae9fb1def53ba3abeade811c322 |
| Windows manifest SHA256 | a50e8069549bdcb02c77aa588a48ddbd68352bbd41bb6d4e72335b2545445dfb |

macOS and Linux keep the exact 0.6.2 native bytes and their original producer
receipts. Their source recipes did not change. Reuse is not a new native build
or a fresh cross-platform runtime acceptance pass.

## Executed scopes and retained failure

The fixed official source archive and all public Windows patches were replayed
to a new directory. All 9376 file contents/sizes matched the admitted canonical
source, with exactly five changed files. The complete five-target production
build finished normally: 1214 cached artifacts, 67 compiled artifacts, compiler
and original process/resource closeout passed.

The complete native layout contains eight unique binaries and two canonical
Windows resource mirrors. The unchanged HTTP helper's 222 normal/build
dependencies do not reach WindowsSandbox. All 59 local transitive source inputs
also match the historical HTTP producer map; official host/ripgrep pins remain.

The new helper was copied, hashed and actually executed using normal Windows
protection. A Low-owned user-directory home and native elevated provisioning
created only that fixture's local namespace. Native uninstall/idempotence removed
only its users/group and WFP objects; shared account properties and existing
process identities were compared before/after.

The original first cold command used a 45-second test deadline and failed with
zero command effect. Its project was below the AppData inheritance domain being
updated in the background. The main helper completed after about 83 seconds and
the background helper after about 101 seconds; both exited naturally. Source
and timing support a legitimate related-domain wait, not a claim of a captured
historical wait chain. This failure remains failed. Native lock/deadline policy
was not changed. A later same-case warm result is separately recorded.

One additional genuine cold fixture used a Low-owned protected home and an
independent, owned D-drive workspace. Its first ordinary native command finished
in 3.074 seconds while the same-birth background read-ACL helper remained alive.
The actual sandbox account wrote the allowed file, received a real Windows
AccessDenied on an outside canary, and left the canary unchanged. The native cwd
junction was resolved and checked against the physical workspace FileID.
Both fixture namespaces were natively uninstalled and checked idempotently.

These observations do not establish a Defender classifier root cause or future
immunity. They also do not establish complete launcher, model-quality or real
business-task acceptance. Exact temporary retired-SID filesystem inventories
remain private for the final scoped QA cleanup; no general ACL recovery engine
was added.

## Initial pending gates and their disposition

- Complete installed SDK/wrapper qualification: passed in the current-source
  installed scope recorded below.
- Real DeepSeek task and independent checks: passed for the pinned Jinja case
  recorded below; historical failed attempts remain failed.
- Version/source/archive/README reconciliation: measured separately below.
  Publication and public asset readback remain a distinct delivery step.
- Final host-level project cleanup remains separate from the completed
  per-fixture native namespace uninstall and idempotence checks.

The old 0.6.5 and earlier reports remain immutable historical evidence.

## Retained packaging correction

The first private archive (`5aba077f...`) was rejected by the unchanged strict
installer before creating an SDK release. Hard-linked staging of the two Windows
resource mirrors had been serialized as TAR link entries. The normal packager
now emits independent regular-file byte content, keeps the symlink rejection and
does not relax installer validation. A real hardlink-staging regression failed
before the fix; the complete packaging suite then passed under a Limited Windows
token, including deterministic duplicate archives. The failed archive and
isolated installation root remain private evidence; no active entry changed.


### Windows SQLite sidecar regression retained

The first complete High source-suite candidate run collected 2357 cases: 2347 passed,
6 image-provider cases failed, and 4 named OS-inapplicable cases skipped. The
failing image database filename was 253 characters and its rollback journal was
261 characters. SQLite returned SQLITE_CANTOPEN while beginning the transaction;
the resulting HTTP 500 and cancellation assertions were retained as failures.

Independent new-database controls reproduced the failure with ordinary Windows
filenames. An explicit extended-length Windows filename preserved transactions,
rollback journals, Unicode filenames and escaped read-only URIs. The adapter now
formats SQLite I/O filenames centrally at all 13 connection sites. Logical Path
ownership/link guards, schemas, journal mode, transaction boundaries and read-only
policy are unchanged. The normal installed SDK and complete regression must be
requalified before this candidate is promoted; no native recompilation is needed.


### Installed-package acceptance origins

The original Low and High complete-suite controller added ROOT/src to
PYTHONPATH. Those retained aggregate runs are source-suite evidence, including
their original failures; matching bytes do not establish installed import origin.
The separate installed native-transport and focused SDK checks retain their
actual installed scope.

The public complete-offline command now uses the selected immutable environment,
-I/-B and trusted repository QA helpers without ROOT/src. It checks installed
adapter bytes before and after execution, rejects foreign lazy product imports
before execution, checks loaded product module locations throughout the suite,
and hashes an import-origin receipt into the verdict. Fresh/current receipt
assembly requires and validates that evidence; explicit historical producer
receipts remain marked as unverified for installed import origins. Existing
hostile-cwd/PYTHONPATH test IDs also cover the full controller and lazy-import
negative control. No test IDs were removed and no extra skip was introduced.


### Ordinary Windows home ownership regression

The first complex-task launch failed before native initialization, sandbox
provisioning or model inference. Its ordinary pre-created home was already owned
by the current user and granted inherited Modify rights. The old private-home
constructor requested WRITE_OWNER unnecessarily and failed with Winerror5;
read-only same-inode controls showed WRITE_DAC without WRITE_OWNER succeeded.
The candidate now sets only the DACL for an already-correct owner. Exact protected
legacy BA-owner migration retains explicit owner-write reopening, same-file
verification and the existing rejection of foreign owners and reparse paths.

The original home, SDDL, raw stderr and failed task artifacts were retained.
Secondary missing-reviewer database errors now remain explicit failures and
cannot replace the primary startup/exit/stderr evidence. Installed Low/High
security-focused and normal native/model workflow qualification are separate
gates; this correction alone does not mark the complex task complete.


## Final Windows qualification: benchmark producer

The benchmark-qualified immutable adapter is source ID `896f79f4cf091a83e368`
(product source `bd464109…`). The complete installed-only High suite at
`6e6a304d…` collected the same 2368 declared IDs: **2364 passed,
0 failed, 4 exact OS-inapplicable skips**. All product imports came from the
immutable environment; the independent public receipt validator accepted the
origin, collection, JUnit, source and launcher bindings.

A real pinned Jinja repair completed through DeepSeek, native Windows commands
and a native read-only reviewer. The agent changed production symbol tracking and
two test files. Independent checks ran **915 workspace tests and 908 held-out
tests**, all passed, with imports bound to the modified production tree. Native
MCP browser evidence includes open, positive find and click to a distinct official
documentation page: templates → API. The oracle read guard and controlled inputs
were retained and the 4247-file Python 3.11 environment stayed byte-identical.

Current-source reconstruction uses all of the paid task's root and native child
events and all 98 request/receipt pairs. All 14 runtime mechanisms pass in that
explicit **current 896 source scope**. One HTTP-200 request was cancelled
downstream and remains cancelled; `single_run_all_success=false`.
The earlier c221 startup failure and the separate initialize-only smoke remain
separate phases with original hashes. The unscoped whole-history reconstruction
remains failed; its result was not relabeled or rewritten.

The fresh native namespace's group/offline/online SIDs 2137/2138/2139 were removed
by normal native uninstall, followed by a successful idempotent uninstall. Its
exact WFP provider and sublayers are absent. Default SAM password timestamps,
enabled state and membership, known default WFP getters, and the existing CDS,
official SYSTEM sandbox service and gateway process births stayed unchanged.
The project-owned High worker exited normally and its temporary task was removed.
Native filesystem policy None preserves existing user/session/workspace
permissions; cleanup did not introduce an ACL recovery engine.

The earlier rejected hardlink archive, source-importing aggregate runs, SQLite
long-journal failure, original Winerror5 startup, verifier masking error and
stale skip-catalog failures remain retained as failures. The normal corrected
archive is `59fbae5093423746d3625876e82d7a4106a1dae9fb1def53ba3abeade811c322`;
the wheel is `6e93be1b1b388e1906ffbbf25247f8790bf8e4235f0cb8e53d3c728c5fc2baa9`.
No new native compilation was performed for the Python/QA corrections.

## Cross-platform scope

The macOS and Linux native assets remain the exact published 0.6.2 producers and
recipes; they were not rebuilt or relabeled as new Windows outputs.

| Target | Native archive SHA256 | Native recipe |
| --- | --- | --- |
| macOS arm64 | `38f748b4398d65810184d226879d51d5246e81c15ff9d24cb1fd6f94d0a415d9` | `02e413453fbecd93bdebad2ba5f7033a7ea201206b3a708e5593b84b62b424d1` |
| Linux x86_64 GNU | `94758a863ffe644bb3b8477b611014c43c657fdda00ac668d87fecb540fb8651` | `3e752d53b8122d0402cd89850d1368585c8ff6dc800336e796a7875028a745ad` |

This run did not execute fresh Mac tests. The primary Mac was untouched. A bounded
read-only query of the existing Windows WSL environment timed out after 30 seconds,
so no fresh Linux SDK execution is claimed; no distro, user, global package,
daemon or proxy settings were changed. Portable protocol and non-Windows filename
fallback branches remain covered by the declared Windows-hosted tests; that is
not described as native Linux/macOS execution.

At the final local qualification signoff, publication, active installation
changes and final host-wide project cleanup remained separate delivery gates.
The original local evidence is preserved; later publication does not rewrite it.


Requested provider routes in the Jinja proof are explicit: `deepseek` for
the official default route and `morph/fp8` for native peer/continuation
histories with hosted tools. They remain the same fixed DeepSeek model; these are
recorded intentional routing rules, not request-failure fallback or a claim that
all requests used the official provider. Requested route names do not establish
the independently attested identity of every upstream provider.

## Final README identity and published-artifact candidate

The final root README changes the normal installer source identity to
`759aa4e7585bc09eeb5f`. It does **not** change the product implementation:
the source-identity map differs from benchmark source `896f79f4cf091a83e368`
only at `README.md`. The product source remains `bd464109…`, the complete
2368-case qualification producer remains `6e6a304d…`, and the real Jinja
proof remains explicitly bound to benchmark source 896. Those historical
receipts are not relabeled as a new run under 759.

A normal offline `--no-activate` installation created the new immutable 759
release under the ordinary Limited admin token. All 29 installed product files
and the native package path/archive are byte-identical to 896. A fresh process
imported all 28 product modules from the new immutable environment. Actual
`--version` and `--help` returned zero with empty stderr; native initialize,
config/read and natural stdin-EOF returned zero. No thread/turn was started,
there were zero model receipts, and the exact native setup/namespace-owner
markers remained absent. Existing CDS PID identity remained unchanged.

The first private smoke worker had a final QA assertion which treated existence
of `.sandbox` as provisioning. Native initialization legitimately writes a
sandbox-selection log there. That worker failure and the initial help/version
warning about the not-yet-created home are retained; the corrected probe checks
the actual native marker paths, and help/version on the normally initialized
home have empty stderr. No product code, security policy or provisioning
mechanism was changed to satisfy that probe.

The normal final wheel has the same 58 ZIP member names as the benchmark wheel.
Its 56 product/license/entry metadata members are identical; only METADATA
(the exact updated README body, unchanged metadata headers) and RECORD change.
The benchmark test/model evidence is transferred through this measured
product/native byte identity. The full suite and paid task were not replayed
merely for a README-only identity change.

| Release asset | SHA256 |
| --- | --- |
| `codex_deepseek_runtime-0.6.6-py3-none-any.whl` | `7fd2d1968fcd9ed9034f24b8dbb0082bf586b1028f7f663cd0d587c1a9c3ed31` |
| `codex-deepseek-native-0.6.6-x86_64-pc-windows-msvc.tar.gz` | `59fbae5093423746d3625876e82d7a4106a1dae9fb1def53ba3abeade811c322` |
| `runtime-x86_64-pc-windows-msvc.json` | `a50e8069549bdcb02c77aa588a48ddbd68352bbd41bb6d4e72335b2545445dfb` |

The former `6e93be1b…` wheel remains the benchmark producer; it is not the
final README-bearing release wheel. macOS/Linux native asset pins above remain
unchanged. This reconciliation adds no fresh Mac/Linux or additional paid-model
execution claim.

