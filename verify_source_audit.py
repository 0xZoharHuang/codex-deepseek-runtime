"""Independent native read-only audit; model findings are review inputs, not acceptance proof."""
import argparse
import json
from pathlib import Path
import uuid
from verify_live import Peer,text


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="high"\nweb_search="disabled"\n[features]\nmemories=false\n')
    source=args.source.resolve();peer=Peer(root)
    try:
        thread=peer.new(sandbox='read-only',approvalPolicy='never')
        prompt=f'''Perform a fresh independent source audit of the dedicated Codex/DeepSeek runtime at {source}.
Read only that source tree and its native/codex-runtime.patch. Do not access any
Codex App state, home credentials, keychains, network services, sibling private
profiles, or raw API traces. Do not delegate, mutate files, or execute installer.
This is a read-only review of the current implementation, not a design proposal.
Focus on actual correctness risks: native capability lost or silently overridden;
provider/model isolation across all transports; API-key exposure; helpers and
child-process lifetimes; retries/cancellation; streaming tool identities;
custom grammar and delayed tool visibility; active-goal, nested-peer and memory
semantics; credential-store namespacing; immutable installation and recovery.
The intended scope is local Codex runtime equivalence, excluding model quality,
OpenAI cloud services and entitlements. Native limitations reproduced on official
upstream must be distinguished from new adapter defects. Inspect the code and
existing test coverage, not just README claims. Report concrete actionable defects
with severity, exact source path, trigger and likely consequence. For each finding,
state whether it is established by source or needs an experiment. Do not invent
issues from hypothetical style preferences. Also list any uncovered high-risk
behavior and the files actually inspected. If no actionable defect is established,
state that plainly; do not claim the overall release complete. Return a concise
technical review in Chinese.'''
        _,start=peer.start(thread,prompt)
        items=peer.complete(thread,start,timeout=900)
        report=text(items);(root/'review.md').write_text(report+'\n')
        if not report.strip() or not any(item.get('type')=='commandExecution' for item in items):raise AssertionError('Independent reviewer did not inspect source with native tools')
        (root/'results.json').write_text(json.dumps({'completed':True,'source':str(source),'thread':thread,'report':'review.md','acceptance_claim':False},indent=2));print('EVIDENCE',root,flush=True)
    finally:peer.close()


if __name__=='__main__':main()
