# 0.6.5 qualification record

Status: candidate under qualification. Production build, installed profile-home regression, current installed suites and publication are not yet accepted. No stable claim is made by this record.

The Windows metadata-owner overlay accepts only the namespace user, SYSTEM and BUILTIN\\Administrators. It does not change directory ownership. OS WRITE_DAC, explicit DENY behavior, pinned directory identities, no-reparse checks, non-inheriting FILE_READ_ATTRIBUTES-only ACEs and the exact grant/revoke journal remain in force.

A fresh real user-profile home reproduced the released 0.6.4 failure before any model request. The earlier C-root fixture and later profile installation/help did not cover that topology. All previous failures, including the earlier Administrators-owned fixture workaround, remain historical evidence; that workaround is not the product fix.

The current source recipe is Windows-only. Its complete 0.6.4 prefix remains unchanged. macOS and Linux select their original 0.6.2 native assets and recipe IDs. Reused evidence must be identified separately from current executions.

Current source-level checks include the named-overlay integrity tests, public Git patch replay for all three targets, and actual non-elevated native ancestor/critical-read tests. These do not establish installed production or Codex App qualification. Final receipts and the actual scope will be added after their checks finish.
