"""Read public web documents with durable references, exact find and link navigation."""
import asyncio
import bisect
import hashlib
import io
import ipaddress
import json
import os
import re
from pathlib import Path
import socket
import ssl
import sqlite3
import time
from urllib.parse import urljoin,urlsplit,urldefrag
from urllib.request import getproxies,proxy_bypass
import uuid
import aiohttp
from bs4 import BeautifulSoup
from pypdf import PdfReader
import truststore
from .policy import isolated_path

MAX_BODY=10*1024*1024
MAX_TEXT=4*1024*1024
CACHE_BUDGET=64*1024*1024


def public_address(value):
    address=ipaddress.ip_address(value.split('%',1)[0])
    if getattr(address,'ipv4_mapped',None):address=address.ipv4_mapped
    return address.is_global


async def tunnel_fake_ip(host,value):
    """Honor macOS VPN Fake-IP DNS only when the kernel routes it through a TUN."""
    address=ipaddress.ip_address(value.split('%',1)[0])
    try:ipaddress.ip_address(host);return None
    except ValueError:pass
    if address not in ipaddress.ip_network('198.18.0.0/15') or not Path('/sbin/route').exists():return None
    process=await asyncio.create_subprocess_exec('/sbin/route','-n','get',str(address),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        output,_=await asyncio.wait_for(process.communicate(),2)
    finally:
        if process.returncode is None:process.kill();await process.wait()
    match=re.search(rb'^\s*interface:\s*((?:utun|tun)\d+)\s*$',output,re.MULTILINE)
    return match.group(1).decode() if process.returncode==0 and match else None


class PublicResolver(aiohttp.resolver.DefaultResolver):
    def __init__(self,allowed=()):
        super().__init__();self.allowed=set(allowed);self.evidence={}

    async def validate(self,host,values):
        if host in self.allowed:return
        for address in values:
            if public_address(address):continue
            interface=await tunnel_fake_ip(host,address)
            if not interface:raise ValueError('Web browser refuses private, loopback or reserved network addresses')
            self.evidence[host]={'mode':'system_tunnel_fake_ip','interface':interface,'address':address}

    async def resolve(self,host,port=0,family=socket.AF_INET):
        rows=await super().resolve(host,port,family)
        await self.validate(host,[r['host'] for r in rows])
        return rows


class Browser:
    def __init__(self,home: Path,*,allowed_private_hosts=()):
        self.home=isolated_path(home)
        directory=isolated_path(self.home/'web-cache')
        if not directory.is_relative_to(self.home):raise ValueError('Browser cache must remain inside its isolated home')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=directory/'pages.sqlite'
        if self.path.is_symlink():raise ValueError('Browser cache cannot follow a symlink')
        self.db=sqlite3.connect(self.path,timeout=30)
        os.chmod(self.path,0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY, payload TEXT, bytes INTEGER, created REAL)')
        self.db.commit();self.allowed=set(allowed_private_hosts);self.client=None;self.resolver=None

    async def close(self):
        if self.client:await self.client.close()
        self.db.close()

    async def validate_url(self,url):
        parsed=urlsplit(url)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Only absolute HTTP(S) URLs without embedded credentials are supported')
        if parsed.hostname in self.allowed:return
        rows=await asyncio.get_running_loop().getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)
        if not rows:raise ValueError('Web hostname did not resolve')
        await self.resolver.validate(parsed.hostname,[row[4][0] for row in rows])

    async def fetch(self,url):
        if self.client is None:
            self.resolver=PublicResolver(self.allowed)
            context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            if os.environ.get('CODEX_CA_CERTIFICATE'):context.load_verify_locations(os.environ['CODEX_CA_CERTIFICATE'])
            self.client=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45),
                connector=aiohttp.TCPConnector(resolver=self.resolver,ssl=context),
                cookie_jar=aiohttp.DummyCookieJar(),trust_env=False,
                headers={'User-Agent':'codex-deepseek-runtime/0.4 (document reader)'})
        for _ in range(10):
            await self.validate_url(url)
            parsed=urlsplit(url)
            proxy=None if proxy_bypass(parsed.hostname) else getproxies().get(parsed.scheme)
            async with self.client.get(urldefrag(url)[0],allow_redirects=False,proxy=proxy) as response:
                if response.status in {301,302,303,307,308}:
                    if not response.headers.get('Location'):raise ValueError('Redirect lacks a Location')
                    url=urljoin(url,response.headers['Location']);continue
                response.raise_for_status();body=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body)>MAX_BODY:raise ValueError('Page exceeds the 10 MiB download limit; no partial page was saved')
                return str(response.url),bytes(body),response.content_type,response.charset or 'utf-8'
        raise ValueError('Too many redirects')

    @staticmethod
    def extract(url,body,content_type,encoding):
        links=[]
        if content_type in {'text/html','application/xhtml+xml'}:
            soup=BeautifulSoup(body,'html.parser')
            title=soup.title.get_text(' ',strip=True) if soup.title else url
            base=soup.find('base',href=True)
            link_base=urljoin(url,base['href']) if base else url
            for tag in soup(['script','style','noscript','template','svg','head']):tag.decompose()
            for tag in soup.find_all('a',href=True):
                target=urljoin(link_base,tag['href'])
                if urlsplit(target).scheme not in {'http','https'}:continue
                label=tag.get_text(' ',strip=True) or target
                links.append({'id':len(links)+1,'text':label,'url':target})
                tag.replace_with(label+' ['+str(len(links))+']')
            raw=soup.get_text('\n',strip=True)
        elif content_type=='application/pdf' or body.startswith(b'%PDF-'):
            reader=PdfReader(io.BytesIO(body))
            if reader.is_encrypted:raise ValueError('Encrypted PDF is not supported')
            if len(reader.pages)>200:raise ValueError('PDF exceeds the 200-page limit')
            raw='\n'.join((page.extract_text() or '') for page in reader.pages);title=url
        elif content_type.startswith('text/') or content_type in {'application/json','application/xml'}:
            raw=body.decode(encoding,errors='replace');title=url
        else:raise ValueError('Unsupported document content type: '+content_type)
        if len(raw.encode())>MAX_TEXT:raise ValueError('Extracted text exceeds the 4 MiB page limit')
        lines=[];offsets=[];offset=0
        for line in raw.splitlines(keepends=True):
            visible=line.rstrip('\r\n')
            for start in range(0,max(1,len(visible)),120):
                lines.append(visible[start:start+120]);offsets.append(offset+start)
            offset+=len(line)
        page={'ref_id':'page_'+uuid.uuid4().hex,'url':url,'title':title,'lines':lines,'links':links,
              'text':raw,'line_offsets':offsets,
              'content_type':content_type,'retrieved_at':time.time(),'source_sha256':hashlib.sha256(body).hexdigest(),
              'javascript_executed':False,'untrusted_content':True}
        return page

    def save(self,page):
        payload=json.dumps(page,ensure_ascii=False)
        with self.db:
            self.db.execute('INSERT INTO pages VALUES(?,?,?,?)',(page['ref_id'],payload,len(payload.encode()),page['retrieved_at']))
            total=self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM pages').fetchone()[0]
            for old,size in self.db.execute('SELECT id,bytes FROM pages ORDER BY created').fetchall():
                if total<=CACHE_BUDGET:break
                self.db.execute('DELETE FROM pages WHERE id=?',(old,));total-=size
        return page

    def page(self,ref_id):
        row=self.db.execute('SELECT payload FROM pages WHERE id=?',(ref_id,)).fetchone()
        if row is None:raise ValueError('Unknown or evicted page reference; open its URL again')
        return json.loads(row[0])

    @staticmethod
    def view(page,start_line=0,num_lines=100):
        if not 0<=start_line or not 1<=num_lines<=500:raise ValueError('Use start_line>=0 and 1<=num_lines<=500')
        lines=page['lines'];result={k:v for k,v in page.items() if k not in {'lines','links','text','line_offsets'}}
        result.update(total_lines=len(lines),start_line=start_line,
            content='\n'.join(f'L{i}: {line}' for i,line in enumerate(lines[start_line:start_line+num_lines],start_line)),
            links=page['links'][:100],total_links=len(page['links']),links_truncated=len(page['links'])>100,
            content_truncated=start_line+num_lines<len(lines))
        return result

    async def open(self,url_or_ref,start_line=0,num_lines=100):
        if url_or_ref.startswith('page_'):page=self.page(url_or_ref)
        else:
            url,body,kind,encoding=await self.fetch(url_or_ref)
            page=await asyncio.to_thread(self.extract,url,body,kind,encoding)
            page['dns_resolution']=self.resolver.evidence.get(urlsplit(url).hostname,{'mode':'system_dns'})
            page=self.save(page)
        return self.view(page,start_line,num_lines)

    def find(self,ref_id,pattern,case_sensitive=False,context_lines=2):
        if not pattern or not 0<=context_lines<=20:raise ValueError('A nonempty pattern and 0<=context_lines<=20 are required')
        page=self.page(ref_id)
        spans=[];count=0
        for match in re.finditer(re.escape(pattern),page['text'],0 if case_sensitive else re.IGNORECASE):
            count+=1
            if len(spans)<50:spans.append(match.span())
        matches=[(max(0,bisect.bisect_right(page['line_offsets'],start)-1),max(0,bisect.bisect_right(page['line_offsets'],end-1)-1)) for start,end in spans]
        return {'ref_id':ref_id,'url':page['url'],'pattern':pattern,'match_count':count,'matches_truncated':count>50,
                'matches':[{'line':i,'end_line':end,'content':'\n'.join(f'L{j}: {page["lines"][j]}' for j in range(max(0,i-context_lines),min(len(page['lines']),end+context_lines+1)))} for i,end in matches[:50]],'untrusted_content':True}

    async def click(self,ref_id,link_id):
        page=self.page(ref_id)
        matches=[link for link in page['links'] if link['id']==link_id]
        if not matches:raise ValueError('Link ID is not present in this page')
        result=await self.open(matches[0]['url']);result['followed_from']={'ref_id':ref_id,'link_id':link_id}
        return result
