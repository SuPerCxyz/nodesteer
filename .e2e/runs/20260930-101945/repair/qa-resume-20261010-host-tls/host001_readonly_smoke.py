"""Shell-only read-only Host001 mount-identity and nsenter/systemd smoke."""
import hashlib,json,pathlib,shlex,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());ST=json.loads((B/'host001-stage.json').read_text());RUN=P['identifiers']['run'];NODE=P['target_native2_node_id'];NODE_ID=ST['node_id'];APP=P['identifiers']['app_root'];UNIT=P['identifiers']['unit']

def shell_body():
    return """set -eu
if [ /proc/1/ns/mnt -ef /proc/self/ns/mnt ]; then echo MNT_NS_SAME; exit 21; else echo MNT_NS_PRIVATE_OK; fi
if [ /host/etc/systemd/system -ef /proc/1/root/etc/systemd/system ]; then echo SYSTEMD_BIND_SAMEFILE; else echo SYSTEMD_BIND_MISMATCH; exit 22; fi
if [ /hostAPP -ef /proc/1/rootAPP ]; then echo APP_BIND_SAMEFILE; else echo APP_BIND_MISMATCH; exit 23; fi
if unit_output=$(/usr/bin/nsenter -t 1 -m -u -i -n -p --root=/proc/1/root systemctl show --no-pager -p LoadState -p ActiveState -p MainPID -p FragmentPath UNIT 2>&1); then unit_rc=0; else unit_rc=$?; fi
printf 'NSENTER_SYSTEMCTL_RC=%s\\n' "$unit_rc"
printf '%s\\n' "$unit_output"
if [ "$unit_rc" -ne 0 ]; then exit 24; fi
case "$unit_output" in *LoadState=not-found*) echo EXACT_UNIT_NOT_FOUND_OK ;; *) echo EXACT_UNIT_NOT_ABSENT; exit 25 ;; esac
""".replace('/hostAPP','/host'+APP).replace('/proc/1/rootAPP','/proc/1/root'+APP).replace(' UNIT',' '+UNIT)

def create_shell_task(name,command,node_id):
    payload={'name':name,'type':'command','target':{'type':'node','node_ids':[node_id]},'parameters':[],'param_values':{},'command':command,'timeout':40,'retry':0,'offline_policy':'hub_online_required','enabled':True}
    api.write_json('task-definition-'+name+'.json',{'method':'POST','path':'/api/tasks','target_node_id':node_id,'payload':{k:v for k,v in payload.items() if k!='command'},'command_sha256':hashlib.sha256(command.encode()).hexdigest()})
    st,raw=api.request('POST','/api/tasks',payload)
    if st!=200:raise RuntimeError('shell_task_create_http_'+str(st))
    task=json.loads(raw)
    if not task.get('id'):raise RuntimeError('shell_task_id_missing')
    rb=api.get_json('/api/tasks/'+task['id'])
    if rb.get('command')!=command or rb.get('target',{}).get('node_ids')!=[node_id] or rb.get('retry')!=0:raise RuntimeError('shell_task_readback_mismatch')
    api.write_json('task-created-'+name+'.json',{'id':task['id'],'name':name,'target_node_id':node_id,'command_sha256':hashlib.sha256(command.encode()).hexdigest(),'create_http':st})
    return rb

