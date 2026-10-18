"""Read-only current Docker server version+commit and owner/status recheck."""
import hashlib,json,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());PRE=json.loads((B/'host-execution-preflight.json').read_text());LIVE=json.loads((B/'host001-container-running-recovered.json').read_text());NODE=P['target_native2_node_id'];CHILD=LIVE['host001_node_id'];CID=LIVE['container']['id'];RUN=P['identifiers']['run']

def main():
 src=r'''import hashlib,json,os,shutil,stat,subprocess
from pathlib import Path
NODE=%r;CHILD=%r;CID=%r;RUN=%r;EXPECT=%s

def run(a):
 try:
  p=subprocess.run(a,text=True,capture_output=True,timeout=20);return {'rc':p.returncode,'out':p.stdout.strip(),'err_chars':len(p.stderr),'err_type':None}
 except Exception as e:return {'rc':-1,'out':'','err_chars':0,'err_type':type(e).__name__}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(p/'exe');return {'pid':pid,'starttime':start,'uid':uid,'exe':exe,'exe_sha256':sha(p/'exe'),'cgroup':(p/'cgroup').read_text().strip()}
p=proc(EXPECT['pid'])
if any(p[k]!=EXPECT[k] for k in ('pid','starttime','uid','exe_sha256','cgroup')):raise RuntimeError('native2_process_owner_changed')
version=run(['docker','version','--format','{{json .Server}}']);security=run(['docker','info','--format','{{json .SecurityOptions}}'])
if version['rc'] or security['rc']:raise RuntimeError('docker_server_metadata_unavailable')
server=json.loads(version['out']);opts=json.loads(security['out']);props=run(['systemctl','show','nodesteer-agent-repair-20260930-101945-native-2.service','-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath']);kv=dict(x.split('=',1) for x in props['out'].splitlines() if '=' in x)
if kv.get('ActiveState')!='active' or int(kv.get('MainPID','0'))!=p['pid']:raise RuntimeError('native2_unit_owner_changed')
ci=run(['docker','container','inspect',CID])
if ci['rc']:raise RuntimeError('owned_host001_container_missing')
x=json.loads(ci['out'])[0];cfg=x.get('Config') or {};state=x.get('State') or {};labels=cfg.get('Labels') or {}
if x.get('Id')!=CID or labels.get('nodesteer-e2e-run')!=RUN or labels.get('nodesteer.e2e.role')!='host001' or state.get('Running') is not True:raise RuntimeError('owned_host001_identity_or_running_state_changed')
print(json.dumps({'status':'PASS','docker_server':{k:server.get(k) for k in ('Version','GitCommit','ApiVersion','MinAPIVersion','BuildTime','Os','Arch','KernelVersion')},'docker_security_options':opts,'native2_agent':p,'native2_unit':kv,'host001_container':{'id':x.get('Id'),'name':x.get('Name'),'labels':labels,'running':state.get('Running'),'pid':state.get('Pid'),'apparmor_profile':x.get('AppArmorProfile'),'security_opt':(x.get('HostConfig') or {}).get('SecurityOpt')},'docker_cli':run(['docker','--version'])['out'],'read_only':True},separators=(',',':')))
'''%(NODE,CHILD,CID,RUN,json.dumps(PRE['remote']['native2_agent'],separators=(',',':')))
 cmd='python3 - <<\'PY\'\n'+src.rstrip()+'\nPY';api.validate_command(cmd,'host-docker-server-owner-recheck');task=api.create_task('qa-resume-20261010-host-tls-docker-server-owner-recheck-f174890c3846',cmd,NODE)
 try:r=api.run_task(task,timeout=50)
 finally:c=api.cleanup_task(task['id'],NODE)
 if r.get('status')!='SUCCESS':raise RuntimeError('docker_owner_recheck_task_failed')
 remote=json.loads(r.get('stdout') or '');node=api.get_json('/api/nodes/{}'.format(NODE));child=api.get_json('/api/nodes/{}'.format(CHILD));stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
 peers=[]
 for x in json.loads((B.parent/'qa-resume-20261009-backend'/'preflight.json').read_text())['original_repair_nodes']:
  q=api.get_json('/api/nodes/'+x['node_id']);peers.append({'node_id':x['node_id'],'status':q.get('status'),'sync_status':q.get('sync_status')})
 if any(x['status']!='online' or x['sync_status']!='synced' for x in peers):raise RuntimeError('original_nodes_unhealthy')
 out={'status':'PASS','actual_at':stamp,'repair_authority':'http://192.168.100.209:18080','docker_server':remote['docker_server'],'docker_security_options':remote['docker_security_options'],'native2':{k:node.get(k) for k in ('id','status','sync_status')},'host001':{k:child.get(k) for k in ('id','agent_id','hostname','ip','status','sync_status')},'container':remote['host001_container'],'task_id':task['id'],'execution_id':r['id'],'task_cleanup':c,'original_four_nodes':peers}
 api.write_json('host-docker-server-owner-recheck.json',out)
 p=B/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'status':'APPARMOR_POLICY_SOURCE_RESEARCH_RUNNING','stage':'docker-server-gitcommit-owner-recheck-complete','current_operation':'Fresh read-only Native2 Task obtained actual Docker Server.Version/GitCommit/ApiVersion/SecurityOptions and confirmed Native2 + Host001 Node/container owners and states. No resource or policy lifecycle change.','next_action':'Retrieve exact Moby profile source at the returned GitCommit from official source only; save narrow excerpt/hash and assess whether a faithful proc-root allowance can be proposed.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+[str((B/'host-docker-server-owner-recheck.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',v)
 print(json.dumps({'status':'PASS','at':stamp,'docker_server':remote['docker_server'],'security_options':remote['docker_security_options'],'native2':out['native2'],'host001':out['host001'],'container':remote['host001_container'],'task_get404':c.get('get_after_delete_http')==404},ensure_ascii=False))
if __name__=='__main__':main()
