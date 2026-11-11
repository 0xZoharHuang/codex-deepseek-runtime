"""Native v2 sibling messaging and nested agent ownership with real file effects."""
import argparse
import json
import hashlib
from pathlib import Path
import uuid
from verify_live import Peer,text,qa_sandbox_mode
from codex_deepseek.request_metadata import native_request_metadata



def _canonical_json_sha256(value):
    digest = hashlib.sha256()
    for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(value):
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _strict_json_loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("nonfinite JSON number")
    def finite_float(text):
        value = float(text)
        if abs(value) == float("inf"):
            raise ValueError("nonfinite JSON number")
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=finite_float)


def _peer_semantics(item, *, native_source=False):
    """Decode source data or the exact C transport; never prove comprehension.

    Native model input is AgentMessage (record_inter_agent_communication uses
    to_model_input_item). The unused legacy to_response_input_item serializer
    does not establish a model fallback contract. Prepared qualification accepts
    only a user message containing metadata first, then unchanged native parts.
    """
    if not isinstance(item, dict):
        return None
    if native_source:
        allowed = {"type", "id", "author", "recipient", "content",
                   "internal_chat_message_metadata_passthrough"}
        if item.get("type") != "agent_message" or not set(item) <= allowed:
            return None
        peer = {key: item.get(key) for key in ("author", "recipient", "content")}
    else:
        if (set(item) != {"type", "role", "content"} or item.get("type") != "message"
                or item.get("role") != "user"):
            return None  # No phase/id or alternate carrier in the current codec.
        parts = item.get("content")
        if (not isinstance(parts, list) or len(parts) < 2 or not isinstance(parts[0], dict)
                or set(parts[0]) != {"type", "text"} or parts[0].get("type") != "input_text"
                or not isinstance(parts[0].get("text"), str)):
            return None
        try:
            metadata = _strict_json_loads(parts[0]["text"])
        except (ValueError, TypeError, RecursionError):
            return None
        if (not isinstance(metadata, dict) or set(metadata) != {"type", "author", "recipient"}
                or metadata.get("type") != "agent_message"):
            return None
        peer = {"author": metadata["author"], "recipient": metadata["recipient"],
                "content": parts[1:]}
    if any(not isinstance(peer.get(key), str) or not peer[key] for key in ("author", "recipient")):
        return None
    parts = peer.get("content")
    if (not isinstance(parts, list) or not parts
            or any(not isinstance(part, dict) or set(part) != {"type", "text"}
                   or part.get("type") != "input_text" or not isinstance(part.get("text"), str)
                   for part in parts)):
        return None
    return peer


def load_native_peer_evidence(trace_dir):
    """Pair private observer sidecars with their exact prepared trace receipt.

    The prepared boundary precedes image_transport.prepare. It does not prove
    upstream consumption. Legacy request-only traces never gain source evidence.
    """
    import uuid
    trace_dir = Path(trace_dir)
    records = {}
    suffix = "-native-peer-evidence.json"
    for path in trace_dir.glob("*" + suffix):
        receipt = path.name[:-len(suffix)]
        try:
            if str(uuid.UUID(receipt)) != receipt or path.is_symlink():
                raise ValueError("invalid receipt or linked sidecar")
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized peer sidecar")
            evidence = _strict_json_loads(raw)
            prepared = trace_dir / (receipt + "-request.json")
            if prepared.is_symlink():
                raise ValueError("linked prepared request")
            records[receipt] = {"evidence": evidence,
                                "prepared_request": _strict_json_loads(prepared.read_bytes())}
        except (OSError, ValueError, TypeError, RecursionError):
            records[receipt] = {"error": "invalid_or_missing_native_peer_evidence_pair"}
    return records


