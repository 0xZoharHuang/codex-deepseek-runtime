"""Local test-only OAuth + Streamable HTTP MCP; never accepts real credentials."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import urlencode,urlsplit

from aiohttp import web


async def main(root):
    root=Path(root);events=[];clients={};codes={};base=None
    token='FIXTURE_ONLY_'+secrets.token_hex(16)
    nonce='MCP_OAUTH_'+secrets.token_hex(12)
    def record(kind,**detail):
        events.append({'kind':kind,**detail});(root/'fixture-events.json').write_text(json.dumps(events,indent=2))
    async def resource(request):
        return web.json_response({'resource':base+'/mcp','authorization_servers':[base],
                                  'scopes_supported':['audit.read'],'bearer_methods_supported':['header']})
    async def metadata(request):
        return web.json_response({'issuer':base,'authorization_endpoint':base+'/authorize','token_endpoint':base+'/token',
            'registration_endpoint':base+'/register','response_types_supported':['code'],
            'grant_types_supported':['authorization_code','refresh_token'],'code_challenge_methods_supported':['S256'],
            'token_endpoint_auth_methods_supported':['none'],'scopes_supported':['audit.read']})
    async def register(request):
        body=await request.json();client='fixture_'+secrets.token_hex(8);clients[client]=body
        record('register',redirect_uris=body.get('redirect_uris'))
        return web.json_response({**body,'client_id':client,'token_endpoint_auth_method':'none'})
    async def authorize(request):
        q=request.query;assert q['client_id'] in clients and q['code_challenge_method']=='S256'
        redirect=q['redirect_uri'];assert redirect in clients[q['client_id']]['redirect_uris']
        assert urlsplit(redirect).hostname in {'localhost','127.0.0.1','::1'}
        code=secrets.token_hex(16);codes[code]=dict(q)
        record('authorize',pkce='S256',resource=q.get('resource'),scope=q.get('scope'))
        raise web.HTTPFound(redirect+'?'+urlencode({'code':code,'state':q.get('state',''),'iss':base}))
    async def exchange(request):
        form=await request.post()
        if form['grant_type']=='authorization_code':
            q=codes.pop(form['code']);assert form['client_id']==q['client_id']
            actual=base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).decode().rstrip('=')
            assert actual==q['code_challenge'];record('token',pkce_valid=True,resource=form.get('resource'))
        else:
            assert form['grant_type']=='refresh_token' and form['refresh_token']=='FIXTURE_REFRESH';record('refresh')
        return web.json_response({'access_token':token,'token_type':'Bearer','expires_in':3600,
                                  'refresh_token':'FIXTURE_REFRESH','scope':'audit.read'})
    async def mcp(request):
        if request.headers.get('Authorization')!='Bearer '+token:
            record('unauthenticated',method=request.method)
            return web.Response(status=401,headers={'WWW-Authenticate':f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource", scope="audit.read"'})
        if request.method=='GET':return web.Response(status=405)
        if request.method=='DELETE':return web.Response(status=200)
        body=await request.json();method=body.get('method');record('mcp',method=method)
        if 'id' not in body:return web.Response(status=202)
        if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{},'resources':{}},'serverInfo':{'name':'oauth-audit-fixture','version':'1'}}
        elif method=='tools/list':result={'tools':[{'name':'read_nonce','description':'Read the OAuth protected fresh audit value.','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}}]}
        elif method=='tools/call':
            assert body['params']['name']=='read_nonce';record('tool_executed',nonce=nonce)
            result={'content':[{'type':'text','text':nonce}],'isError':False}
        elif method=='resources/list':result={'resources':[{'uri':'audit://ledger','name':'OAuth ledger','mimeType':'text/plain'}]}
        elif method=='resources/templates/list':result={'resourceTemplates':[]}
        elif method=='resources/read':result={'contents':[{'uri':'audit://ledger','mimeType':'text/plain','text':'RESOURCE_'+nonce}]}
        else:return web.json_response({'jsonrpc':'2.0','id':body['id'],'error':{'code':-32601,'message':'Unknown fixture method'}})
        return web.json_response({'jsonrpc':'2.0','id':body['id'],'result':result})
    app=web.Application();app.router.add_get('/.well-known/oauth-protected-resource',resource)
    app.router.add_get('/.well-known/oauth-authorization-server',metadata)
    app.router.add_post('/register',register);app.router.add_get('/authorize',authorize);app.router.add_post('/token',exchange)
    app.router.add_route('*','/mcp',mcp)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    base=f'http://127.0.0.1:{runner.addresses[0][1]}'
    (root/'ready.json').write_text(json.dumps({'base':base,'nonce':nonce}))
    try:await asyncio.Event().wait()
    finally:await runner.cleanup()

if __name__=='__main__':asyncio.run(main(sys.argv[1]))
