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
