# Windows sandbox namespace isolation (source candidate)

The Windows overlays retain the native restricted-token sandbox, account provisioning,
approval and command execution paths. They isolate resource identity and ownership,
and make the narrow filesystem and network compatibility corrections documented below.
They do not add an agent loop, tool runner, approval system or thread manager.

Initial source snapshot: the exact fe136d4e70ef2b5bbed40a2d22b3aabf267796d3
source plus the four baseline patches listed in native/windows-sandbox-source-candidate.json
was reconstructed independently; all 9,358 baseline file hashes matched. The namespace patch
applied cleanly, round-tripped all 24 changed file hashes, and passed Rust parsing /
format checks. Its JSON retains the status at that initial stage.

Later CDS candidate revisions were built and a new-home namespace setup was exercised;
those results do not qualify the final 0.6.0 package. The current complete recipe is
`native/runtime-overlays.json`, including later loopback/readiness/read-root corrections.
See the [0.6.0 candidate guide](release-0.6.0-candidate.zh-CN.md) for release gates.
The related App implementation's results do not qualify CDS. Platform release manifests
must retain their published identities until the corresponding new acceptance closes.

## Identity and protected state

CDS passes the stable runtime home into the native CLI. In cli.py, native_environment
sets CODEX_HOME, CODEX_DEEPSEEK_HOME and CODEX_DEEPSEEK_POLICY_HOME to that same
resolved home. The default comes from data_root()/codex-home, with an explicit
CODEX_DEEPSEEK_HOME override. It is not a per-turn temporary directory. The native
sandbox uses its supplied canonical home and resolved real user SID; it does not
trust a supplied username pair as the ownership authority.

The key binds canonical home spelling, the directory volume serial and 128-bit
FileId, plus the owner's SID. Directory identity prevents case-sensitive directories
whose lowercase names coincide from sharing resources. Replacing the home directory
creates a different identity; do not copy state into a new home and expect to adopt
old accounts. Product-separated SHA-256 domains keep CDS and the App gateway apart.

Resources include the offline/online 20-character account names, local group,
firewall names, WFP provider/sublayer/filter identities, hidden-login registry values,
and a read-ACL progress mutex. Protected owner reservation and state files must
agree with the computed identity. Missing ownership, legacy state, another home's
state, mismatched payload usernames, and pre-existing unowned principals fail
before provisioning. Interrupted empty setup markers recover only with the exact
previously persisted owner reservation.

The original Global\CodexSandboxSetup mutex is unchanged byte-for-byte. It still
coordinates native setup mutations of shared filesystem ACLs with the official App
and older CLI installations. Local\CodexDeepSeekReadAcl-<digest> is a distinct
progress signal for this home; reusing the old signal could skip needed work.
These are different responsibilities. Ordinary upstream read-ACL refresh has a
pre-existing shared-DACL read/modify/write concurrency risk; this patch does not
invent a replacement ACL algorithm or claim to fix the old binary's behavior.

The private CLI uses the original direct helper path. It does not contact or register
with the global RegisteredCore provisioning service. Registered payloads and shared
installation registry mutations are rejected. Kept legacy constants are used only
by retained RegisteredCore code or test fixtures, not the admitted provisioning or
cleanup path. Cleanup derives the namespace again, validates current principals,
and targets its names and paths. No old global account is renamed, rotated or deleted.

## Directory metadata and shared handles

A private runtime home still needs native directory metadata access for PowerShell to
resolve its working directory. `windows-home-metadata.patch` grants only the existing
non-inheriting `FILE_READ_ATTRIBUTES` mask to this home's sandbox group. It does not
grant directory enumeration or file content access, and does not grant access to
`.sandbox-secrets`. Real permission checks must prove both usable working-directory
resolution and continued denial of private data.

The original root-only ACL update opens a directory with `MAXIMUM_ALLOWED` before
using `SetSecurityInfo`. A directory handle that denies sharing DELETE access can make
that open fail with Win32 sharing violation 32, even when the requested change only
needs ACL access. This appeared in the native `windowsSandbox/setupStart` path after
the ordinary cold CLI setup had passed.

