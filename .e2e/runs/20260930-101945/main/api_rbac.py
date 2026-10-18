"""本轮公开接口与真实角色矩阵；凭据仅来自私密 JSON。"""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
PRIVATE = Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
CFG = json.loads((PRIVATE / 'connection.json').read_text())
BASE = CFG['base_url']
TOKEN = CFG['admin_token']
RESULTS = []


def record(case_id, title, response, expected, classification='REAL_API', detail=None):
    row = {'case_id': case_id, 'title': title, 'classification': classification,
           'status': 'PASS' if response.status_code == expected else 'FAIL',
           'expected_http': expected, 'actual_http': response.status_code,
           'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'detail': detail,
           'request': {'method': response.request.method,
                       'url': response.request.url},
           'evidence': ['main/api-results.json']}
    RESULTS.append(row)
    (ROOT / 'api-results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + '\n')
    print(case_id, row['status'], response.status_code, flush=True)


def req(method, path, token=None, **kwargs):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    return requests.request(method, BASE + path, headers=headers, timeout=25, **kwargs)


accounts_path = PRIVATE / 'main-accounts.json'
if accounts_path.exists():
    accounts = json.loads(accounts_path.read_text())
else:
    accounts = {}
    for role in ['operator', 'viewer']:
        username = 'qa-20260930-101945-main-' + role
        password = secrets.token_urlsafe(24)
        r = req('POST', '/api/users', TOKEN, json={'username': username, 'password': password, 'role': role})
        record('MAIN-USER-' + role, '创建本轮专用角色账户', r, 200)
        if r.status_code != 200:
            raise RuntimeError('Cannot create owned RBAC user: ' + str(r.status_code))
        accounts[role] = {'id': r.json()['id'], 'username': username, 'password': password, 'role': role}
    accounts_path.write_text(json.dumps(accounts) + '\n')
    os.chmod(accounts_path, 0o600)
    (ROOT / 'test-data.json').write_text(json.dumps({'resources': [
        {'type': 'user', 'id': v['id'], 'name': v['username'], 'role': v['role'],
         'owner': 'main', 'cleanup': 'KEEP_NO_DELETE_API'} for v in accounts.values()
    ]}, ensure_ascii=False, indent=2) + '\n')

tokens = {'administrator': TOKEN}
for role, user in accounts.items():
    r = req('POST', '/api/login', json={'username': user['username'], 'password': user['password']})
    record('MAIN-LOGIN-' + role, '专用角色真实密码登录', r, 200)
    tokens[role] = r.json()['token']
    user['token'] = tokens[role]
accounts_path.write_text(json.dumps(accounts) + '\n')
os.chmod(accounts_path, 0o600)

for i, path in enumerate(['/healthz', '/readyz', '/api/oidc/state']):
    record('MAIN-PUBLIC-' + str(i + 1), '公开接口无需会话', req('GET', path), 200)
r = req('HEAD', '/api/agent/binary?architecture=amd64')
record('MAIN-BINARY-HEAD', '公开 amd64 下载元数据', r, 200,
       detail={'size': r.headers.get('Content-Length'), 'sha256': r.headers.get('X-Agent-Binary-SHA256')})
r = req('GET', '/api/agent/binary?architecture=amd64', stream=True)
h = hashlib.sha256()
size = 0
for chunk in r.iter_content(65536):
    h.update(chunk)
    size += len(chunk)
digest = h.hexdigest()
record('MAIN-BINARY-SHA', '真实下载内容匹配 SHA256', r,
       200 if digest == r.headers.get('X-Agent-Binary-SHA256') else -1,
       detail={'size': size, 'sha256': digest, 'header_matches': digest == r.headers.get('X-Agent-Binary-SHA256')})
record('MAIN-BINARY-BADARCH', '非法架构明确拒绝', req('GET', '/api/agent/binary?architecture=invalid'), 400)
record('MAIN-BINARY-METHOD', '公开下载拒绝写方法', req('POST', '/api/agent/binary?architecture=amd64'), 405)
record('MAIN-OIDC-DISABLED', '未配置OIDC时登录入口拒绝', req('GET', '/api/oidc/login', allow_redirects=False), 404)

paths = ['/api/me', '/api/nodes', '/api/groups', '/api/scripts', '/api/tasks',
         '/api/schedules', '/api/applications', '/api/artifacts', '/api/executions',
         '/api/transfers', '/api/audit', '/api/settings', '/api/users', '/metrics']
for i, path in enumerate(paths, 1):
    record(f'MAIN-NOAUTH-{i:02}', '受保护接口无会话拒绝', req('GET', path), 401)
    for role, token in tokens.items():
        expected = 403 if path == '/api/users' and role != 'administrator' else 200
        record(f'MAIN-READ-{role}-{i:02}', '真实角色读取权限', req('GET', path, token), expected)

for role in ['operator', 'viewer']:
    for i, path in enumerate(['/api/scripts', '/api/tasks', '/api/groups', '/api/schedules',
                              '/api/applications', '/api/users', '/api/transfers', '/api/nodes/enrollment'], 1):
        record(f'MAIN-WRITE-DENY-{role}-{i:02}', '真实角色写入口拒绝', req('POST', path, tokens[role], json={}), 403)
    record('MAIN-SETTINGS-DENY-' + role, '真实角色设置写入口拒绝', req('PUT', '/api/settings', tokens[role], json={}), 403)

before = req('GET', '/api/settings', TOKEN).json()
for i, bad in enumerate([{'heartbeat_interval_sec': '0'}, {'revision_check_interval_sec': '4'},
                         {'changelog_window': '0'}, {'max_log_bytes': '1023'},
                         {'agent_gateway_base_url': 'file:///etc/passwd'}, {'unknown': '1'}], 1):
    r = req('PUT', '/api/settings', TOKEN, json=bad)
    after = req('GET', '/api/settings', TOKEN).json()
    record(f'MAIN-SETTING-INVALID-{i:02}', '设置非法值原子拒绝且旧值不变', r,
           400 if before == after else -1, detail={'unchanged': before == after})

for path in ['/api/tasks/not-a-real-id', '/api/scripts/not-a-real-id', '/api/applications/not-a-real-id']:
    record('MAIN-MISSING-' + path.split('/')[2], '不存在对象读取', req('GET', path, TOKEN), 404)

# 只对本轮 viewer 做升降权，验证既有 token 读取实时角色。
viewer = accounts['viewer']
record('MAIN-ROLE-PROMOTE', '专用viewer升为administrator',
       req('PUT', '/api/users/' + viewer['id'], TOKEN, json={'role': 'administrator'}), 200)
try:
    record('MAIN-ROLE-OLDTOKEN-PROMOTE', '既有token实时获取升权', req('GET', '/api/users', tokens['viewer']), 200)
finally:
    record('MAIN-ROLE-RESTORE', '恢复专用viewer角色',
           req('PUT', '/api/users/' + viewer['id'], TOKEN, json={'role': 'viewer'}), 200)
record('MAIN-ROLE-OLDTOKEN-DEMOTE', '既有token实时获取降权', req('GET', '/api/users', tokens['viewer']), 403)
print('summary', json.dumps({s: sum(x['status'] == s for x in RESULTS) for s in ['PASS', 'FAIL']}))
