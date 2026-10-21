"""Validate structured final messages before publishing native completion events."""
import json
import math
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
        tools=any(x.get('type') in {'function_call','custom_tool_call'} for x in output)
        messages=[x for x in output if x.get('type')=='message' and x.get('role')=='assistant']
        # A legacy, unphased message accompanying a client tool is commentary.
        # An explicit final_answer remains a final answer even alongside tools.
        # Check every final message before releasing any buffered completion.
        final=[m for m in messages if m.get('phase')=='final_answer' or (not tools and m.get('phase')!='commentary')]
        if not final and not tools:raise ProtocolError('Structured response completed without a final assistant message')
        for message in final:self.validate_message(message)

    def validate_message(self,message):
        text=''.join(part.get('text','') for part in message.get('content',[]) if part.get('type')=='output_text')
        def reject_constant(value):raise ValueError('Non-JSON number: '+value)
        def finite_float(value):
            number=float(value)
            if not math.isfinite(number):raise ValueError('JSON number exceeds the finite native range')
            return number
        try:value=json.loads(text,parse_constant=reject_constant,parse_float=finite_float)
        except ValueError as error:raise ProtocolError('Provider structured output is not JSON; refusing to publish a completed schema result') from error
        try:self.validator.validate(value)
        except Exception as error:raise ProtocolError('Provider structured output violates the requested schema: '+str(error)[:400]) from error

    def events(self,event):
        if self.validator is None:return [event]
        kind=event.get('type');item=event.get('item',{})
        if kind=='response.output_item.done' and item.get('type')=='message' and item.get('phase')=='final_answer':
            # Reject a known bad final before later native tool completions can
            # be dispatched. Legacy unphased commentary still needs the snapshot.
            self.validate_message(item)
        if kind=='response.output_item.added' and item.get('type')=='message':self.messages.add(item.get('id'))
        completion=(kind=='response.output_item.done' and item.get('type')=='message') or kind=='response.output_text.done' or (kind=='response.content_part.done' and (event.get('item_id') in self.messages or event.get('part',{}).get('type')=='output_text'))
        if completion:self.pending.append(event);return []
        if kind in {'response.completed','response.incomplete','response.failed'}:
            self.validate(event.get('response',{}))
            result=[*self.pending,event];self.pending=[];return result
        return [event]
