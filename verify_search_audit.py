"""Live Exa constraints and cited provenance, using the official DeepSeek route."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import uuid
import aiohttp

from codex_deepseek.cli import catalog,credential
from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store
from codex_deepseek.native_http import NativeTransport


async def main():
    root=Path(__file__).resolve().parent.parent/'delivery/independent-audit/search'/str(uuid.uuid4())
    root.mkdir(parents=True,mode=0o700);os.environ['CODEX_DEEPSEEK_TRACE_DIR']=str(root/'traces')
    helper=Path(os.environ['CODEX_DEEPSEEK_TEST_HTTP_BINARY']) if os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY') else None
    model_catalog=json.loads(Path(os.environ['CODEX_DEEPSEEK_TEST_CATALOG']).read_text()) if os.environ.get('CODEX_DEEPSEEK_TEST_CATALOG') else catalog()
    store=Store(root/'state.sqlite');result={}
    try:
        async with Gateway(credential,uuid.uuid4().hex,store,model_catalog,native_http_binary=helper) as gateway,aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as client:
            query='Stack Overflow Python asyncio TaskGroup cancellation'
            body={'model':MODEL,'input':[{'role':'user','content':'Use web search exactly once with the query '+repr(query)+'. Do not rewrite or add site operators to this query. Return real result URLs and a brief factual summary. Do not perform a second search.'}],
                  'tools':[{'type':'web_search','external_web_access':True,'search_context_size':'low','filters':{'allowed_domains':['docs.python.org']}}],
                  'max_tool_calls':1,'max_output_tokens':2048,'reasoning':{'effort':'low'},'store':False}
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',headers={'Authorization':'Bearer '+gateway.token},json=body) as response:
                reply=await response.json();assert response.status==200,reply
            (root/'response.json').write_text(json.dumps(reply,indent=2,ensure_ascii=False))
            searches=[json.loads(row[0]) for row in store.db.execute('SELECT item FROM searches').fetchall()]
            (root/'searches.json').write_text(json.dumps(searches,indent=2,ensure_ascii=False))
            citations=[annotation for item in reply['output'] if item['type']=='message' for part in item.get('content',[]) for annotation in part.get('annotations',[]) if annotation.get('type')=='url_citation']
            urls=[item.get('url') or item.get('url_citation',{}).get('url') for item in citations]
            sources=[source['url'] for search in searches for source in search.get('action',{}).get('sources',[])]
            queries=[search.get('action',{}).get('query') for search in searches]
            result={'passed':queries==[query] and bool(sources) and bool(urls) and all(url and urlsplit(url).hostname=='docs.python.org' for url in urls+sources),
                    'response_id':reply['id'],'status':reply['status'],'search_count':len(searches),'actual_queries':queries,'search_source_urls':sources,'citation_urls':urls,'usage':reply.get('usage')}
            assert result['passed'],result
    finally:
        store.close();(root/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False));print(json.dumps(result,ensure_ascii=False));print('EVIDENCE',root)


if __name__=='__main__':asyncio.run(main())
