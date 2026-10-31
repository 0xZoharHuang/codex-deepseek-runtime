# Guardian provider transport: pinned 0.164 candidate

Status: prepared source candidate, not a qualified native release. No Cargo build,
Rust test, model request, installation or native asset relabeling was performed
while preparing this change.

The earlier 0.6.8 plan to reuse every 0.6.7 native byte applied only to the pure
protocol change. It is superseded for this transport defect: the shared Guardian
V2 connection pool ignores a provider's supports_websockets=false setting.
Reusing the affected CLI cannot close that defect.

## Minimal change and admission

The base remains fe136d4e70ef2b5bbed40a2d22b3aabf267796d3,
Codex 0.164.0-alpha.7. The production patch is three lines in start_refill,
returning before opening a WebSocket pool for an HTTP-only provider. Runtime
approvals, scoring, scheduling, cancellation, concurrency, HTTP transport and the
WebSocket-capable path remain native.

guardian-provider-transport-0164.patch is byte-identical to the independently
prepared 0.165 patch because both original pool and test files have the same
preimages. Its loopback test exercises actual HTTP scoring twice, no socket
opens, no idle sockets and the Guardian request header. Existing true-provider,
refill, timeout, capacity and cancellation tests remain required controls.

guardian-provider-transport-0164-candidate.json extends the entire current
recipe with this one common overlay on all three shipped targets. Every changed
file has a before/after SHA. In this source candidate, runtime-overlays.json is
promoted to its exact canonical LF bytes before parallel fresh Unix builds. The
original runtime-overlays-0.6.7-acc16483.json is retained byte for byte (SHA256
acc164834bdf4bc3be257156ed82917eda3e81720a5ed6b7008be24c83f7f6db).
Existing 0.6.7 ready manifests and assets remain unmodified rollback provenance;
they do not attest this new source or qualify as guarded candidate binaries.

The default builder and explicit --recipe candidate now require the same reviewed
26-overlay order, ending in the shared guard. The archived 0.6.7 recipe is not a
build selection. The builder rejects
an unreviewed external recipe path, omission/reordering of the guard and
Windows-only coverage for this common patch. Its receipt records the selected
recipe's actual SHA. Both active and candidate canonical SHA256 are
29ffae4eeae3733592626ea1cc0c8154867006af4e761f61cb3f03e17dac9fca.
JSON semantic equality does not replace this raw-byte identity: changing its LF
newlines to CRLF yields 56b332e1dd23ef8835016e062036d2846cc8a5aa67bc7a7b68c677d93537cba0.
Generate source locks with write_bytes or an explicit LF newline policy.

## Formal Windows producer

Use build_native.py, not another runtime, launcher or build supervisor. A fresh
output is required. Set process-local MSVC INCLUDE/LIB and PATH from the known
working compiler environment. No machine environment, App profile or existing
CDS installation needs changing.

Example maintainer command after those environment checks:

    python -I -B build_native.py --work <owned-existing-CDS-work> --output <fresh-output> --jobs 2 --recipe <checkout>/native/guardian-provider-transport-0164-candidate.json --toolchain-bin <pinned-1.99.1-toolchain-bin>

The optional toolchain directory selects its rustc, rustdoc and cargo directly;
the builder still rejects a rustc version other than the recipe's exact pin.
Without it, the established Rustup path is unchanged. This avoids installing a
Rustup proxy merely to use a compiler already present on the Windows host.

The standard work/target and CARGO_HOME caches can be reused. The builder extracts
a fresh source directory, so registry dependencies may be cached while workspace
crates still rebuild. Do not promise a one-crate rebuild or rewrite Cargo
fingerprints to obtain it.

The standard producer retains the HTTP helper and complete Windows CLI/sandbox
target command. A prior dependency graph shows Guardian V2 is reachable from the
CLI and not from the HTTP/sandbox/service helper normal/build closures. That is
only a necessary condition for component reuse. Skipping a helper additionally
requires exact source, dependency, compiler, flags and producer-byte evidence.
Until that is independently qualified, keep the standard complete producer.
Never label old assets as repaired just because their other components are
unchanged.

