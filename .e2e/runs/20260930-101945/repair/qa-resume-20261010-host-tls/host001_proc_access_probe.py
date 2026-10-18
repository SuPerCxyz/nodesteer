"""Read-only in-container proc/capability/security-state probe."""
import hashlib,json,pathlib,shlex,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;ST=json.loads((B/'host001-stage.json').read_text());NODE=ST['node_id'];HUB_NODE='611c02a5-1859-4465-878b-d543b7f6fdec'

def main():
 shell='''set -u
while IFS=: read -r k v; do case "$k" in CapEff|CapBnd|CapPrm|NoNewPrivs|Seccomp|Seccomp_filters) printf '%s=%s\\n' "$k" "$v";; esac; done < /proc/self/status
if [ -r /proc/1/root ]; then echo PROC1_ROOT_READABLE=yes; else echo PROC1_ROOT_READABLE=no; fi
if [ -d /proc/1/root/etc/systemd/system ]; then echo PROC1_SYSTEMD_DIR=yes; else echo PROC1_SYSTEMD_DIR=no; fi
if IFS= read -r aa < /proc/self/attr/current; then printf 'APPARMOR_CURRENT=%s\\n' "$aa"; else echo APPARMOR_CURRENT=unreadable; fi
if IFS= read -r y < /proc/sys/kernel/yama/ptrace_scope; then printf 'YAMA_PTRACE_SCOPE=%s\\n' "$y"; else echo YAMA_PTRACE_SCOPE=unreadable; fi
'''
 syntax=subprocess.run(['bash','-n'],input=shell,text=True,capture_output=True,timeout=10)
 if syntax.returncode:raise RuntimeError('proc_probe_shell_syntax_failed')
 name='qa-resume-20261010-host-tls-host001-proc-access-probe-f174890c3846';cmd='/bin/sh -c '+shlex.quote(shell);task={'name':name,'type':'command','target':{'type':'node','node_ids':[NODE]},'parameters':[],'param_values':{},'command':cmd,'timeout':25,'retry':0,'offline_policy':'hub_online_required','enabled':True}
 api.write_json('task-definition-'+name+'.json',{'target_node_id':NODE,'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'parameters':[],'command_persisted_in_evidence':False});st,raw=api.request('POST','/api/tasks',task)
 if st!=200:raise RuntimeError('proc_probe_create_http_'+str(st))
 t=json.loads(raw);rb=api.get_json('/api/tasks/'+t['id'])
 if rb.get('command')!=cmd or rb.get('target',{}).get('node_ids')!=[NODE]:raise RuntimeError('proc_probe_readback_mismatch')
 try:r=api.run_task(rb,timeout=40)
 finally:c=api.cleanup_task(t['id'],NODE)
 if r.get('status')!='SUCCESS':raise RuntimeError('proc_probe_task_failed')
 node=api.get_json('/api/nodes/'+NODE);host=api.get_json('/api/nodes/'+HUB_NODE);stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
 rec={'status':'PASS','actual_at':stamp,'node_id':NODE,'node':{k:node.get(k) for k in ('status','sync_status')},'stdout':r.get('stdout',''),'stderr':r.get('stderr',''),'task_id':t['id'],'execution_id':r['id'],'task_cleanup':c,'native2_node':{k:host.get(k) for k in ('status','sync_status')},'read_only':True}
 api.write_json('host001-proc-access-security-probe.json',rec)
 print(json.dumps({'status':'PASS','actual_at':stamp,'node_online_synced':node.get('status')=='online' and node.get('sync_status')=='synced','stdout':rec['stdout'],'task_get404':c.get('get_after_delete_http')==404},ensure_ascii=False))
if __name__=='__main__':main()