def delivered_peer_messages(requests, recipient, sender, kind, markers, *, native_messages, owner,
                            native_peer_evidence=None):
    """Prove the current C codec/source binding, not understanding or task success."""
    import uuid
    header = f"Message Type: {kind}\nTask name: {recipient}\nSender: {sender}\nPayload:\n"
    sources, ambiguous = {}, set()
    for record in native_messages:
        if not isinstance(record, dict) or record.get("thread_id") != owner:
            continue
        item = record.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        item_id, peer = item.get("id"), _peer_semantics(item, native_source=True)
        if not isinstance(item_id, str) or not item_id or peer is None:
            continue
        try:
            fingerprint = _canonical_json_sha256(peer)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            continue
        if item_id in sources and sources[item_id][0] != fingerprint:
            ambiguous.add(item_id)
        sources[item_id] = (fingerprint, peer)
    request_hashes = set()
    for request in requests.values() if isinstance(requests, dict) else requests:
        if isinstance(request, dict):
            body = request.get("body", request)
            try:
                request_hashes.add(_canonical_json_sha256(body))
            except (ValueError, TypeError, RecursionError, UnicodeError):
                pass
    found = {}
    for receipt, paired in (native_peer_evidence or {}).items():
        try:
            if str(uuid.UUID(receipt)) != receipt or not isinstance(paired, dict) or "error" in paired:
                continue
            evidence, request = paired["evidence"], paired["prepared_request"]
            if not isinstance(evidence, dict) or not isinstance(request, dict):
                continue
            expected_keys = {"schema_version", "scope", "receipt_id", "status", "native_request", "bindings",
                             "prepared_request_sha256", "prepared_input_sha256", "record_sha256", "native_input_count"}
            if (set(evidence) != expected_keys or type(evidence["schema_version"]) is not int
                    or evidence["schema_version"] != 1 or evidence["status"] != "qualified"
                    or evidence["scope"] != "native_to_prepared_before_image_transport"
                    or evidence["receipt_id"] != receipt):
                continue
            if evidence["record_sha256"] != _canonical_json_sha256({k:v for k,v in evidence.items() if k != "record_sha256"}):
                continue
            request_hash = _canonical_json_sha256(request)
            items = request.get("input")
            if (request_hash not in request_hashes or evidence["prepared_request_sha256"] != request_hash
                    or not isinstance(items, list)
                    or evidence["prepared_input_sha256"] != _canonical_json_sha256(items)):
                continue
            metadata = native_request_metadata(request, {})
            original_metadata = evidence["native_request"]
            if (metadata.get("status") != "recognized" or metadata.get("request_kind") != "turn"
                    or metadata.get("thread_id") != owner or not isinstance(original_metadata, dict)
                    or original_metadata.get("status") != "recognized"
                    or any(original_metadata.get(k) != metadata.get(k) for k in ("thread_id", "turn_id", "request_kind"))):
                continue
            bindings = evidence["bindings"]
            native_count = evidence["native_input_count"]
            if (type(native_count) is not int or native_count < 0 or not isinstance(bindings, list)
                    or not bindings or len(bindings) > min(128, native_count)):
                continue
            ids, indices, native_indices = set(), set(), set()
            valid_rows = []
            for binding in bindings:
                if not isinstance(binding, dict) or set(binding) != {"native_input_index", "native_item_id", "native_peer_sha256",
                        "author", "recipient", "prepared_input_index", "prepared_item_sha256"}:
                    raise ValueError("invalid peer binding")
                item_id, index, native_index = binding["native_item_id"], binding["prepared_input_index"], binding["native_input_index"]
                if (not isinstance(item_id, str) or not item_id or len(item_id) > 256 or item_id in ids
                        or type(index) is not int or index < 0 or index >= len(items) or index in indices
                        or type(native_index) is not int or native_index < 0 or native_index >= native_count or native_index in native_indices
                        or binding["prepared_item_sha256"] != _canonical_json_sha256(items[index])):
                    raise ValueError("invalid peer binding identity/index/hash")
                ids.add(item_id);indices.add(index);native_indices.add(native_index)
                valid_rows.append(binding)
            for binding in valid_rows:
                item_id, index = binding["native_item_id"], binding["prepared_input_index"]
                source = sources.get(item_id)
                if source is None or item_id in ambiguous:
                    continue
                fingerprint, peer = source
                if (fingerprint != binding["native_peer_sha256"] or peer["author"] != sender
                        or peer["recipient"] != recipient or binding["author"] != sender or binding["recipient"] != recipient):
                    continue
                prepared_peer = _peer_semantics(items[index])
                if prepared_peer is None or _canonical_json_sha256(prepared_peer) != fingerprint:
                    continue
                text = "\n".join(part["text"] for part in peer["content"])
                if not text.startswith(header) or not all(marker in text[len(header):] for marker in markers):
                    continue
                # Historical source turn IDs may legitimately appear in later
                # requests; identity is owner+item ID+content, not equal turn IDs.
                found[(receipt, index)] = {"receipt_id": receipt, "owner": owner, **binding,
                    "prepared_request_sha256": request_hash,
                    "prepared_input_sha256": evidence["prepared_input_sha256"]}
        except (KeyError, OSError, ValueError, TypeError, RecursionError, UnicodeError):
            continue
    return [found[key] for key in sorted(found)]


