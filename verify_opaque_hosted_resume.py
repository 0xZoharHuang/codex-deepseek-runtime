"""Verify official DeepSeek routing after an interrupted hosted-search turn.

Use an immutable CDS Windows release interpreter and a new output directory.
This sends only synthetic test content. It never loads a business session or
displays credentials, provider output, or reasoning.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import time
import urllib.error
import urllib.parse
import urllib.request

import codex_deepseek.protocol as protocol
from codex_deepseek.credentials import credential
from codex_deepseek.opaque_replay import restore
from codex_deepseek.protocol import MODEL, PROVIDER, prepare
from codex_deepseek.state import Store

EXPECTED_ADAPTER_SHA256 = '5261056d71d97ad697e4870f0722be6acbceacc83fb58345436607702c5bff5b'

ENDPOINT='https://openrouter.ai/api/v1/responses'
ROUTE={'only':['deepseek'],'allow_fallbacks':False,'require_parameters':True}
LOOKUP={'type':'function','name':'route_fixture_lookup','description':'Returns the fixture value for the requested item.',
        'parameters':{'type':'object','properties':{'item':{'type':'string'}},'required':['item']}}

def pending(reason=False):
    return {'model': MODEL, 'provider':ROUTE,'reasoning':{'effort':'max'},'stream':True,'store':False,
            'max_output_tokens':600, 'max_tool_calls':2, **({'include':['reasoning.encrypted_content']} if reason else {})}

def call(key, name, body):
    req=urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers={
        'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'text/event-stream'})
    verification = {}
    rows = []
    try:
        with urllib.request.urlopen(req,timeout=55) as response:
            verification['http']=response.status
            for line in response:
                if not line.startswith(b'data: '): continue
                try: event=json.loads(line[6:])
                except ValueError: continue
                kind=event.get('type') or ''
                if kind=='response.output_item.done':
                    item=event.get('item')
                    if isinstance(item,dict): rows.append(item)
                if kind in ('response.completed','response.failed','response.incomplete'):
                    verification.update(terminal=kind,id=event.get('response',{}).get('id'),model=event.get('response',{}).get('model'),
                                        provider=event.get('response',{}).get('provider'))
                    err=event.get('response',{}).get('error')
                    if isinstance(err,dict): verification['error_code']=err.get('code')
                if kind=='error': verification['error_code']=event.get('code')
    except urllib.error.HTTPError as exc:
        verification['http']=exc.code
        try:
            failure=json.loads(exc.read(8192)).get('error',{})
            verification['error_code']=failure.get('code')
        except (ValueError, AttributeError): pass
    verification.update(probe=name, types=[x.get('type') for x in rows],
                        opaque=sum(isinstance(x.get('encrypted_content'),str) and bool(x['encrypted_content']) for x in rows),
                        completed_searches=sum(row.get('type') == 'openrouter:web_search'
                                               and row.get('status') == 'completed' for row in rows))
    print(json.dumps(verification), flush=True)
    return rows,verification

def generation(key, identifier):
    if not identifier:
        return None
    url = 'https://openrouter.ai/api/v1/generation?' + urllib.parse.urlencode({'id': identifier})
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key})
    for delay in (0, 1, 2, 3):
        if delay: time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                found = json.load(response)['data']
            return found.get('provider_name')
        except (ValueError, KeyError, OSError):
            continue
    return None

def canonical_reasoning(item):
    allowed = ('type', 'id', 'summary', 'content', 'encrypted_content')
    return {k: item[k] for k in allowed if k in item}

def native_history(outputs, registry):
    return [
        canonical_reasoning(item) if item.get('encrypted_content') else item
        for item in (registry.output(item) for item in outputs)
    ]

def attested(store, key, registry, rows, observed):
    if observed.get('http') != 200 or observed.get('terminal') != 'response.completed':
        raise RuntimeError('Real provider did not complete')
    events = SimpleNamespace(response_id=observed['id'], usage={}, terminal=True,
                             status='completed', registry=registry, opaque_credential=key,
                             done={n: row for n, row in enumerate(rows)})
    receipt = store.start(PROVIDER)
    store.finish(receipt, 'completed', 200, events)
    return receipt

def verify(owned, key):
    lookup = LOOKUP
    tools = [lookup, {'type':'web_search','external_web_access':True,'filters':{'allowed_domains':['docs.python.org']}}]
    request = pending(True)
    request.pop('provider')
    request['tools'] = tools
    prompt = {'role':'user','content':'Call route_fixture_lookup exactly once with item blue to obtain its label and number; then report the result.'}
    with closing(Store(owned / 'adapter.sqlite')) as store:
        p1, registry = prepare({**request,'input':[prompt]}, store, opaque_replay=True)
        first, result1 = call(key, 'tool_origin', p1)
        attested(store, key, registry, first, result1)
        calls = [item for item in first if item.get('type')=='function_call' and item.get('name')==lookup['name']]
        if len(calls) != 1:
            raise RuntimeError('Expected exactly one fixture lookup')
        call_id = calls[0]['call_id']
        receipt = {'type':'function_call_output','call_id':call_id,'output':'Synthetic blue fixture: RIVER, 584.'}
        (owned / 'synthetic-effect-count.txt').write_text('1\n', encoding='ascii')
        prior = [prompt, *native_history(first, registry), receipt]
        user_search = {'role':'user','content':'Use web search for the official Python str.removeprefix documentation, and also report the number from the blue fixture, without calling route_fixture_lookup again.'}
        p2, registry = prepare({**request,'input':[*prior,user_search]}, store, opaque_replay=True)
        restore(p2, store.db, key)
        second, result2 = call(key, 'official_search_origin', p2)
        attested(store, key, registry, second, result2)
        if not any(x.get('type')=='openrouter:web_search' and x.get('status')=='completed' for x in second):
            raise RuntimeError('Expected source search completion')
        opaque = [item for item in second if item.get('encrypted_content')]
        if not opaque:
            raise RuntimeError('Live search origin yielded no opaque item')
        producer = generation(key, result2['id'])
        if producer not in ('DeepSeek','deepseek'):
            raise RuntimeError('Official origin generation not attested')
        history = [*prior,user_search,*native_history(second,registry)]
        tail = {'type':'reasoning','id':'rs_synthetic_interrupted_1122','summary':[],
                'content':[{'type':'reasoning_text','text':'The interrupted turn had the blue number and was about to check list.append in the official Python documentation.'}]}
        continuation = {'role':'user','content':'Resume after the interruption. Use web search for the official Python list.append documentation. State the blue fixture number from the existing tool result and cite the official Python documentation. Do not call route_fixture_lookup again.'}
        body = {**request,'input':[*history,tail,continuation]}
        prepared, registry = prepare(body, store, opaque_replay=True)
        assert registry.route_reason=='official_opaque_history_with_hosted_tools'
        assert prepared['provider'] == ROUTE
        restore(prepared, store.db, key)
        by_id = {item.get('id'):item for item in prepared['input'] if item.get('encrypted_content')}
        assert all(by_id[item['id']]==item for item in opaque)
        assert [x['type'] for x in prepared['input'] if x.get('call_id')==call_id] == ['function_call','function_call_output']
        assert any(x['type']=='openrouter:web_search' for x in prepared['tools'])
        result, result3 = call(key, 'candidate_mixed_opaque_resume', prepared)
        if result3.get('terminal') != 'response.completed':
            raise RuntimeError('Live continuation did not complete')
        assert not any(x.get('type')=='function_call' and x.get('name')==lookup['name'] for x in result)
        recovered = generation(key, result3['id'])
        searched = sum(x.get('type')=='openrouter:web_search' and x.get('status')=='completed' for x in result)
        final = ' '.join(p.get('text','') for x in result if x.get('type')=='message' for p in x.get('content',[]) if p.get('type')=='output_text')
        count = (owned / 'synthetic-effect-count.txt').read_text(encoding='ascii').strip()
        proof = {'official_origin':producer,'official_resume':recovered,'model':MODEL,'verified_opaque_count':len(opaque),
                 'route_reason':registry.route_reason,'native_call_pair_preserved':True,'effect_count':int(count),
                 'searches_completed':searched,'final_mentions_prior_result':'584' in final,
                 'final_mentions_python_method':'append' in final,'inference_completed':True}
        return proof

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New directory for the sanitized JSON receipt')
    options = parser.parse_args()
    if sys.platform != 'win32':
        raise RuntimeError('Run this verifier from the Windows employee account')
    if hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest() != EXPECTED_ADAPTER_SHA256:
        raise RuntimeError('The installed adapter is not the qualified candidate')
    if os.environ.get('OPENROUTER_API_KEY'):
        raise RuntimeError('Run in a terminal using the existing Windows credential store')
    output = options.output.resolve()
    output.mkdir(exist_ok=False, parents=False)
    with tempfile.TemporaryDirectory(prefix='cds-opaque-resume-', dir=output) as scratch:
        proof = verify(Path(scratch), credential())
    passed = (proof['official_resume'] in ('DeepSeek', 'deepseek')
              and proof['searches_completed'] > 0
              and proof['final_mentions_prior_result']
              and proof['final_mentions_python_method']
              and proof['effect_count'] == 1)
    proof['passed'] = bool(passed)
    (output / 'verified.json').write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(proof), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