def main():
    node=api.get_json('/api/nodes/'+NODE_ID)
    if node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('host001_not_online_synced_before_smoke')
    originals=json.loads((B.parent/'qa-resume-20261009-backend'/'preflight.json').read_text())['original_repair_nodes'];before=[]
    for x in originals:
        q=api.get_json('/api/nodes/'+x['node_id']);before.append({'node_id':x['node_id'],'status':q.get('status'),'sync_status':q.get('sync_status')})
    if any(x['status']!='online' or x['sync_status']!='synced' for x in before):raise RuntimeError('original_nodes_unhealthy_before_smoke')
    body=shell_body();p=subprocess.run(['bash','-n'],input=body,text=True,capture_output=True,timeout=10)
    if p.returncode:raise RuntimeError('smoke_shell_syntax_failed')
    name='qa-resume-20261010-host-tls-host001-readonly-nsenter-smoke-f174890c3846';command='/bin/sh -c '+shlex.quote(body)
    api.write_json('host001-readonly-nsenter-smoke-syntax.json',{'status':'PASS','bash_n':'PASS','shell_sha256':hashlib.sha256(body.encode()).hexdigest(),'command_sha256':hashlib.sha256(command.encode()).hexdigest(),'remote_runtime':'/bin/sh; no Python binary required'})
    task=create_shell_task(name,command,NODE_ID)
    try:r=api.run_task(task,timeout=60)
    finally:cl=api.cleanup_task(task['id'],NODE_ID)
    stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    if r.get('status')!='SUCCESS':
        e=api.get_json('/api/executions/'+r['id']);api.write_json('host001-readonly-nsenter-smoke-failed.json',{'status':'TASK_FAILED','actual_at':stamp,'task_id':task['id'],'execution_id':r['id'],'exit_code':r.get('exit_code'),'stderr':e.get('stderr',''),'stdout':e.get('stdout',''),'cleanup':cl});raise RuntimeError('readonly_smoke_task_failed')
    stdout=r.get('stdout','');passed=all(x in stdout for x in ('MNT_NS_PRIVATE_OK','SYSTEMD_BIND_SAMEFILE','APP_BIND_SAMEFILE','EXACT_UNIT_NOT_FOUND_OK'))
    post=api.get_json('/api/nodes/'+NODE_ID);after=[]
    for x in originals:
        q=api.get_json('/api/nodes/'+x['node_id']);after.append({'node_id':x['node_id'],'status':q.get('status'),'sync_status':q.get('sync_status')})
    if any(x['status']!='online' or x['sync_status']!='synced' for x in after):raise RuntimeError('original_nodes_health_changed_during_smoke')
    rec={'status':'PASS' if passed and post.get('status')=='online' and post.get('sync_status')=='synced' else 'FAIL','actual_at':stamp,'node_id':NODE_ID,'node':{k:post.get(k) for k in ('id','agent_id','hostname','ip','status','sync_status')},'stdout':stdout,'stderr':r.get('stderr',''),'exit_code':r.get('exit_code'),'operation':'readonly only','host_unit_written':False,'app_or_artifact_created':False,'task_id':task['id'],'execution_id':r['id'],'task_cleanup':cl,'original_four_nodes':after}
    api.write_json('host001-readonly-nsenter-smoke.json',rec)
    prog=json.loads((B/'progress.json').read_text());prog.update({'actual_at':stamp,'status':'HOST001_READONLY_SMOKE_'+rec['status'],'stage':'host001-readonly-mount-and-nsenter-smoke-complete' if rec['status']=='PASS' else 'host001-readonly-smoke-failed','current_operation':'Shell-only read-only Agent Task checked host/private mount namespace identity, exact /host systemd/app bind paths via samefile semantics, and used nsenter to query only the exact absent Host001 App Unit. No App/Artifact/Unit writes.','next_action':'If PASS, capture a fresh Native2 systemd-tree metadata snapshot and compare every name/mode/SHA with baseline; only then create unique Artifact/Managed App. If FAIL, preserve actual output and do not write App/Unit.','persisted_evidence':sorted(set(prog.get('persisted_evidence',[])+[str((B/'host001-readonly-nsenter-smoke.json').resolve()),str((B/'host001-readonly-nsenter-smoke-syntax.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',prog)
    print(json.dumps({'status':rec['status'],'actual_at':stamp,'node_online_synced':post.get('status')=='online' and post.get('sync_status')=='synced','smoke_markers':{k:k in stdout for k in ('MNT_NS_PRIVATE_OK','SYSTEMD_BIND_SAMEFILE','APP_BIND_SAMEFILE','EXACT_UNIT_NOT_FOUND_OK')},'exit_code':r.get('exit_code'),'stderr':r.get('stderr',''),'task_get404':cl['get_after_delete_http']==404}))

if __name__=='__main__':main()
