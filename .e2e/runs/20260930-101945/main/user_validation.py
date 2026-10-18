"""仅唯一QA账号的空密码与非法role服务端边界；最后恢复安全Viewer。"""
import json
import secrets
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).parent
PRIVATE = Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
cfg = json.loads((PRIVATE / 'connection.json').read_text())
base = cfg['base_url']
username = 'qa-20260930-101945-main-contract-' + uuid.uuid4().hex[:8]
password = secrets.token_urlsafe(32)
login = requests.post(base + '/api/login', json={'username': cfg['admin_username'], 'password': cfg['admin_password']}, timeout=20)
login.raise_for_status()
headers = {'Authorization': 'Bearer ' + login.json()['token']}
private = {'username': username, 'password': password, 'role': 'viewer'}
private_path = PRIVATE / 'main-contract-user.json'
private_path.write_text(json.dumps(private, indent=2) + '\n')
private_path.chmod(0o600)
rows, failures = [], []
user_id = None


def record(case, ok, **details):
    rows.append({'case_id': case, 'classification': 'REAL_API', 'status': 'PASS' if ok else 'FAIL', 'detail': details})
    (ROOT / 'user-validation-results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    print(case, rows[-1]['status'], flush=True)


try:
    response = requests.post(base + '/api/users', headers=headers, json={'username': username, 'password': '', 'role': 'viewer'}, timeout=20)
    record('MAIN-USER-EMPTY-PASSWORD-REJECT', response.status_code == 400, http=response.status_code, username=username)
    if response.status_code == 200:
        user_id = response.json()['id']
        private['id'] = user_id
        private_path.write_text(json.dumps(private, indent=2) + '\n')
        blank = requests.post(base + '/api/login', json={'username': username, 'password': ''}, timeout=20)
        me_http = None
        if blank.status_code == 200:
            blank_headers = {'Authorization': 'Bearer ' + blank.json()['token']}
            me_http = requests.get(base + '/api/me', headers=blank_headers, timeout=20).status_code
            requests.post(base + '/api/logout', headers=blank_headers, timeout=20)
        record('MAIN-USER-EMPTY-PASSWORD-LOGIN', blank.status_code != 200, http=blank.status_code, authenticated_me_http=me_http)
        failures.append({'id': 'MAIN-USER-EMPTY-PASSWORD', 'severity': 'P1', 'status': 'OPEN', 'title': '创建用户服务端接受空密码，空密码可真实登录', 'case_ids': ['API-CONTRACT', 'P20-03'], 'user_id': user_id,
            'steps': ['管理员POST /api/users，唯一QA用户名、password为空、role=viewer', '用该QA用户名及空密码POST /api/login', '检查登录会话/api/me，随后登出并恢复随机非空密码'],
            'expected': '创建用户的密码必填约束在服务端生效，拒绝空密码；不得产生可空密码登录的账户',
            'actual': {'create_http': response.status_code, 'login_http': blank.status_code, 'me_http': me_http},
            'evidence': ['main/user-validation-results.json'], 'source_refs': ['internal/hub/api/api.go:2018', 'internal/hub/auth/auth.go:89'],
            'cleanup': '仅本轮新QA账号，已立即恢复随机非空密码/Viewer；没有删除User API，账号与Audit历史保留。'})
        restore = requests.put(base + '/api/users/' + user_id, headers=headers, json={'password': password, 'role': 'viewer'}, timeout=20)
        restore.raise_for_status()
        invalid = requests.put(base + '/api/users/' + user_id, headers=headers, json={'role': 'qa_invalid_role'}, timeout=20)
        users = requests.get(base + '/api/users', headers=headers, timeout=20).json()
        stored = next(u['role'] for u in users if u['id'] == user_id)
        record('MAIN-USER-INVALID-ROLE-REJECT', invalid.status_code == 400 and stored == 'viewer', http=invalid.status_code, stored_role=stored, expected_roles=['administrator', 'operator', 'viewer'])
        if invalid.status_code == 200 and stored == 'qa_invalid_role':
            failures.append({'id': 'MAIN-USER-INVALID-ROLE', 'severity': 'P2', 'status': 'OPEN', 'title': '用户Role更新接受三种有效角色之外的字符串并持久化', 'case_ids': ['API-CONTRACT', 'P20-03'], 'user_id': user_id,
                'steps': ['管理员对唯一QA用户PUT role=qa_invalid_role', '管理员GET /api/users检查该用户role', '恢复该QA用户role=viewer'],
                'expected': '仅接受administrator/operator/viewer，非法role原子拒绝并保留原角色', 'actual': {'update_http': invalid.status_code, 'persisted_role': stored},
                'evidence': ['main/user-validation-results.json'], 'source_refs': ['internal/hub/api/api.go:2066', 'docs/PRODUCT.md:1281'],
                'cleanup': '仅本轮新QA账户已恢复Viewer，不改真实管理员或现有测试账户。'})
finally:
    if user_id:
        r = requests.put(base + '/api/users/' + user_id, headers=headers, json={'password': password, 'role': 'viewer'}, timeout=20)
        r.raise_for_status()
        blank = requests.post(base + '/api/login', json={'username': username, 'password': ''}, timeout=20)
        valid = requests.post(base + '/api/login', json={'username': username, 'password': password}, timeout=20)
        valid.raise_for_status()
        actor = requests.get(base + '/api/me', headers={'Authorization': 'Bearer ' + valid.json()['token']}, timeout=20)
        role = actor.json().get('Role', actor.json().get('role'))
        record('MAIN-USER-VALIDATION-RESTORE', blank.status_code == 401 and actor.status_code == 200 and role == 'viewer', empty_password_http=blank.status_code, valid_me_http=actor.status_code, final_role=role, retained_user_id=user_id)
        requests.post(base + '/api/logout', headers={'Authorization': 'Bearer ' + valid.json()['token']}, timeout=20)
        data = json.loads((ROOT / 'test-data.json').read_text())
        data['resources'].append({'type': 'user', 'id': user_id, 'name': username, 'role': 'viewer', 'owner': 'main', 'cleanup': 'KEEP_NO_DELETE_API', 'password_restored_to_private_random': True})
        (ROOT / 'test-data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    existing = json.loads((ROOT / 'failures.json').read_text())
    existing_rows = existing['failures'] if isinstance(existing, dict) else existing
    existing_rows += failures
    (ROOT / 'failures.json').write_text(json.dumps(existing, ensure_ascii=False, indent=2) + '\n')
    requests.post(base + '/api/logout', headers=headers, timeout=20)
