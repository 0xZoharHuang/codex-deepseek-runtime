# Private image transport: source candidate

Release scope: the current distribution version is an unpublished 0.6.0 candidate. The versioned user procedure and current release gates are in [the 0.6.0 candidate guide](release-0.6.0-candidate.zh-CN.md). The qualification updates below retain their exact component identities; they do not qualify every later adapter or Windows overlay revision.

This work is staged on CDS `83056e7cf320461c30a609e911f3bcf0bea41be0` with native upstream `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`. It is not an installed or fully qualified CDS release. The new Windows HTTP helper has been independently built and qualified below; full CLI/runtime qualification is separate. No installed runtime was changed and no platform manifest was marked ready.

## Boundary

The native runtime still owns threads, tools, approvals, sandboxes, compaction, and tool effects. CDS transforms only the provider HTTP body and tracks private uploaded-file resources. Native history retains its original images. Files, model calls, and provider maintenance use the existing native HTTP factory, proxy routing, CA/TLS configuration and connection pool. Python performs authenticated loopback IPC; it has no alternative public egress path for this feature.

```text
native Responses request
  -> existing CDS protocol conversion
  -> optional lossless image transport
  -> authenticated /forward-raw IPC (bounded metadata + exact body bytes)
  -> existing native HTTP factory
  -> fixed OpenRouter origin / Responses or private Files API
```

The raw helper endpoint supports the configured Responses POST, multipart POST `/api/v1/files`, metadata GET, and DELETE for a validated Files object path. It rejects changed origins, credentials embedded in URLs, fragments, unexpected methods/content types, oversized metadata/bodies and ambiguous metadata JSON. The previous `/forward` JSON interface is preserved. Startup advertises `raw_protocol_version=1`; a mismatched helper fails before image uploads, without falling back to JSON or base64 encoding. A known version mismatch is a deterministic 400 configuration error, not a transient retryable model failure.

The Python policy names `default`, `default-fallback` and `system` describe native outbound proxy routing. They are not agent permission tokens. Explicit provider maintenance follows the existing CDS doctor pattern; it does not invent an agent approval or delegation system. Existing managed application-policy handling and model-request cancellation remain part of native qualification.

## Explicit opt-in

Absent configuration means disabled and preserves the existing JSON request path. Before enabling a qualified build, place an owner-private file at `$CODEX_DEEPSEEK_HOME/provider-state/image-transport.json`:

```json
{
  "schema_version": 1,
  "enabled": true,
  "native_outbound_policy": "default",
  "activate_above_bytes": 16777216,
  "wire_budget_bytes": 31457280,
  "idle_ttl_seconds": 3600,
  "verify_after_seconds": 60,
  "max_file_references": 16
}
```

These byte values are an operator prevention policy, not a claimed provider limit. The actual API accepts at most 16 file-reference occurrences per request in our prior controlled probes. The module selects the greatest estimated byte savings, retains every other original image inline, preserves order/detail, and fails explicitly if the configured wire budget cannot be met. It never resizes, drops, concatenates or summarizes images. A deterministic local budget failure is HTTP 413, separate from model context-window errors.

The service identity includes this configuration. Existing active-profile upgrade protections still apply. Changing configuration must not replace a worker underneath active native sessions. Use the normal isolated-profile procedure rather than killing all Codex processes.

## Resource lifecycle

The provider uses a private `provider-state/image-transport/` directory, credential-scoped original-byte hashes, an independent file ledger and independent request receipts. It does not instantiate native Store for maintenance and does not use native thread tables as file leases. Each inference captures one credential, including across file operations; key rotation cannot switch the key halfway through a request.

Only validated provider terminal events or definite HTTP rejections release consumption pins. Client cancellation, 500, truncated SSE and unknown upload results retain their resource evidence. A later output-schema/delivery failure does not overwrite an already validated provider terminal. No inference is replayed during reconciliation.

