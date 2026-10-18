import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

OUT = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
base = conn['hub_url'].rstrip('/')
if urlsplit(base).port != 18080:
    raise SystemExit('repair_endpoint_guard_failed')
# Existing authenticated session is the private repair Bearer, never printed or persisted.
token = conn['token']
opener = build_opener(ProxyHandler({}))

def get(path):
    req = Request(base + path, headers={'Authorization': 'Bearer ' + token})
    try:
        with opener.open(req, timeout=12) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read(2048)

def parse_json(status, raw, label):
    if status != 200:
        raise SystemExit(label + '_http_' + str(status))
    try:
        return json.loads(raw)
    except Exception:
        raise SystemExit(label + '_invalid_json')

me_status, me_raw = get('/api/me')
me = parse_json(me_status, me_raw, 'me')
nodes_status, nodes_raw = get('/api/nodes')
nodes_value = parse_json(nodes_status, nodes_raw, 'nodes')
if isinstance(nodes_value, dict):
    nodes = nodes_value.get('nodes', nodes_value.get('items', []))
else:
    nodes = nodes_value
if not isinstance(nodes, list):
    raise SystemExit('nodes_shape_invalid')
by_id = {str(n.get('id')): n for n in nodes if isinstance(n, dict)}
child_id = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'
child_expected_agent = 'agent-ab15a133-73cd-4025-8c97-f2ff37e000f5'
parent_id = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
original_ids = {
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773',
    '37989297-3bc8-4c0c-b302-7c6efbdba630',
    '611c02a5-1859-4465-878b-d543b7f6fdec',
    '91f44866-8a28-435e-bbda-2f2367126c00',
}
artifact_id = '7b8929ed-c880-46f1-90e6-a5e9c0dab096'
artifact_status, artifact_raw = get('/api/artifacts/' + artifact_id)
artifact = parse_json(artifact_status, artifact_raw, 'artifact')
if isinstance(artifact, dict) and isinstance(artifact.get('artifact'), dict):
    artifact = artifact['artifact']
# Detect an existing colliding task name without exposing commands or unrelated task bodies.
tasks_status, tasks_raw = get('/api/tasks')
tasks_value = parse_json(tasks_status, tasks_raw, 'tasks')
if isinstance(tasks_value, dict):
    tasks = tasks_value.get('tasks', tasks_value.get('items', []))
else:
    tasks = tasks_value
if not isinstance(tasks, list):
    raise SystemExit('tasks_shape_invalid')
nonce = 'auth-artifact-agent-' + str(__import__('uuid').uuid4())
name_collision = any(isinstance(t, dict) and t.get('name') == nonce for t in tasks)
original = []
for node_id in sorted(original_ids):
    n = by_id.get(node_id)
    original.append({
        'id': node_id,
        'present': n is not None,
        'status': n.get('status') if n else None,
        'sync_status': n.get('sync_status') if n else None,
    })
child = by_id.get(child_id)
parent = by_id.get(parent_id)
record = {
    'captured_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'hub_port': 18080,
    'admin_role': (me.get('role') or me.get('Role')) if isinstance(me, dict) else None,
    'admin_me_status': me_status,
    'parent': {k: parent.get(k) for k in ('id', 'status', 'sync_status', 'agent_id')} if parent else None,
    'child': {k: child.get(k) for k in ('id', 'status', 'sync_status', 'agent_id')} if child else None,
    'original_four': original,
    'artifact': {k: artifact.get(k) for k in ('id', 'size', 'sha256')} if isinstance(artifact, dict) else None,
    'task_name_collision': name_collision,
    'existing_task_count': len(tasks),
    'secrets_emitted': False,
}
record['guards_passed'] = bool(
    record['admin_me_status'] == 200 and record['admin_role'] in ('admin', 'administrator')
    and parent and parent.get('id') == parent_id and parent.get('status') == 'online' and parent.get('sync_status') == 'synced'
    and child and child.get('id') == child_id and child.get('agent_id') == child_expected_agent
    and all(x['present'] and x['status'] == 'online' and x['sync_status'] == 'synced' for x in original)
    and isinstance(record['artifact'], dict)
    and record['artifact'] == {'id': artifact_id, 'size': 1601698, 'sha256': '9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed'}
    and not name_collision
)
record['proposed_task_name'] = nonce
(OUT / 'agent-branch-preflight.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({k: record[k] for k in ('captured_at_utc', 'hub_port', 'admin_role', 'parent', 'child', 'original_four', 'artifact', 'task_name_collision', 'guards_passed')}, indent=2))
if not record['guards_passed']:
    raise SystemExit('preflight_guard_failed')
