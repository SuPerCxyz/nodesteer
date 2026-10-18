import hashlib
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')
OUT = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend')
HUB = 'http://192.168.100.209:18080'
TARGET_ID = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
TARGET_NAME = 'qa-repair-20260930-101945-native-4'


def api(token, method, path, body=None):
    data = None if body is None else json.dumps(body, separators=(',', ':')).encode()
    headers = {'Authorization': 'Bearer ' + token}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(HUB + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def command_source():
    return r'''import hashlib,json,os,re,stat
from pathlib import Path
pattern=re.compile(r'(/tmp/qa-resume-20261008-child-[A-Za-z0-9_-]+/daemon\.py)')
def start(pid):
 raw=Path('/proc',str(pid),'stat').read_text(); tail=raw[raw.rfind(')')+2:].split(); return int(tail[19])
def uid(pid):
 for line in Path('/proc',str(pid),'status').read_text().splitlines():
  if line.startswith('Uid:'): return tuple(map(int,line.split()[1:3]))
 raise ValueError('uid')
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
hits=[]
for entry in Path('/proc').iterdir():
 if not entry.name.isdigit(): continue
 try:
  pid=int(entry.name); argv=[x.decode(errors='replace') for x in (entry/'cmdline').read_bytes().split(b'\0') if x]
  matched=next((m.group(1) for arg in argv for m in [pattern.search(arg)] if m),None)
  if not matched: continue
  exe=os.readlink(str(entry/'exe'))
  if exe.endswith(' (deleted)'): exe_sha='deleted'
  else: exe_sha=sha(exe)
  directory=Path(matched).parent
  try: dst=directory.lstat(); directory_present=True; directory_identity=[dst.st_dev,dst.st_ino]; marker_present=(directory/'owner.marker').is_file()
  except FileNotFoundError: directory_present=False; directory_identity=None; marker_present=False
  hits.append({'pid':pid,'starttime':start(pid),'uid':uid(pid)[0],'exe_sha256':exe_sha,'script_path':matched,'directory_present':directory_present,'directory_dev_ino':directory_identity,'owner_marker_present':marker_present})
 except (FileNotFoundError,ProcessLookupError,PermissionError,ValueError,OSError):
  continue
print(json.dumps({'phase':'read-only-owned-prefix-inventory','status':'PASS' if not hits else 'MATCHES_FOUND','candidate_count':len(hits),'candidates':hits,'signals_sent':False,'directories_removed':False},separators=(',',':')))
'''


def main():
    os.umask(0o077)
    info = PRIVATE.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise SystemExit('private_connection_mode_gate_failed')
    private = json.loads(PRIVATE.read_text())
    token = private.get('token')
    if private.get('hub_url') != HUB or not isinstance(token, str) or not token:
        raise SystemExit('private_connection_gate_failed')
    status, raw = api(token, 'GET', '/api/nodes')
    if status != 200:
        raise SystemExit('nodes_get_http_' + str(status))
    rows = json.loads(raw)
    matches = [x for x in rows if x.get('id') == TARGET_ID and x.get('hostname', x.get('name')) == TARGET_NAME]
    if len(matches) != 1:
        raise SystemExit('native4_exact_mapping_gate_failed')
    suffix = uuid.uuid4().hex[:8]
    name = 'qa-resume-20261008-native4-owned-prefix-inventory-' + suffix
    source = command_source()
    command = "python3 - <<'PY'\n" + source.rstrip() + "\nPY"
    command_sha = hashlib.sha256(command.encode()).hexdigest()
    definition = {'name':name,'type':'command','target':{'type':'node','node_ids':[TARGET_ID]},'parameters':[],'param_values':{},'command':command,'timeout':45,'retry':0,'offline_policy':'hub_online_required','enabled':True}
    write = OUT / 'orphan-process-inventory-task-definition.json'
    write.write_text(json.dumps({'task':definition,'command_sha256':command_sha},indent=2)+'\n');write.chmod(0o600)
    status, raw = api(token,'POST','/api/tasks',definition)
    if status != 200:
        raise SystemExit('inventory_task_create_http_' + str(status))
    task=json.loads(raw);task_id=task.get('id')
    if not task_id:
        raise SystemExit('inventory_task_id_missing')
    status, raw=api(token,'GET','/api/tasks/'+urllib.parse.quote(task_id,safe=''))
    task_read=json.loads(raw) if status==200 else {}
    if status!=200 or task_read.get('command')!=command or task_read.get('target',{}).get('node_ids') != [TARGET_ID] or task_read.get('retry') != 0:
        raise SystemExit('inventory_task_readback_mismatch')
    status, raw=api(token,'POST','/api/tasks/'+task_id+'/run',{'params':{}})
    executions=json.loads(raw) if status==200 else []
    if status!=200 or len(executions)!=1 or executions[0].get('node_id')!=TARGET_ID:
        raise SystemExit('inventory_task_run_failed')
    execution_id=executions[0]['id'];deadline=time.monotonic()+60;execution=None
    while time.monotonic()<deadline:
        status, raw=api(token,'GET','/api/executions/'+urllib.parse.quote(execution_id,safe=''))
        execution=json.loads(raw) if status==200 else None
        if execution and execution.get('status') not in ('PENDING','RUNNING'): break
        time.sleep(1)
    if not execution or execution.get('status')!='SUCCESS':
        raise SystemExit('inventory_execution_not_success')
    output=json.loads(execution.get('stdout') or '{}')
    delete_http,_=api(token,'DELETE','/api/tasks/'+task_id)
    after_http,_=api(token,'GET','/api/tasks/'+task_id)
    evidence={'task_id':task_id,'execution_id':execution_id,'task_create_http':200,'task_readback_http':status,'command_sha256':command_sha,'execution_status':execution.get('status'),'output':output,'task_delete_http':delete_http,'task_get_after_delete_http':after_http,'target_id':TARGET_ID,'target_name':TARGET_NAME}
    out=OUT/'orphan-process-inventory.json';out.write_text(json.dumps(evidence,indent=2)+'\n');out.chmod(0o600)
    print(json.dumps({'candidate_count':output.get('candidate_count'),'inventory_status':output.get('status'),'signals_sent':False,'task_delete_http':delete_http,'task_get_after_delete_http':after_http},separators=(',',':')))


if __name__=='__main__':
    main()
