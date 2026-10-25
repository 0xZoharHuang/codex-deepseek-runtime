# CDS 0.6.2 qualification

Status: **candidate, not published. Windows frozen qualification and normal candidate installation checks are complete. Final hosted CI, including current macOS/Linux runs, and formal main/tag publication remain pending.** The existing stable entry has not been activated to this candidate.

The fixed upstream is Codex `0.164.0-alpha.7` (`fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`), an alpha release. The model is `deepseek/deepseek-v4.1-flash-1017` through OpenRouter.

| Evidence | Actual result |
| --- | --- |
| Frozen D69 `d69a4edd5b8de66f3db3` | Strict Windows aggregate accepted; 2,150 offline passes and four platform skips |
| Standard scenarios | 30 effective combined gates; original 29 passes/one TUI failure plus two passing terminal gates; `single_run_all_passed=false` |
| Native methods and schemas | 128 positive RPC methods and three native boundaries from 66 sessions; all 64 remaining entries checked; 456 schemas with zero differences |
| Complex tasks | Jinja: runtime 14/14 and application passed. HTTPcore: runtime 14/14, application failed; original timeout, provider error and failed suites preserved |
| Current CF9 `cf9f19eaf67b0fdd8151` | Complete current offline: 2,179 passes, four platform skips, zero failures/errors |

The [qualification summary](windows-qualification-summary.json) retains original failures and producer identities. The [component proof](component-equivalence.json) compares both actual installations: all 29 adapter source files, catalog, dependency lock, installed native manifest and 2,991 native files match. Only two of the 99 product inputs changed: explicit LF output in the manifest generator and CRLF-to-LF manifest representation, with equal JSON. CF9's deployment-bound launcher has its own hash; D69 raw tests were not re-signed as a CF9 full rerun.

[CF9 installation health](candidate-installation-summary.json) is independently closed: normal doctor verified official DeepSeek; doctor plus one saved-thread request used two completed real provider requests. A zero-model continuation proved restricted native Python dispatch, inside write/outside denial, EOF exit 0 and same-thread cold history/file recovery. All 15 continuation actors and the provider retired naturally; forced/remaining/unknown/errors were zero. Initial PowerShell constrained-language rejection and controller bookkeeping failure remain preserved.

D69 source and consumer were sealed before the five [QA observer upgrades](qa-upgrade-lineage-summary.json), which are outside product99. Their separate supplements and [current offline result](current-offline-summary.json) keep their own identities; old helper evidence is not relabelled as upgraded-helper execution.

The exact final commit's hosted CI, formal main/tag, matching public assets and public install readback are still pending. Future URLs and older CI results do not establish publication. See [candidate release notes](release-notes.zh-CN.md); [the dated progress record](progress-2026-10-25.json) remains historical. Codex App gateway, model-generated application quality and OpenAI cloud entitlements are separate scopes.
