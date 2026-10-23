# Temporary current-source qualification with a verified native build

The reuse workflow runs on disposable GitHub-hosted macOS ARM64 and Linux x64 runners. It downloads the two fixed artifacts from successful run **39899815305**, source commit 9e5f4a5b4312b5036c8b13655273073df5c371aa, using the official gh run download command. The workflow needs only contents: read and actions: read; the job token is provided only to acquisition.

The native bytes are reused. The current checkout's Python distribution and **every collected offline test** run again. The 15 real native transport cases execute separately and must exactly partition the complete collection together with the other cases. No test can disappear through deselection, an unexpected skip, a stale native report, or a changed adapter/source tree. The exact 31 Linux / 32 macOS platform-only skips are listed in ci/offline-platform-skips.json. Both phases treat warnings as errors and preserve raw logs on failure.

Reuse requires all of these checks:

- The fixed run, attempt, source commit, artifact ID, artifact name and GitHub-reported digest match the pins and the artifact has not expired.
- The native tarball matches the current platform manifest's archive SHA256. Every member is checked before extraction; unsafe paths, links, duplicates, unexpected members and privileged modes are rejected. The complete extracted inventory matches the manifest.
- The original build receipt remains unchanged. Its original global recipe SHA256 is verified against the recipe shipped in that artifact.
- Removing only Windows-specific recipe entries leaves an exact match with the current recipe. The selected target recipe, patch inputs, source hashes, upstream archives, compiler, metadata and complete package inventory must also match.
- Installed Python files equal the checkout. Current source hashes and independently collected cases are bound into both phase reports and checked afterward.
- Test subprocesses are bounded and identified by PID plus kernel identity. A timeout, unresolved identity, leaked process or forced cleanup is a failure.

Normal qualification pushes use the reuse workflow. The original native-platform-qualification.yml remains available through workflow_dispatch for an **explicit rebuild**. Reuse never silently changes artifact or recipe and never falls back to compiling.

These original artifacts currently expire on **2026-10-30**. Expiration, removal, changed target recipes or archive mismatch intentionally fail acquisition. To establish a new baseline, run the manual build workflow, inspect its native and offline proofs, then update the pins and manifests together. A new report does not renew or reinterpret an old build receipt.

This establishes synthetic-provider native transport and current-source offline qualification. It does not claim a real-model run or complete product-release approval.

Official references:
- [GitHub CLI: gh run download](https://cli.github.com/manual/gh_run_download)
- [GitHub: download workflow artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)

## Required release transition

This fixed Actions-run pin is a temporary qualification input for the current release; it is not a durable CI download source. Before calling the published release stable:

1. Publish the already verified tarballs under the exact filenames in the platform manifests. Keep their archive SHA256 values unchanged.
2. Publish the **original** recipe bytes, SHA256 9fe80f70bc477dc061992096c2cba8c1b4cbb16145e1646a0ddc111a81966c6c, as a versioned release asset such as native-build-recipe-9fe80f70.json. Do not substitute the current Windows-updated recipe or rewrite the old build receipts.
3. Change only the acquisition source from fixed Actions run 39899815305 to the pinned v0.6.0 release assets. Use the filenames and release URLs recorded in the current platform manifests, and pin the original recipe asset URL and SHA256 explicitly. Keep every archive/member/current-manifest/target-recipe/receipt validation and all current-source tests.
4. Run both GitHub-hosted jobs again through that release-asset acquisition path, preserving their proofs. Keep the manual rebuild workflow for intentional native changes. Never make an expired Actions artifact silently choose another run, model, recipe or binary.

The release-asset acquisition path is a required follow-up gate; this proposal does not claim it has already been implemented or verified. A release asset at a versioned URL may still be replaced by a maintainer, so checksum and complete inventory validation must remain mandatory.