`windows-root-acl-sharing.patch` uses an ACL-only handle for the root-only operation
and the kernel `NtSetSecurityObject` DACL operation already used elsewhere in the
native source. This avoids propagating an ACL change to children. It preserves the
original DACL control flags and deny rules; normal file and inheriting updates keep
their existing path. If WRITE_DAC access is denied, only that specific access error
permits a READ_CONTROL-only handle. A true no-op remains a no-op, while a needed
write without permission fails. The same successfully opened handle is used for
reading and updating, without reopening by pathname for the write.

Component tests execute the actual native function under both High and Limited
tokens: held sharing handles, protected and unprotected inheritance, unchanged child
and grandchild ACLs, existing deny entries, null DACL no-ops, and refusal of required
writes without WRITE_DAC. An initial implementation incorrectly rejected read-only
no-ops; its failing regression and the corrected result are retained. Component
qualification is not final installed-runtime qualification: normal setupStart,
persisted backend selection, Limited cold reopen and a fresh first sandbox command
remain release gates.

The adapter separately protects its generated private directories using the actual
user SID and SYSTEM. This matters because Python's Windows mode=700 creation under
an elevated token can produce an Administrators/Owner Rights ACL that the same
user's Limited token cannot use. The normal entry recreates its managed policy file
atomically inside the protected directory, including on an elevated upgrade of an
older generated policy. It does not rewrite user sessions or migrate sandbox accounts.

## Plugin Git process ownership

The fixed official Windows runtime can exit while its background curated-plugin
Git synchronization still has descendants running and holding stdout/stderr. A
deterministic comparison used an isolated empty home and a process-scoped Git URL
rewrite to a delayed loopback server: normal native EOF exit left the Git transport
tree and pipe readers alive. No global Git configuration or real plugin source was
changed for that comparison.

`windows-plugin-git-lifecycle.patch` connects the two synchronous plugin Git helpers
(startup synchronization and marketplace upgrade) to the runtime's existing Windows
JobObject implementation. The strict helper suspends the child, assigns it to the
job, then resumes it. Assignment failure is an error and reaps the exact suspended
child; it does not fall back to an uncontained process. On normal root completion
or timeout, the helper terminates its job before collecting pipe output. Kernel job
ownership also closes the contained tree when its owner process exits.

This is Windows process ownership, not an alternative plugin scheduler or agent
loop. Unix execution branches retain their existing behavior, external Cargo
package versions are unchanged, and the default plugin feature stays enabled.
The patch includes regression tests in the existing native test modules for output,
nonzero exit, timeout, inherited pipe holders, owner exit and assignment failure.

The helper deliberately reuses the upstream suspended-spawn primitive. It does not
claim atomic handling if the owner dies between child creation and job assignment.
The pre-existing poll-before-drain behavior for large simultaneous pipe output is
also retained and documented by the comparison tests. These boundaries are not
reported as newly fixed mechanisms.

Native-function tests and source review qualify the patch for a complete build.
The final installed package must still pass default startup/EOF cleanup with plugin
sync active, as well as the broader runtime matrix, before stable publication.

## Ancestor metadata and concurrent ACL updates

Private home ancestors can be traversable yet lack the attribute access that
PowerShell and Python need to resolve their current directory. The metadata
overlay grants only non-inheriting FILE_READ_ATTRIBUTES on required ancestors
owned by the current user. It journals before writing, pins the owner and physical
object identity, and removes only its exact recorded ACE. It does not grant
directory listing, file content access or write permission.

The first implementation used an owner-wide mutex. Actual cold-start testing
showed an AppData ACL propagation holding it for roughly 15 seconds while an
unrelated tiny work root failed at the added five-second timeout. Replacing it
with a target-only mutex was also insufficient: parent inheritance propagation
could race with a child's metadata snapshot and lose an inherited ACE.

The final source uses OS shared/exclusive file locks for the existing DACL
operations. Related parent/child updates coordinate; unrelated protected work
roots can progress. Locks use volume/FileID identities, stable ordering and a
protected private directory. The kernel releases a crashed process's locks.
Journal transactions remain per target and are not held while waiting for another
target's inheritance locks. No agent scheduling, approval decision or tool
execution loop is implemented here.

ACL reads and writes refer to the same pinned object. If a write handle is needed,
the physical path is opened and its volume/FileID rechecked before same-handle
access; a retargeted alias cannot select another object. No-op checks keep their
read-only permission semantics. Production lock waits use the existing native
lifecycle; permanent access errors fail explicitly.

