# CDS 0.6.1 publication plan — qualification passed

Producer source: ab482879192772fafab903a25692076c690a49a5. Tested product source ID: c9870a1014d6af890b10. Final README-only release source ID: 74780d15c9a54ff5486f. The semantic release qualification in qualification.json passed; tagging, publication and tagged public-install readback remain coordinator actions. This plan is not a publication receipt.

## Completed qualification

- Original tested c987 95-product / 180-QA freeze retained byte-for-byte. The release-source-derivative.json receipt independently verifies README.md is the only product delta: other 94 product files, runtime28, QA180 and all native inputs/manifests unchanged.
- Windows: 1,812 passed + three explicit skips = 1,815.
- Linux: 1,784 passed + 31 explicit skips = 1,815.
- macOS: 1,783 passed + 32 explicit skips = 1,815.
- Native 15/15 per platform is part of each complete collection, not additional to it. Hosted Controller50 passes are separate.
- Original hosted run metadata and both raw ZIPs independently reviewed; exact archive/recipe/adapter identity, partition, warning policy, skips and recorded owners passed.
- Exactly two official DeepSeek provider requests completed, without retry/fallback. Original immediate generation 404 remains; later zero-model metadata readback verifies actual provider/canonical model. One owned File was deleted and verified absent.
- The first Windows zero-case configuration failure and all original producer accepted:false records remain intact. The business HTTP 400 cause is still unassigned.

## Reuse existing native packages

Keep native/runtime.json, native/runtime-x86_64-pc-windows-msvc.json and native/runtime-x86_64-unknown-linux-gnu.json byte-identical. Their v0.6.0 archive URLs, SHA-256, per-file maps and recipe IDs remain the source installation contract.

The hosted workflow actually used v0.6.0-native.1 archive URLs and the original archived recipe. Both URL sets are explicit in proof-index.json and ci/independent-review.json; the pinned archive bytes and recipe IDs match. This workflow override does not rewrite the source manifests.

The unchanged three native archives total 507,041,407 bytes. No new native build or duplicate upload is needed. Existing archived build recipes, original build receipts and provenance retain their original source identities.

## Remaining coordinator actions

1. Review and stage the reports only. Compare Git blob bytes with the final report checksums, including both raw ZIPs and all unchanged producer JSON. Recheck that release95 matches release-source-derivative.json, QA180 matches the original freeze, and the README-only delta from tested c987 remains exact and that no API keys, private keys, raw business prompts or company images were included.
2. Create the v0.6.1 source tag at the final reviewed source/report commit. Keep the producer commit distinct from this later report commit. Verify final release product ID 74780d15c9a54ff5486f and the reviewed unchanged QA map; do not report tested c987 as the final full-source identity.
3. Publish the selected qualification files and their checksums from reviewed original bytes, together with GitHub's normal tagged source distribution. Choose the assets needed for this release; there is no fixed eight-asset contract. Link/reuse existing immutable native assets instead of duplicating them.
4. Perform a bounded final public source-install/readback smoke test using the tagged source and the unchanged source-lock native URLs. Record actual version, release source ID 74780d15c9a54ff5486f, archive/recipe identity, help/status and any failure. Use a new owned installation prefix; do not silently switch another user's current installation.
5. Publish the release after the remaining publication/install-readback gates close. Record the final tag, source/report commit, selected assets and receipts. Preserve rollback to the prior immutable installation.

Do not turn the overall semantic-release qualification flag into a claim of App GUI completion, a fresh all-runtime paid qualification, or causality/recovery of the earlier business 400. No additional model request is needed for this publication work.
