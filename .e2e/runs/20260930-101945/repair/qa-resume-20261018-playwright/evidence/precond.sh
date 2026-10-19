#!/usr/bin/env bash
set -u
cd /tmp/opencode/nodesteer-pw-20261018
: > precond.log
python3 - <<'PY' > precond-task.json
import json, subprocess, uuid, time
cred=json.load(open('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json'))
BASE='http://192.168.100.209:18080'
tok=json.loads(subprocess.run(['curl','-s','-X','POST',BASE+'/api/login','-H','Content-Type: application/json',
  '-d',json.dumps({'username':cred['admin_username'],'password':cred['admin_password']})],capture_output=True,text=True).stdout)['token']
name='qa-pw-20261018-precond-task-'+uuid.uuid4().hex[:8]
payload={'name':name,'type':'command','command':'echo precond','target':{'type':'node','node_ids':['91f44866-8a28-435e-bbda-2f2367126c00']},
         'enabled':True,'timeout':30,'retry':0,'offline_policy':'hub_online_required'}
r=subprocess.run(['curl','-s','-X','POST',BASE+'/api/tasks','-H','Content-Type: application/json',
  '-H','Authorization: Bearer '+tok,'-d',json.dumps(payload)],capture_output=True,text=True)
body=json.loads(r.stdout)
print(json.dumps({'id':body.get('id'),'name':name,'token':tok}))
PY
TASK_ID=$(python3 -c "import json;print(json.load(open('precond-task.json'))['id'])")
echo "fixture task: $TASK_ID" >> precond.log
echo "=== p23-02 with task present $(date -Is)" >> precond.log
timeout 600 python3 run-pw.py precond-p23-02 tests/p23-detail-layout.spec.ts -g "P23-02" >> precond.log 2>&1
echo "=== rbac-08 with task present $(date -Is)" >> precond.log
timeout 600 python3 run-pw.py precond-rbac-08 tests/rbac.spec.ts -g "RBAC-08" >> precond.log 2>&1
echo "=== cleanup $(date -Is)" >> precond.log
python3 - "$TASK_ID" <<'PY' >> precond.log 2>&1
import json,subprocess,sys
cred=json.load(open('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json'))
BASE='http://192.168.100.209:18080'; tid=sys.argv[1]
tok=json.loads(subprocess.run(['curl','-s','-X','POST',BASE+'/api/login','-H','Content-Type: application/json',
  '-d',json.dumps({'username':cred['admin_username'],'password':cred['admin_password']})],capture_output=True,text=True).stdout)['token']
d=subprocess.run(['curl','-s','-o','/dev/null','-w','%{http_code}','-X','DELETE',BASE+'/api/tasks/'+tid,'-H','Authorization: Bearer '+tok],capture_output=True,text=True)
g=subprocess.run(['curl','-s','-o','/dev/null','-w','%{http_code}',BASE+'/api/tasks/'+tid,'-H','Authorization: Bearer '+tok],capture_output=True,text=True)
print(json.dumps({'task_id':tid,'delete_http':d.stdout.strip(),'get_http_after_delete':g.stdout.strip()}))
PY
echo "=== PRECOND DONE $(date -Is)" >> precond.log
