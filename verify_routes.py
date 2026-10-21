"""Verify simultaneous strict-schema and serial-tool requirements on the fixed route."""
from process_acceptance import evidence_root
import asyncio
import json
import os
from pathlib import Path
import uuid
import aiohttp
from codex_deepseek.cli import catalog,credential,generation_receipt
from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store
from codex_deepseek.native_http import NativeTransport

ROUTE_CASES=(('primeintellect','PrimeIntellect'),('primeintellect','PrimeIntellect'),('morph/fp8','Morph'),('deepseek','DeepSeek'))
GENERATION_MODEL='deepseek/deepseek-v4.1-flash-20261017'


def generation_proof_row(response_id,expected_provider,generation,metadata_attempts):
    """Keep returned metadata intact and bind its identity to this exact response."""
    row={key:generation.get(key) for key in ['id','provider_name','model','total_cost','tokens_prompt','tokens_completion']}
    row.update(expected_response_id=response_id,expected=expected_provider,metadata_attempts=metadata_attempts)
    row['passed']=(isinstance(response_id,str) and bool(response_id) and row['id']==response_id
                   and row['provider_name']==expected_provider and row['model']==GENERATION_MODEL)
    return row


def route_evidence_passed(routes,proof,response_ids):
    """Require all four response, local receipt and generation identities to agree."""
    if (len(response_ids)!=len(ROUTE_CASES) or any(not isinstance(value,str) or not value for value in response_ids)
            or len(set(response_ids))!=len(response_ids) or len(routes)!=len(ROUTE_CASES) or len(proof)!=len(ROUTE_CASES)):
        return False
    expected_routes=[[provider,'completed',response_id] for (provider,_),response_id in zip(ROUTE_CASES,response_ids)]
    if any(not isinstance(row,(list,tuple)) or len(row)!=3 for row in routes):return False
    if [list(row) for row in routes]!=expected_routes:return False
    return all(isinstance(row,dict) and row.get('passed') is True
               and row.get('expected_response_id')==response_id and row.get('id')==response_id
               and row.get('expected')==expected_provider and row.get('provider_name')==expected_provider
               and row.get('model')==GENERATION_MODEL
               for row,(_,expected_provider),response_id in zip(proof,ROUTE_CASES,response_ids))


async def main():
    root=evidence_root('combined-route')/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    helper=Path(os.environ['CODEX_DEEPSEEK_TEST_HTTP_BINARY']) if os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY') else None
    model_catalog=json.loads(Path(os.environ['CODEX_DEEPSEEK_TEST_CATALOG']).read_text()) if os.environ.get('CODEX_DEEPSEEK_TEST_CATALOG') else catalog()
    store=Store(root/'state.sqlite');result={}
    try:
        async with Gateway(credential(),uuid.uuid4().hex,store,model_catalog,native_http_binary=helper) as gateway,aiohttp.ClientSession() as client:
            schema={'type':'object','properties':{'marker':{'type':'string','enum':['COMBINED_NATIVE']},'value':{'type':'integer','enum':[437]}},'required':['marker','value'],'additionalProperties':False}
            body={'model':MODEL,'input':[{'role':'user','content':'First call the fixture tool once with value 437. After its output, respond with marker COMBINED_NATIVE and value 437 in the required schema.'}],'tools':[{'type':'function','name':'fixture','description':'Return the provided integer value.','parameters':{'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}}],'parallel_tool_calls':False,'tool_choice':'required','text':{'format':{'type':'json_schema','name':'combined','strict':True,'schema':schema}},'reasoning':{'effort':'low'},'max_output_tokens':1024,'store':False}
            async def post(payload):
                number=len(list(root.glob('response-*.json')))+1
                (root/f'request-{number}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
                async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',headers={'Authorization':'Bearer '+gateway.token},json=payload) as response:
                    data=await response.json();(root/f'response-{number}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2));assert response.status==200,data;return data
            first=await post(body);calls=[x for x in first['output'] if x['type']=='function_call'];assert len(calls)==1 and calls[0]['name']=='fixture' and json.loads(calls[0]['arguments'])=={'value':437}
            body['input'].extend(first['output']);body['input'].append({'type':'function_call_output','call_id':calls[0]['call_id'],'output':'437'});body['tool_choice']='none'
            second=await post(body);answer=''.join(p.get('text','') for x in second['output'] if x['type']=='message' for p in x['content']);assert json.loads(answer)=={'marker':'COMBINED_NATIVE','value':437}
            morph=await post({'model':MODEL,'input':'Return marker COMBINED_NATIVE and value 437 as required JSON.','text':body['text'],'reasoning':{'effort':'low'},'store':False})
            ordinary=await post({'model':MODEL,'input':'Reply exactly ROUTE_VERIFIED.','reasoning':{'effort':'low'},'store':False})
            rendered=lambda response:''.join(part.get('text','') for item in response['output'] if item['type']=='message' for part in item['content'])
            assert json.loads(rendered(morph))=={'marker':'COMBINED_NATIVE','value':437}
            ordinary_answer=rendered(ordinary).strip()
            assert ordinary['status']=='completed' and ordinary_answer,'Ordinary provider must return a completed, visible model answer'
            model_echo={'expected':'ROUTE_VERIFIED','actual':ordinary_answer,'exact_match':ordinary_answer=='ROUTE_VERIFIED','scope':'Model instruction-following metric, not the runtime/provider-routing acceptance gate'}
            response_ids=[first['id'],second['id'],morph['id'],ordinary['id']]
            proof=[];transport=NativeTransport('https://openrouter.ai/api/v1/responses',binary=helper)
            try:
                for response_id,(_,expected) in zip(response_ids,ROUTE_CASES):
                    generation,metadata_attempts=await generation_receipt(transport,credential(),response_id,'default')
                    proof.append(generation_proof_row(response_id,expected,generation,metadata_attempts))
            finally:await transport.close()
            (root/'generation-proof.json').write_text(json.dumps(proof,indent=2))
            routes=store.db.execute('SELECT provider,status,response_id FROM receipts ORDER BY started').fetchall()
            result={'passed':route_evidence_passed(routes,proof,response_ids),'response_ids':response_ids,'routes':routes,'provider_generation_proof':proof,'tool_calls':len(calls),'answer':json.loads(answer),'model_echo_metric':model_echo}
    finally:
        store.close();(root/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));print('EVIDENCE',root)
    if not result.get('passed'):raise AssertionError('Strict schema and serial route acceptance failed')

if __name__=='__main__':asyncio.run(main())
