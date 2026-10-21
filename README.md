# Codex CLI runtime with DeepSeek

An isolated distribution of the native Codex CLI runtime, using a pinned DeepSeek
model through OpenRouter. Tools, sandboxing, approvals, sessions, MCP, Skills,
Hooks, Code Mode, queues, goals and subagent scheduling run in the native Codex
implementation. The adapter does not execute agent tools or replace the agent loop.

**Acceptance:** a green unit test or a development-binary run is not a release
acceptance receipt. Consult the acceptance report shipped with the release for
the exact installed artifact, gates and any remaining limits.

The frozen upstream baseline is `0.164.0-alpha.7`, commit
`fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`. This is an upstream prerelease.
The verified platform is macOS arm64 with independent Python 3.11+. The model is
`deepseek/deepseek-v4.1-flash-1017`, rather than a floating latest alias.

## Install and run

The installer requires the release's hash-pinned native manifest and archive.
It creates an immutable Python environment and a complete native package, checks
both, then atomically updates only `~/.local/bin/codex-deepseek`.

```sh
python3 install.py --native-archive /path/to/verified-native-release.tar.gz
codex-deepseek runtime setup-key
codex-deepseek runtime doctor
cd /path/to/project
codex-deepseek
codex-deepseek exec "Implement the change and verify it"
codex-deepseek resume --last
codex-deepseek app-server
```

`runtime doctor` runs an actual native Codex turn and verifies its provider using
OpenRouter generation metadata. The native Codex `doctor` command remains
available under its original name. `--help` and `--version` require no API key,
provider service or state-directory writes.

Credentials use a dedicated macOS Keychain entry: service
`ai.codex.deepseek.openrouter`, account `default`. `runtime setup-key` invokes the
native hidden Keychain prompt. `OPENROUTER_API_KEY` can be supplied by the launching
environment; it is removed before launching native agent tools. No key belongs in
argv, Git, configuration, traces or a public acceptance report.

## App isolation

| Item | Location or behavior |
|---|---|
| Entrypoint | `~/.local/bin/codex-deepseek`; existing global `codex` stays separate |
| Native artifacts | `~/.local/share/codex-deepseek/native/<archive digest>/` |
| Immutable adapter and catalog | `~/.local/share/codex-deepseek/releases/<source digest>/` |
| Default native home | `~/.local/share/codex-deepseek/codex-home/` |
| Additional profile | Set `CODEX_DEEPSEEK_HOME` to a separate directory |
| Provider receipts and search replay | `<home>/provider-state/adapter.sqlite` |
| Native MCP credentials | Native File, Keyring, Auto and Secrets modes; direct MCP Keychain service is namespaced by isolated home |
| CLI authentication | Native storage modes; native account keys are already scoped to CODEX_HOME |

The launcher removes inherited App IPC/session identities, binds an independent
CODEX_HOME, and rejects state/trace paths overlapping `~/.codex`. Profile-local
`codex` shims keep nested shell invocations on this distribution. Private Python
helpers use isolated imports, so a project `PYTHONPATH` cannot replace them.

Local WebSocket attachment requires a live, kernel-owned listener registered in
this home. Local aliases are resolved to that verified endpoint. Explicit Unix
proxy sockets must lie within this home. Explicit external remote servers retain
the native remote-client behavior; their identity and configuration belong to
the operator of that server. The App-launching subcommand is excluded by the
requested isolation boundary.

Project rules, project tools and shared `~/.agents/skills` follow native CLI
behavior. A separate home is state isolation, not a virtual machine.

## Native package and patch boundary

The package contains the patched native CLI, matching official Code Mode Host,
bundled ripgrep, a small native HTTP transport, license notices and build receipts.
`native/codex-runtime.patch` applies to the exact upstream source archive. External
Cargo dependency versions remain locked. Native public schemas are compared
against the same-version official binary, including experimental methods.

The source changes are limited to:

- An opt-in provider boundary applied at native configuration loading, covering
  stdio, Unix/WebSocket daemon and direct WebSocket requests. System/enterprise
  requirements retain precedence; incompatible policy fails clearly.
- Overridable factory defaults for Code Mode, v2 collaboration and live search.
  User settings can select direct/hybrid tools and v1/v2 collaboration. The
  model catalog does not silently force a tool mode.
