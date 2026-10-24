# CDS 0.6.1 semantic release qualification

**The 0.6.1 image semantic guard release qualification passed.** This non-attested conclusion is tied to producer source commit ab482879192772fafab903a25692076c690a49a5, tested product source ID c9870a1014d6af890b10, 95 product files and 180 QA inputs. It covers the current-source offline regressions, fresh native transport boundaries and two actual official provider cases described below. It does not claim a fresh run of every paid runtime workflow or a Codex App qualification.

The final release source ID is 74780d15c9a54ff5486f. It is a verified documentation-only derivative: README.md is the only changed file among the 95 product inputs. The other 94 product files, all 28 runtime modules, all 180 QA inputs and all native manifests/inputs remain byte-identical to tested c987. The release-source-derivative.json file records both full identities and the exact one-file delta. No model request, CI dispatch or native build was repeated for this documentation change. The final source/report commit will be recorded by the release coordinator; it is distinct from the producer commit.

The original input maps are in frozen-inputs.json (SHA-256 79f2ff4bf1099f244956373b1473e34e234d6e24c44d87b7f2798434b1537eb1). The final qualification.json and proof-index.json bind the raw producer reports. The final source/report tag and publication remain coordinator actions.

## Observed results

| Platform / scope | Result |
| --- | --- |
| Windows full offline suite | 1,815 collected: 1,812 passed, three explicit platform skips, zero failures/errors/deselections |
| Linux complete offline partition | 1,815 collected: 1,784 passed, 31 explicit platform skips, zero failures/errors/deselections |
| macOS complete offline partition | 1,815 collected: 1,783 passed, 32 explicit platform skips, zero failures/errors/deselections |
| Fresh native transport boundaries | 15/15 passed on each platform; included in each 1,815-case collection |
| Hosted controller checks | 50/50 per hosted platform, reported separately from the 1,815 collection |
| Legal developer typed image through prepare and Files | Real HTTP 200/completed; developer role, parts and original bytes preserved |
| Same image bytes in user-low and tool-high | Real HTTP 200/completed; low remains inline and only high becomes a File; call ID, order, detail and bytes preserved |
| Actual provider identity | Later metadata GETs confirmed DeepSeek and canonical model deepseek/deepseek-v4.1-flash-20261017; official model metadata verifies the requested ...-1017 alias |
| Owned provider file cleanup | One upload; DELETE 200 followed by GET 404; no unknown calls/uploads, pins or remaining assets |

All platform pytest invocations used warnings-as-errors. Hosted CI split the complete collection into 15 native cases and a disjoint 1,800-case remainder. The 15 must not be added to the 1,815 again. Controller50 is independent of that collection and does not create an additional observed-owner receipt.

Windows used an isolated immutable 0.6.1 installation. Hosted Linux/macOS installed the same current 28 adapter modules and reused the pinned native packages. The recorded native, collection and remainder process trees ended naturally, with zero forced/remaining/unknown owners in those recorded scopes. The shared user installation was not activated or replaced.

## Original CI artifacts and native reuse

Actions run [40099278406](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40099278406) completed successfully. Its original linux-x64.zip, macos-arm64.zip, run.json and independent-review.json are copied byte-for-byte under ci/. The independent review passed against the fixed producer commit and the exact 95/180 input maps; platform skip names, collection partition, warnings-as-errors and owner scopes remain explicit.

The three source locks retain exact v0.6.0 archive URLs. Hosted CI actually used the workflow's v0.6.0-native.1 URL override and the original archived recipe. The source-lock archive hashes and recipe IDs match those same pinned bytes. Both URL sets are reported explicitly; no lock was rewritten and no URL discrepancy is hidden.

No native rebuild was required. Reuse the existing three archives (507,041,407 bytes total); do not upload duplicate native archives for this Python protocol update.

## Failures remain visible

The first Windows full-suite attempt exited 3 during pytest configuration with zero collected cases because asyncio_default_fixture_loop_scope was unset under warnings-as-errors. Its original failed verdict remains included. The supported function scope was then added to the QA invocation, with warnings still treated as errors.

Both real model calls completed, but their immediate generation lookups returned 404. The original provider/qualification-initial.json still says accepted: false. A later authorized, single metadata-only GET for each original upstream generation ID returned 200; provider/generation-followup.json separately records that readback. There were exactly two model calls, no model retry and no fallback. Both actual provider workers were observed no longer alive; their broker runs ended naturally.

The producer handoffs and frozen-inputs.json are historical raw snapshots. Their fields such as commit_created: false or qualification_complete: false describe their production time. They are not edited to match the later final qualification. The ZIPs also retain the synthetic retry failed-turn outputs and local synthetic catalog Code Mode metadata warnings.

## Explicit scope limits

The earlier 49-image business HTTP 400 remains unassigned. These synthetic successes neither identify its cause nor establish business recovery. No original company image, business prompt or business request was replayed for this qualification.

This update did not repeat the paid 30-task acceptance suite, rebuild native binaries, switch the shared installation, or qualify Codex App. The 0.6.0 reports retain their exact historical source scope and are not relabelled as new 0.6.1 results.

The qualification overall flag applies only to this semantic release scope. Publication and final tagged public-install readback remain separate actions in release-plan.md. This report-preparation step created no tag or Release and performed no commit or push.

Producer bytes are preserved through the directory's Git attributes. The public bundle includes synthetic metadata, hashes and execution receipts, without actual API keys, private keys, business prompts or original company images. SHA-256 content binding is not a signed provenance attestation.
