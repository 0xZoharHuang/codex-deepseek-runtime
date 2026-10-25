# Parallel Windows candidate installation

Use the [main README](../README.md) and [0.6.2 qualification record](../reports/releases/0.6.2/README.md) for the current candidate's status and matching artifacts. This page retains generic Windows installation boundaries and earlier snapshots; the [0.6.0 candidate guide](release-0.6.0-candidate.zh-CN.md) is historical context, not current qualification.

This plan uses the existing immutable installer and profile launchers. It does not replace the installed stable CDS, its global entrypoint, or any active native process. Candidate evidence and published release identity are separate: a local hash is not a published Git commit or GitHub release.

## Why use a new root

`install.py` already supports `--root` and `--no-activate`. Native packages are stored under an archive-hash directory and adapter environments under `releases/<source_id>`. Invalid existing slots are preserved and repaired into new slots. However, even `--no-activate` initializes and protects `root/codex-home` (inside `install()`, before its activation check). Therefore the first candidate installation must use a new project-owned root, not the old installation root.

The installer now compares each platform manifest's `native_recipe_id` with the target-specific source/toolchain/patch recipe. An older `ready` record cannot qualify a new combination. Old immutable installed releases keep their original code and metadata and remain usable. The new recipe pins the project's qualified Rust toolchain to 1.99.1 and separately records the upstream source's 1.95.0 pin. Windows, Linux and macOS each require their own native qualification; source patch application on Windows does not qualify another OS.

## Preparation and activation scope

1. Complete the matching CLI, HTTP helper, Code Mode Host, sandbox setup/command runner/deny probe/service package and its source/build receipts. Include the official canonical resource layout and upstream notices. Verify the helper's separate build provenance honestly; do not invent a single cargo invocation for components built separately.
2. Produce a complete local archive and matching Windows manifest only after its native acceptance gates close. Local candidate metadata must identify its unpublished state; do not put an unverified public commit into the receipt.
3. Choose a new, previously absent installation root with an ownership receipt. Run the existing installer with `--root <new-root> --no-activate --native-manifest <qualified-local-manifest> --native-archive <qualified-local-archive>` and the locked wheelhouse when available. The explicit local manifest is checked against the same target and source recipe and does not replace the repository's published platform manifest. The archive override is still hash-checked. This creates a fresh `codex-home` and a new immutable release. It does not activate the global command.
4. Use the new release interpreter to call the existing `windows_launchers.profile_launchers(<new-root>/codex-home)`. Its recipe pins the interpreter, installed package and home, and generates immutable `codex.exe` and `codex-deepseek.exe` entries with a manifest. Use the returned absolute `codex-deepseek.exe` path for subsequent tasks. A pinned entry overrides a stale inherited `CODEX_DEEPSEEK_HOME`; an unpinned generic release entry alone does not.
5. Validate that exact entry: offline version/integrity, isolated runtime status, actual model/provider connectivity, native sandbox and representative work. Read the configured OpenRouter credential only from the authorized Limited InteractiveToken account. Coordinate elevated native sandbox provisioning separately; do not give a high-privilege maintenance wrapper new upstream-credential work.
6. Hand off the explicit entry path, profile path, native/adapter identities and verified resume command to dependent tasks. Keep the current global `cds` and old releases in place. A future global promotion is a separate atomic entrypoint operation that verifies ownership and refuses a locked or unrelated file.

## Existing mechanisms reused

- `install.py`: immutable native/archive cache and release slots, locked requirements, source attestation, complete native manifest checks, no-activate installation and atomic managed entrypoint replacement.
- `windows_launchers.py`: immutable profile-specific executable launchers and repair slots. It enters the normal CDS CLI without a new relay or runtime.
- `cli.py`: explicit home bindings, per-profile native command paths and isolated model/provider configuration.
- `service.py`: profile-specific gateway state/port/leases, immutable code identity and refusal to replace a mismatched provider while native sessions are active. Its active check is not an atomic migration lock and must not authorize a concurrent home migration.
- Native namespace patch: distinct sandbox principals/FW/WFP identities and matching protected ownership for each new home, while preserving the native sandbox algorithms.

The upstream OpenRouter credential may be read through the existing dedicated Windows credential entry. MCP credentials, image-upload ledgers, transport receipts, gateways, daemon files and native session state belong to the new home. Do not copy old `.sandbox`, `.sandbox-secrets`, `.sandbox-bin`, `provider-state`, rollouts or login files into this fresh candidate profile.

## Legacy home migration and rollback

There is no existing atomic legacy-state-backup-and-migrate command. The existing native setup flow is reusable; a new generic supervisor or agent lifecycle system is unnecessary. A home with active processes must not be migrated. The published old `sandbox uninstall` acts on shared legacy machine resources and must not be used as a per-home migration shortcut.

For this candidate, rollback means stop only its own tasks/services, retain its evidence, and continue using the untouched stable entry. Retain uploaded-file receipts until owned remote files are deleted/read back or unknown state is explicitly preserved. Removing a local cache does not perform remote cleanup. Any later same-home migration requires a separate quiescent-profile backup/rollback procedure that preserves the root directory identity, sessions and projects and accounts for legacy ACLs. No old live home is migrated by this plan.

## Current implementation versus remaining delivery

The immutable slots, no-activate mode, explicit local-manifest selection, profile launcher, provider active-session protection and new target recipe gate already exist in source. The build recipe and packaging/installation boundary tests pass. Remaining delivery is the fully qualified native package, real new-profile sandbox/workflow acceptance, actual independent installation and explicit entry handoff. No stable installation has been changed by this source/design work, and no new macOS native result is claimed.

## Earlier Windows acceptance snapshot

The local package at that earlier stage completed the actual CLI retry/compaction fixtures and a real official-DeepSeek large-image request through the new native HTTP helper. The dedicated native sandbox ProvisionOnly setup succeeded in a new home; exact before/after comparisons preserved the existing shared sandbox and separate App namespace users, password timestamps, group membership, firewall rules, WFP objects and service registry metadata. The first provisioning fixture failed before setup because it omitted the CDS policy configuration that the normal wrapper writes; that original failure is retained.

Real Limited-token commands verified workspace reads/writes, denial of writes outside the workspace, and read-only denial of writes both inside and outside it. Read-only PowerShell retains native ConstrainedLanguage; the initial fixture's .NET object creation was correctly rejected, so its subsequent permissions check uses native cmdlets. Offline public TCP was denied and the explicitly online private identity could connect.

**Preserved original failure:** an offline child connected to an owned random loopback listener despite `allow_local_binding=false`, an empty proxy-port allowance and active, correctly scoped block rules. The observation remains a failure. Later v6 fixtures observed the intended offline policy and explicit exceptions, while separate cold-reprovision failures remain retained. The final v7 recipe adds execution-time WFP readiness and protected artifact read roots; its exact package still needs its own native and installed-workflow receipts before release. None of these component results delivers a stable 0.6.0 release or authorizes changing machine-wide firewall settings, existing homes or active installations.
