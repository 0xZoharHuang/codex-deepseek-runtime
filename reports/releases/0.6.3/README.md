# 0.6.3 response identity qualification

The current adapter regression and Windows installed live checks passed. This
release preserves the exact v0.6.2 native packages and their original build
recipe; no Rust rebuild or native repack was performed.

A response that explicitly declares a different model or a provider inconsistent
with the route selected for that request now fails. A provider being allowed for
a different request is insufficient. Omitted or null identity remains unknown;
verified equivalent model aliases and provider display names/slugs are accepted.
This is a response contract check. No real upstream misrouting incident was
observed.

The adapter does not replay a failed request. A tool event already delivered
before a late contradiction remains delivered; the failure does not erase
possible side effects or attest the response as completed.

## Current evidence

Implementation qualification commit:
521a62a0fd7d37f1d3868f920e478552f99f2097. The implementation changes one production
Python file, gateway.py, plus the package version. Final publication adds only
documentation and qualification receipts.

| Current check | Result | Evidence |
| --- | --- | --- |
| New real loopback HTTP identity boundaries | Original 31 failed / 21 passed; repaired 52 passed | [Targeted record](targeted-response-identity.json) |
| Existing focused adapter regression | 98 passed | [Targeted record](targeted-response-identity.json) |
| Windows complete current collection | 2281 passed / 4 platform skips; 2285 accounted once | [Windows qualification](windows-qualification.json) |
| Linux current installed adapter | 2224 passed / 40 platform skips | [Hosted readback](unix-installed-regression.json) |
| macOS current installed adapter | 2223 passed / 41 platform skips | [Hosted readback](unix-installed-regression.json) |
| Linux and macOS native transport | 21 passed per platform, plus 171 controller checks each | [CI 40242229086](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40242229086) |
| Windows actual installed positive route | Doctor and saved-thread ACK: 2 real requests, independently observed DeepSeek and fixed canonical model | [Windows qualification](windows-qualification.json) |
| Windows restricted runtime and cold resume | Restricted non-admin execution, inside write, outside denial, same-thread history/file readback, two EOF/exit-0 servers, natural process drain | [Windows qualification](windows-qualification.json) |

The Windows full suite uses a separately installed QA wheel whose 29 runtime
source files match the production installation byte for byte. Its isolated
pytest entry admits the trusted repository root but not source/src. It preserves
the public collector and strict consumer assertions. High privilege is limited
to the offline platform fixtures; actual model and native execution use the
normal Low interactive session.

The Linux/macOS artifacts were downloaded independently, CRC/SHA checked, and
their JUnit, installed adapter hashes, unchanged native packages, complete
collection partition and process results were read back. They contain no real
model requests and no native builds. Transient process observations remain in
the raw records; zero remaining or unknown-live processes is not a claim that
every intermediate observation was certain.

## Final installed documentation derivative

The full Windows qualification binds product source09e4a55a30a9454f7196.
Final README wording produces source22e550a37fc4cf78aaf6. Only README differs
among the 99 product inputs. The 29 installed runtime source files, 2991 native
files, catalog, runtime manifest and dependency lock are byte-identical.

The final source was normally installed with no activation. Its real version
and exec-help entry points passed. Launcher executable bytes match, with only
the exact immutable site-packages path changed in the embedded launcher
program. This derivative used no new model requests or native build; it does
not relabel the source09e4 results. [Exact derivative receipt](windows-readme-derivative.json)

## Scope and retained boundaries

The [v0.6.2 runtime qualification](../0.6.2/README.md), including complex tasks,
original failures and native mechanisms, remains evidence for the unchanged
native and common components. Its HTTPcore runtime checks and model-generated
business-code outcome remain separate; no new engineering benchmark success is
claimed here. None of these CLI results qualifies Codex App.

Native assets still come from the exact v0.6.2 release URLs and checksums in the
source manifests. All adapter and native versions remain explicit. The upstream
Codex baseline is still 0.164.0-alpha.7, not an upstream stable release. Model
quality, unsupported modalities and OpenAI cloud/account entitlements remain
outside the local-runtime equivalence claim.

Installation does not imply activation of a machine's daily command. In the
qualification host, the old daily launcher was kept while an existing console
held it open; no other task was terminated. Use the explicit immutable entry
and an independent home when validating in parallel.
