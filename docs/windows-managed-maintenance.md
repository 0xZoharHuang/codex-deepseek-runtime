# Managed Windows namespace maintenance

An ordinary Windows user can have permission to use a project without permission to change security descriptors on its administrator-owned parent directories. A CDS namespace beneath that project must be prepared by the existing native elevated maintenance command. Ordinary startup must not grant the host `WRITE_DAC`, replace the sandbox with full access, or manufacture a Full setup payload.

The normal v0.6.14 package passed this command in an elevated Windows desktop session on the existing namespace. The ordinary user subsequently verified permitted and denied operations in the same home, including a cold restart. [Version qualification record](../reports/releases/0.6.14/windows-runtime-qualification.json)

Once the qualified package is installed, an administrator uses that exact package's native CLI, the intended Windows account, and the existing CDS home:

```powershell
& "<qualified-native-package>\bin\codex.exe" sandbox setup --elevated --user "<DOMAIN>\<user>" --codex-home "<existing-CDS-home>"
```

The account and home must be the existing namespace owner. This command requires an actually elevated administrator shell; adding `--elevated` does not make an ordinary shell privileged. It uses native ProvisionOnly. Schedule maintenance when that namespace has no active tasks. Leave other users and namespaces running.

A native bin directory created by SYSTEM can grant the Administrators group data rights without granting the right to change its descriptor. For this exact native layout, P39 verifies its owner and four ACL entries and uses the elevated caller's existing restore privilege on a short-lived thread token to reopen the same physical directory. The native setter preserves Windows inheritance behavior. A changed owner, explicit denial, mismatched ACL, redirected target or missing restore privilege terminates that maintenance attempt. Inspect the reported target and Windows error code before retrying.

The expected change is a non-inheriting namespace metadata grant (`READ_ATTRIBUTES | SYNCHRONIZE`, `0x100080`) on the validated home ancestors that require it. It grants no directory listing, file contents, or write access, and does not grant the ordinary host descriptor-edit rights. The namespace identity, sandbox user SIDs, home file identity, original owner, and unrelated ACEs must remain the same. Existing metadata denials must be preserved; an incompatible denial is an error, not permission to rewrite it.

## External private workspace integration contract

Native maintenance covers the validated CDS home ancestors. For an already-authorized physical read/write root inside that same home, the native runtime also prepares the necessary intermediate private parents below the home. A root outside the home is excluded from this private-parent grant set. The pinned `ancestor_metadata.rs` implements that boundary in `private_descendant` / `pin_requested_parents`; `unrelated_or_relative_roots_cannot_expand_private_metadata_scope` checks that an unrelated workspace cannot expand it. The [source recipe](../native/runtime-overlays.json) records the exact native source chain.

When a host application places its own private workspace outside the CDS home, its existing one-time owned-directory initializer remains responsible for the intermediate private parents it manages. For the verified fixed namespace group, that initializer's non-inheriting metadata-only grant **and its readiness check must cover `READ_ATTRIBUTES | SYNCHRONIZE` (`0x100080`)**. An old `READ_ATTRIBUTES`-only (`0x80`) check can pass while Node's real Windows `lstat` still fails.

Update only that existing metadata grant/check for the same namespace group and the host's own intermediate directories. Preserve the directory owner, all DENY entries and unrelated ACEs, their order, and the leaf cwd's existing read/write policy. Keep the metadata grant non-inheriting. This adds no directory listing, file-content read or write access. Use the host's existing initialization lifecycle; do not introduce per-task ACL mutation, construct a Full setup payload, or extend native home maintenance across arbitrary third-party paths.

In the downstream managed-user run, the ordinary runtime became ready and its Node executable started after home maintenance. A later `lstat` failure occurred in the host's external private workspace. Independent runs reproduced that failure in both read-only and workspace-write modes. Changing only the two existing host-owned metadata entries to `0x100080` allowed the same Node executable to load the JavaScript entry. Parent enumeration, parent writes and access to a private sibling remained denied. The downstream application owns its production workspace initializer and product integration. [Before and after results](../reports/releases/0.6.14/external-workspace-qualification.json)

After maintenance, launch CDS from its normal entry as the ordinary user. Verify the same tool command that failed, readonly and workspace-write behavior, nearby denied paths, and a cold restart. A successful setup exit alone is insufficient. The release report records this procedure against an administrator-owned shared-parent fixture; it is distinct from an administrator-host test.

Rollback restores the previous immutable CDS version and its existing home selection. It must not remove another namespace's grants, accounts, or active processes. No manual ACL reset, fabricated setup state, Defender exception, or global `codex.exe` termination is part of this procedure.
