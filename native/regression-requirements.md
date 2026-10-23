# Native fork regression gates

The source and package manifest must refer to the same fixed upstream commit.
No mutable source wrapper is a substitute for final installed-artifact acceptance.

- Replay the fixed base patches and target-specific overlays from
  runtime-overlays.json against a fresh verified upstream archive, without fuzz.
- Check every declared before/after file hash, the complete resulting source tree
  and the protected-runtime inventory. External Cargo package definitions must
  remain unchanged from upstream; a patch file or successful build is not this proof.
- Build CLI and native HTTP helper with the recorded release profile and compiler.
  Package the matching official Code Mode Host, verified ripgrep and notices.
- Run native isolated-provider unit tests and the Guardian sampler's model-routing
  test. The HTTP integration test must prove logical upstream denial, permission
  grant, proxy-context transmission and active-stream revocation.
- Compare all stable and experimental protocol schemas with the official binary
  from that same release.
- Verify direct tools, hybrid Code Mode, v1 collaboration and v2 full/none/nested
  lifecycles. The v2 descendant capability opt-in must neither force v2 when it
  was disabled nor change upstream depth, ownership or sandbox checks.
- Verify File, direct Keyring, Auto and encrypted Secrets MCP OAuth with real PKCE,
  cold restart and another home using the same server URL/name. Fixture cleanup
  may delete only the exact isolated Keychain service created by that test.
- Verify native HTTP CA precedence, TLS rejection, authenticated CONNECT, NO_PROXY,
  pooling, helper crash, parent SIGKILL and forwarding of Retry-After.
- Final verification must use the immutable installed entry and record native,
  Python source, catalog and entrypoint hashes. Record failures as failures.


Windows plugin Git ownership uses the existing strict native Job Object path. The normal Cargo entry points are the codex-core-plugins library filter windows_lifetime_tests:: (eight tests across startup sync and marketplace upgrade) and the codex-utils-pty library filter std_tests:: (two tests), with --locked --offline --release and --test-threads=1.

The same regressions can be qualified through an isolated rustc --test entry point that imports the unchanged native Job source and original test modules, and extracts the exact Git runner functions without rewriting their bodies. This path must bind the source, test-module, compiler and linked-dependency hashes and run the same ten tests; it does not count as a completed whole-crate Cargo test run.

For this candidate, the ten same-source native tests and the 29 declared original/candidate process cases completed with their assertions passing. The process cases include expected failures of the original implementation and diagnostics of inherited behavior; they are not 29 successful production scenarios. The separate codex-utils-pty Cargo run passed its two tests. The whole codex-core-plugins Cargo test build was canceled for production-build priority and is not reported as passed.

Both entry points must exercise inherited output pipes, success and timeout cleanup, nonzero status/output, failed spawn, rejected job assignment without fallback, and owner EOF/process termination after readiness. The final installed runtime still requires its real plugin-owner and complete runtime acceptance. The upstream suspended-spawn/assignment boundary and the existing large-output poll-before-drain behavior remain inherited; neither is claimed atomic or newly fixed by this overlay.

## Windows background setup handle boundary

A fresh elevated setup must allow its native background read-ACL task to finish while app-server stdin EOF releases stdout promptly. The background child inherits only its explicitly listed NUL handle; unrelated inheritable parent pipe handles must not extend the client connection. Qualify setupStart, cold readiness, restricted inside writes and outside denials, and real Code Mode/unified exec relative writes while the read-ACL path is active. Preserve UTF-16 arguments, launch-payload environment overrides, inherited hidden drive environment entries and the native background ACL completion. A helper-only incremental build must identify its own source/build receipt and retain exact provenance for reused CLI/runner/service binaries; it is not evidence that those binaries were rebuilt.

## Windows ACL inheritance and metadata

The original owner-wide metadata mutex serialized unrelated directory updates.
The inheritance-domain overlay coordinates the existing DACL operations by
physical volume/FileID: exclusive for the changed object, shared for the
unprotected ancestor inheritance domains. Acquire the whole sorted set before
reading the ACL; re-plan if the protected boundary or physical identity changes.
Grant/revoke use scope, owner journal, then home transaction order and release the
guards before advancing to another target. A pathname alias must not redirect a
write away from the object whose lock was acquired.

Run the Windows sandbox library filters acl_lock::tests::,
ancestor_metadata::tests::, acl::tests::, and deny_read_state::tests:: using
the pinned compiler, --locked --offline --release, and --test-threads=1.
The first filter also matches private_acl_lock::tests::; retain exact test names
and do not count a repeated execution as another case. Some ledger fixtures create
Windows symlinks and require the corresponding privilege; qualify these in an
explicitly elevated, owned fixture rather than broadening a runtime sandbox.

Required effects include independent sibling progress, conflicting parent/child
and alias/hardlink coordination, inherited ACE preservation, protected-boundary
replanning, exact metadata revocation, OS lock release on process death, and
same-handle lock-file retirement without creating a second lock generation.
Production waits follow the existing native operation lifecycle, with no newly
introduced five-second cutoff. A genuine related wait longer than five seconds
must finish successfully; permanent access denial must return an error instead of
spinning indefinitely. Read-only no-ops must remain possible without WRITE_DAC.

Component results are insufficient: on the final immutable installation observe
a real first-use ReadAclsOnly helper actively propagating permissions in AppData
while a separate tiny work root executes successfully. Pin the helper PID, birth
and binary hash; require the overlap, expected file effect, native completion and
natural EOF. A warm path with no active propagation cannot satisfy this test.
Then rerun daemon, crash recovery, default/deep-home and complete runtime gates.

These locks coordinate cooperating code using the new implementation. They do not
take over running older installations, make external ACL writers participate, or
freeze concurrent changes to the hardlink topology. Do not turn readback checks
into an unsupported claim of an atomic Windows DACL compare-and-swap.

## Windows launch-critical read permissions

The Windows overlay must admit the concrete reads required by a command before starting that command. General user-profile and AppData discovery remains background work; a broad symbolic read policy must not force a synchronous traversal of the entire disk.

- Resolve explicit read leaves even when the policy also contains a symbolic root read entry. Recheck each leaf against the original policy, and retain the final read-root override and deny filtering.
- Include an authorized read-only command cwd and explicitly requested read inputs in the synchronous set. A write root already handled by the native synchronous write path does not need a second read grant.
- Grant only read/execute rights through the existing object-coordinated ACL operations, then check the complete required mask. Reject a critical path absent from the final authorized payload; retain denied writes and unrelated private siblings.
- A daemon's running, canonical managed package must match its home, one selected release, the expected package layout and required assets. Reject reparse or special members. Admit the validated public package before a restricted child searches or executes its tools; do not grant the entire home or scan other releases.
- Actual acceptance must exercise a cold read-only cwd while the broad helper is still active, the first explicitly allowed private-file read with a denied write, and the daemon's own copied rg selected and executed without falling back to the original installation. Check native EOF, owned process exit and scoped teardown.
- Preserve the original cold error 267, missing explicit read-leaf denial, and copied-rg error 5/fallback evidence. Unit/source results and a successful build do not replace those installed-artifact checks.

The reviewed change is limited to Windows permission materialization and setup payloads. It must not replace native approval, command dispatch, agent scheduling, session handling or tool execution.
