"""Browser navigation must come from real fetched documents and isolated state."""
import asyncio
from pathlib import Path
import sys
import tomllib
import pytest
from aiohttp import web
from codex_deepseek.browser import Browser,tunnel_fake_ip
from codex_deepseek.web_config import ensure_browser


@pytest.fixture
async def website():
    hits=[]
    async def page(request):
        hits.append(request.path)
        if request.path=='/redirect':raise web.HTTPFound('/next')
        if request.path=='/private':raise web.HTTPFound('http://127.0.0.2/not-allowed')
        if request.path=='/missing':raise web.HTTPNotFound()
        return web.Response(text='<html><title>Actual document</title><script>HIDDEN_SECRET</script><body><p>'+('x'*115)+' literal phrase across wrap</p><p>FETCHED_'+request.path+'</p><a href="/next">Read next</a></body></html>',content_type='text/html')
    app=web.Application();app.router.add_get('/{path:.*}',page)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    try:yield 'http://127.0.0.1:'+str(runner.addresses[0][1]),hits
    finally:await runner.cleanup()


async def test_fetched_page_literal_find_link_effect_and_cold_references(tmp_path,website):
    url,hits=website;b=Browser(tmp_path,allowed_private_hosts=['127.0.0.1'])
    try:
        page=await b.open(url+'/first');ref=page['ref_id']
        assert page['source_sha256'] and 'FETCHED_/first' in page['content'] and 'HIDDEN_SECRET' not in page['content']
        match=b.find(ref,'literal phrase across wrap',context_lines=0)
        assert match['match_count']==1 and match['matches'][0]['end_line']>=match['matches'][0]['line']
        assert page['links'][0]['url']==url+'/next'
        next_page=await b.click(ref,1)
        assert 'FETCHED_/next' in next_page['content'] and next_page['followed_from']['ref_id']==ref
        assert hits==['/first','/next']
    finally:await b.close()
    b=Browser(tmp_path)
    try:
        assert (await b.open(ref))['source_sha256']==page['source_sha256']
        assert b.find(ref,'fetched_/first')['match_count']==1
        assert hits==['/first','/next']
        with pytest.raises(ValueError,match='Link ID'):await b.click(ref,999)
    finally:await b.close()


async def test_private_address_redirect_errors_and_bounded_response(tmp_path,website,monkeypatch):
    url,hits=website;b=Browser(tmp_path)
    try:
        with pytest.raises(ValueError,match='private'):await b.open(url+'/first')
        assert hits==[]
        for bad in ['file:///etc/passwd','http://user:password@example.com','ftp://example.com/x']:
            with pytest.raises(ValueError,match='HTTP'):await b.open(bad)
    finally:await b.close()
    b=Browser(tmp_path,allowed_private_hosts=['127.0.0.1'])
    try:
        with pytest.raises(ValueError,match='private'):await b.open(url+'/private')
        page=await b.open(url+'/redirect');assert page['url']==url+'/next'
        with pytest.raises(Exception,match='404'):await b.open(url+'/missing')
        monkeypatch.setattr('codex_deepseek.browser.MAX_BODY',20)
        with pytest.raises(ValueError,match='download limit'):await b.open(url+'/oversize')
        assert b.db.execute('SELECT COUNT(*) FROM pages').fetchone()[0]==1
    finally:await b.close()


def test_managed_browser_config_preserves_user_servers_comments_and_disablement(tmp_path,monkeypatch):
    p=tmp_path/'config.toml';p.write_text('# keep me\nmodel_reasoning_effort="high"\n[mcp_servers.user_tool]\ncommand="own-tool"\n')
    ensure_browser(tmp_path)
    assert '# keep me' in p.read_text();a=tomllib.loads(p.read_text())
    assert a['mcp_servers']['user_tool']['command']=='own-tool'
    assert a['mcp_servers']['codex_web']['command']==sys.executable
    p.write_text(p.read_text().replace('[mcp_servers.codex_web]','[mcp_servers.codex_web]\nenabled=false'))
    monkeypatch.setattr(sys,'executable','/new/installed/python')
    ensure_browser(tmp_path);a=tomllib.loads(p.read_text())
    assert a['mcp_servers']['codex_web']['enabled'] is False
    assert a['mcp_servers']['codex_web']['command']=='/new/installed/python'


def test_browser_config_collision_and_app_overlap_are_explicit(tmp_path):
    p=tmp_path/'config.toml';p.write_text('[mcp_servers.codex_web]\ncommand="user-owned"\n')
    with pytest.raises(RuntimeError,match='user-owned'):ensure_browser(tmp_path)
    assert p.read_text()=='[mcp_servers.codex_web]\ncommand="user-owned"\n'
    with pytest.raises(ValueError,match='Codex App'):Browser(Path.home()/'.codex/not-allowed')


def test_minimal_native_config_can_disable_bundled_browser(tmp_path):
    p=tmp_path/'config.toml';p.write_text('[mcp_servers.codex_web]\nenabled=false\n')
    ensure_browser(tmp_path)
    config=tomllib.loads(p.read_text())['mcp_servers']['codex_web']
    assert config['enabled'] is False and 'codex_deepseek.web_mcp' in config['args']


async def test_tunnel_dns_exception_never_allows_literal_or_other_private_ranges():
    assert await tunnel_fake_ip('198.18.0.1','198.18.0.1') is None
    assert await tunnel_fake_ip('private.test','127.0.0.1') is None
    assert await tunnel_fake_ip('private.test','169.254.169.254') is None
    assert await tunnel_fake_ip('private.test','10.0.0.1') is None


async def test_html_base_links_literal_find_bounds_and_cache_eviction(tmp_path,monkeypatch):
    b=Browser(tmp_path)
    try:
        page=Browser.extract('https://example.com/start',b'<head><base href="/docs/"></head><p>'+b'a'*5000+b'</p><a href="next.html">Next</a>','text/html','utf-8')
        b.save(page)
        assert page['links'][0]['url']=='https://example.com/docs/next.html'
        match=b.find(page['ref_id'],'a',context_lines=0)
        assert match['match_count']==5000 and len(match['matches'])==50 and match['matches_truncated']
        second=Browser.extract('https://example.com/two',b'<p>small</p>','text/html','utf-8')
        import json
        monkeypatch.setattr('codex_deepseek.browser.CACHE_BUDGET',len(json.dumps(second,ensure_ascii=False).encode())+20)
        b.save(second)
        with pytest.raises(ValueError,match='evicted'):b.page(page['ref_id'])
        assert b.page(second['ref_id'])['url']=='https://example.com/two'
    finally:await b.close()
