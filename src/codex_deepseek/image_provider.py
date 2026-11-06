"""Optional provider image resources; no native thread, tool or process ownership."""
from __future__ import annotations
import json,os,sqlite3,tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from .image_transport import ImageTransport,OpenRouterFilesBackend,TransportPolicy,_strict_json,_private_directory
from .platforms import private_fd,replace_file,sqlite_filename
from .protocol import MODEL

POLICIES={'default','default-fallback','system'}


def atomic_write(path: Path,data: bytes):
    path=Path(path)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink!=1):raise ValueError('Image state must be a private regular file')
    if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in path.parents):raise ValueError('Image state path cannot traverse links')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('wb',dir=path.parent,prefix='.image-',delete=False) as stream:
            temporary=Path(stream.name);private_fd(stream.fileno());stream.write(data);stream.flush();os.fsync(stream.fileno())
        replace_file(temporary,path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


def load_image_config(state_root: Path):
    path=Path(state_root)/'image-transport.json'
    if not path.exists():return None
    if path.is_symlink() or path.stat().st_nlink!=1 or path.stat().st_size>16384:raise ValueError('Invalid image transport configuration path')
    with path.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('Image transport configuration exceeds its read limit')
    return parse_image_config(_strict_json(raw))


def parse_image_config(value):
    """Validate the same non-secret policy for live and explicit offline use."""
    allowed={'schema_version','enabled','native_outbound_policy','activate_above_bytes','wire_budget_bytes','idle_ttl_seconds','verify_after_seconds','max_file_references'}
    if not isinstance(value,dict) or not set(value)<=allowed or type(value.get('schema_version')) is not int or value['schema_version']!=1 or type(value.get('enabled')) is not bool:
        raise ValueError('Invalid image transport configuration schema')
    if value['enabled'] is False:
        if set(value)!={'schema_version','enabled'}:raise ValueError('Disabled image configuration must not hide unused options')
        return None
    if not isinstance(value.get('native_outbound_policy'),str) or value['native_outbound_policy'] not in POLICIES or 'activate_above_bytes' not in value:
        raise ValueError('Enabled image transport needs explicit outbound policy and byte threshold')
    options={k:v for k,v in value.items() if k not in {'schema_version','enabled','native_outbound_policy'}}
    policy=TransportPolicy(**options)
    return {'policy':policy,'native_outbound_policy':value['native_outbound_policy']}


async def bounded_metadata(response):
    chunks=[];size=0
    while True:
        chunk=await response.content.read(65536)
        if not chunk:break
        size+=len(chunk)
        if size>1024*1024:raise ValueError('Provider metadata exceeds its budget')
        chunks.append(chunk)
    return b''.join(chunks)


class NativeFilesSession:
    """aiohttp-shaped local view; all public egress executes in native HTTP."""
    def __init__(self,native,policy):
        if policy not in POLICIES:raise ValueError('Invalid explicit native outbound policy')
        self.native,self.policy=native,policy
    def request(self,method,url,*,headers,allow_redirects=False,proxy=None,data=None,params=None):
        if proxy is not None or allow_redirects:raise ValueError('Files cannot override native proxy/TLS or redirects')
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='openrouter.ai' or not parsed.path.startswith('/api/v1/files') or parsed.fragment:
            raise ValueError('Files request changed its fixed provider endpoint')
        authorization=headers.get('Authorization','')
        if not authorization.startswith('Bearer '):raise ValueError('Missing provider credential')
        if params:url+='?'+urlencode(params)
        return self.native.request_raw(method,url,api_key=authorization[7:],body=b'' if data is None else data,
            content_type=headers.get('Content-Type'),policy=self.policy)


class ImageProvider:
    def __init__(self,state_root,native,credentials,config):
        self.root=Path(state_root)/'image-transport';_private_directory(self.root)
        self.native,self.credentials,self.config=native,credentials,config
        self.receipts=self.root/'request-receipts.sqlite'
        with closing(self._db()) as db, db:db.execute('CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,record TEXT NOT NULL)')
    def _db(self):
        if self.receipts.is_symlink() or (self.receipts.exists() and self.receipts.stat().st_nlink!=1):raise ValueError('Image receipt database cannot be linked')
        return sqlite3.connect(sqlite_filename(self.receipts),timeout=.1)
    def transport(self,policy=None):
        policy=policy or self.config['native_outbound_policy']
        return ImageTransport(self.root/'files',self.credentials,OpenRouterFilesBackend(NativeFilesSession(self.native,policy)),self.config['policy'])
    def record(self,ref,**updates):
        with closing(self._db()) as db, db:
            row=db.execute('SELECT record FROM calls WHERE id=?',(ref,)).fetchone()
            value=_strict_json(row[0]) if row else {'id':ref,'status':'running','response_id':None,'requested_model':MODEL,'requested_upstream_model':MODEL,'requested_provider':None,'adaptations':{'image_transport':{}}}
            image=value['adaptations']['image_transport']
            for key,item in updates.items():
                if key in {'status','response_id','requested_provider'}:value[key]=item
                elif key in {'preparation','consumption_status','provider_terminal','provider_response_id','release_error_type','native_outbound_policy','budget_failure','generation_header_candidate'}:image[key]=item
                else:raise ValueError('Unknown image receipt field')
            db.execute('INSERT OR REPLACE INTO calls VALUES (?,?)',(ref,json.dumps(value,allow_nan=False)))
    @staticmethod
    def read_receipt(path,ref):
        with closing(sqlite3.connect(sqlite_filename(path,read_only=True),uri=True,timeout=.1)) as db, db:
            row=db.execute('SELECT record FROM calls WHERE id=?',(ref,)).fetchone()
        return _strict_json(row[0]) if row else None
    async def generation_reader(self,receipt,credentials,**kwargs):
        # Explicit provider maintenance: same native route/TLS, one GET, no inference.
        key=credentials();identifier=receipt['response_id'];policy=self.config['native_outbound_policy']
        result={'receipt_id':receipt['id'],'response_id':identifier,'requested':{'upstream_model':MODEL,'provider':receipt['requested_provider']},'inference_requests':0,'lookup_status':'not_found','generation_lookup':{'attempts':[]},'model_identity':{'matches_requested':None},'observed':None}
        async with self.native.request('GET','https://openrouter.ai/api/v1/generation?'+urlencode({'id':identifier}),api_key=key,policy=policy) as response:
            status=response.status
            row={'http_status':status}
            if status!=200:
                advice=response.headers.get('Retry-After');row['error']={'headers':{'retry-after':advice} if advice and len(advice)<=128 and key not in advice else {}}
                result['generation_lookup']['attempts'].append(row);result['lookup_status']='not_found' if status==404 else 'http_error';return result
            raw=await bounded_metadata(response)
            if len(raw)>1024*1024:raise ValueError('Generation metadata exceeds its budget')
            data=_strict_json(raw).get('data',{})
            if data.get('id')!=identifier:raise ValueError('Generation identity mismatch')
            result['generation_lookup']['attempts'].append(row)
            names=('id','model','provider_name','cancelled','finish_reason','native_finish_reason')
            result['observed']={name:data.get(name) for name in names}
            if any(v is not None and type(v) not in {str,bool,int,float} for v in result['observed'].values()):raise ValueError('Invalid generation metadata')
            result['lookup_status']='found'
        if result['observed']['model']==MODEL:
            result['model_identity']={'matches_requested':True,'status':'exact_id'};return result
        import hashlib
        async with self.native.request('GET','https://openrouter.ai/api/v1/model/'+MODEL,api_key=key,policy=policy) as response:
            status=response.status;proof={'http_status':status}
            if status==200:
                raw=await bounded_metadata(response)
                if len(raw)>1024*1024:raise ValueError('Model metadata exceeds its budget')
                data=_strict_json(raw).get('data',{});metadata={name:data.get(name) for name in ('id','canonical_slug')}
                if not all(isinstance(v,str) for v in metadata.values()):raise ValueError('Invalid model metadata')
                proof.update(metadata=metadata,raw_sha256=hashlib.sha256(raw).hexdigest())
                matched=metadata['id']==MODEL and metadata['canonical_slug']==result['observed']['model']
                result['model_identity']={'matches_requested':matched,'status':'verified_canonical_alias' if matched else 'model_mismatch','proof':proof}
            else:
                advice=response.headers.get('Retry-After');proof['error']={'headers':{'retry-after':advice} if advice and len(advice)<=128 and key not in advice else {}}
                result['model_identity']={'matches_requested':None,'status':'metadata_unavailable','proof':proof}
        return result


def maintenance_exit_code(result):
    if 'status' in result:return 0 if result['status']=='complete' else 1
    return 0 if result.get('complete') is True else 1


def _daemon_identity(record):
    """Decode existing native PID metadata without treating unknown as exit."""
    import re
    import sys
    if not isinstance(record, dict) or type(record.get('pid')) is not int or not 0 < record['pid'] <= 0xffffffff:
        raise ValueError('Invalid native daemon PID identity')
    def integer(value):
        if type(value) is int and 0 <= value <= 0xffffffffffffffff:
            return str(value)
        if isinstance(value, str) and re.fullmatch(r'[0-9]{1,32}', value) and int(value) <= 0xffffffffffffffff:
            return str(int(value))
        raise ValueError('Invalid native daemon start identity')
    if os.name == 'nt':
        birth = 'windows:' + integer(record['processStartTime'])
    else:
        detail = record['processIdentity']
        if not isinstance(detail, dict):
            raise ValueError('Invalid native daemon identity')
        if sys.platform == 'linux':
            boot = detail['bootId']
            if not isinstance(boot, str) or re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', boot) is None:
                raise ValueError('Invalid native daemon boot identity')
            birth = 'linux:' + boot.lower() + ':' + integer(detail['startTicks'])
        else:
            micros = integer(detail['startMicroseconds'])
            if int(micros) >= 1000000:
                raise ValueError('Invalid native daemon start identity')
            birth = integer(detail['startSeconds']) + ':' + micros
    return record['pid'], birth


async def maintenance_command(state_root,native,credentials,operation,*,all_unpinned=False,target_version=None):
    if operation == 'migrate-format':
        # Explicit offline maintenance reuses the provider's existing startup
        # serialization and lifecycle evidence. It never launches native HTTP,
        # reads a provider credential, or stops a process.
        from .provider_config import locked, _service_lease_held
        from .service import native_active, read_descriptor
        from .image_transport import _owner_alive, _private_file_stat, _open_private_file
        state_root = Path(state_root).absolute()
        home = state_root.parent
        with locked(home, create=True) as state:
            if state != state_root:
                raise ValueError('Format maintenance requires this profile provider-state directory')
            # Validate leaves before existing lifecycle readers can open them.
            for metadata in (state / 'adapter.sqlite', state / 'gateway.json',
                             state / 'gateway.last-good.json',
                             home / 'app-server-daemon/daemon.pid',
                             home / 'app-server-daemon/app-server.pid'):
                _private_file_stat(metadata, missing=True)
            for name in ('daemon.pid', 'app-server.pid'):
                path = home / 'app-server-daemon' / name
                if _private_file_stat(path, missing=True) is None:
                    continue
                try:
                    with _open_private_file(path, os.O_RDONLY, 'rb') as source:
                        raw = source.read(16385)
                    if len(raw) > 16384:
                        raise ValueError('Native daemon identity exceeds its metadata bound')
                    pid, birth = _daemon_identity(_strict_json(raw))
                except (OSError, KeyError, TypeError, ValueError):
                    raise RuntimeError('Native daemon ownership metadata is unreadable; retirement is not proven') from None
                if _owner_alive(pid, birth) is not False:
                    raise RuntimeError('Native daemon retirement is not proven; format maintenance refused')
            if native_active(home) or _service_lease_held(state):
                raise RuntimeError('Drain and stop this profile native/provider owners before image cache format maintenance')
            descriptor = read_descriptor(home)
            descriptor_files = [state / 'gateway.json', state / 'gateway.last-good.json']
            if not descriptor and any(path.exists() for path in descriptor_files):
                raise RuntimeError('Provider ownership metadata is unreadable; preserve it for lifecycle reconciliation')
            if not descriptor:
                raise RuntimeError('Provider retirement is unproven for this unregistered profile; use its owning controller lifecycle evidence')
            for pid_key, birth_key in (('pid', 'birth'), ('worker_pid', 'worker_birth')):
                pid, birth = descriptor.get(pid_key), descriptor.get(birth_key)
                if pid is None and birth is None:
                    continue
                if (type(pid) is not int or pid <= 0 or not isinstance(birth, str)
                        or not birth or _owner_alive(pid, birth) is not False):
                    raise RuntimeError('Provider process retirement is not proven; format maintenance refused')
            runtime_db = state / 'adapter.sqlite'
            if _private_file_stat(runtime_db, missing=True) is not None:
                with closing(sqlite3.connect(sqlite_filename(runtime_db, read_only=True), uri=True)) as db:
                    for pid, birth, native_pid, native_birth in db.execute(
                            'SELECT pid,birth,native_pid,native_birth FROM runtime_owners WHERE closed IS NULL'):
                        for candidate, identity in ((pid, birth), (native_pid, native_birth)):
                            if candidate is None and identity is None:
                                continue
                            if (type(candidate) is not int or candidate <= 0 or identity is None
                                    or _owner_alive(candidate, identity) is not False):
                                raise RuntimeError('Native/provider client retirement is not proven; format maintenance refused')
            def forbidden():
                raise AssertionError('Offline image format maintenance must not read a credential')
            transport = ImageTransport(state / 'image-transport/files', forbidden, None,
                                       TransportPolicy(activate_above_bytes=1))
            result = await transport.migrate_cache_format(target_version)
            result['managed_provider_retirement_verified_here'] = True
            return result, maintenance_exit_code(result)
    config=load_image_config(state_root)
    if config is None:return {'status':'disabled','complete':False,'enabled':False},(0 if operation=='status' else 1)
    if operation!='status':await native.require_raw()
    provider=ImageProvider(state_root,native,credentials,config);transport=provider.transport()
    if operation=='status':return await transport.diagnostics(),0
    if operation=='cleanup':result=await transport.cleanup(all_unpinned=all_unpinned)
    elif operation=='reconcile-uploads':result=await transport.reconcile_unknown_uploads()
    elif operation=='reconcile-calls':
        from .image_call_reconciliation import reconcile_image_calls
        result=await reconcile_image_calls(transport,provider.receipts,provider.root/'call-reconciliation.json',credentials,
            generation_reader=provider.generation_reader,receipt_reader=provider.read_receipt)
    else:raise ValueError('Unknown image maintenance operation')
    return result,maintenance_exit_code(result)