The archived official source, original Code Mode Host and ripgrep downloads must
match build_assets() hashes. A verified existing file may seed the download cache;
do not reconstruct an archive and pretend it matches the original archive pin.

## Qualification and promotion

1. Produce the first real candidate package and retain its compiler commands,
   full recipe, source hashes, original logs, receipt and exact output hashes.
2. Run the Guardian connection_pool native tests, including the false-provider
   test and existing true-provider/refill/cancellation controls. These are real
   local transport tests and require no model credential.
3. This branch promotes the reviewed recipe as a source-only candidate so the
   existing Windows producer and fresh Unix CI can qualify the same inputs in
   parallel. No generated readiness manifest is changed. Existing package and
   qualification commands retain their exact recipe/source checks and reject
   old ready binary identities. Do not bypass those checks or call this source
   transition a completed build or stable release.
4. Qualify and package actual outputs with the active reviewed recipe. Generate
   new manifest pins only from those real artifacts. Update the candidate-phase
   admission tests when promoting; the final active recipe must reject guard
   omission, not accept the old stack as a fallback.
5. Use the existing native-platform-qualification workflow for Linux and macOS
   after that source promotion. Its full pinned producer and native/adapter
   qualification remain required. The native-platform-reuse workflow must not
   reuse an old CLI for this common source change.
6. Publish stable support only after target qualification and exact remote asset
   readback. Source replay and Python tests do not substitute for Rust, native,
   live CDS or App acceptance.

Run the selected native tests from this producer's recorded new source tree,
using the same target cache and release profile:

    cargo +1.99.1 test --locked --release -j 2 -p codex-guardian-v2 --lib async_scorer::sampler::connection_pool -- --test-threads=2 --show-output

On Windows with the explicit toolchain, invoke its cargo.exe directly and keep
RUSTC/RUSTDOC and CARGO_TARGET_DIR in that child environment. Derive the new source
tree from this producer's directory admission; do not pick an unrelated newest
directory.

## Existing Unix CI dependencies

Keep the current pinned checkout/upload actions and Python 3.13.3 setup. Linux
uses ubuntu-24.04 with build-essential, binutils, make, pkg-config, libcap-dev,
libasound2-dev and perl. macOS uses the existing macos-15 arm64 workflow with its
uname check; do not assume a runner's architecture from its label alone.
Install the exact Rust 1.99.1 recipe pin and existing hashed Python test lock.
No new crate or OS dependency is added by the three-line guard or its test.

Linux still builds/strips its pinned bwrap resource before embedding its SHA in
the CLI. Code Mode Host, ripgrep and license provenance remain pinned. Use hosted
runners for Unix verification; this work does not require operating on the Mac
development machine.

## Current evidence boundary

The initial stable and explicit-candidate source replay is historical evidence.
This active-source promotion was separately checked with real three-target Git
source replay for the active, explicit-candidate and exact archived 0.6.7 stacks,
plus fail-closed recipe and qualification-controller negative controls. These
checks do not compile or execute a new native binary.

The fresh Unix workflow additionally runs ci/qualify_guardian_pool.py against the
producer's actual new source tree and target cache. The existing complete source
verifier projects the fixed official archive plus all reviewed overlays and
checks every native source before and after the tests, including dependencies
not listed in the producer's changed-file receipt. It requires the exact ten
reviewed Rust tests, rejects ignored tests and upstream network-skip macros that
return Ok, and reports filtered tests as not executed. Linux tests verify and
reuse the producer's shipped bwrap digest. The original 22 real CLI/helper
fixtures and complete installed Python offline collection still run separately.
Fresh Unix native execution remains a release gate, not a result inferred from
source or controller checks. App/live-model behavior and installation also
retain their independent acceptance gates.
