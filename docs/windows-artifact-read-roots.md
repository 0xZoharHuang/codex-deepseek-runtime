# Windows model-visible artifact read roots

The private Windows CODEX_HOME keeps credentials and runtime state separate from other installations. Native full-read sandbox permissions must still reach files that Codex explicitly tells the model to read. A protected home can stop inherited Windows read permissions, even when the logical permission profile already permits those reads.

The fixed upstream baseline is fe136d4e70ef2b5bbed40a2d22b3aabf267796d3. The source patch is native/windows-artifact-readroots.patch. It changes Windows read-root discovery and calls the existing native ACL grant function; it adds no agent loop, tool executor, approval system, or model behavior.

| Home-relative path | Why the sandbox needs it | Fixed source |
| --- | --- | --- |
| skills | Standalone and bundled skill instructions, scripts, and assets are opened on demand. | exec-server/src/discoverV2/capability_locations.rs:73, ext/skills |
| plugins/cache | Installed plugin skill instructions and accompanying resources are opened on demand. | exec-server/src/discoverV2/capability_locations.rs:82, core-plugins/src/store.rs |
| memories | Version 1 instructions require searching MEMORY.md and reading selected summaries, skills, and extension notes. | ext/memories/templates/memories/read_path.md:22, protocol/src/memory_version.rs:19 |
| memories_v2 | Version 2 instructions require reading selected rollout summaries and extension resources. | ext/memories/templates/memories/read_path_v2.md:7, protocol/src/memory_version.rs:20 |

This is a physical Windows ACL correction within the existing full-read policy. core/src/config/mod.rs:4320 already adds the selected memory root logically when memories are enabled. protocol/src/permissions.rs:1351 can discard redundant additional roots under full-disk-read semantics; that logical shortcut does not itself repair a protected Windows directory's ACL. Dedicated memory tools are optional and default off (config/src/types.rs:384, ext/memories/src/extension.rs:148), so enabling them is not used as a workaround.

The fixed source was also checked for other home-relative prompt and capability paths. Global AGENTS.md is read and injected by the host (codex-home/src/instructions/mod.rs:41-74). Configuration, roles, rules, history, authentication, plugin data and MCP setup are loaded by their native host services. The patch does not add those as sandbox read roots. A worktree is handled as an explicit working directory by the existing workspace policy. The entire home, sessions, archived_sessions, auth.json, secrets, .sandbox-secrets, and state databases are outside this list.

## Permission and path boundaries

- Only native full-read root gathering adds these paths. Restricted or minimal profiles are not expanded.
- Existing filesystem permission evaluation and ReadDenyMatcher remain authoritative. Explicit denials win.
- The home, its ancestors, and each selected root component must be physical directories. Reparse points, junctions, symlinks, files in place of directories, and canonical paths outside the expected location are rejected with a bounded diagnostic.
- Missing directories are rechecked on later requests, so installing a skill or plugin after setup does not require an unbounded home grant.
- Provisioning synchronously grants only the selected roots already present in its native payload, using grant_read_execute_aces with inheritable read/execute access. Existing denials are preserved. This removes a first-read race against the background ACL worker.
- The ordinary background ACL pass excludes precisely the roots already handled synchronously. Other native permission handling remains unchanged.
- No write access is added. Memory note writes remain governed by the original requested permission/approval path.

## Qualification scope

Original failures are retained: a real private CDS candidate sandbox could read the workspace but could not read synthetic skills, plugin resources, or either memory version. The same native memory failure was reproduced with memories explicitly enabled and dedicated tools left disabled. No real memory, credentials, or other task files were read.

Independent Windows qualification covers physical paths, redirected roots, policy denials, late directory creation, actual native permission-profile evaluation, and the exact native ACL primitive. In the ACL fixture, existing and newly created files inherited the grant while a nested junction's external target retained its original DACL and bytes.

Whole-crate tests and the final compiled candidate's immediate first reads, explicit deny cases, readonly mode, installation-after-setup, and negative home/credential canaries remain release gates until their own runtime receipts pass. A source patch or standalone test is not a released runtime qualification.
