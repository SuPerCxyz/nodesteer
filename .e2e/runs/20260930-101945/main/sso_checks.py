"""真实浏览器 → Dex → OIDC callback → 会话和三角色权限。"""
import json
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/isolated.json').read_text())['sso']
RESULTS = []


def browser(session, args, script=None):
    r = subprocess.run(['agent-browser', '--session', session, *args], input=script,
                       text=True, capture_output=True, timeout=40)
    if r.returncode:
        # 不回传可能带 OAuth URL/code 或输入值的工具原始错误。
        raise RuntimeError('browser command failed: ' + args[0])
    return r.stdout.strip()


for user in CFG['users']:
    role = user['role']
    session = 'nodesteer-e2e-sso-' + role + '-20260930-101945'
    row = {'case_id': 'MAIN-OIDC-UI-' + role, 'classification': 'REAL_UI_API', 'expected_role': role}
    try:
        browser(session, ['open', CFG['hub_url'] + '/sign-in'])
        browser(session, ['eval', '--stdin'], 'localStorage.removeItem("nodesteer_token");({cleared:true})')
        browser(session, ['cookies', 'clear'])
        browser(session, ['open', CFG['hub_url'] + '/sign-in'])
        browser(session, ['wait', '--fn', '[...document.querySelectorAll("button")].some(x=>x.innerText.includes("SSO"))'])
        browser(session, ['find', 'role', 'button', 'click', '--name', '使用 SSO 登录'])
        browser(session, ['wait', '--fn', '!!document.querySelector("#login")'])
        script = '(()=>{const a=document.querySelector("#login"),p=document.querySelector("#password");a.value=' + json.dumps(user['email']) + ';p.value=' + json.dumps(user['password']) + ';a.dispatchEvent(new Event("input",{bubbles:true}));p.dispatchEvent(new Event("input",{bubbles:true}));return {filled:true};})()'
        browser(session, ['eval', '--stdin'], script)
        browser(session, ['find', 'role', 'button', 'click', '--name', 'Login'])
        browser(session, ['wait', '--fn', 'location.origin===' + json.dumps(CFG['hub_url']) + '&&location.pathname==="/"'])
        script = '(async()=>{const h={Authorization:"Bearer "+localStorage.getItem("nodesteer_token")};const me=await fetch("/api/me",{headers:h});const s=await me.json();const users=await fetch("/api/users",{headers:h});const nodes=await fetch("/api/nodes",{headers:h});return {me:me.status,role:s.Role,users:users.status,nodes:nodes.status,path:location.pathname};})()'
        actual = json.loads(browser(session, ['eval', '--stdin'], script))
        row['actual'] = actual
        row['status'] = 'PASS' if actual['me'] == 200 and actual['role'] == role and actual['users'] == (200 if role == 'administrator' else 403) and actual['nodes'] == 200 else 'FAIL'
        name = 'sso-' + role + '-dashboard.png'
        browser(session, ['screenshot', 'body', str(ROOT / name)])
        row['evidence'] = ['main/' + name]
    except Exception as exc:
        row.update(status='FAIL', reason=str(exc))
    RESULTS.append(row)
    (ROOT / 'sso-results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + '\n')
    print(row['case_id'], row['status'], flush=True)

state = requests.get(CFG['hub_url'] + '/api/oidc/state', timeout=10)
local = requests.post(CFG['hub_url'] + '/api/login', json={'username': CFG['bootstrap_username'], 'password': CFG['bootstrap_password']}, timeout=10)
bad_callback = requests.get(CFG['hub_url'] + '/api/oidc/callback?state=invalid-e2e-state&code=invalid-e2e-code', timeout=10, allow_redirects=False)
row = {'case_id': 'MAIN-OIDC-MODE-AND-STATE', 'classification': 'REAL_API',
       'actual': {'mode': state.json(), 'local_password_http': local.status_code, 'invalid_callback_http': bad_callback.status_code,
                  'invalid_callback_returns_error': bad_callback.headers.get('Location', '').startswith(CFG['hub_url'] + '/sign-in?error=')},
       'status': 'PASS' if state.json() == {'enabled': True, 'local_fallback': False} and local.status_code == 403 and bad_callback.status_code == 302 and bad_callback.headers.get('Location', '').startswith(CFG['hub_url'] + '/sign-in?error=') else 'FAIL'}
RESULTS.append(row)
(ROOT / 'sso-results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + '\n')
print(row['case_id'], row['status'], flush=True)
