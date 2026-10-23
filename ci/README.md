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

The controller requires fresh, separate evidence and fixture paths. CI also runs\nthe remaining Python adapter suite and native dispatch fixtures against the same\nverified package. Only explicit Windows-only cases (and the Linux identity case\non macOS) may skip; missing source archives, unsupported fixture operations and\nother skipped checks fail this gate. The receipt records the actual skip list. The uploaded
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

The workflow runs manually, or when the dedicated codex/cds-qualification
branch is pushed. Candidate CI evidence is reviewed before promoting any native
archive into the supported release manifests. Earlier platform results are not
silently assigned to a new binary.
