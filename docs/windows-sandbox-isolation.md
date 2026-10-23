# Windows sandbox namespace isolation (source candidate)

This patch preserves the native Windows restricted-token, ACL, account provisioning,
firewall and WFP algorithms. It changes their resource identity and ownership checks.
It does not add an agent loop, tool runner, approval system or thread manager.

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
