"""Install only the distribution-owned browser MCP stanza in the isolated home."""
from .platforms import file_lock, private_fd, replace_file
import os
from pathlib import Path
import re
import sys
import tempfile
import tomlkit
from .policy import isolated_path


def ensure_browser(home: Path):
    home=isolated_path(home)
    with (home/'browser-config.lock').open('a') as lock, file_lock(lock):
        path=home/'config.toml'
        if path.is_symlink():raise RuntimeError('Isolated browser configuration cannot follow a symlink')
        if path.exists():
            # Universal-newline decoding would silently rewrite user lines on
            # another platform whenever the bundled command changes.
            with path.open('r',encoding='utf-8',newline='') as source:old=source.read()
        else:old=''
        config=tomlkit.parse(old)
        servers=config.setdefault('mcp_servers',tomlkit.table())
        if 'codex_web' in servers:
            server=servers['codex_web']
            disabled_builtin=server.get('enabled') is False and set(server)<= {'enabled'}
            if not disabled_builtin and 'codex_deepseek.web_mcp' not in server.get('args',[]):
                raise RuntimeError('mcp_servers.codex_web is already user-owned; rename it before installing the bundled browser')
        else:
            server=tomlkit.table();servers['codex_web']=server
            server['startup_timeout_sec']=30
            server['tool_timeout_sec']=60
            server['default_tools_approval_mode']='approve'
        server['command']=sys.executable
        server['args']=['-I','-X','utf8','-m','codex_deepseek.web_mcp','--home',str(home)]
        content=tomlkit.dumps(config)
        # tomlkit retains existing CRLF but inserts new table lines with LF.
        # Keep an entirely CRLF input consistent without normalizing a mixed file.
        if '\r\n' in old and '\n' not in old.replace('\r\n',''):
            content=re.sub(r'(?<!\r)\n','\r\n',content)
        if content==old:return
        temporary=None
        try:
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',newline='',dir=home,prefix='.browser-config-',delete=False) as f:
                temporary=Path(f.name)
                private_fd(f.fileno());f.write(content);f.flush()
            replace_file(temporary,path)
        finally:
            if temporary is not None and temporary.exists():temporary.unlink()
