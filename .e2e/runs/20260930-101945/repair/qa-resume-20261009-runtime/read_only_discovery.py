#!/usr/bin/env python3
"""Read-only NodeSteer repair inventory; never writes to Hub or prints credentials."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
OUT = HERE / 'readonly-discovery.json'


def get_json(base, token, path):
    req = Request(base.rstrip('/') + path,
                  headers={'Authorization': 'Bearer ' + token}, method='GET')
    with urlopen(req, timeout=15) as response:
        payload = response.read()
        return response.status, json.loads(payload), hashlib.sha256(payload).hexdigest()


conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
base, token = conn['hub_url'], conn['token']
parts = urlsplit(base)
if parts.port != 18080:
    raise SystemExit('blocked: repair endpoint is not :18080')
if not token:
    raise SystemExit('blocked: repair bearer token missing')

result = {
    'endpoint_guard': {'scheme': parts.scheme, 'host': parts.hostname, 'port': parts.port,
                       'only_repair_18080': True},
    'reads': {},
}

for path, name in [('/api/nodes', 'nodes'), ('/api/tasks', 'tasks'),
                   ('/api/applications', 'applications')]:
    status, value, digest = get_json(base, token, path)
    result['reads'][name] = {'http_status': status, 'payload_sha256': digest,
                             'items': []}
    if name == 'nodes' and isinstance(value, list):
        for n in value:
            result['reads'][name]['items'].append({k: n.get(k) for k in [
                'id', 'name', 'hostname', 'status', 'sync_status', 'arch',
                'deployment_mode', 'host_integration', 'agent_version', 'version',
                'capabilities', 'labels']
                if k in n})
    elif name == 'tasks' and isinstance(value, list):
        for t in value:
            result['reads'][name]['items'].append({k: t.get(k) for k in [
                'id', 'name', 'type', 'enabled', 'target', 'node_ids',
                'revision', 'created_at', 'updated_at'] if k in t})
    elif name == 'applications' and isinstance(value, list):
        for a in value:
            result['reads'][name]['items'].append({k: a.get(k) for k in [
                'id', 'name', 'enabled', 'revision', 'created_at', 'updated_at']
                if k in a})
    else:
        # Do not persist unexpected response shapes or arbitrary fields.
        result['reads'][name]['shape'] = type(value).__name__

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({
    'output': str(OUT.relative_to(Path.cwd())),
    'endpoint': result['endpoint_guard'],
    'read_counts': {k: len(v['items']) for k, v in result['reads'].items()},
    'http_statuses': {k: v['http_status'] for k, v in result['reads'].items()},
}, ensure_ascii=False))
