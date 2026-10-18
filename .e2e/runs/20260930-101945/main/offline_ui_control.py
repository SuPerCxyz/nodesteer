"""与Web私密flag协调短暂Native2离线；始终恢复自己控制的服务。"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

parser = argparse.ArgumentParser()
parser.add_argument('--task-id', required=True)
args = parser.parse_args()
ROOT = Path(__file__).resolve().parent
RUN = ROOT.parent
PRIVATE = Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
CFG = json.loads((PRIVATE / 'connection.json').read_text())
PEER = 'd77e2440-919f-4431-a488-733817dcd809'
FLAG = PRIVATE / 'runweb/web-offline-ui-flag.json'
COMPLETED = PRIVATE / 'runweb/web-offline-ui-completed.json'
session = requests.Session()
login = session.post(CFG['base_url'] + '/api/login', json={'username': CFG['admin_username'], 'password': CFG['admin_password']}, timeout=12)
assert login.status_code == 200
session.headers['Authorization'] = 'Bearer ' + login.json()['token']
result = {'time': datetime.now(timezone.utc).isoformat(), 'classification': 'PHYSICAL_CONTROL_FOR_REAL_UI_TEST',
          'task_id': args.task_id, 'peer_node_id': PEER, 'status': 'IN_PROGRESS'}
stopped = False


def guest(script):
    proc = subprocess.run([sys.executable, str(RUN / 'environment/guest-exec.py'), '--node', PEER, '--timeout', '20'],
                          input=script, text=True, capture_output=True, timeout=25)
    assert proc.returncode == 0
    return proc.stdout.strip()


def wait_peer(status, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        response = session.get(CFG['base_url'] + '/api/nodes/' + PEER, timeout=12)
        assert response.status_code == 200
        peer = response.json()
        if peer['status'] == status and (status != 'online' or peer['sync_status'] == 'synced'):
            return {k: peer.get(k) for k in ['id', 'status', 'sync_status', 'agent_id', 'agent_version', 'global_revision']}
        time.sleep(0.5)
    raise TimeoutError('peer state did not become ' + status)


try:
    response = session.get(CFG['base_url'] + '/api/tasks/' + args.task_id, timeout=12)
    result['task_precondition_http'] = response.status_code
    assert response.status_code == 200
    assert response.json()['target']['node_ids'] == [PEER]
    result['baseline'] = wait_peer('online', 10)
    result['service_before'] = guest('systemctl show nodesteer-agent.service -p ActiveState -p MainPID\n')
    stopped = True
    result['service_stopped'] = guest('systemctl stop nodesteer-agent.service\nsystemctl show nodesteer-agent.service -p ActiveState -p MainPID\n')
    result['physical_offline'] = wait_peer('offline', 15)
    FLAG.write_text(json.dumps({'node_id': PEER, 'phase': 'offline-ready'}) + '\n')
    FLAG.chmod(0o600)
    flag_time = FLAG.stat().st_mtime_ns
    end = time.monotonic() + 55
    while time.monotonic() < end:
        if COMPLETED.exists() and COMPLETED.stat().st_mtime_ns >= flag_time:
            done = json.loads(COMPLETED.read_text())
            if done.get('phase') == 'completed':
                result['web_completion'] = done
                break
        time.sleep(0.5)
    assert 'web_completion' in result, 'UI runner did not finish within physical window'
    result['status'] = 'CONTROL_WINDOW_COMPLETED'
except Exception as error:
    result.update(status='FAILED_ATTEMPT', error_type=type(error).__name__, error=str(error)[:500])
    raise
finally:
    try:
        if stopped:
            result['service_restored'] = guest('systemctl start nodesteer-agent.service\nsystemctl show nodesteer-agent.service -p ActiveState -p MainPID\n')
        result['restored_peer'] = wait_peer('online', 45)
    finally:
        result['owned_session_logout_http'] = session.post(CFG['base_url'] + '/api/logout', timeout=12).status_code
        (ROOT / 'offline-task-ui-physical-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'offline': result.get('physical_offline', {}).get('status'),
                          'peer_restored': result.get('restored_peer', {}).get('status'), 'completion_received': 'web_completion' in result}, ensure_ascii=False), flush=True)
