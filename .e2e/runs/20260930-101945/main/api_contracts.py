"""当前实现的逐端点读取、方法与错误 JSON 契约；无定义或配置写入。"""
import json
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json').read_text())
ROWS = []
MISSING = 'qa-20260930-101945-main-absent-' + uuid.uuid4().hex


def check(method, path, expected, body=None, response_kind='error'):
    r = requests.request(method, CFG['base_url'] + path, headers={'Authorization': 'Bearer ' + CFG['admin_token'], 'Content-Type': 'application/json'}, data=body, timeout=20)
    try:
        data = r.json()
        valid_shape = isinstance(data, dict) and isinstance(data.get('error'), str) if response_kind == 'error' else data is None or isinstance(data, list)
    except ValueError:
        data, valid_shape = None, False
    row = {'case_id': 'MAIN-CONTRACT-' + str(len(ROWS) + 1).zfill(3), 'classification': 'REAL_API',
           'status': 'PASS' if r.status_code == expected and valid_shape else 'FAIL',
           'method': method, 'path': path, 'expected_http': expected, 'actual_http': r.status_code,
           'response_kind': response_kind, 'shape_matches': valid_shape,
           'error': data.get('error') if isinstance(data, dict) else None}
    ROWS.append(row)
    (ROOT / 'api-contract-results.json').write_text(json.dumps(ROWS, ensure_ascii=False, indent=2) + '\n')
    print(method, path.split('/')[2], row['status'], r.status_code, flush=True)


for kind in ['nodes', 'groups', 'scripts', 'tasks', 'schedules', 'artifacts', 'applications', 'executions', 'transfers']:
    check('GET', '/api/' + kind + '/' + MISSING, 404)
check('DELETE', '/api/nodes/' + MISSING, 404)
for kind in ['nodes', 'groups', 'scripts', 'tasks', 'schedules', 'artifacts', 'applications', 'executions', 'transfers', 'users']:
    check('PATCH', '/api/' + kind, 405)
for kind in ['groups', 'scripts', 'tasks', 'schedules', 'artifacts', 'applications', 'executions']:
    check('PATCH', '/api/' + kind + '/' + MISSING, 405)
check('GET', '/api/users/' + MISSING, 405)
for kind in ['groups', 'scripts', 'tasks', 'schedules', 'applications']:
    check('PUT', '/api/' + kind + '/' + MISSING, 400, '{')
check('PUT', '/api/users/' + MISSING, 400, '{')
check('PUT', '/api/users/' + MISSING, 400, '{}')
check('POST', '/api/nodes', 400, '{')
check('POST', '/api/nodes', 400, '{}')
check('PUT', '/api/nodes/811d72ef-75f8-47ba-84de-8cb1e6974315', 400, '{')
check('POST', '/api/nodes/upgrade', 400, '{')
check('PUT', '/api/settings', 400, '{')
check('POST', '/api/applications/' + MISSING + '/assign', 400, '{')
check('POST', '/api/applications/' + MISSING + '/deploy', 400, '{')
check('POST', '/api/executions/' + MISSING + '/logs', 405)
check('GET', '/api/executions/' + MISSING + '/cancel', 405)
check('GET', '/api/scripts/' + MISSING + '/revisions/not-a-revision', 400)
check('GET', '/api/scripts/' + MISSING + '/revisions/1', 404)
# 这些子资源是集合，空集合的200是当前契约，不能统一断言404。
for path in ['/api/scripts/' + MISSING + '/revisions', '/api/executions/' + MISSING + '/logs',
             '/api/applications/' + MISSING + '/state', '/api/applications/' + MISSING + '/executions']:
    check('GET', path, 200, response_kind='empty-list')
