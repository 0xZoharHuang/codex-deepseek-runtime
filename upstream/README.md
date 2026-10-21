# Upstream follow-ups

These files are reviewable patches and reproductions against a pinned upstream
revision. Their presence does not mean either upstream has accepted them.

## Codex app-server control proxy

Baseline: OpenAI Codex `0.164.0-alpha.7`, commit
`fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`.

The CLI `app-server proxy` forwards raw stdio bytes into a daemon control socket
that now speaks WebSocket. A JSON-RPC initialize request is consequently parsed
as an invalid HTTP upgrade request. The patch uses a JSONL-to-WebSocket relay for
this specific CLI subcommand; `stdio-to-uds` retains its raw transport for its
other users. Outbound messages honor the daemon advertised unfragmented-frame
budget. No compression extension is negotiated.

The dedicated distribution enables this fix behind its policy-home opt-in. The
standalone upstream patch applies it to the affected command for all providers.
Actual dedicated-runtime acceptance includes initialize, config policy, native
commands, a 18.3 MB file roundtrip, daemon restart, persistent queue execution,
and a provider worker crash. The public protocol schemas remain unchanged.

## OpenRouter provider interoperability

Pinned model: `deepseek/deepseek-v4.1-flash-1017`.

Three provider differences were reproduced with explicit `provider.only`,
`allow_fallbacks=false`, and `require_parameters=true`:

- The official DeepSeek endpoint rejects strict JSON-schema output in the tested
  Responses request. The adapter selects Morph for this declared capability.
- Tool serialization with `parallel_tool_calls=false` uses Prime Intellect.
- Thinking plus hosted web search plus tool-result context for native peer
  messages is rejected on the official endpoint. The same model request works
  on Morph and Prime Intellect; the adapter explicitly selects Morph for that
  combination, preserving thinking and peer provenance.

Ordinary requests continue to use the official DeepSeek provider. These are
preselected routes with visible receipts, rather than retries that replay an
agent action. A proper upstream fix would make the official provider's declared
parameter support and its Responses translation agree on these requests. No
OpenRouter proprietary server patch is claimed here. The runtime tests preserve
failed attempts and successful provider/generation evidence privately; published
reports include sanitized model, route, status and capability evidence only.

### Incomplete-response continuation and strict output

The 0.4.0 audit also reproduced a complete native request whose input ended in
plaintext reasoning retained after a rejected undeclared tool call. The official
DeepSeek endpoint returned 400; unchanged requests on alternative providers could
interpret that suffix as assistant prefill. A labeled, nonexecuting continuation
context preserves the exact historical output without allowing it to become a
partial final answer. The compatible context/search route is selected before the
request; no failed tool action is replayed by the bridge.

One full native image/history/JSON-schema request also returned Markdown-fenced
JSON despite `strict=true`. Subsequent controlled probes reproduced provider
variability. `structured.py` validates the requested schema locally before native
message completion, without coercing values or fetching remote schema resources.
Native sampling retry remains the recovery mechanism.

`verify_protocol_recovery.py --entry /immutable/release/venv/bin/codex-deepseek
--output /private/recovery` is a reviewable reproduction of both recovery contracts:
it injects one explicit upstream fault, then forwards the native retry to the real
fixed model. It checks actual native errors, continuation evidence and final output,
and rejects any client-tool execution. It does not claim an OpenRouter server fix.

### Native MCP interruption lifetime

The official and patched alpha.7 binaries were run with the same deterministic local
model fixture and SDK browser. Both stop the turn on interrupt while the dispatched
MCP request can continue; both close the connection when its owner exits. The
SDK browser separately cancels HTTP on explicit MCP cancellation notifications.
`verify_native_mcp_lifetime.py` and `verify_browser_protocol.py` preserve that
distinction. No business-layer cancellation behavior is silently replaced.