Coordination applies to participating versions for the same user. Existing old
CDS processes, other users and external ACL editors are not stopped, upgraded or
silently brought under this lock protocol. Windows DACL updates have no
compare-and-swap operation, so this is not a claim of universal atomicity against
noncooperating writers or a changing link topology. Upgrade only the chosen
profile through the procedure below. Source and library results do not replace
the final installed-package concurrency and runtime acceptance.

## Existing profile upgrade and rollback

Legacy credentials cannot safely be adopted: another old home may still depend on
those machine-wide accounts. A rejecting candidate is intentional, but there are
safe ways to initialize its separate resources. These steps are a rollout procedure
to qualify, not evidence that migration has already run.

1. Preserve the old pinned binary and its platform manifest. Inventory only the
   chosen profile's native root/child processes by executable, home, PID and creation
   time. Finish or stop only that profile's work. Do not kill all codex.exe processes,
   and do not stop unrelated App/CDS homes or other users.
2. Prefer an explicit new CDS profile for qualification. Create its directory with
   owner-only protection, point CODEX_DEEPSEEK_HOME there, and let the new native CLI
   provision it. Configure the same upstream model using the normal CDS entrypoint;
   do not copy .sandbox, .sandbox-secrets or .sandbox-bin from a different home.
   The prior profile and sessions remain directly usable with the prior installation.
3. To upgrade the same profile while retaining its sessions/config, wait until that
   profile's entire native tree exits. Record the home directory FileId and owner;
   preserve that root directory. Move only .sandbox, .sandbox-secrets and .sandbox-bin
   to an owner-protected backup on the same volume, preserving their existing ACLs.
   Do not read/decrypt sandbox_users.json, copy credentials into the new namespace,
   edit the session databases, or delete shared users/group/firewall/WFP objects.
   Record each completed move; a partial interruption must be reconciled before
   any setup starts. A future automated upgrader must provide this journal and
   refuse an unknown/inconsistent state; the source patch does not silently do it.
4. With those old native-state directories safely retained, the new CLI can create
   a fresh owner reservation and its own derived accounts. Use the official elevated
   sandbox setup entrypoint under the intended user/home, once. Verify original shared
   SIDs/password timestamps/group/firewall/WFP before and after, and verify the new
   names, owner record, actual restricted shell, deny and approved-command behavior.
   Never switch the whole thread to full access to get an acceptance test through.
5. For rollback, stop only the new profile's native tree. Move its three native-state
   directories to a distinct protected rollback record; restore the old directories
   at their original paths with preserved ACLs, then use the old pinned binary.
   Keep the home root object, sessions and project files. Retained private accounts
   and rules do not change the old accounts. If removing them, perform namespace-aware
   cleanup while the new state and matching owner record are still at their original
   paths, before moving state or restoring the legacy backup. Otherwise retain them
   for later owned cleanup. Do not invoke legacy machine-wide cleanup for this purpose.

A new profile does not automatically transfer old chat history. The same-home route
preserves it because only native sandbox state is moved. Neither route changes Mac
configuration, OpenAI subscription login, another user's profile, or App state.

## Remaining qualification gates

- Compile the exact locked workspace and dependent codex binary plus all three
  Windows sandbox helpers. The sole Cargo.lock change is an existing sha2 0.10.9
  dependency edge for codex-windows-sandbox; external dependency records are identical.
- Execute only the namespace key / temporary-directory ownership fixture tests first.
  They must not create users, mutate ACLs/firewall/WFP or invoke setup. Then separately
  qualify actual privileged provisioning with a new owned profile and explicit receipts.
- Check the unchanged global setup mutex and interleave only owned profiles for
  shared filesystem ACL refresh. Record upstream concurrency limitations; do not use
  another person's live work as a fixture.
- Run an ordinary restricted command, genuine denied file/network operations,
  one explicitly approved command, main/child/grandchild inheritance and cold resume.
  Compare shared account password timestamps and network object identities afterward.
- Exercise interrupted provisioning, invalid owner record, legacy state rejection,
  move/restore interruption, cleanup failure and rollback. A process exit code alone
  is insufficient: assert the resulting resource and filesystem state.
- Rebuild Linux/macOS targets as appropriate for the release, preserving their native
  runtime and existing MCP credential isolation. This Windows-only patch must not
  silently alter their behavior or claim unperformed platform tests.