Enabled services run bounded TTL cleanup every 60 seconds, at most 2 eligible assets and 10 seconds per pass. The first Files error stops that pass; safe Retry-After is persisted. This worker only accesses known owned IDs and never runs Files List or model inference. Shutdown cancels only its own maintenance task.

Explicit commands:

```text
cds runtime image-status
cds runtime image-cleanup --all-unpinned
cds runtime image-reconcile-uploads
cds runtime image-reconcile-calls
```

Status is informational, including when disabled. Other maintenance operations require explicit completion to return 0. Pending, cooldown, unknown, service-error and timed-out outcomes return nonzero. These commands bypass Store/ProviderService construction, so they do not reconcile or terminate native runtime owners.

Upload reconciliation may inspect a bounded metadata page set for the current credential, using only previously persisted private random nonces/MIME/size. Only a unique complete-page match becomes cleanup-only, never a ready content cache. Call reconciliation reads this provider's own receipts, then bounded native GET generation/model metadata if needed; it writes a private verification proof before releasing a pin. Neither path replays an upload or inference implicitly.

Deleting a local cache directory is not remote cleanup. Retain receipts until owned files have been GET 404 or an unresolved state is explicitly preserved. Disabling the feature does not prove prior files were removed.

The ledger binds its schema, credential scope and canonical cache-root identity. Copying or moving a cache to another root does not transfer permission to reuse or delete its provider objects. Private locks, salts and ledgers reject links/junctions/hardlinks. Empty unbound legacy ledgers may initialize; nonempty unbound ledgers are preserved and rejected for explicit ownership reconciliation. Key rotation leaves other scopes visible as pending rather than cleaning them with the new key. Supported same-root Windows case and extended-path aliases do not create a new owner.

## Source and qualification

`native/payload-source-candidate.json` preserves the initial payload component snapshot. The complete current ordered recipe is `native/runtime-overlays.json`; it also includes later Windows namespace/network/read-root overlays. The initial payload stack applied the normal distribution patches first, including the Windows parent-handle patch on Windows, then `payload-http-transport.patch` and `payload-too-large.patch`. The latter preserves 413 as an HTTP payload error and prevents blind retries without server Retry-After; it does not replace native compaction or map bytes to token overflow.

The migration candidate for the shared pure implementation lives in CDS. The App repository remains frozen until the coordinator publishes/pins the exact new CDS commit and replaces its imports. The App must not import CDS Store, launcher, supervisor or native thread logic. Existing App direct-HTTP probe results do not qualify this newly extended native helper.

The initial source checks used synthetic credentials, fake Files services, real loopback IPC bytes and Windows Python process locks. Those checks did not invoke a native executable or real provider and are reported separately from the later native and real-provider qualification below. No real account-wide Files List was performed. Full CLI/runtime release acceptance, Linux qualification and any future macOS qualification remain separate gates.


## Windows helper qualification update

The isolated Windows helper was built in release mode with locked offline dependencies, two build jobs and below-normal priority. The source snapshot did not change during the build. The binary SHA256 is `f6d12de3595866a9afc69fbe5866ca3c5088bfea0f63d787cb02646e4c35e1e9`. Three real Rust unit tests and twelve real-helper loopback HTTP/TLS/CONNECT tests passed, separately from the 144 Python/fake/IPC tests. The native tests include the 34 MB/101-image mixed request, original multipart bytes, DELETE/GET 404, warm-cache reuse, no error replay, CA/proxy behavior, pre-header cancellation and parent-process death. No real OpenRouter request was made during these native tests.

This is a helper qualification, not a full CDS runtime release: the 413 main-runtime patch was also built and its actual CLI request-count validation passed as described below. The Windows sandbox namespace defect identified in `payload-isolation-audit.md` was a separate release gate at that stage; later CDS namespace, network and read-root revisions have their own identities and acceptance requirements. App results do not qualify CDS. The prepared [platform CI](../ci/README.md) covers disposable macOS/Linux transport fixtures; it has not yet supplied a run result for this candidate. No new maintainer-Mac qualification is claimed. This helper qualification did not modify installed CDS or App source/target.

