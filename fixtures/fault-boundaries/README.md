# Native fault-boundary acceptance (Windows)

Run from a source checkout whose attested product files match the immutable installed CDS entry. Install the repository's locked QA dependencies first. Use a normal, unelevated interactive Windows desktop; an SSH session or Task Scheduler job does not substitute for that desktop context.

    python verify_fault_boundaries.py --entry <immutable-cds-entry.exe> --reference <official-same-version-codex.exe> --output <short-owned-output-directory>

The default runs two direct-reference HTTP authentication probes and seven fault scenarios against both the reference and candidate binaries. All model responses and credentials are synthetic. The provider service and native HTTP helper connect only through the current process's temporary CONNECT proxy to its owned loopback TLS server. No system proxy, certificate store, real account, model endpoint, global Codex process, or credential store is changed.

The scenarios cover truncated SSE, a real TCP reset, upstream 401/403 followed by explicit fixture credential repair, incomplete tool dispatch, owner death after a non-idempotent append but before its result, and provider-worker death after the tool result is persisted and a subsequent native output delta is observed. The MCP fixture appends on every invocation; it contains no deduplication.

Evidence includes native events and persisted call/output records, the actual append ledger, request counts, receipt terminal states, reference/candidate comparisons, and PID/birth observations. A provider receipt saying completed proves the upstream terminal, not native delivery or a tool effect. An unreturned result may be repaired by the native runtime as aborted; the test records that result and verifies that cold recovery did not dispatch MCP again.

A prior direct-reference authentication evidence directory can be supplied with --reference-auth-evidence; each result must bind to the exact reference binary hash and a failed native 401/403 turn. Omit this option to reproduce those probes. --kind selects a diagnostic subset; only the default complete set reports all_scenarios=true.

These are deterministic runtime/transport tests, not real-model quality tests or a replacement for the separate complete runtime matrix, OS sandbox enforcement, App GUI acceptance, or the real-model crash-recovery verifier. Windows is the only qualified platform for this entry point.
