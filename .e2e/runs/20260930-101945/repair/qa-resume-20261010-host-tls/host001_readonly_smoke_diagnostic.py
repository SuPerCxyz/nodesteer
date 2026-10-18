"""Continue read-only Host001 diagnostics after samefile shell mismatch; never writes app/unit."""
import hashlib,json,pathlib,shlex,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());ST=json.loads((B/'host001-stage.json').read_text());NODE=P['target_native2_node_id'];NODE_ID=ST['node_id'];APP=P['identifiers']['app_root'];UNIT=P['identifiers']['unit']

def main():
 s=f'''echo BEGIN_READ_ONLY\nif [ /proc/1/ns/mnt -ef /proc/self/ns/mnt ]; then echo MNT_NS_SAME; else echo MNT_NS_PRIVATE_OK; fi\nfor p in /proc/1/root /proc/1/root/etc /proc/1/root/etc/systemd /proc/1/root/etc/systemd/system /host/etc/systemd/system; do if [ -d "$p" ]; then echo "DIR_OK:$p"; else echo "DIR_NOT_ACCESSIBLE:$p"; fi; done\nif [ /host/etc/systemd/system -ef /proc/1/root/etc/systemd/system ]; then echo SYSTEMD_BIND_SAMEFILE; else echo SYSTEMD_BIND_MISMATCH; fi\nif [ /host{APP} -ef /proc/1/root{APP} ]; then echo APP_BIND_SAMEFILE; else echo APP_BIND_MISMATCH; fi\nif unit_output=$(/usr/bin/nsenter -t 1 -m -u -i -n -p --root=/proc/1/root systemctl show --no-pager -p LoadState -p ActiveState -p MainPID -p FragmentPath {UNIT} 2>&1); then unit_rc=0; else unit_rc=$?; fi\nprintf 'NSENTER_SYSTEMCTL_RC=%s\\n' "$unit_rc"\nprintf '%s\\n' "$unit_output"\necho END_READ_ONLY\n'''
 command='/bin/sh -c '+shlex.quote(s);syntax=subprocess.run(['bash','-n'],input=s,text=True,capture_output=True,timeout=10)
 if syntax.returncode:raise RuntimeError('diagnostic_shell_syntax_failed')
 name='qa-resume-20261010-host-tls-host001-readonly-smoke-diagnostic-f174890c3846';node=api.get_json('/api/nodes/'+NODE_ID)
 if node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('host001_node_unhealthy_before_diag')
 task={'name':name,'type':'command','target':{'type':'node','node_ids':[NODE_ID]},'parameters':[],'param_values':{},'command':command,'timeout':35,'retry':0,'offline_policy':'hub_online_required','enabled':True}
 api.write_json('task-definition-'+name+'.json',{'method':'POST','target_node_id':NODE_ID,'parameters':[],'retry':0,'command_sha256':hashlib.sha256(command.encode()).hexdigest(),'command_persisted_in_evidence':False})
 status,raw=api.request('POST','/api/tasks',task)
 if status!=200:raise RuntimeError('diagnostic_task_create_http_'+str(status))
 t=json.loads(raw);rb=api.get_json('/api/tasks/'+t['id'])
 if rb.get('command')!=command or rb.get('target',{}).get('node_ids')!=[NODE_ID]:raise RuntimeError('diagnostic_task_readback_mismatch')
 api.write_json('task-created-'+name+'.json',{'id':t['id'],'name':name,'target_node_id':NODE_ID,'create_http':status,'command_sha256':hashlib.sha256(command.encode()).hexdigest()})
 try:r=api.run_task(rb,timeout=60)
 finally:cl=api.cleanup_task(t['id'],NODE_ID)
 stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
 if r.get('status')!='SUCCESS':
  e=api.get_json('/api/executions/'+r['id']);api.write_json('host001-readonly-smoke-diagnostic-failed.json',{'status':'TASK_FAILED','actual_at':stamp,'task_id':t['id'],'execution_id':r['id'],'stdout':e.get('stdout',''),'stderr':e.get('stderr',''),'cleanup':cl});raise RuntimeError('readonly_diagnostic_task_failed')
 out=r.get('stdout','');markers={x:x in out for x in ['MNT_NS_PRIVATE_OK','SYSTEMD_BIND_SAMEFILE','APP_BIND_SAMEFILE','DIR_NOT_ACCESSIBLE:/proc/1/root/etc/systemd/system','EXACT_UNIT_NOT_FOUND_OK']}
 record={'status':'RECORDED_READ_ONLY_DIAGNOSTIC','actual_at':stamp,'node_id':NODE_ID,'markers':markers,'stdout':out,'stderr':r.get('stderr',''),'exit_code':r.get('exit_code'),'unit':'nodesteer-qa-host-f174890c3846-app.service','exact_readonly_command':['/usr/bin/nsenter','-t','1','-m','-u','-i','-n','-p','--root=/proc/1/root','systemctl','show','--no-pager','-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath',UNIT],'task_id':t['id'],'execution_id':r['id'],'task_cleanup':cl,'no_host_write':True,'node_after':{k:api.get_json('/api/nodes/'+NODE_ID).get(k) for k in ('id','status','sync_status')},'native2_independent_stat_evidence':'host001-mapping-diagnostic.json'}
 api.write_json('host001-readonly-smoke-diagnostic.json',record)
 pr=json.loads((B/'progress.json').read_text());pr.update({'actual_at':stamp,'status':'HOST001_READONLY_SMOKE_DIAGNOSTIC_CAPTURED','stage':'host001-proc-root-and-nsenter-readonly-diagnostic','current_operation':'A second shell-only Agent Task recorded /proc/1/root access and still invoked only read-only nsenter/systemctl despite samefile mismatch; no Unit/App/Artifact writes. Independent Native2 stat confirmed bind device/inode equals hostview.','next_action':'Compare child `/proc/1/root` availability with Native2 independent samefile and nsenter stderr. Do not create app until the Agent-level readonly path is correctly understood and smoke passes.','persisted_evidence':sorted(set(pr.get('persisted_evidence',[])+[str((B/'host001-readonly-smoke-diagnostic.json').resolve()),str((B/('task-cleanup-'+t['id']+'.json')).resolve())]))});api.write_json('progress.json',pr)
 print(json.dumps({'status':record['status'],'actual_at':stamp,'markers':markers,'stdout':out,'stderr':r.get('stderr',''),'task_get404':cl['get_after_delete_http']==404}))
if __name__=='__main__':main()
