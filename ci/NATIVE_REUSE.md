# Current-source qualification with pinned native release assets

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
