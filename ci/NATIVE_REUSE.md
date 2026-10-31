# Current-source qualification with pinned native release assets


> Current source candidate: the shared Guardian provider transport guard changes
> both Unix recipe identities. Run native-platform-qualification.yml for a fresh
> build. The retained qualified-native-build.json describes historical 0.6.2
> artifacts, and the 0.6.7 manifests remain rollback provenance. Neither is a
> qualification of this new source. Reuse entry points intentionally reject the
> current branch rather than rewriting old pins or accepting an older binary.

The reuse workflow runs on disposable GitHub-hosted macOS ARM64 and Linux x64 runners. It downloads reviewed native bytes from the fixed public prerelease **v0.6.0-native.1** in this repository. This prerelease contains native build assets; it is not approval of a stable CDS product release.

Acquisition uses canonical versioned URLs under:

`https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/v0.6.0-native.1/`

| Target | Archive asset |
| --- | --- |
| macOS ARM64 | `codex-deepseek-native-0.6.0-aarch64-apple-darwin.tar.gz` |
| Linux x64 | `codex-deepseek-native-0.6.0-x86_64-unknown-linux-gnu.tar.gz` |

The original recipe asset is `native-build-recipe-9fe80f70.json`, with SHA256 `9fe80f70bc477dc061992096c2cba8c1b4cbb16145e1646a0ddc111a81966c6c`. Archive hashes and byte sizes are fixed in `ci/reuse_native.py` and must agree with the current platform manifests. The normal installation manifests are unchanged; CI deliberately selects this separate native-assets prerelease rather than changing their product-release URLs.

No API key or GitHub token is sent to these public asset downloads. The workflow needs only `contents: read` for checkout. It does not use Actions artifact retention, a mutable latest URL, another release, or automatic compilation as a fallback. A missing, truncated, oversized or changed asset fails acquisition and preserves the failure evidence.

## What gets verified

- The target, ready status, exact asset filename and archive SHA256 agree with the current manifest before download.
- Each asset has its exact pinned byte size and SHA256. Every tar member is checked before extraction; unsafe paths, links, duplicate or unexpected members and privileged modes are rejected. The complete extracted inventory matches the manifest.
- The original build receipt remains unchanged. Its original global recipe SHA256 must match the downloaded original recipe.
- Removing only Windows-specific recipe entries leaves an exact match with the current recipe. The selected target recipe, patch inputs, source hashes, upstream archives, compiler, metadata and complete package inventory must also match.
- The separately downloaded upstream source archive retains its fixed commit URL and checksum.
- Installed Python files equal the current checkout. The explicitly declared native transport cases and the remaining offline tests must exactly partition the complete current collection. No deselection, unexpected skip, stale report or changed source can qualify.
- Test processes are bounded and identified by PID plus kernel identity. A timeout, unresolved identity, leaked process or forced cleanup is a failure.

The exact platform-only skip inventory remains in `ci/offline-platform-skips.json`. Both test phases treat warnings as errors and retain raw logs on failure. Current qualification reruns the Python tests and native transport cases; it does not rebuild unchanged native binaries.

## Provenance and deliberate upgrades

The Unix native archives originally came from Actions run **39899815305**, commit `9e5f4a5b4312b5036c8b13655273073df5c371aa`. Acquisition records that historical run, artifact identity and digest as provenance only. No Actions API or artifact-download branch is used to acquire bytes. Moving the unchanged tarballs to a release does not rewrite their build receipts or turn their historical build into a new build.

A versioned GitHub release asset can still be replaced by a maintainer. The checksum, byte-size and complete inventory checks therefore remain mandatory. To change native bytes or a target recipe, use the manual `native-platform-qualification.yml` workflow, review the resulting native and offline evidence, publish a new explicitly versioned asset set, and update the pins and manifests together. A failed acquisition never initiates that upgrade.

After changing acquisition or its pins, run both hosted jobs through the actual release-asset path and retain their proofs. Successful acquisition establishes input integrity; it does not by itself establish real-model behavior, App GUI acceptance or complete product-release readiness.

Official references: [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases), [Linking to releases](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases).

## Retained 49da native-only forensic qualification

The registered native-platform-qualification.yml dispatch uses retained_mode=true\nand retained_target=linux-x64 to call retained-native-qualification.yml at the\nsame candidate commit. The default retained_mode=false preserves the original\nfresh-build job. The helper is workflow_call-only, a single-target recovery job.
Its separate retained-native-build-49da.json pins the real compiled package and
proofs from the original Actions job. The full original workflow/adapter failure
is preserved. The entry point accepts only concrete build, pool10 and native22
step successes, exact artifact/package/receipt digests, unchanged complete Rust
source/lock/patch/test/driver inputs, and exact original test identities.

It does not run Cargo or recompile Rust. It carries only the original unchanged
Rust component scope, then reruns the current-source native22 partition and full
installed-adapter offline collection. An old summary, skipped test, changed
native input or expired artifact is rejected. No ready manifest is fabricated.
Each reviewed target may be dispatched independently; an unlisted or expired target fails closed.

These Actions artifacts expire. This explicit diagnostic path is **not a stable
installer/default dependency** and has no fallback to another build. Once the
new native release is fully qualified, publish permanent versioned native
archives/receipts and use their reviewed immutable release-asset pins for normal
long-term qualification. Keep the expiring input only as historical provenance;
do not make users rely on a seven-day CI download.

### Reviewed 49da inputs and expiry

Both entries below belong to original run **41178694277**, producer commit
**49da17e1f72c1effde8aeb6c4f3432ca50eab2a5**, attempt **1**. The original
overall workflow and both adapter jobs failed; only their exact successful
build, Guardian-pool10 and native22 scopes are eligible for carry.

| Target | Original job | Artifact | Expires (UTC) |
| --- | --- | --- | --- |
| Linux x64 | 124471467160 | 13036790418 | 2026-11-07 08:18:40 |
| macOS ARM64 | 124471467446 | 13039727032 | 2026-11-07 10:30:52 |

The Mac artifact was checked independently against the official completed job
and artifact metadata, its complete package inventory, original receipt and
the unchanged current native source/controller/test inputs. Its original
adapter run reported **1 failed, 2441 passed, 45 skipped**: an unclosed
SQLite connection surfaced as an unraisable resource warning during the
malformed-SSE case. The failure remains a failure. Closing the known legacy
fixture connection in current source does not itself establish that the new
Mac run passed.

Select the registered **native-platform-qualification.yml** entry with
retained_mode=true and retained_target=macos-arm64 for a bounded current
qualification without Cargo. The new workflow must execute the actual
current native22 partition and the remaining complete installed-adapter
collection. Adding this pin neither reruns those cases nor extends the
separately observed Linux qualification to Mac.

These exact expiry dates are availability limits, not permission to weaken
live metadata/digest checks. On expiry or any origin/content mismatch,
acquisition fails and retains evidence. Normal stable installation must use
reviewed permanent release assets after complete qualification; it must never
depend on the expiring artifacts or automatically rebuild as a fallback.