def nested_fork_evidence(native_calls, *, root_thread_id, agents):
    """Bind requested fork choices to successful native spawns, never alter defaults."""
    expected = (("/root", "/root/producer", "producer", "all"),
                ("/root", "/root/auditor", "auditor", "none"),
                ("/root/producer", "/root/producer/totals", "totals", "none"))
    by_path = {}
    for agent in agents:
        if isinstance(agent, dict):
            by_path.setdefault(agent.get("path"), []).append(agent)
    calls, outputs, malformed = [], [], []
    for record in native_calls:
        if not isinstance(record, dict) or not isinstance(record.get("item"), dict):
            continue
        item = record["item"]
        metadata = item.get("internal_chat_message_metadata_passthrough")
        turn = metadata.get("turn_id") if isinstance(metadata, dict) else None
        common = {"owner_thread_id": record.get("thread_id"), "source_item_id": item.get("id"),
                  "call_id": item.get("call_id"), "turn_id": turn}
        if item.get("type") == "function_call_output":
            raw = item.get("output")
            try:
                value = _strict_json_loads(raw) if isinstance(raw, str) else None
            except (ValueError, TypeError, RecursionError):
                value = None
            if (isinstance(value, dict) and set(value) == {"task_name"}
                    and isinstance(value["task_name"], str)):
                outputs.append({**common, "canonical_child_path": value["task_name"],
                                "output_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()})
            continue
        if (item.get("type") != "function_call" or item.get("namespace") != "collaboration"
                or item.get("name") != "spawn_agent"):
            continue
        raw = item.get("arguments")
        try:
            arguments = _strict_json_loads(raw) if isinstance(raw, str) else None
            if not isinstance(arguments, dict):
                raise ValueError("native spawn arguments must be an object")
        except (ValueError, TypeError, RecursionError):
            malformed.append({**common, "reason": "invalid_native_spawn_arguments"})
            continue
        present, value = "fork_turns" in arguments, arguments.get("fork_turns")
        effective = value if present and value in ("all", "none") else "all" if not present else None
        calls.append({**common, "task_name": arguments.get("task_name"),
                      "arguments_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                      "explicit_argument_present": present, "explicit_fork_turns": value,
                      "effective_native_fork_turns": effective, "model_explicitly_supplied": present,
                      "native_default_applied": not present})
    edges = []
    for owner_path, target_path, task_name, required in expected:
        owners = by_path.get(owner_path, [])
        owner = root_thread_id if owner_path == "/root" else owners[0].get("id") if len(owners) == 1 else None
        targets = by_path.get(target_path, [])
        target = targets[0] if len(targets) == 1 else {}
        metadata_bound = (isinstance(owner, str) and bool(owner)
                          and isinstance(target.get("id"), str) and bool(target["id"])
                          and target.get("parent") == owner)
        observations = [call for call in calls if call["owner_thread_id"] == owner
                        and call["task_name"] == task_name] if owner else []
        # Exact retained copies are observations of one native call, not a new execution.
        unique = {(call["source_item_id"], call["call_id"], call["turn_id"],
                   call["arguments_sha256"]): call for call in observations}
        candidates, successful = [], []
        for call in unique.values():
            identified = all(isinstance(call[key], str) and call[key]
                             for key in ("source_item_id", "call_id", "turn_id"))
            acknowledgments = [ack for ack in outputs if ack["owner_thread_id"] == owner and ack["call_id"] == call["call_id"]
                and ack["turn_id"] == call["turn_id"] and ack["canonical_child_path"] == target_path]
            ack_map = {(ack["source_item_id"], ack["output_sha256"]): ack for ack in acknowledgments}
            ack_identified = all(isinstance(ack["source_item_id"], str) and ack["source_item_id"]
                                 for ack in ack_map.values())
            observation = {**call, "source_identity_qualified": bool(identified and ack_identified),
                           "successful_native_ack_count": len(ack_map),
                           "successful_native_acks": list(ack_map.values())}
            candidates.append(observation)
            if metadata_bound and identified and ack_identified and len(ack_map) == 1:
                successful.append(observation)
        selected = successful[0] if len(successful) == 1 else None
        ambiguous_ack = any(call["successful_native_ack_count"] > 1 for call in candidates)
        unqualified_success = any(call["successful_native_ack_count"] and not call["source_identity_qualified"]
                                  for call in candidates)
        satisfied = bool(selected and not ambiguous_ack and not unqualified_success
                         and selected["explicit_argument_present"]
                         and selected["explicit_fork_turns"] == required)
        reason = ("satisfied" if satisfied else "native_owner_or_child_binding_missing" if not metadata_bound
                  else "native_spawn_call_missing" if not candidates else "native_success_identity_missing"
                  if unqualified_success else "ambiguous_native_success_acks" if ambiguous_ack
                  else "native_success_ack_missing" if not successful else "ambiguous_successful_native_spawns" if len(successful) != 1
                  else "model_omitted_fork_turns" if not selected["explicit_argument_present"]
                  else "explicit_fork_turns_does_not_match_prompt")
        edges.append({"owner_path": owner_path, "owner_thread_id": owner, "target_path": target_path,
                      "target_thread_id": target.get("id"), "expected_explicit_fork_turns": required,
                      "metadata_bound": metadata_bound, "observed_call_count": len(observations),
                      "unique_native_call_count": len(candidates), "calls": candidates,
                      "successful_native_spawn_count": len(successful),
                      "selected_successful_call": selected,
                      "explicit_argument_present": selected["explicit_argument_present"] if selected else None,
                      "explicit_fork_turns": selected["explicit_fork_turns"] if selected else None,
                      "effective_native_fork_turns": selected["effective_native_fork_turns"] if selected else None,
                      "effective_matches_expected": bool(selected and selected["effective_native_fork_turns"] == required),
                      "model_explicitly_supplied": selected["model_explicitly_supplied"] if selected else None,
                      "requirement_satisfied": satisfied, "reason": reason})
    return {"passed": all(edge["requirement_satisfied"] for edge in edges),
            "native_default_fork_turns": "all", "edges": edges, "malformed_native_spawn_calls": malformed,
            "scope": "Scenario prompt choices bound to native success acknowledgments and child provenance; runtime fork capability is separate"}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default=qa_sandbox_mode());args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('sandbox_mode='+json.dumps(args.sandbox_mode)+'\nmodel_reasoning_effort="low"\n[agents]\nmax_depth=3\n', encoding='utf-8')
    (root/'work/inputs.json').write_text(json.dumps({'credits':[125,250,375],'currency':'cents','audit_id':uuid.uuid4().hex}), encoding='utf-8')
    nonce='NESTED_'+uuid.uuid4().hex
    peer=Peer(root);result={}
    try:
        thread=peer.new(sandbox=args.sandbox_mode)
        task=f'''This is an explicitly authorized isolated native v2 lifecycle acceptance.
Use exactly three native children in the following ownership tree: parent /root;
its two direct children /root/producer and /root/auditor; and one child owned by
producer named totals. No other children or descendants. These tasks are authorized.
Spawn producer with fork_turns=all. Its job: spawn its one totals child with
fork_turns=none; that totals child must read inputs.json using a real native tool,
compute the total integer cents and write totals.json with total_cents=750 and
marker={nonce!r}. Producer must wait for its actual completion, read totals.json,
and send parent a progress MESSAGE containing PRODUCER_READY and the marker.
Producer then reports its result. Do not spawn totals yourself.
Spawn auditor with fork_turns=none. Its job: wait until totals.json exists, read it
and inputs.json with real tools, independently verify the sum and write audit.json
with passed=true and the marker. Auditor must not spawn children.
After spawning both direct children, send auditor a native MESSAGE stating that
producer owns the nested worker and that the target is integer cents. Wait for
both actual final results. Use followup_task on the same auditor to ask it to
verify both artifacts again and reply FOLLOWUP_VERIFIED plus the marker. Wait for
that actual followup completion. Then read both JSON files yourself, verify them,
and report the marker and FOLLOWUP_VERIFIED. Do not simulate tool outputs or
reassign incoming peer evidence as a fresh parent task. Only use this workspace;
do not search, access App IPC, or modify files outside it.'''
        turn,start=peer.start(thread,task)
        items=peer.complete(thread,start,timeout=600)
        # JSON's stdlib byte decoder recognizes the BOM emitted by Windows
        # PowerShell's UTF-8 writer without changing the actual artifact bytes.
        totals=json.loads((root/'work/totals.json').read_bytes());audit=json.loads((root/'work/audit.json').read_bytes())
        agents=[];messages=[];native_calls=[]
        for path in (root/'home/sessions').rglob('*.jsonl'):
            events=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            meta=events[0]['payload'];source=meta.get('source',{})
            spawn=source.get('subagent',{}).get('thread_spawn',{}) if isinstance(source,dict) else {}
            calls=[event['payload'] for event in events if event.get('type')=='response_item' and event.get('payload',{}).get('type') in {'function_call','custom_tool_call'}]
            native_calls.extend({'thread_id':meta['id'],'item':event['payload']} for event in events if event.get('type')=='response_item' and event.get('payload',{}).get('type') in {'function_call','function_call_output'})
            if spawn:
                complete=[event for event in events if event.get('type')=='event_msg' and event.get('payload',{}).get('type')=='task_complete']
                agents.append({'id':meta['id'],'parent':spawn.get('parent_thread_id'),'path':spawn.get('agent_path'),'tools_used':bool(calls),'completed':bool(complete),'completed_turn_ids':sorted({event['payload']['turn_id'] for event in complete if event['payload'].get('turn_id')})})
            messages.extend({'thread_id':meta['id'],'item':event['payload']} for event in events if event.get('type')=='response_item' and event.get('payload',{}).get('type')=='agent_message')
        direct=[agent for agent in agents if agent['parent']==thread]
        nested=[agent for agent in agents if agent['parent'] in {a['id'] for a in direct}]
        by_path={agent['path']:agent for agent in agents}
        trace = root/'adapter-traces'
        requests=[_strict_json_loads(path.read_bytes()) for path in trace.glob('*-request.json')]
        peer_evidence = load_native_peer_evidence(trace)
        # Native siblings share the root prompt-cache key. It is a cache hint,
        # not the actual request owner; use the native client thread metadata.
        auditor=by_path.get('/root/auditor',{})
        deliveries={
            'producer_progress':delivered_peer_messages(requests,'/root','/root/producer','MESSAGE',['PRODUCER_READY',nonce],native_messages=messages,owner=thread,native_peer_evidence=peer_evidence),
            'parent_to_auditor':delivered_peer_messages(requests,'/root/auditor','/root','MESSAGE',['producer','integer'],native_messages=messages,owner=auditor.get('id'),native_peer_evidence=peer_evidence),
            'auditor_followup':delivered_peer_messages(requests,'/root','/root/auditor','FINAL_ANSWER',['FOLLOWUP_VERIFIED',nonce],native_messages=messages,owner=thread,native_peer_evidence=peer_evidence),
        }
        fork_evidence=nested_fork_evidence(native_calls,root_thread_id=thread,agents=agents)
        answer=text(items)
        passed=len(direct)==2 and len(nested)==1 and len(agents)==3 and set(by_path)=={'/root/producer','/root/auditor','/root/producer/totals'} and all(a['completed'] and a['tools_used'] for a in agents) and len(auditor.get('completed_turn_ids',[]))>=2 and all(deliveries.values()) and totals.get('total_cents')==750 and totals.get('marker')==nonce and audit.get('passed') is True and audit.get('marker')==nonce and 'FOLLOWUP_VERIFIED' in answer and nonce in answer and fork_evidence['passed']
        result={'passed':passed,'fork_evidence':fork_evidence,'sandbox_mode':args.sandbox_mode,'agents':agents,'totals':totals,'audit':audit,'peer_message_count':len(messages),'verified_peer_deliveries':deliveries,'native_peer_evidence_diagnostics':{key:value.get('error') or value.get('evidence',{}).get('reason') or value.get('evidence',{}).get('status') for key,value in peer_evidence.items()},'legacy_prepared_receipts_without_peer_sidecar':sorted(path.name[:-len('-request.json')] for path in trace.glob('*-request.json') if path.name[:-len('-request.json')] not in peer_evidence),'peer_evidence_scope':'C codec/source binding native_to_prepared_before_image_transport; not model understanding, task success or upstream consumption','answer':answer}
        (root/'results.json').write_text(json.dumps(result,indent=2), encoding='utf-8');print(json.dumps(result),flush=True)
        if not passed:raise AssertionError('Nested agent lifecycle/effects did not satisfy the declared ownership tree')
    finally:peer.close();print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
