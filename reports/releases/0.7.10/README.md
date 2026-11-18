# CDS 0.7.10 reasoning replay

CDS can replay verified opaque reasoning from the pinned official DeepSeek
route after a tool call or a cold resume. The adapter binds each retained
item to its provider receipt and credential before forwarding it. It records
the proof and completed receipt atomically after a clean upstream stream
ends. Native Codex remains responsible for tool execution and sessions.

Older opaque items without the producer evidence still fail explicitly.
The replay proof is scoped to the provider route, model and credential.
Commands already dispatched by the native CLI before an opaque stream failure
retain their original effects.

This source reuses the reviewed native archive for each target. Release
qualification is per platform and records the exact source and native
identities, offline opaque replay through the actual CLI, and the installation
path used for that platform. Real-provider workflow and synthetic opaque replay
results are reported separately.
