# CDS candidate: Windows sandbox and MCP credential isolation audit

Historical source finding: this page records the pre-namespace payload candidate. Later namespace, loopback, readiness and artifact read-root overlays are ordered in `native/runtime-overlays.json`. Current 0.6.0 release conditions are in [the candidate guide](release-0.6.0-candidate.zh-CN.md); the original failure analysis below is retained rather than treated as the latest package status.

This is a read-only source audit of Codex fe136d4e70ef2b5bbed40a2d22b3aabf267796d3 with CDS 83056e7 published overlays and the payload transport/413 candidate patches. It is not a new provisioning experiment. No installed CDS, other user's state, credentials, Windows principals, firewall, or WFP configuration was read or changed.

## Windows sandbox: original release-blocking finding

Separate CODEX_HOME directories do not isolate machine resources in this source version:

- `codex-rs/windows-sandbox-rs/src/setup.rs:53-54` defines fixed `CodexSandboxOffline` and `CodexSandboxOnline`; payload constructors at 351-353, 1163-1164 and 1244-1245 use them. `winutil.rs:29` fixes `CodexSandboxUsers`.
- `setup_provisioning/sandbox_users.rs:86-97` generates fresh passwords each provisioning, calls `ensure_sandbox_user`, and stores passwords in the requested home. `ensure_local_user` at 137-149 resets the password of an existing machine account with NetUserSetInfo. `setup.rs:195-196` puts the encrypted secrets in each home's `.sandbox-secrets`. Thus a subsequent home's provisioning can invalidate the first home's saved credentials. The machine setup mutex serializes writers but does not give each home a separate identity.
- `setup_provisioning/firewall.rs:35-47` fixes rule names; 303-330 selects/reconfigures the same named rule. Proxy-port policy can be replaced by a different home's provisioning.
- `wfp.rs:76-77` fixes provider/sublayer GUIDs; 92-94 deletes and replaces the same fixed filter IDs. Deletion at 102-123 also operates on the shared IDs. A new set of account names alone would not fix this.
- `provisioning_protocol.rs:14` fixes the named pipe; read-ACL mutex names, group lookup/ACL subjects, provisioning-service payloads and command-runner synchronization also require coherent namespacing.
- `uninstall_windows.rs:159-187` removes shared WFP/FW/hidden-user/group resources; `uninstall_windows/firewall.rs:19-25` deletes the fixed rule names. Any backport must cover teardown and recovery, not only first install.

Minimum repair is to backport the App C native namespace mechanism into this source baseline: one deterministic validated home/product identity propagated through account/group resolution, payload verification, ACL subjects, desktop/cache identities, FW names, WFP IDs, setup/provisioning synchronization and uninstall. Keep native provisioning, tools, permissions, and process execution. Do not implement a separate sandbox or rely on changing CODEX_HOME alone. Legacy resource adoption/deletion must be explicit, never automatically reset or remove old global principals.

Build impact: the HTTP helper does not depend on Windows sandbox code and should retain its qualified binary hash. A repaired native distribution needs a rebuilt CLI plus matching sandbox-setup and command-runner binaries, and namespace tests plus real isolated provisioning/read-write-deny/network/teardown checks. With the current independent release cache, the sandbox crate and reverse-dependency/link stages should be incremental; budget roughly 10-30 minutes and under 3 GB additional cache, an estimate until measured. The source freeze at that time applied to the then-running 413-only CLI build. This finding blocked claiming that initial candidate was safe to provision beside another independent Codex home.

## MCP: the same user-global keyring defect is already addressed in CDS

- `rmcp-client/src/oauth.rs:105-117` selects `Codex DeepSeek MCP Credentials|<canonical-home SHA256 prefix>` when `CODEX_DEEPSEEK_POLICY_HOME` is present and rejects a mismatch with canonical CODEX_HOME. Without CDS selection it deliberately preserves upstream service naming.
- Direct-keyring load (422), save (558), and delete (744) all use this service selector. Auto/resolved store paths call these same helpers (`oauth/resolved_store.rs`) and do not bypass the selector. Tests referencing the legacy constant are test-only, not additional production writers.
- The Secrets backend constructs its manager with current home and McpOAuth namespace (`oauth.rs:448-454`, 754-760). The encryption key's account hashes canonical home (`secrets/src/lib.rs:184-202`); a constant service string alone is not a collision because the keyring account differs. The encrypted store and file fallback live under that home. `SecretScope::Global` means within that local store, not global across all homes.
- CDS `src/codex_deepseek/cli.py:53-61` sets matching CODEX_HOME / CODEX_DEEPSEEK_HOME / CODEX_DEEPSEEK_POLICY_HOME after clearing unapproved inherited CODEX variables. The Windows overlay uses std Path canonicalization on both sides to avoid mismatched verbatim-prefix representation.

No new MCP namespace backport is justified by this audit. This conclusion is source-level; no real credential store was inspected or modified. A future candidate release still needs its usual isolated keyring/File/Secrets/Auto qualification; this audit does not claim a fresh live MCP login result.

## Existing maintenance entrypoints and migration boundary

The baseline already exposes native `cds sandbox setup --elevated --current-user` with optional `--codex-home`, or an explicit `--user` plus required `--codex-home` (`codex-rs/cli/src/sandbox_setup.rs:19-43`). It loads the selected home config, invokes `run_elevated_provisioning_setup`, and persists the elevated mode (86-117). Native TUI setup uses the same sandbox implementation; `cds doctor` exposes native diagnostics.

No existing CDS command was found that atomically backs up legacy sandbox state within one home and reprovisions that home under a new namespace. The baseline disabled-account repair restores existing account/network restrictions; it is not a state migration. The published baseline `sandbox uninstall` targets the old machine-wide sandbox and must not be used as a per-home migration shortcut. The namespace candidate changes the actual cleanup implementation to require an explicit home and matching ownership; it refuses legacy state rather than adopting it. The old CLI help wording was corrected in `native/windows-sandbox-help.patch` and exercised in the later helper/CLI candidate. The final package must still bind its help and behavior to its own build receipt.

A future operational migration should reuse native setup after explicitly quiescing only the selected profile and preserving that root directory identity, its sessions and projects. Back up only its sandbox state with a rollback receipt, preserve old shared machine resources, and handle old ACL ownership explicitly. No current live profile was migrated, stopped, or modified by this audit. This is a migration boundary, not a claim that a migration command already exists.
