"""Exercise native local API families with persisted objects and filesystem effects."""
import argparse
import base64
import json
from pathlib import Path
import uuid

from verify_live import Peer,text
from process_acceptance import python_command


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\n')
    peer=Peer(root);results=[]
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'detail':detail};results.append(record)
        (root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(record),flush=True)
        if not condition:raise AssertionError(name)
    try:
        folder=root/'work/native-files';peer.call('fs/createDirectory',{'path':str(folder),'recursive':True})
        peer.call('fs/watch',{'path':str(folder),'watchId':'fixture'})
        start=len(peer.events);original=folder/'original.txt';copy=folder/'copy.txt';nonce=uuid.uuid4().hex
        peer.call('fs/writeFile',{'path':str(original),'dataBase64':base64.b64encode(nonce.encode()).decode()})
        peer.wait(lambda e:e.get('method')=='fs/changed' and e.get('params',{}).get('watchId')=='fixture',start,15)
        peer.call('fs/copy',{'sourcePath':str(original),'destinationPath':str(copy)})
        read=peer.call('fs/readFile',{'path':str(copy)});meta=peer.call('fs/getMetadata',{'path':str(copy)});directory=peer.call('fs/readDirectory',{'path':str(folder)})
        passed('native_files_copy_watch_metadata',base64.b64decode(read['dataBase64']).decode()==nonce and meta['isFile'] and len(directory['entries'])==2,{'metadata':meta,'directory':directory})
        peer.call('fs/unwatch',{'watchId':'fixture'});peer.call('fs/remove',{'path':str(copy)})
        passed('native_file_remove_effect',not copy.exists(),{})
        project=peer.call('project/create',{'idempotencyKey':uuid.uuid4().hex,'name':'Runtime audit project','roots':[{'path':str(root/'work')}],'metadata':{'purpose':'isolated-acceptance'}})['project']
        project_id=project['id'];peer.call('project/update',{'projectId':project_id,'name':'Runtime audit renamed'})
        project=peer.call('project/read',{'projectId':project_id})['project'];projects=peer.call('project/list',{})
        passed('native_project_crud_persistence',project['name']=='Runtime audit renamed' and project_id in {item['id'] for item in projects['data']},project)
        section=peer.call('threadSection/create',{'name':'Runtime section'})['section'];section_id=section['id']
        peer.call('threadSection/update',{'sectionId':section_id,'name':'Runtime section renamed'})
        thread=peer.new(projectId=project_id);peer.call('thread/name/set',{'threadId':thread,'name':'Native method audit'})
        peer.call('thread/section/move',{'threadId':thread,'sectionId':section_id})
        attachment={'threadId':thread,'attachmentType':'runtime_fixture','identityKey':'fixture:'+nonce,'payload':{'nonce':nonce}}
        peer.call('thread/attachment/add',attachment)
        attached=peer.call('thread/attachment/list',{'threadId':thread})['data']
        owners=peer.call('thread/attachmentOwner/list',{'attachmentType':attachment['attachmentType'],'identityKey':attachment['identityKey']})
        passed('native_thread_section_attachment_ownership',len(attached)==1 and attached[0]['payload']=={'nonce':nonce} and thread in str(owners),{'attachments':attached,'owners':owners})
        peer.call('thread/memoryMode/set',{'threadId':thread,'mode':'disabled'})
        peer.call('thread/settings/update',{'threadId':thread,'effort':'low','approvalPolicy':'never'})
        items=peer.turn(thread,'Do not use tools, search or delegate. Remember PAGE_HISTORY_'+nonce+' and reply with it.')
        turns=peer.call('thread/turns/list',{'threadId':thread,'limit':10})['data'];turn_id=turns[0]['id']
        entries=peer.call('thread/items/list',{'threadId':thread,'turnId':turn_id,'limit':20})['data']
        passed('native_paginated_turn_and_item_history',nonce in text(items) and bool(entries),{'turns':turns,'item_entries':entries})
        peer.close();peer=Peer(root)
        saved=peer.call('thread/read',{'threadId':thread,'includeTurns':True})['thread'];attachments=peer.call('thread/attachment/list',{'threadId':thread})['data']
        passed('cold_local_metadata_and_transcript_persist',saved['name']=='Native method audit' and nonce in json.dumps(saved) and len(attachments)==1,saved)
        peer.call('thread/resume',{'threadId':thread})
        start=len(peer.events)
        peer.call('process/spawn',{'command':python_command('print("PROCESS_READY",flush=True);value=input();print("RESULT:"+value)'),'cwd':str(root/'work'),'processHandle':'native-process','streamStdin':True,'streamStdoutStderr':True,'timeoutMs':20000})
        peer.wait(lambda e:e.get('method')=='process/outputDelta',start,15)
        peer.call('process/writeStdin',{'processHandle':'native-process','deltaBase64':base64.b64encode(b'PROCESS_INPUT_693\n').decode(),'closeStdin':True})
        terminal=peer.wait(lambda e:e.get('method')=='process/exited',start,20)
        output=b''.join(base64.b64decode(e['params']['deltaBase64']) for e in peer.events[start:] if e.get('method')=='process/outputDelta').decode()
        passed('native_process_spawn_stdin_and_exit','RESULT:PROCESS_INPUT_693' in output and terminal['params']['exitCode']==0,{'output':output,'exit':terminal})
        peer.call('thread/attachment/remove',{key:attachment[key] for key in ['threadId','attachmentType','identityKey']})
        peer.call('threadSection/delete',{'sectionId':section_id});peer.call('project/delete',{'projectId':project_id})
        passed('native_local_object_cleanup',not peer.call('thread/attachment/list',{'threadId':thread})['data'] and project_id not in {item['id'] for item in peer.call('project/list',{})['data']},{})
    finally:peer.close();print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
