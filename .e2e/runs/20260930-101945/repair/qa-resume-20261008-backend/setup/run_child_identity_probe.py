import hashlib
import json
import os
import stat
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_env01_05 as api

BASE = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/setup')
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')
HUB = 'http://192.168.100.209:18080'
OLD_IDS = {
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773',
    '91f44866-8a28-435e-bbda-2f2367126c00',
    '611c02a5-1859-4465-878b-d543b7f6fdec',
    '37989297-3bc8-4c0c-b302-7c6efbdba630',
}
api.OUT = BASE


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(path, 0o600)


def load_token():
    st = PRIVATE.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
        raise RuntimeError('private_connection_file_gate_failed')
    data = json.loads(PRIVATE.read_text())
    if data.get('hub_url') != HUB or not isinstance(data.get('token'), str) or not data.get('token'):
        raise RuntimeError('private_connection_gate_failed')
    return data['token']


def peer_snapshot(token, baseline):
    nodes = api.get_json(token, '/api/nodes')
    by = {r.get('id'): r for r in nodes if isinstance(r, dict)}
    if not OLD_IDS.issubset(by):
        raise RuntimeError('original_repair_peer_set_changed')
    old_by = {r['id']: r for r in baseline}
    rows = []
    for node_id in sorted(OLD_IDS):
        row = by[node_id]
        old = old_by[node_id]
        if row.get('status') != 'online' or row.get('sync_status') != 'synced' or row.get('agent_version') != old.get('agent_version'):
            raise RuntimeError('original_repair_peer_health_or_version_changed')
        rows.append({'id':node_id,'name':row.get('hostname'),'status':row.get('status'),'sync_status':row.get('sync_status'),'agent_version':row.get('agent_version')})
    return rows, by


def verify_no_assignments(token, node_id):
    tasks = api.get_json(token, '/api/tasks')
    schedules = api.get_json(token, '/api/schedules')
    apps = api.get_json(token, '/api/applications')
    task_ids = [r.get('id') for r in tasks if isinstance(r, dict) and node_id in r.get('target', {}).get('node_ids', [])]
    schedule_ids = [r.get('id') for r in schedules if isinstance(r, dict) and node_id in r.get('target', {}).get('node_ids', [])]
    app_ids = []
    # The node ID is fresh and must not occur anywhere in a pre-existing Application definition.
    def has(value):
        if isinstance(value, dict): return any(k == node_id or has(v) for k,v in value.items())
        if isinstance(value, list): return any(has(v) for v in value)
        return value == node_id
    app_ids = [r.get('id') for r in apps if isinstance(r, dict) and has(r)]
    if task_ids or schedule_ids or app_ids:
        raise RuntimeError('preexisting_child_assignment_found')
    return {'task_ids':task_ids,'schedule_ids':schedule_ids,'application_ids':app_ids}


