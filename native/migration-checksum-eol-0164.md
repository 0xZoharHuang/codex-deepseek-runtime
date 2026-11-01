# SQLite migration EOL compatibility: 0.164 source candidate

This is a source-only compatibility patch for the existing Codex SQLite migrator.
It does not replace SQLite, SQLx, the native runtime, or the agent loop. No SQL
migration file, dependency, profile marker, or existing checksum row is changed.

The public upstream Windows `rust-v0.164.0-alpha.7` binary has the CRLF SHA384
constants of all 61 embedded state migrations. The prior CDS native payload has
the LF constants. A subsequent isolated two-binary control also confirmed the
runtime failure: each binary initialized its own fresh profile successfully,
but both cross-opens failed initialization with exit 1 and left all 61 existing
checksum rows unchanged. The independent actual-control receipt is
`f5f8a08b799c340d3a5be793f47b0f09dbc7d915d01fb9fbc5ee116e4310dfe4`.
This confirms a gap in the existing payload; it does not qualify this uncompiled
source patch. Exact input identities are:

- Upstream source: `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`; source archive
  `3f9d7e83c9f152a438633813daefacee944d29165101c305e8ac08dc00a0e361`.
- Official Windows compressed asset (87,349,233 bytes):
  `cfb63db63521ceb7e8571a912ae3b36d41d929f2a3f3a8ca2f2d739b895afd25`.
- Official PE (356,098,864 bytes):
  `8c24181c61c94ce8982ab6a5a9408a068f1385d1427c6d424edd5cd330fb6994`.
- Published CDS 0.6.8 PE:
  `cc3aa33a08a2bbb969fad48f79319cc23749ddbbb2a600b9af6fe13c62f62020`.

The existing six native migrator factories and common database initialization
remain the only execution path. For a known applied migration, the new selector
accepts only the exact LF or CRLF representation of that version's trusted SQL,
and selects its SQL and checksum together in memory. Unknown content and dirty
migrations retain native failure behavior. The existing narrow 38-to-39 recency
repair and SQLx `ignore_missing` policy are retained.

A single established LF or CRLF style is preserved for pending migrations on
Windows, Linux, and macOS. Missing or empty auxiliary stores inherit only a
single style proved by the primary ledger, using a read-only connection. An
empty or mixed primary provides no single-style hint. Mixed known rows remain
valid individually; pending migrations then use the platform canonical style.
New empty profiles use CRLF on Windows and LF on Unix. Existing mixed rows are
not rewritten, and interoperability with an old strict single-style reader is
not claimed for mixed ledgers.

The 27th overlay applies the same three-file source change to all targets:
`state/src/migrations.rs`, `state/src/sqlite.rs`, and native migration tests.
`runtime-overlays-0.6.8-29ffae4e.json` preserves the exact prior recipe. The
existing 0.6.8 ready manifests and archives remain unmodified historical assets;
they do not qualify as binaries built with this new active recipe. The explicit
old Guardian candidate selector retains its fixed historical source stack.
No 0.6.9 binary or ready manifest is asserted by this source change.

Before a release can claim this fix, each platform must compile this exact source
and run the native `codex-state` migration tests, including the new `eol_` cases:
canonical fresh stores, existing LF stores and userdata, LF and CRLF pending
prefixes, strict unknown/dirty/changed content, precise legacy recency repair,
SQL/checksum/literal consistency and rollback, missing and empty auxiliary
inheritance in both styles, and mixed/empty-ledger semantics. Then run the
existing native API/platform qualification and installed adapter checks. On
Windows, isolated fresh/old profiles must additionally be opened in both orders
by the official 0.164 PE and the new payload, with ledger and userdata assertions.
These are required future runtime checks, not results of source replay or
formatting. No Cargo, native binary, model, or production-profile execution was
performed to prepare this source candidate.
