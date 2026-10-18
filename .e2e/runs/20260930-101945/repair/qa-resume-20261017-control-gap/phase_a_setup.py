#!/usr/bin/env python3
"""Phase A (idempotent): pre-state + owned fixture creation (qa-cgap-1cdad4fe)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'evidence' / 'tools'))
import cgap_lib as L  # noqa: E402

hub = L.Hub()
hub.login()

state = {'phase': 'A', 'nonce': L.NONCE, 'owner': L.OWNER, 'at_utc': datetime.now(timezone.utc).isoformat()}


def find_by_name(path, name, version=None):
    c, b, _ = hub.get(path)
    items = b if isinstance(b, list) else (b.get('items') if isinstance(b, dict) else [])
    for x in items or []:
        if x.get('name') == name and (version is None or x.get('version') == version):
            return x
    return None


# ---- nodes ----
code, nodes, _ = hub.get('/api/nodes')
assert code == 200, code
nodes = nodes.get('nodes', nodes) if isinstance(nodes, dict) else nodes
state['nodes'] = [{'id': n['id'], 'hostname': n.get('hostname'), 'status': n.get('status')} for n in nodes]
native1 = next((n for n in nodes if 'native-1' in (n.get('hostname') or '')), None)
assert native1, 'native-1 node not found'
state['native1_id'] = native1['id']

# ---- pre-state counts + watermarks ----
def total(path):
    code, body, hdrs = hub.get(path)
    return {'code': code, 'x_total_count': hdrs.get('X-Total-Count'),
            'first_id': (body[0]['id'] if isinstance(body, list) and body else (body.get('id') if isinstance(body, dict) else None))}

state['pre_counts'] = {
    'tasks': total('/api/tasks'),
    'groups': total('/api/groups'),
    'schedules': total('/api/schedules'),
    'scripts': total('/api/scripts'),
    'applications': total('/api/applications'),
    'artifacts': total('/api/artifacts'),
    'executions': total('/api/executions?limit=1'),
    'audit': total('/api/audit?limit=1'),
    'users': total('/api/users'),
}

# ---- fixture: script ----
name = L.PREFIX + 'script'
script = find_by_name('/api/scripts', name)
if not script:
    code, script, _ = hub.post('/api/scripts', {
        'name': name,
        'description': 'owned control-gap fixture (cancel edit test)',
        'interpreter': 'bash',
        'content': '#!/bin/bash\necho ' + L.PREFIX + 'script-ok\n',
        'parameters': [], 'environment': {}, 'working_dir': '/tmp', 'timeout': 30, 'enabled': True,
    })
    assert code == 200, (code, script)
state['script'] = {'id': script['id'], 'name': script['name'], 'revision': script.get('revision'), 'sha256': script.get('sha256')}

# ---- fixture: task ----
name = L.PREFIX + 'task'
task = find_by_name('/api/tasks', name)
if not task:
    code, task, _ = hub.post('/api/tasks', {
        'name': name, 'type': 'command',
        'target': {'type': 'node', 'node_ids': [native1['id']]},
        'parameters': [{'name': 'msg', 'type': 'string', 'required': True, 'description': 'owned run-page param'}],
        'command': 'echo ' + L.PREFIX + 'task-ok',
        'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'run_user': 'root', 'enabled': True,
    })
    assert code == 200, (code, task)
state['task'] = {'id': task['id'], 'name': task['name'], 'revision': task.get('revision')}

# ---- fixture: group ----
name = L.PREFIX + 'group'
group = find_by_name('/api/groups', name)
if not group:
    code, group, _ = hub.post('/api/groups', {
        'name': name, 'description': 'owned control-gap fixture', 'type': 'static', 'members': [native1['id']],
    })
    assert code == 200, (code, group)
state['group'] = {'id': group['id'], 'name': group['name'], 'members': group.get('members')}

# ---- fixture: artifact1 ----
name, version = L.PREFIX + 'art', '1.0.0'
art = find_by_name('/api/artifacts', name, version)
if not art:
    code, art = hub.upload_artifact(name, version, 'qa-cgap-1cdad4fe-art.bin', b'QA-CGAP-ARTIFACT-1CDAD4FE\n' * 8)
    assert code == 200, (code, art)
state['artifact'] = {'id': art['id'], 'name': art['name'], 'version': art['version'], 'sha256': art['sha256']}

# ---- fixture: application referencing artifact1 ----
name = L.PREFIX + 'app'
app = find_by_name('/api/applications', name)
if not app:
    code, app, _ = hub.post('/api/applications', {
        'name': name, 'description': 'owned control-gap fixture (inline upload / cancel test)',
        'artifact_id': art['id'], 'version': art['version'],
        'binary_path': '/usr/local/bin/' + L.PREFIX + 'app',
        'arguments': [], 'environment': {}, 'config': '', 'unit_name': L.PREFIX + 'app.service',
    })
    assert code == 200, (code, app)
state['application'] = {'id': app['id'], 'name': app['name'], 'artifact_id': app.get('artifact_id'), 'version': app.get('version'), 'revision': app.get('revision')}

# ---- verify GET readback for each ----
verify = {}
for kind, path in (('script', '/api/scripts/'), ('task', '/api/tasks/'), ('group', '/api/groups/'),
                   ('artifact', '/api/artifacts/'), ('application', '/api/applications/')):
    fid = state[kind]['id']
    c, b, _ = hub.get(path + fid)
    verify[kind] = {'code': c, 'ok': c == 200 and isinstance(b, dict) and b.get('id') == fid}
state['create_verify'] = verify
assert all(v['ok'] for v in verify.values()), verify

state['hub_api_calls'] = hub.calls
L.save('phase-a-setup.json', state)
print(json.dumps({'ok': True,
                  'fixtures': {k: state[k] for k in ('script', 'task', 'group', 'artifact', 'application')},
                  'native1': native1['id'],
                  'pre_counts': {k: v['x_total_count'] for k, v in state['pre_counts'].items()}},
                 ensure_ascii=False, indent=1))