- WebSocket framing for `app-server proxy`, whose current control socket speaks
  WebSocket, while retaining the separate raw `stdio-to-uds` command.
- Guardian v2's auxiliary classifier model follows the selected DeepSeek model
  in this distribution. The native classifier and approval implementation remain.
- Native HTTP egress with the caller's resolved proxy policy, native certificates,
  pooled connections and cancellation. The provider's loopback URL is treated as
  IPC; application destination checks and revocation retain the logical upstream
  destination. Transport advice such as `Retry-After` crosses the adapter.
- Home-scoped MCP direct-Keychain identity, preserving native credential-store
  choices without sharing the App's MCP login entries.

`build_native.py` extracts verified sources into a fresh directory, applies and
checks the patch, and builds with Rust 1.95.0. `package_native.py` creates a
reproducible archive with an explicit file manifest. A rebuild can have a different
binary hash due to compiler/absolute-path effects; source, compiler, flags and
output hashes are recorded rather than claiming bit-identical independent builds.

```sh
python3 build_native.py --work /separate/build-cache --output /separate/native-package --jobs 2
python3 package_native.py --package /separate/native-package \
  --archive /separate/native-release.tar.gz --manifest native/runtime.json \
  --url https://your-reviewed-release/native-release.tar.gz
```

## Provider routes and semantics

Every request fixes the same model and uses explicit `provider.only`,
`allow_fallbacks=false` and `require_parameters=true`.

| Request capability | Selected provider |
|---|---|
| Ordinary model requests | Official `deepseek` |
| Strict JSON-schema output | `morph/fp8` |
| Tools plus explicit `parallel_tool_calls=false` | `primeintellect` |
| Thinking, native peer or incomplete-response context, and hosted web search together | `morph/fp8` |

These are capability selections before sending the request. A provider error does
not cause adapter retries or replay tool effects. Native retry settings remain
configurable and native retry behavior remains native. Official-route limitations
and comparison probes are documented in `upstream/README.md`.

Custom tools preserve grammar/regex validation and native call identities.
Namespaced tools retain authoritative namespaces. Historical tool calls do not
make unavailable tools callable. Peer messages retain author, recipient and type
as tool context; transport metadata is labeled and is not claimed to be model
reasoning. SSE terminal, item identity and incremental arguments are checked.
Unknown or unrepresentable semantics fail explicitly instead of disappearing.

Interrupted native responses can leave trailing plaintext reasoning or assistant
output in sampling history. DeepSeek does not accept unfinished assistant prefill. The wire
adapter retains the exact plaintext as an explicitly labeled, nonexecuting context
envelope; native saved history remains unchanged. The compatible context route is
selected before sending that request, just as for peer context. No transport
context tool is exposed for execution and the adapter does not retry agent steps.

Structured responses are validated locally against their JSON Schema without
network schema retrieval. Text deltas and tool arguments remain streamed. Final
message completion events are committed only after the provider's completed
response satisfies the schema; Markdown wrappers and invalid values produce an
explicit compatibility failure for the native retry mechanism. The adapter does
not invent, coerce or silently repair a model's structured values.
Every explicit final-answer message is checked, including responses that also
contain client tools. Legacy tool commentary remains commentary. Non-finite
numbers cannot cross the native JSON boundary.

Native compaction uses its plaintext summary path. Opaque OpenAI encrypted state,
OpenAI-hosted cloud search/cache semantics, Realtime model services, image-generation
models, ChatGPT Apps/marketplace entitlements and cloud task accounts require their
respective backend/model capabilities. Runtime equivalence does not grant those
services or claim equal model judgment. Live web search uses OpenRouter Exa;
unsupported location/cache options report a compatibility error.

## Document browsing through native MCP

The distribution registers a `codex_web` stdio server in its isolated home's
configuration. The server uses the official MCP Python SDK; native Codex still
discovers, approves, dispatches, logs and cancels its calls. No agent tools run in
the model protocol bridge. Native hosted live search continues to use OpenRouter
Exa. Search excerpts are never represented as a successful page open.

