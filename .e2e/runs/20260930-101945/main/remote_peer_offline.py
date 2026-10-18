"""只暂停本轮新增Native2；验证真实remote-online条件后无条件恢复。"""
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
RUN = ROOT.parent
CFG = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json').read_text())
PLAN = json.loads((RUN / 'backend/continuation/sys27-plan.json').read_text())
PEER = 'd77e2440-919f-4431-a488-733817dcd809'
TARGET = '811d72ef-75f8-47ba-84de-8cb1e6974315'
assert PLAN['peer_node_id'] == PEER and PLAN['target_node_id'] == TARGET
node = next(n for n in CFG['nodes'] if n['id'] == PEER)
assert node['name'] == 'e2e-20260930-101945-nodesteer-agent-2'
assert node['service'] == 'nodesteer-agent.service'
SESSION = requests.Session()
login = SESSION.post(CFG['base_url'] + '/api/login', json={'username': CFG['admin_username'], 'password': CFG['admin_password']}, timeout=15)
assert login.status_code == 200
SESSION.headers['Authorization'] = 'Bearer ' + login.json()['token']
result = {'case_id': 'MAIN-REMOTE-PHYSICAL-PEER-OFFLINE', 'classification': 'REAL_API_AGENT',
          'time': datetime.now(timezone.utc).isoformat(), 'status': 'IN_PROGRESS',
          'peer_node_id': PEER, 'target_node_id': TARGET, 'task_id': PLAN['task_id']}


def save():
    (ROOT / 'remote-peer-offline-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


def guest(node_id, script):
    proc = subprocess.run([sys.executable, str(RUN / 'environment/guest-exec.py'), '--node', node_id, '--timeout', '25'],
                          input=script, text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, {'node_id': node_id, 'exit_code': proc.returncode, 'stderr': proc.stderr[:500]}
    return proc.stdout.strip()


def get(path):
    response = SESSION.get(CFG['base_url'] + path, timeout=15)
    assert response.status_code == 200, {'path': path, 'http': response.status_code}
    return response.json()


def wait_peer(status, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        peer = get('/api/nodes/' + PEER)
        if peer['status'] == status and (status != 'online' or peer.get('sync_status') == 'synced'):
            return {k: peer.get(k) for k in ['id', 'status', 'sync_status', 'agent_id', 'agent_version', 'global_revision']}
        time.sleep(0.5)
    raise TimeoutError('peer did not reach ' + status)


try:
    result['baseline'] = wait_peer('online', 15)
    result['peer_service_before'] = guest(PEER, 'systemctl show nodesteer-agent.service -p ActiveState -p MainPID\n')
    marker = shlex.quote(PLAN['expected_offline_marker_absent'])
    result['marker_before'] = guest(TARGET, f'if [ -e {marker} ]; then echo PRESENT; else echo ABSENT; fi\n')
    assert result['marker_before'] == 'ABSENT'
    result['stop'] = guest(PEER, 'systemctl stop nodesteer-agent.service\nsystemctl show nodesteer-agent.service -p ActiveState -p MainPID\n')
    result['physical_offline_peer'] = wait_peer('offline', 20)
    save()
    response = SESSION.post(CFG['base_url'] + PLAN['manual_run']['path'], json=PLAN['manual_run']['body'], timeout=15)
    result['manual_run_http'] = response.status_code
    assert response.status_code == 200
    body = response.json()
    result['manual_run_response'] = body
    entries = body if isinstance(body, list) else body.get('executions', [body])
    assert len(entries) == 1
    execution_id = entries[0].get('id') or entries[0].get('execution_id')
    assert execution_id
    end = time.monotonic() + 25
    while time.monotonic() < end:
        execution = get('/api/executions/' + execution_id)
        if execution['status'] not in ['PENDING', 'RUNNING']:
            break
        time.sleep(0.5)
    result['execution'] = execution
    result['marker_after'] = guest(TARGET, f'if [ -e {marker} ]; then echo PRESENT; else echo ABSENT; fi\n')
    assert execution['status'] == PLAN['expected_offline_terminal'] and result['marker_after'] == 'ABSENT'
    result['status'] = 'PASS'
except Exception as error:
    result['status'] = 'FAILED_ATTEMPT'
    result['error'] = str(error)[:800]
    raise
finally:
    try:
        result['restore_service'] = guest(PEER, 'systemctl start nodesteer-agent.service\nsystemctl show nodesteer-agent.service -p ActiveState -p MainPID\n')
        result['restored_peer'] = wait_peer('online', 45)
    finally:
        result['owned_session_logout_http'] = SESSION.post(CFG['base_url'] + '/api/logout', timeout=15).status_code
        save()
        print(json.dumps({'status': result['status'], 'execution_id': result.get('execution', {}).get('id'),
                          'terminal': result.get('execution', {}).get('status'), 'marker_after': result.get('marker_after'),
                          'peer_restored': result.get('restored_peer', {}).get('status')}, ensure_ascii=False), flush=True)
