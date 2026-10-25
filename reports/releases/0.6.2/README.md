# CDS 0.6.2 qualification

Status: **v0.6.2 is formally published as a stable GitHub Release** (Release `423636524`, draft=false, prerelease=false; latest-release readback verified). The [Release](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.2) remains authoritative. Product tag `4f24af36f62c538ba35817ad48aa49d4308220b5` is unchanged; QA verification commit `d325437588e7fbde418aa52f008740ea96fab0fd` keeps the same CF9 product99 and native assets. Main was fast-forwarded to the verified QA commit and read back from GitHub; the merge event is recorded in [publication-record.json](publication-record.json). Publication does not activate an existing user installation.

The fixed upstream is Codex `0.164.0-alpha.7` (`fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`), an alpha release. The model is `deepseek/deepseek-v4.1-flash-1017` through OpenRouter.

| Evidence | Actual result |
| --- | --- |
| Frozen D69 `d69a4edd5b8de66f3db3` | Strict Windows aggregate accepted; 2,150 offline passes and four platform skips |
| Standard scenarios | 30 effective combined gates; original 29 passes/one TUI failure plus two passing terminal gates; `single_run_all_passed=false` |
| Native methods and schemas | 128 positive RPC methods and three native boundaries from 66 sessions; all 64 remaining entries checked; 456 schemas with zero differences |
| Complex tasks | Jinja: runtime 14/14 and application passed. HTTPcore: runtime 14/14, application failed; original timeout, provider error and failed suites preserved |
| Current CF9 `cf9f19eaf67b0fdd8151` | Complete current offline: 2,229 passes, four platform skips, zero failures/errors |

The [qualification summary](windows-qualification-summary.json) retains original failures and producer identities. The [component proof](component-equivalence.json) compares both actual installations: all 29 adapter source files, catalog, dependency lock, installed native manifest and 2,991 native files match. Only two of the 99 product inputs changed: explicit LF output in the manifest generator and CRLF-to-LF manifest representation, with equal JSON. CF9's deployment-bound launcher has its own hash; D69 raw tests were not re-signed as a CF9 full rerun.

[CF9 installation health](candidate-installation-summary.json) is independently closed: normal doctor verified official DeepSeek; doctor plus one saved-thread request used two completed real provider requests. A zero-model continuation proved restricted native Python dispatch, inside write/outside denial, EOF exit 0 and same-thread cold history/file recovery. All 15 continuation actors and the provider retired naturally; forced/remaining/unknown/errors were zero. Initial PowerShell constrained-language rejection and controller bookkeeping failure remain preserved.

D69 source and consumer were sealed before the five [QA observer upgrades](qa-upgrade-lineage-summary.json), which are outside product99. Their separate supplements and [current offline result](current-offline-summary.json) keep their own identities; old helper evidence is not relabelled as upgraded-helper execution.

[Hosted qualification 40227053597](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40227053597) passed at commit `71224bc1ebb40374ea816f9ae12cf78134537a83` using the declared Actions-artifact route. Linux passed 171 controller, 21 native and 2,124 adapter cases with 40 platform skips; macOS passed 171 controller, 21 native and 2,123 adapter cases with 41 platform skips. All collected cases executed once and process hygiene passed; no model request or native rebuild was performed. The [sanitized hosted report](hosted-qualification-summary.json) preserves exact identities.

The earlier 2,179-pass/four-skip Windows collection and [first hosted collection failure 40225926715](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40225926715) remain historical. Two added CI entry-point tests cover the isolated import fix: trusted repository helpers are admitted without dropping `-I` or shadowing the installed adapter with `ROOT/src`. That collection completed 2,181 passes/four skips and remains preserved; the latest complete Windows result is 2,229 passes/four skips.

The public `release-asset` route then passed [CI 40233851528](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40233851528) at QA commit `d325437588e7fbde418aa52f008740ea96fab0fd`: Linux 171 controller + 21 native + 2,172 adapter cases/40 skips; macOS 171 + 21 + 2,171/41 skips. Both platforms downloaded the exact public v0.6.2 archive and original recipe, passed process hygiene, and performed zero native rebuilds or model requests. The [portable report](public-asset-qualification-summary.json) records the actual acquisition URLs and hashes.

The first public-asset run [40230459754](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40230459754) remains recorded: macOS passed, while Linux had 20 native passes and one QA metadata-race failure. The [observer audit](public-asset-observer-audit.json) preserves the failure, 48 new pure cases (60 including 12 existing), strict no-external-tools guard, and a real restricted-command control that the guard correctly rejected. The fix changes QA only; the tag, product99, native assets, ownership and timeout rules are unchanged.

A separate [normal Windows public installation](public-install-readback.json) downloaded the release archive without a local native archive or manifest override, verified all 2,991 native files, and passed real version/help dispatch with clean process retirement. Python dependencies came from a hash-locked local wheelhouse. This check did not activate the daily default `cds` entry or repeat model inference.

See the [publication record](publication-record.json) for the verified release state and separate main-merge status, [release notes](release-notes.zh-CN.md), and the unchanged [historical progress record](progress-2026-10-25.json). Codex App gateway, model-generated application quality and OpenAI cloud entitlements remain separate scopes.
