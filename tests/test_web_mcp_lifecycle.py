"""Focused lazy MCP startup/first-use behavior without model or network requests."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import aiohttp
from pypdf.errors import PyPdfError
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from codex_deepseek import web_mcp


def test_handshake_lifespan_and_discovery_do_not_import_browser_or_parser_dependencies(tmp_path):
    source=Path(web_mcp.__file__).resolve().parents[1]
    program=r"""
import asyncio,importlib.abc,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
class NoToolDependencies(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='codex_deepseek.browser' or fullname.split('.')[0] in {'aiohttp','pypdf'}:
   raise AssertionError('Tool dependency imported before invocation: '+fullname)
sys.meta_path.insert(0,NoToolDependencies())
from codex_deepseek.web_mcp import server
home=Path(sys.argv[2]);mcp=server(home)
async def check():
 async with mcp.settings.lifespan(mcp):
  tools=await mcp.list_tools()
  assert {tool.name for tool in tools}=={'open','find','click'}
asyncio.run(check())
assert not home.exists()
print(json.dumps({'no_tool_imports':True,'no_cache_created':True}))
"""
    completed=subprocess.run([sys.executable,'-I','-B','-c',program,str(source),str(tmp_path/'unused-home')],
                             capture_output=True,text=True,timeout=30)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)=={'no_tool_imports':True,'no_cache_created':True}


@pytest.mark.asyncio
async def test_concurrent_first_tools_share_one_browser_and_lifespan_closes_it(monkeypatch,tmp_path):
    made=[];closed=[];both_entered=asyncio.Event()
    class Browser:
        def __init__(self,home,*,allowed_private_hosts):
            made.append((self,home,allowed_private_hosts));self.calls=0
        async def open(self):
            self.calls+=1
            if self.calls==2:both_entered.set()
            await asyncio.wait_for(both_entered.wait(),1)
            return id(self)
        async def close(self):closed.append(self)
    fake=ModuleType('codex_deepseek.browser');fake.Browser=Browser
    monkeypatch.setitem(sys.modules,'codex_deepseek.browser',fake)
    mcp=web_mcp.server(tmp_path,('fixture.local',))
    async with mcp.settings.lifespan(mcp) as lazy:
        assert made==[]
        async def invoke():return await lazy.get().open()
        results=await asyncio.gather(invoke(),invoke())
        assert len(made)==1 and results==[id(made[0][0])]*2
        assert made[0][1:]==(tmp_path,('fixture.local',)) and closed==[]
    assert closed==[made[0][0]]


@pytest.mark.parametrize('error',[ValueError('bad URL'),OSError('read failed'),TimeoutError('timed out'),
                                  aiohttp.ClientError('HTTP failed'),PyPdfError('PDF failed')])
@pytest.mark.asyncio
async def test_first_tool_retains_readable_document_errors(error):
    @web_mcp.readable_errors
    async def invoke():raise error
    with pytest.raises(ToolError,match='Document operation failed:') as caught:
        await invoke()
    assert caught.value.__cause__ is error and str(error) in str(caught.value)


@pytest.mark.asyncio
async def test_unknown_tool_error_is_not_swallowed():
    original=RuntimeError('unknown implementation failure')
    @web_mcp.readable_errors
    async def invoke():raise original
    with pytest.raises(RuntimeError) as caught:await invoke()
    assert caught.value is original
