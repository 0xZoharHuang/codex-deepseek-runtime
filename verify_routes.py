"""Verify simultaneous strict-schema and serial-tool requirements on the fixed route."""
import asyncio
import json
import os
from pathlib import Path
import uuid
import aiohttp
from codex_deepseek.cli import catalog,credential
from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store
from codex_deepseek.native_http import NativeTransport

async def main():
    root=Path(__file__).resolve().parent.parent/'delivery/evidence/combined-route'/str(uuid.uuid4());root.mkdir(parents=True)
    helper=Path(os.environ['CODEX_DEEPSEEK_TEST_HTTP_BINARY']) if os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY') else None
    model_catalog=json.loads(Path(os.environ['CODEX_DEEPSEEK_TEST_CATALOG']).read_text()) if os.environ.get('CODEX_DEEPSEEK_TEST_CATALOG') else catalog()
    store=Store(root/'state.sqlite');result={}
    try:
        async with Gateway(credential(),uuid.uuid4().hex,store,model_catalog,native_http_binary=helper) as gateway,aiohttp.ClientSession() as client:
            schema={'type':'object','properties':{'marker':{'type':'string','enum':['COMBINED_NATIVE']},'value':{'type':'integer','enum':[437]}},'required':['marker','value'],'additionalProperties':False}
            body={'model':MODEL,'input':[{'role':'user','content':'First call the fixture tool once with value 437. After its output, respond with marker COMBINED_NATIVE and value 437 in the required schema.'}],'tools':[{'type':'function','name':'fixture','description':'Return the provided integer value.','parameters':{'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}}],'parallel_tool_calls':False,'tool_choice':'required','text':{'format':{'type':'json_schema','name':'combined','strict':True,'schema':schema}},'reasoning':{'effort':'low'},'max_output_tokens':1024,'store':False}
            async def post(payload):
                async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',headers={'Authorization':'Bearer '+gateway.token},json=payload) as response:
                    data=await response.json();assert response.status==200,data;return data
            first=await post(body);calls=[x for x in first['output'] if x['type']=='function_call'];assert len(calls)==1 and calls[0]['name']=='fixture' and json.loads(calls[0]['arguments'])=={'value':437}
            body['input'].extend(first['output']);body['input'].append({'type':'function_call_output','call_id':calls[0]['call_id'],'output':'437'});body['tool_choice']='none'
            second=await post(body);answer=''.join(p.get('text','') for x in second['output'] if x['type']=='message' for p in x['content']);assert json.loads(answer)=={'marker':'COMBINED_NATIVE','value':437}
            morph=await post({'model':MODEL,'input':'Return marker COMBINED_NATIVE and value 437 as required JSON.','text':body['text'],'reasoning':{'effort':'low'},'store':False})
            ordinary=await post({'model':MODEL,'input':'Reply exactly ROUTE_VERIFIED.','reasoning':{'effort':'low'},'store':False})
            rendered=lambda response:''.join(part.get('text','') for item in response['output'] if item['type']=='message' for part in item['content'])
            assert json.loads(rendered(morph))=={'marker':'COMBINED_NATIVE','value':437}
            assert rendered(ordinary).strip()=='ROUTE_VERIFIED'
            from urllib.parse import urlencode
            proof=[];transport=NativeTransport('https://openrouter.ai/api/v1/responses',binary=helper)
            try:
                for response_id,expected in [(first['id'],'PrimeIntellect'),(second['id'],'PrimeIntellect'),(morph['id'],'Morph'),(ordinary['id'],'DeepSeek')]:
                    generation={}
                    for attempt in range(7):
                        async with transport.request('GET','https://openrouter.ai/api/v1/generation?'+urlencode({'id':response_id}),api_key=credential()) as response:
                            if response.status==200:
                                generation=(await response.json()).get('data',{})
                                if generation.get('provider_name'):break
                        await asyncio.sleep(min(2**attempt,8))
                    row={key:generation.get(key) for key in ['id','provider_name','model','total_cost','tokens_prompt','tokens_completion']}
                    row['expected']=expected;row['passed']=generation.get('provider_name')==expected and generation.get('model')=='deepseek/deepseek-v4.1-flash-20261017'
                    proof.append(row)
            finally:await transport.close()
            (root/'generation-proof.json').write_text(json.dumps(proof,indent=2))
            routes=store.db.execute('SELECT provider,status,response_id FROM receipts ORDER BY started').fetchall()
            result={'passed':[row[0] for row in routes]==['primeintellect','primeintellect','morph/fp8','deepseek'] and all(row[1]=='completed' for row in routes) and all(row['passed'] for row in proof),'routes':routes,'provider_generation_proof':proof,'tool_calls':len(calls),'answer':json.loads(answer)}
    finally:
        store.close();(root/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));print('EVIDENCE',root)
    if not result.get('passed'):raise AssertionError('Strict schema and serial route acceptance failed')

if __name__=='__main__':asyncio.run(main())
