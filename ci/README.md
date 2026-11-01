# Native transport CI

These jobs build and exercise CDS's changed native transport code on disposable
GitHub-hosted macOS and Linux machines. They do not access the maintainer's Mac, a Codex App profile,
an OpenRouter account, or a provider credential.

```text
Fixed source commit + patch hashes + compiler pin
                       |
                 build_native.py
                       |
             Actual CLI + HTTP helper
                       |
       Owned loopback Responses / TLS / proxy fixtures
                       |
       Receipts, original failures, binary/source hashes
                       |
        Release review across all required gates
```

The jobs use GitHub's standard macos-15 arm64 and ubuntu-24.04 x64 runners, verified
in the [official runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
macOS uses one build job for its smaller memory limit; Linux uses two. Each job
records its actual platform and requires 20 GiB of free disk before building.
It stops on insufficient space instead of deleting unrelated preinstalled software.
Linux installs and records the fixed-source build prerequisites, builds the native
bubblewrap resource, and retains its exact digest and sources. Its GNU binary must
also be checked against the intended deployment distribution and sandbox environment;
a successful Ubuntu build does not certify every older Linux distribution.
The three GitHub-owned actions are pinned by commit; their reviewed release
versions are comments in the workflow.

After the fresh producer, qualify_guardian_pool.py uses its one admitted source
tree, combined patch, every asserted source file, and complete source inventory
against the fixed official archive plus reviewed overlays before and after the
native tests. It retains the full native inventory/hash/count separately from
the adapter checkout identity, and uses the exact compiler/release environment
and target cache. It executes the ten specifically named Guardian pool tests:
false-provider zero-WebSocket HTTP scoring, true-provider prewarm/refill/recovery,
capacity, and cancellation. Linux reuses the exact verified bwrap digest in the
package and target cache. It rejects changed/missing/duplicate test identities,
ignored tests, and upstream skip macros that return Ok. Filtered tests are not
counted as executed. Original Cargo logs and a separate scope receipt are kept.
No provider credential or model call is involved.

The same controller, with `--suite state-migrations`, separately executes the
22 explicitly named `codex-state` migration tests on that producer source/cache.
It includes both coherent LF and CRLF pending migrations on each platform, all
six stores, lazy auxiliary inheritance, mixed known forms, legacy recency repair,
strict unknown/dirty failures, and SQL/checksum consistency. Each exact test must
actually execute with zero failures or ignored tests; filtered tests do not count.
The `state-migrations` receipt and full native log are separate from Guardian's.
The compiler/profile/package/full-source checks apply before and after both
suites. This step builds the test target, not a replacement native package, and
does not certify old stock readers for mixed-style ledgers or any live profile.

qualify_native.py checks the complete package inventory, fixed input archives,
patch stack, source digests, compiler pin, and target before testing. It executes
both native regression modules without skipping any case, rejects failures,
errors and skipped tests, checks that sources and binaries did not change, and
removes only the synthetic fixture directory it created. The test dependencies
are exact versions with wheel hashes.

Run the same controller against a freshly built package:

```sh
python -m pip install --only-binary=:all: --require-hashes -r ci/requirements-test.lock
python -m pip install --no-deps --no-build-isolation .
python -I ci/qualify_native.py --package /path/to/package --evidence /new/evidence --work /new/private-test-work
```

The controller requires fresh, separate evidence and fixture paths. CI also runs
the remaining Python adapter suite and native dispatch fixtures against the same
verified package. Only explicit Windows-only cases (and the Linux identity case
on macOS) may skip; missing source archives, unsupported fixture operations and
other skipped checks fail this gate. The receipt records the actual skip list. The uploaded
artifact contains the package archive, build log, test results, source commit
and receipts. It excludes temporary TLS keys and native test homes. Artifacts
expire after seven days unless retained separately for an approved release.

A successful job certifies only the native payload/transport/retry/compaction
scope covered by these real-binary tests and the separately reported adapter regressions. Responses and Files services in these
tests are synthetic. Live DeepSeek workflows, platform permissions, installation,
state recovery and the rest of the runtime matrix remain separate acceptance
gates. The receipt deliberately keeps product_release_ready false. This
workflow does not create a release, publish a package, or update a readiness
manifest automatically.

The fresh-build workflow, `native-platform-qualification.yml`, runs only by
manual `workflow_dispatch`. Select the exact candidate ref when dispatching it;
pushing a branch does not start this workflow. Review its evidence before
promoting a native archive into the supported release manifests. Earlier
platform results do not qualify a new binary.

After a qualified release is promoted to main, its temporary qualification
branch may be deleted. For a future changed recipe, dispatch the fresh-build
workflow on the new candidate ref. The separate reuse workflow applies only to
its exact pinned native assets and recipe-equivalence rules; it cannot qualify
changed common native code by reusing an older binary.

## Compile failure preservation

The builder records an immutable compiled-not-qualified checkpoint before Cargo
metadata and notice collection. It binds the exact compiled package files,
compiler commands/profile/driver inputs and complete native source inventory,
which must remain unchanged through compilation. Actual Cargo metadata stdout
and its command receipt are retained separately when that stage succeeds.

The always step preserves a bounded native-package-unqualified archive even
when the final build receipt was never written, plus source/recipe/checkpoint
metadata. It does not archive the Cargo target/cache, label output ready, bypass
qualification or resume a build. A later repair must keep the original failure
and receipt unchanged and record explicit postcompile derivation/provenance;
existing compiled bits do not by themselves prove the native tests passed.

Native notice collection resolves relative notice-file aliases only inside an
admitted crate or its exact Cargo-pinned Git checkout. Git HEAD, tree/index mode,
alias text and full target content must match the locked Git objects. This also
handles Windows checkout materialization; arbitrary short strings are not paths.
Logical source, alias chain, resolved target and content SHA remain attributed.


## Requalify Python changes with the exact P27 native build

The registered fresh workflow's explicit `retained_mode=true` uses the reviewed
`retained-native-build-p27-a585.json` inputs from successful run 41308737962.
Select `retained_target=linux-x64` and `retained_target=macos-arm64` in separate
manual dispatches of the same reviewed current candidate ref. Their concurrency
keys are target-specific, so both may run without canceling another target.
This path never invokes Cargo. The source/Python candidate remains distinct from
the original native producer a5853bf6c3468af3a93bc0094bf09d1df4158693.

Acquisition verifies the exact repository, producer commit/run/attempt/job and all
required original successful steps; artifact identity/expiry/size/digest; full
original package and receipt; complete native source, compiler and patch recipe.
State22 and Guardian10 are explicitly carried from their exact original logs and
source-bound receipts, with zero Rust tests executed in this new run. The named
Guardian scope does not independently prove cancellation under an explicitly
false provider configuration. Its original scope limits are preserved.

The current candidate then actually reruns native CLI/helper22 and the complete
installed Python offline collection against those same unchanged binaries.
These are new current-source results, not a relabeling of the original run.
The API token is supplied only in the dedicated acquisition environment, never
as an argument or printed value; it is not forwarded to the signed storage URL.
Only the official Actions storage host/path is accepted and secondary redirects
are refused. The downloaded bytes must match the exact pinned ZIP SHA and size.

These Actions inputs expire and are not a permanent stable distribution channel.
Missing/expired records, changed native sources/recipe/test drivers, unknown
skips or metadata mismatches fail closed. There is no old26, release-asset or
fresh-build fallback. The historical49da pins and their original failed full
workflow status remain immutable records, not the current selection.
