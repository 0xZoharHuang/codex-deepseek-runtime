# Current-source qualification with pinned native release assets

## Current default: permanent v0.6.9 P27 assets

Both registered reuse entries now explicitly select
`ci/qualified-native-build-0.6.9.json` through the single controller `PINS` value.
It records real release 430798112, the published annotated v0.6.9 tag resolving
to a70417460d823a906ebc7274834e2412be1ebe53, and actual asset IDs, sizes and hashes.
The original native producer remains a5853bf6c3468af3a93bc0094bf09d1df4158693.
The temporary a585 and historical0.6.2/49da input files stay unchanged; they are
not the current default and there is no fallback to them.

All ten public fixed-URL asset bodies have joint exact SHA/size readback evidence:
nine on Windows and the Linux archive on macOS, without authentication or cookies.
The Windows Linux-archive GET/reset/timeout and HEAD failure remain failures.
This does not replace Linux's own permanent-download CI, native22 or software
qualification. The newly selected permanent path must still be run on both hosted
targets; dataset creation and local guards alone do not declare that run passed.

The older version-specific narratives below are retained historical provenance,
not instructions to select their original assets for the current release.


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


## Retain an original native archive when preparing a permanent asset

Use the existing `package_native.py` utility's explicit archive-input mode when a reviewed native package is already retained as a TAR. Preserve the qualified archive identity when staging Unix assets on Windows. A fresh archive changes its byte identity and replaces original directory/ownership/timestamp metadata. The existing fresh packager already assigns canonical executable modes; this input mode does not claim that its default path drops those execution bits.

```text
python package_native.py --existing-archive ORIGINAL.tar.gz \
  --expected-target TARGET --archive-sha256 TRUSTED_SHA256 --archive-size TRUSTED_BYTES \
  --manifest PRIVATE_CANDIDATE.json --url DECLARED_VERSIONED_ASSET_URL
```

This mode is mutually exclusive with the existing `--package ... --archive ...` fresh-packaging path. Supply the independently reviewed archive SHA256, byte size and target. The utility reads but never extracts or rewrites the input archive. It verifies every regular file against the original build receipt, metadata, current full recipe and toolchain, canonical resource layout, and target-specific executable modes. It permits the original root/parent directories and the canonical empty resource directories; it rejects redirected/special members, duplicate or unsafe paths, unknown PAX semantics, privileged or inappropriate modes, incomplete TAR/gzip endings, missing or unattested files and mismatched targets/receipts/recipes. Output must be fresh and is UTF-8 with LF newlines.

The standard manifest's `status: ready` means only that this local artifact meets the existing installer artifact contract. It does not publish it, prove that the declared URL exists, establish model/runtime acceptance, or approve a stable release. The archive-input CLI reports `public_release_asserted: false`. Keep candidates outside tracked `native/runtime-*.json` files until all required qualification is complete. A release owner then publishes the exact original bytes and performs independent asset download/hash verification before promoting permanent release-asset pins. The default fresh-package path and standard installer manifest schema remain unchanged.


## Permanent P27 source contract

The registered retained controller now supports an explicitly selected
`source=release-asset` dataset in addition to its existing Actions source. Its
current `PINS` constant selects the real permanent0.6.9 dataset described above.
Its descriptors came from actual published metadata and exact public byte
readbacks, with no placeholder IDs. The workflow reads and preserves the
controller's selected dataset; it does not keep a stale hardcoded copy or select
a file merely because it exists.

The permanent dataset records the real release ID, known version/tag pair
0.6.9/v0.6.9, resolved publication commit, and exact recipe asset. Each target
records the actual native archive and small scope-bundle asset ID/name/byte
size/SHA256, the ready manifest identity, and all original compiled/carry fields.
The recipe uses the same descriptor. `release_archive`, `release_scope`, and
`release.recipe_asset` descriptors contain `id`, `name`, `bytes`, and `sha256`.
Versioned URLs are constructed only under this repository and the explicit tag.
No arbitrary base URL, latest release, model, runtime source or native recipe is
introduced. The old 0.6.2 pins and historical failed49da records stay unchanged.

Permanent acquisition checks live release metadata, uploaded asset identities,
resolved lightweight or annotated tag commit, exact size/digest when provided,
and independently downloaded bytes. The API token is used only for repository
metadata, never in argv or sent to release storage. Public asset redirects are
restricted to the official release storage host, not arbitrary URLs.

A small bundle contains the original eight native scope records, original full
recipe/source-commit bytes, and original raw terminal run/jobs/artifacts JSON in
`producer/`. Exactly those 13 regular-file members are admitted. No native tar,
binary, cache, live model context or credential enters the scope bundle. A bundle
container is an explicit byte-preserving packaging derivation, not a new native
build or new Rust test run. Mac's raw receipt identity remains the hash of the
original TAR member; a parsed-and-reserialized audit copy is not raw evidence.

The controller separates historical qualification identity from current delivery
liveness. Actions acquisition still requires a currently unexpired artifact.
Permanent acquisition instead validates the current published assets and the
byte-pinned historical snapshot, preserving its original expiry fact without
claiming the old Actions artifact is currently available. There is no CLI
ignore-expiry flag and no fallback between sources. Complete source/recipe/
compiler/checkpoint/State22/Guardian10 carry checks and fresh current-source
native22 plus installed offline qualification remain mandatory.

After publication and dataset promotion, execute both hosted targets through
that actual permanent path. A successful temporary-source run is not evidence
that permanent downloads work. Keep the original producer a585 and the later
publication/Python commits distinct; publishing unchanged tarballs never changes
their build receipts or original qualification limits.


### Explicit P35 macOS requalification

`native-platform-qualification.yml` accepts `retained_mode=true`, `retained_target=macos-arm64`, and `retained_pin=macos-p35-f958`. The default permanent 0.6.9 pin remains unchanged. The CLI equivalent is an explicit `ci/reuse_retained_native.py --pins ci/retained-native-build-p35-f958.json` with its usual output arguments.

This selects original run 41839521557 and exact artifact 13346764748. Its native build, State, Guardian, capacity-core and native transport steps passed; its complete offline step failed six cases. That failure is preserved in the selected records. Requalification executes the current full native/offline collections without Cargo, and does not itself qualify model behavior, other platforms or the App. The temporary artifact expires; stable releases require separately verified permanent assets.
