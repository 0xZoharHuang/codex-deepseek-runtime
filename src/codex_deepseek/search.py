"""Explicit native hosted-search to OpenRouter Exa search capability mapping."""
from .protocol_errors import ProtocolError


def tool(spec):
    known={'type','external_web_access','indexed_web_access','filters','user_location','search_context_size','search_content_types'}
    if set(spec)-known:raise ProtocolError('Unrecognized native web-search options cannot be silently discarded')
    if spec.get('external_web_access') is False:
        raise ProtocolError('OpenAI cached web-search index is unavailable on the Exa backend; select web_search="live" explicitly')
    if spec.get('indexed_web_access'):
        raise ProtocolError('OpenAI indexed web-search mode is unavailable on the Exa backend; select web_search="live" explicitly')
    if spec.get('user_location'):
        raise ProtocolError('Exa does not honor native user_location search bias; refusing to silently ignore it')
    if spec.get('search_content_types') not in (None,[],['text']):
        raise ProtocolError('The Exa backend does not provide the native image-search content contract')
    parameters={'engine':'exa'}
    if spec.get('search_context_size'):parameters['search_context_size']=spec['search_context_size']
    filters=spec.get('filters') or {}
    if not isinstance(filters,dict) or set(filters)-{'allowed_domains','excluded_domains'}:
        raise ProtocolError('Unsupported native search filter')
    for name,value in filters.items():
        if value is not None:parameters[name]=value
    return {'type':'openrouter:web_search','parameters':parameters}
