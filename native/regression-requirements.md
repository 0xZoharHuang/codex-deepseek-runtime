# Native fork regression gates

The source and package manifest must refer to the same fixed upstream commit.
No mutable source wrapper is a substitute for final installed-artifact acceptance.

- Apply `codex-runtime.patch` to a fresh verified source archive with zero fuzz.
- Compare every changed file against `source-changes.json`; external Cargo lock
  packages remain unchanged from upstream.
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
