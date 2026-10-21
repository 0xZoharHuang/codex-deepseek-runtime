"""Keep the dedicated provider and credential boundary across native config surfaces."""
from pathlib import Path
from .platforms import paths_overlap
import tomllib

from .protocol import MODEL, ProtocolError

PROVIDER_ID='deepseek_runtime'
TRANSPORT_SETTINGS={'request_max_retries','stream_max_retries','stream_idle_timeout_ms','websocket_connect_timeout_ms'}

def provider_identity(value):
    return {key:part for key,part in value.items() if key not in TRANSPORT_SETTINGS} if isinstance(value,dict) else value

def isolated_path(path: Path) -> Path:
    value=path.expanduser().resolve()
    protected=Path.home()/'.codex'
    protected=protected.resolve()
    if paths_overlap(value,protected):
        raise ProtocolError('The isolated runtime home/trace must not overlap Codex App state (~/.codex)')
    return value


def bindings(provider: dict, catalog: Path, home=None) -> dict:
    result={'model':MODEL,'model_provider':PROVIDER_ID,'model_catalog_json':str(catalog),
            'model_providers.deepseek_runtime':provider,
            'memories.extract_model':MODEL,'memories.consolidation_model':MODEL}
    if home:result.update(sqlite_home=str(home),log_dir=str(home/'log'))
    return result


def check_value(key,value,expected):
    for protected,wanted in expected.items():
        if protected=='model_providers.deepseek_runtime':
            if key.startswith(protected+'.') and key[len(protected)+1:] in TRANSPORT_SETTINGS:continue
            wanted=provider_identity(wanted)
            if key==protected:value=provider_identity(value)
        if key==protected:
            if value!=wanted:raise ProtocolError(f'Isolated runtime binding {protected} cannot be overridden')
        elif key.startswith(protected+'.'):
            target=wanted
            for part in key[len(protected)+1:].split('.'):
                if not isinstance(target,dict) or part not in target:
                    raise ProtocolError(f'Isolated runtime binding {protected} cannot be extended')
                target=target[part]
            if value!=target:raise ProtocolError(f'Isolated runtime binding {key} cannot be overridden')
        elif protected.startswith(key+'.'):
            target=value
            for part in protected[len(key)+1:].split('.'):
                if not isinstance(target,dict) or part not in target:
                    raise ProtocolError(f'Config assignment {key} would replace isolated binding {protected}')
                target=target[part]
            if target!=wanted:raise ProtocolError(f'Config assignment {key} changes isolated binding {protected}')


def config_value(raw):
    try:return tomllib.loads('value='+raw)['value']
    except tomllib.TOMLDecodeError:return raw


def check_cli(arguments,expected):
    from .arguments import command_index
    owner=command_index(arguments)
    if owner is not None and arguments[owner]=='app':
        raise ProtocolError('This isolated runtime entrypoint does not launch or modify the Codex App')
    index=0
    while index<len(arguments):
        token=arguments[index]
        if token=='--':break
        if token in {'--oss','--local-provider'} or token.startswith('--local-provider='):
            raise ProtocolError('This dedicated entrypoint pins DeepSeek; selecting another local model provider is incompatible with that binding')
        assignment=None;model=None
        if token in {'-c','--config','-m','--model'} and index+1<len(arguments):
            index+=1
            if token in {'-c','--config'}:assignment=arguments[index]
            else:model=arguments[index]
        elif token.startswith('--config='):assignment=token.split('=',1)[1]
        elif token.startswith('-c') and not token.startswith('--') and len(token)>2:assignment=token[2:]
        elif token.startswith('--model='):model=token.split('=',1)[1]
        elif token.startswith('-m') and not token.startswith('--') and len(token)>2:model=token[2:]
        if assignment and '=' in assignment:
            key,value=assignment.split('=',1);check_value(key.strip(),config_value(value),expected)
        if model is not None and model!=MODEL:raise ProtocolError('This dedicated entrypoint uses the pinned DeepSeek model')
        index+=1