The browser provides `open(url_or_ref, start_line, num_lines)`,
`find(ref_id, pattern, case_sensitive, context_lines)` and `click(ref_id, link_id)`.
Open retrieves actual HTML, text or PDF bytes and returns numbered text lines,
source URL, retrieval time, content hash and numbered links. Find searches the
saved document, including phrases across display-line boundaries. Click resolves
an actual saved link and fetches its target. Persistent page references survive
native MCP process restarts within that isolated home. Unavailable references,
failed downloads and document limits produce tool errors instead of invented text.

The document cache lives in `<home>/web-cache/pages.sqlite` with a 64 MiB logical
payload budget and oldest-first eviction. Downloads are limited to 10 MiB, text
to 4 MiB, PDFs to 200 pages, extracted display lines to 100,000, and expanded
link metadata to 10 MiB. An individual page larger than the cache budget fails
before changing the cache; it cannot return an already-evicted reference.
Responses explicitly report pagination and link
truncation. This is a document reader: JavaScript execution, authenticated browser
sessions and visual browser automation are separate capabilities.

Public HTTP(S) addresses are supported by default, with private-address and
redirect validation. The reader uses system certificate trust and configured
HTTP(S) proxies, without website cookies or implicit `.netrc` login credentials.
macOS VPN Fake-IP DNS in `198.18.0.0/15` is accepted for hostnames only when a
kernel route confirms a TUN interface; literal reserved addresses remain blocked.
The resolution mode is included in the fetched-page evidence.
This MCP server has its own declared network capability, as native MCP servers do;
command sandbox networking and hosted search are separate native permission paths.
Operators can explicitly configure another named server with `--allow-private-host`
for an intranet. The bundled configuration never enables that option.

The launcher maintains only the bundled server's command and arguments, preserving
other MCP servers, user comments, tool settings and explicit disablement. Disable
the bundled reader using the native configuration:

```toml
[mcp_servers.codex_web]
enabled = false
```

Dependency locks include the SDK, parsers and build tools. Regenerate them with
`uv pip compile --generate-hashes --only-binary :all: --python-version 3.11
--output-file requirements.lock pyproject.toml build-requirements.in`.

## Lifetimes and recovery

A socket-owning service watchdog retains the profile's gateway endpoint across a
worker crash. Native sessions and the managed daemon keep it alive; unused services
exit after the idle window. It holds a lifetime lease and keeps a validated backup
of ownership metadata. Native HTTP helpers die with their parent and can restart
for the next request; in-flight requests are not replayed.

The native-process supervisor monitors a private parent pipe. Wrapper termination
asks native Codex to shut down its owned work. Yielded command sessions follow
native lifetimes: interrupting a turn does not mean every background command exits.
MCP calls also retain upstream lifetimes: turn interruption stops sampling but can
leave an already dispatched MCP read in progress. A same-version official/native
comparison verifies this behavior and owner-exit connection cleanup. When the MCP
client explicitly sends a cancellation notification, the browser cancels its HTTP
request. These are separately tested guarantees, not one blanket abort claim.
A direct SIGKILL of native Codex leaves command descendants on the audited upstream
macOS baseline as well; it is recorded as an upstream limitation, not a passing
cleanup guarantee. Signal only recorded PID plus kernel birth identity.

An unknown model result stays unknown in receipts. Provider completion is distinct
from native delivery and actual tool effects. Native events/files are authoritative
for those effects. Concurrent profiles use separate databases, services and tokens.
Changes to runtime or credential/network environment while a profile has active
native work require closing that work or using another home, preventing mixed
versions inside one daemon.

## Verification and publication gates

Offline tests must use a verified or explicitly built native HTTP helper:

```sh
CODEX_DEEPSEEK_TEST_HTTP_BINARY=/verified/package/bin/codex-http-transport \
  PYTHONPATH=src python3 -m pytest tests -q
```

Live verifiers create fresh private homes, invoke paid APIs and preserve events,
receipts and actual effects. They cover native permissions, user/Guardian approval,
MCP OAuth/PKCE and cold credentials, direct/Code Mode tools, dynamic discovery,
images, structured output, daemon/queue/goals, sampling cancellation, process IO,
crash/restart and memory v1/v2. Never substitute an arithmetic reply for a tool or
lifecycle effect. A fixture may authorize its own disposable hook explicitly;
production hook trust remains native.