## Actual official DeepSeek request through the new native helper

A single authorized Windows probe used the new native helper for Files upload, Responses, generation metadata and DELETE/GET cleanup, with a fresh project-owned cache and dedicated Windows credential. It fixed the model to `deepseek/deepseek-v4.1-flash-1017`, provider to official DeepSeek only, and disabled fallback. The provider metadata identified canonical `deepseek/deepseek-v4.1-flash-20261017` and DeepSeek.

The 1,206-item synthetic request contained 101 original PNG images and was 34,009,154 bytes before transport. Sixteen private file references and 85 original inline images produced a 28,625,730-byte request. The exact prepared body was sent through native raw IPC; image order, detail, and original bytes round-tripped without changes. The model identified independently generated first/last image nonces, returned HTTP 200 with a validated completed response, and used 38,341 input / 152 output tokens at a recorded cost of $0.00584235. All 16 owned files were deleted and individually read back as GET 404, with zero pins, unknown uploads or pending cleanup.

This proves one representative long multimodal request through the actual native provider path. It is not a claim that any arbitrary request size fits a provider's limits, a full runtime acceptance, or a server-side download hash proof. The API does not provide downloadable image content for this check; proof comprises the original upload bytes/hash, metadata, exact outbound wire body and actual model vision result. No business images, existing CDS worker, Store/ProviderService ownership logic, or account-wide Files listing was used.

## Actual Windows CLI 413 behavior

A separately built CLI (`6a589d9d6837da715e47bd3f9f7fd6708524be14410a34297c6dbeb474a1ecca`) was tested through the real CDS Gateway and new native HTTP helper against an owned loopback provider. All seven cases passed without changing native retry limits or disabling runtime features:

| Case | Actual provider requests | Outcome |
| --- | ---: | --- |
| Ordinary HTTP 413, no Retry-After | 1 | Explicit failure, nonzero exit; not token overflow |
| Ordinary HTTP 413, Retry-After 1s | 2 | Delayed retry, successful completion |
| Ordinary HTTP 429, Retry-After 1s | 2 | Native retry retained, successful completion |
| Ordinary HTTP 503 | 2 | Native retry retained, successful completion |
| Cold-resumed native compaction, HTTP 413 without advice | 1 | Explicit failure, nonzero exit |
| Cold-resumed native compaction, HTTP 413 with 1s advice | 3 | Two compaction attempts, then successful main request |
| Local operator body budget rejection | 0 | One gateway request, HTTP 413, nonzero exit |

The compaction trigger of one token was set only in the synthetic test home; production context thresholds remain unchanged. A first harness run passed all seven behavioral assertions but failed while deleting its own read-only Git pack files. That failure was preserved, cleanup was repaired within the exact owned directory, and the unchanged binaries and assertions passed a second complete run with exit 0 and all temporary files removed (7 passed in 70.47 seconds). This qualifies HTTP retry behavior, not the entire product; the sandbox namespace candidate and other platform acceptance remain separate.

## Reproducible build and platform admission

`native/runtime-overlays.json` is the authoritative ordered overlay recipe. It separates the upstream 1.95.0 toolchain pin from the 1.99.1 compiler used for this candidate qualification and records each target's exact baseline patches, overlay SHA256 values and source-file input/output hashes. `build_native.py` validates those identities, builds the HTTP helper separately from the main distribution, and retains both real cargo commands. The native probe and license-metadata tooling use the same project compiler pin.

A target-specific recipe ID is carried by the native package, build receipt and installer manifest. New source refuses an old or different-target `ready` manifest instead of silently combining it with an unqualified runtime. The gate leaves old immutable installations untouched. Source-only patch fixtures cover Windows, Linux and macOS recipes on Windows; those fixtures are not cross-platform executable qualification. Actual new Linux and macOS runtime qualification remains separately required.
