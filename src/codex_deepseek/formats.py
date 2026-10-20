"""Retain and validate native freeform formats at the provider boundary."""
from functools import lru_cache
import json
import re
from lark import Lark
from lark.exceptions import LarkError

from .protocol_errors import ProtocolError


@lru_cache(maxsize=64)
def parser(format_json):
    spec=json.loads(format_json)
    if spec.get('type')=='text':return None
    if spec.get('type')!='grammar' or not isinstance(spec.get('definition'),str):
        raise ProtocolError('Unsupported native freeform format')
    definition=spec['definition']
    if spec.get('syntax')=='regex':
        try:return re.compile(definition)
        except re.error as exc:raise ProtocolError('Unsupported native regex grammar') from exc
    if spec.get('syntax')!='lark':raise ProtocolError('Unsupported native grammar syntax')
    for imported in re.findall(r'(?m)^\s*%import\s+(\S+)',definition):
        if not imported.startswith('common.'):
            raise ProtocolError('Native grammar imports must be from bundled Lark common rules')
    # The pinned Codex patch grammar uses nullable anonymous /(...*)/ tokens.
    # Make the token nonempty and the occurrence optional, preserving its language.
    definition=definition.replace('/(.*)/','/(.+)/?')
    try:return Lark(definition,parser='earley')
    except LarkError as exc:raise ProtocolError('Native grammar cannot be represented without loss') from exc


def validate(format_json,text):
    compiled=parser(format_json)
    try:
        if isinstance(compiled,re.Pattern):
            if compiled.fullmatch(text) is None:raise ProtocolError('Tool input violates its native regex grammar')
        elif compiled is not None:compiled.parse(text)
    except LarkError as exc:raise ProtocolError('Tool input violates its native grammar') from exc