def main():
    os.umask(0o077)
    token = load_token()
    if api.get_json(token, '/api/me').get('Role') != 'administrator':
        raise RuntimeError('administrator_role_gate_failed')
    identity = json.loads((BASE / 'stage3-child-start.json').read_text())
    if identity.get('stage') != 'NEW_IDENTITY_ENROLLED_PENDING':
        raise RuntimeError('stage3_identity_checkpoint_gate_failed')
    node_id = identity['node_id']
    if node_id in OLD_IDS:
        raise RuntimeError('child_node_id_reused')
    baseline = json.loads((BASE.parent / 'native4-node-map-preflight-final.json').read_text())['repair_nodes']
    peers_before, by = peer_snapshot(token, baseline)
    child = by.get(node_id)
    if not child or child.get('agent_id') != identity['agent_id'] or child.get('hostname') != identity['node_name'] or child.get('ip') != identity['node_ip'] or child.get('status') != 'online' or child.get('sync_status') != 'synced':
        raise RuntimeError('child_online_identity_api_gate_failed')
    assignments_before = verify_no_assignments(token, node_id)
    template = (BASE / 'child_identity_probe_runtime.py').read_text()
    values = {
        '__OWNED_DIR_JSON__': json.dumps(identity['owned_directory']),
        '__EXPECTED_MANIFEST_SHA256__': json.dumps(identity['owner_manifest_sha256']),
        '__EXPECTED_NODE_ID__': json.dumps(identity['node_id']),
        '__EXPECTED_AGENT_ID__': json.dumps(identity['agent_id']),
        '__EXPECTED_NODE_NAME__': json.dumps(identity['node_name']),
        '__EXPECTED_NODE_IP__': json.dumps(identity['node_ip']),
    }
    source = template
    for key,value in values.items(): source = source.replace(key,value)
    if any(key in source for key in values): raise RuntimeError('identity_probe_source_injection_failed')
    command = api.make_python_command(source)
    if token in command: raise RuntimeError('identity_probe_source_contains_auth_token')
    suffix = hashlib.sha256((node_id + str(time.time_ns())).encode()).hexdigest()[:10]
    name = 'qa-resume-20261008-child-identity-' + suffix
    result = api.create_and_run(token, name, command, node_id, timeout=60)
    cleanup = None
    if result.get('task_id'):
        encoded=urllib.parse.quote(result['task_id'],safe='')
        delete_http,_=api.request(token,'DELETE','/api/tasks/'+encoded)
        get404=api.route_status(token,'/api/tasks/'+encoded)
        cleanup={'task_id':result['task_id'],'delete_http':delete_http,'get_after_delete_http':get404}
    write(BASE / (name + '-identity-result.json'), {'task_id':result.get('task_id'),'execution_id':result.get('execution_id'),'execution_status':result.get('status'),'command_sha256':result.get('command_sha256'),'target_node_id':node_id,'retry':0,'schedule_present':False,'output':result.get('output'),'task_cleanup':cleanup,'definition_evidence':name+'-definition.json','created_evidence':name+'-created.json','execution_evidence':name+'-execution.json'})
    output=result.get('output')
    if result.get('status')!='SUCCESS' or not isinstance(output,dict) or output.get('status')!='PASS' or not cleanup or cleanup['delete_http']!=200 or cleanup['get_after_delete_http']!=404:
        write(BASE/'checkpoint.json',{'stage':'CHILD_IDENTITY_PROOF_BLOCKED','node_id':node_id,'identity_task_id':result.get('task_id'),'output':output,'task_cleanup':cleanup,'child_cleanup_required':True,'updated_at_epoch':time.time()})
        raise SystemExit(2)
    deadline=time.monotonic()+90
    last=None
    while time.monotonic()<deadline:
        last=api.get_json(token,'/api/nodes/'+urllib.parse.quote(node_id,safe=''))
        if last.get('status')=='online' and last.get('sync_status')=='synced': break
        time.sleep(2)
    if not last or last.get('status')!='online' or last.get('sync_status')!='synced':
        raise RuntimeError('child_not_synced_after_identity_task_cleanup')
    peers_after,_=peer_snapshot(token,baseline)
    assignments_after=verify_no_assignments(token,node_id)
    healthz=api.route_status(token,'/healthz'); readyz=api.route_status(token,'/readyz')
    if healthz!=200 or readyz!=200: raise RuntimeError('repair_hub_health_after_child_identity_probe_failed')
    record={'stage':'CHILD_SETUP_COMPLETE_PENDING_ROOT_REVIEW','node_id':node_id,'agent_id':identity['agent_id'],'node_name':identity['node_name'],'node_ip':identity['node_ip'],'child_status':last.get('status'),'sync_status':last.get('sync_status'),'identity_probe':output,'identity_task_id':result['task_id'],'identity_execution_id':result['execution_id'],'identity_command_sha256':result['command_sha256'],'identity_task_deleted_get404':True,'existing_assignments_before_and_after':{'before':assignments_before,'after':assignments_after},'old_repair_peers_before_and_after':{'before':peers_before,'after':peers_after},'healthz_http':healthz,'readyz_http':readyz,'child_and_supervisor_still_owned':True,'primary_8080_accessed':False,'fault_injection_started':False,'credential_revocation_started':False,'reenrollment_started':False,'updated_at_epoch':time.time()}
    write(BASE/'stage4-setup-complete.json',record)
    ledger=json.loads((BASE/'owner-ledger.json').read_text()); ledger.update({'stage':'CHILD_SETUP_COMPLETE_PENDING_ROOT_REVIEW','child_status':last.get('status'),'child_sync_status':last.get('sync_status'),'identity_task_id':result['task_id'],'identity_task_deleted_get404':True,'processes_kept_for_root_review':True})
    write(BASE/'owner-ledger.json',ledger)
    write(BASE/'checkpoint.json',{'stage':'CHILD_SETUP_COMPLETE_PENDING_ROOT_REVIEW','node_id':node_id,'agent_id':identity['agent_id'],'child_processes_alive':True,'identity_matches':True,'env03':'PASS','updated_at_epoch':time.time()})
    write(BASE/'resume-hint.json',{'next_step':'Await Root confirmation that Luna Workflows metadata mutation is complete; retain exact child/supervisor processes and owner manifest. Do not start bad-SHA, RemoteTTL, or reenrollment cases before Root confirmation.'})
    print(json.dumps({'status':'PASS','node_id':node_id,'agent_id':identity['agent_id'],'node_status':last.get('status'),'sync_status':last.get('sync_status'),'identity_db_matches':True,'env03':'PASS','all_four_original_peers_healthy':True,'healthz':healthz,'readyz':readyz,'identity_task_deleted_get404':True},separators=(',',':')))


if __name__=='__main__':
    try: main()
    except SystemExit: raise
    except Exception as error:
        print(json.dumps({'status':'BLOCKED','code':str(error)},separators=(',',':')))
        raise SystemExit(2)
