"""Validate structured final messages before publishing native completion events."""
import json
from jsonschema import Draft202012Validator,validators
from referencing import Registry
from .protocol_errors import ProtocolError


class StructuredOutput:
    def __init__(self,body):
        format=(body.get('text') or {}).get('format') or {}
        self.validator=None;self.pending=[];self.messages=set()
        if format.get('type')=='json_schema':
            schema=format.get('schema')
            try:
                validator=validators.validator_for(schema,default=Draft202012Validator if not isinstance(schema,dict) or '$schema' not in schema else None)
                if validator is None:raise ValueError('unsupported JSON-schema dialect')
                validator.check_schema(schema)
                self.validator=validator(schema,registry=Registry())
            except Exception as error:raise ProtocolError('Invalid structured-output schema: '+str(error)[:400]) from error

    def validate(self,response):
        if self.validator is None or response.get('status')!='completed':return
        output=response.get('output',[])
        if any(x.get('type') in {'function_call','custom_tool_call'} for x in output):return
        messages=[x for x in output if x.get('type')=='message' and x.get('role')=='assistant']
        if not messages:raise ProtocolError('Structured response completed without a final assistant message')
        text=''.join(part.get('text','') for part in messages[-1].get('content',[]) if part.get('type')=='output_text')
        def reject_constant(value):raise ValueError('Non-JSON number: '+value)
        try:value=json.loads(text,parse_constant=reject_constant)
        except ValueError as error:raise ProtocolError('Provider structured output is not JSON; refusing to publish a completed schema result') from error
        try:self.validator.validate(value)
        except Exception as error:raise ProtocolError('Provider structured output violates the requested schema: '+str(error)[:400]) from error

    def events(self,event):
        if self.validator is None:return [event]
        kind=event.get('type');item=event.get('item',{})
        if kind=='response.output_item.added' and item.get('type')=='message':self.messages.add(item.get('id'))
        completion=(kind=='response.output_item.done' and item.get('type')=='message') or kind=='response.output_text.done' or (kind=='response.content_part.done' and (event.get('item_id') in self.messages or event.get('part',{}).get('type')=='output_text'))
        if completion:self.pending.append(event);return []
        if kind in {'response.completed','response.incomplete','response.failed'}:
            self.validate(event.get('response',{}))
            result=[*self.pending,event];self.pending=[];return result
        return [event]