Real-work fixtures use pre-fix snapshots of Jinja and HTTPcore, separate upstream
fixed oracles, pinned/hash-verified dependencies and independent cancellation,
TCP and TLS regressions. The HTTPcore workflow interrupts after source edits and
cold-resumes; both tasks run in separate homes and must actually use their native
reviewer. Regression and oracle tests run outside the model.

```sh
python3 prepare_real_work.py --output /fresh/fixtures --python /path/to/python3.11
python3 verify_real_work.py --fixtures /fresh/fixtures
```

Use `--require-web-research` to require both real workflows to perform actual
open/find/click against official documentation in addition to code changes and
full independent validation. `verify_real_work_followup.py` resumes the same saved
task after explicit acceptance feedback, preserving the failed attempt and requiring
a fresh native reviewer, unchanged oracles and external suite reruns. HTTPcore
follow-up acceptance repeats the real TCP stress three times.

`verify_remaining_methods.py` verifies the fixed v0.3 gap list method by method.
Sixty are local operations with checked state or effects. Four original entries
are remote-backend contracts (hosted-App event streams and remote-plugin service
operations); their account boundary is checked explicitly and never counted as a
positive local lifecycle. Five native user-verification methods also require a
ChatGPT account identity before accessing OS credentials: the same-version
official/native comparison exercises only their unavailable-account guard, without
enrolling, deleting or prompting for a real credential. The method matrix records
these corrected scopes and all other account/platform exclusions.

```sh
python3 verify_remaining_methods.py --entry /immutable/release/venv/bin/codex-deepseek \
  --output /private/method-acceptance --jobs 2
```

`verify_protocol_recovery.py` injects one invalid upstream response per native
session, then observes native retry and real-model recovery. It verifies that
undeclared tools and invalid JSON never execute tools or become final native
results. `verify_browser_runtime.py`, `verify_browser_protocol.py` and
`verify_native_mcp_lifetime.py` cover actual HTTP effects, public HTTPS, cold page
references, native model-driven navigation, explicit MCP cancellation and the
official native interruption baseline.

The complete acceptance runner requires an actual immutable installed entry and
an independently built native policy test probe. Build the probe from the same
patched source with `cargo +1.95.0 test --locked --profile dev-small -p
codex-deepseek-http-transport --test native_policy --no-run`; Cargo prints the
executable path. This test-only binary can be retained while deleting the large
development build cache. The runner passes the actual installed HTTP helper to
the probe and requires observable upstream cancellation.

```sh
python3 run_acceptance.py --entry /immutable/release/venv/bin/codex-deepseek \
  --native-probe /verified/native-policy-test-executable \
  --fixtures /fresh/fixtures --output /private/acceptance --jobs 2
```

`CODEX_DEEPSEEK_TEST_ENTRY` selects an explicit development or installed entry for
live verifiers. Published acceptance must identify the actual immutable installed
release, not only a mutable source wrapper. `CODEX_DEEPSEEK_TRACE_DIR` optionally
stores sensitive project/context bodies; unset preserves daemon tracing and an
explicitly empty value disables it. Do not publish raw traces, OAuth tokens,
private profiles or user file paths.

## Updates, rollback and cleanup

The installer never repairs an active artifact in place. A damaged native package
or adapter gets a new immutable repair directory; the previous launcher is retained
until the new release passes integrity checks. Help/version and daemon stop/version
work without starting a replacement provider service. To update a stopped daemon
from this distribution, use `app-server daemon update --from-cli --yes`. The generic
production updater is rejected because it would remove the provider isolation patch.

Keep one verified release and rollback material only as needed. After all native
owners and services are stopped, generated Rust targets, duplicate downloads and
disposable fixture virtual environments may be removed. Preserve source patches,
build/acceptance receipts, required user sessions and compressed failure evidence.
Measure physical disk recovery with `df`; overlapping `du` totals are not proof of
space reclaimed. Never bulk-delete `~/.codex` or another project's environment.

The source is Apache-2.0. See `THIRD_PARTY_NOTICES.md` and native package notices.
Upstream patches and interoperability reports are review material; they do not
imply acceptance by OpenAI or OpenRouter.
