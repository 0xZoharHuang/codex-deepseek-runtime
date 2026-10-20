"""Local marketplace install, native plugin skill invocation and uninstall."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid
from verify_live import Peer,text


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\n')
    subprocess.run(['git','init','-q',str(root/'work')],check=True)
    marketplace=root/'work/.agents/plugins/marketplace.json';marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({'name':'isolated-fixtures','plugins':[{'name':'runtime-fixture','source':{'source':'local','path':'./runtime-fixture'}}]}))
    plugin=root/'work/runtime-fixture';(plugin/'.codex-plugin').mkdir(parents=True)
    (plugin/'.codex-plugin/plugin.json').write_text(json.dumps({'name':'runtime-fixture','version':'1.0.0','description':'Isolated native runtime plugin fixture'}))
    skill=plugin/'skills/runtime-plugin-skill';skill.mkdir(parents=True)
    nonce='PLUGIN_'+uuid.uuid4().hex
    (root/'work/plugin_fixture.py').write_text('from pathlib import Path\nPath("plugin-effect.txt").write_text('+repr(nonce)+')\nprint('+repr(nonce)+')\n')
    (skill/'SKILL.md').write_text('---\nname: runtime-plugin-skill\ndescription: Execute the isolated plugin acceptance fixture when explicitly invoked.\n---\nRun `python3 plugin_fixture.py` in the task workspace. Report its exact output. Do not delegate.\n')
    peer=Peer(root);results=[]
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'detail':detail};results.append(record)
        (root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(record),flush=True)
        if not condition:raise AssertionError(name)
    try:
        read=peer.call('plugin/read',{'marketplacePath':str(marketplace),'pluginName':'runtime-fixture'})
        installed=peer.call('plugin/install',{'marketplacePath':str(marketplace),'pluginName':'runtime-fixture','installAttemptId':uuid.uuid4().hex})
        (root/'plugin-read.json').write_text(json.dumps(read,indent=2));(root/'plugin-install.json').write_text(json.dumps(installed,indent=2))
        refreshed=peer.call('plugin/read',{'marketplacePath':str(marketplace),'pluginName':'runtime-fixture'})
        passed('native_local_plugin_read_and_install',read['plugin']['summary']['id']=='runtime-fixture@isolated-fixtures' and refreshed['plugin']['summary']['installed'] is True,{'install_response':installed,'summary':refreshed['plugin']['summary']})
        peer.close();peer=Peer(root)
        skills=peer.call('skills/list',{'cwds':[str(root/'work')],'forceReload':True})
        passed('native_plugin_skill_discovered_after_cold_start','runtime-plugin-skill' in json.dumps(skills),{'matching_skills':[item for group in skills['data'] for item in group.get('skills',[]) if item.get('pluginId')=='runtime-fixture@isolated-fixtures']})
        thread=peer.new();items=peer.turn(thread,'$runtime-plugin-skill\nUse this explicitly invoked installed plugin skill. Run its fixture and report the actual output. Do not delegate, search, or simulate the result.')
        passed('native_plugin_skill_real_effect',(root/'work/plugin-effect.txt').is_file() and (root/'work/plugin-effect.txt').read_text()==nonce and nonce in text(items) and any(item.get('type')=='commandExecution' for item in items),text(items))
        peer.call('plugin/uninstall',{'pluginId':'runtime-fixture@isolated-fixtures'})
        skills=peer.call('skills/list',{'cwds':[str(root/'work')],'forceReload':True})
        passed('native_plugin_uninstall_removes_skill','runtime-plugin-skill' not in json.dumps(skills),{'matching_skills':[item.get('name') for group in skills['data'] for item in group.get('skills',[]) if item.get('pluginId')=='runtime-fixture@isolated-fixtures']})
    finally:peer.close();print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
