#!/usr/bin/env python3
"""OIDC 互斥登录模式与错误回调；OAuth 参数仅写私密状态。"""
import datetime, hashlib, json, os, pathlib, secrets, shlex, subprocess, time, urllib.error, urllib.parse, urllib.request

os.umask(0o077)
PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
OUT = pathlib.Path(__file__).parent
sso = json.loads((PRIVATE / 'isolated.json').read_text())['sso']
preflight = json.loads((PRIVATE / 'oidc-negative-preflight.json').read_text())
URL = sso['hub_url']
SECRET_STATE = PRIVATE / 'oidc-negative.json'
secret_state = {'callbacks': []}
result = {'run_id': '20260930-101945', 'status': 'running', 'cases': [], 'source': ['internal/hub/api/api.go:308', 'internal/hub/api/api.go:455', 'internal/hub/api/api.go:487', 'internal/hub/auth/oidc.go:162']}
SSH = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'LogLevel=ERROR', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-i', '/home/superc/.ssh/id_ed25519', 'root@192.168.100.209']

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

client = urllib.request.build_opener(NoRedirect)

def request(path, body=None, token=None, method=None):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with client.open(req, timeout=15) as r: return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()

def save():
    SECRET_STATE.write_text(json.dumps(secret_state, indent=2)); SECRET_STATE.chmod(0o600)
    (OUT / 'oidc-negative-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))

def remote(code, payload):
    p = subprocess.run(SSH + ['python3 -c ' + shlex.quote(code)], input=json.dumps(payload).encode(), capture_output=True, timeout=50)
    if p.returncode:
        (PRIVATE / 'oidc-negative-remote-error.log').write_bytes(p.stdout + p.stderr)
        raise RuntimeError('SSO-owned remote operation failed; see private error log')
    return json.loads(p.stdout)

def ready():
    for attempt in range(45):
        try:
            code, _, raw = request('/api/oidc/state')
            if code == 200: return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, ConnectionError): pass
        time.sleep(.5)
    raise RuntimeError('SSO test Hub ready timeout')

def fresh_state():
    code, headers, _ = request('/api/oidc/login')
    assert code == 302
    auth_url = headers['Location']
    fields = urllib.parse.parse_qs(urllib.parse.urlsplit(auth_url).query)
    assert fields.get('state') and fields.get('code_challenge') and fields.get('code_challenge_method') == ['S256']
    secret_state.setdefault('authorization_urls', []).append(auth_url)
    save()
    return fields['state'][0]

def callback(case, query):
    path = '/api/oidc/callback' + ('?' + urllib.parse.urlencode(query) if query else '')
    code, headers, raw = request(path)
    destination = urllib.parse.urlsplit(headers.get('Location', ''))
    current = urllib.parse.urlsplit(URL)
    me_code, _, _ = request('/api/me')
    row = {'id': case, 'method': 'GET', 'path': '/api/oidc/callback', 'state_present': bool(query.get('state')), 'code_present': bool(query.get('code')), 'http_status': code, 'redirect_origin': destination.scheme + '://' + destination.netloc, 'redirect_path': destination.path, 'same_origin': (destination.scheme, destination.netloc) == (current.scheme, current.netloc), 'oidc_failure_feedback': urllib.parse.parse_qs(destination.query).get('error') == ['oidc_failed'], 'sets_cookie': any(k.lower() == 'set-cookie' for k in headers), 'writes_auth_token_script': b'localStorage.setItem' in raw or b'nodesteer_token' in raw, 'unauthenticated_me_status': me_code}
    row['status'] = 'PASS' if code == 302 and row['same_origin'] and row['redirect_path'] == '/sign-in' and row['oidc_failure_feedback'] and not row['sets_cookie'] and not row['writes_auth_token_script'] and me_code == 401 else 'FAIL'
    secret_state['callbacks'].append({'case': case, 'query': query, 'response_location': headers.get('Location', '')})
    result['cases'].append(row); save()

changed = False
try:
    result['initial_mode'] = ready()
    assert result['initial_mode'] == {'enabled': True, 'local_fallback': False}
    result['configuration'] = remote('''import json,pathlib,subprocess,hashlib,shutil
x=json.load(__import__('sys').stdin);p=pathlib.Path(x['config_path']);assert hashlib.sha256(p.read_bytes()).hexdigest()==x['config_sha256'];backup=p.with_name(p.name+'.before-oidc-negative');assert not backup.exists();shutil.copy2(p,backup);c=json.loads(p.read_text());assert c['oidc']['allow_local_login'] is False;c['oidc']['allow_local_login']=True;p.write_text(json.dumps(c));subprocess.run(['docker','restart',x['container']],check=True,capture_output=True);d=json.loads(subprocess.check_output(['docker','inspect',x['container']]))[0];print(json.dumps({'config_path':str(p),'backup_path':str(backup),'original_sha256':x['config_sha256'],'temporary_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'container_id':d['Id'],'volume':next(m['Name'] for m in d['Mounts'] if m['Type']=='volume')}))
''', {'config_path': preflight['config_path'], 'config_sha256': preflight['config_sha256'], 'container': sso['hub_container']})
    changed = True
    mode = ready(); assert mode == {'enabled': False, 'local_fallback': True}
    code, _, raw = request('/api/login', {'username': sso['bootstrap_username'], 'password': sso['bootstrap_password']})
    assert code == 200; token = json.loads(raw)['token']; secret_state['temporary_local_session_token'] = token
    me_code, _, me_raw = request('/api/me', token=token); me = json.loads(me_raw)
    login_status, _, _ = request('/api/oidc/login'); cb_status, _, _ = request('/api/oidc/callback')
    role = me.get('Role') or me.get('role')
    result['cases'].append({'id': 'ENV-OIDC-LOCAL-MODE', 'status': 'PASS' if mode == {'enabled': False, 'local_fallback': True} and me_code == 200 and role == 'administrator' and login_status == 404 and cb_status == 404 else 'FAIL', 'oidc_state': mode, 'local_login_status': code, 'me_status': me_code, 'role': role, 'oidc_login_status': login_status, 'oidc_callback_status': cb_status})
    save()
finally:
    if changed:
        result['restoration'] = remote('''import json,pathlib,subprocess,hashlib
x=json.load(__import__('sys').stdin);p=pathlib.Path(x['config_path']);b=p.with_name(p.name+'.before-oidc-negative');assert hashlib.sha256(b.read_bytes()).hexdigest()==x['config_sha256'];p.write_bytes(b.read_bytes());subprocess.run(['docker','restart',x['container']],check=True,capture_output=True);d=json.loads(subprocess.check_output(['docker','inspect',x['container']]))[0];print(json.dumps({'config_restored_exactly':hashlib.sha256(p.read_bytes()).hexdigest()==x['config_sha256'],'config_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'container_id':d['Id'],'volume':next(m['Name'] for m in d['Mounts'] if m['Type']=='volume'),'original_allow_local_login':json.loads(p.read_text())['oidc']['allow_local_login']}))
''', {'config_path': preflight['config_path'], 'config_sha256': preflight['config_sha256'], 'container': sso['hub_container']})
        save()

try:
    mode = ready(); code, _, raw = request('/api/login', {'username': sso['bootstrap_username'], 'password': sso['bootstrap_password']})
    result['cases'].append({'id': 'ENV-OIDC-SSO-ONLY-RESTORED', 'status': 'PASS' if mode == {'enabled': True, 'local_fallback': False} and code == 403 and result['restoration']['config_restored_exactly'] else 'FAIL', 'oidc_state': mode, 'local_login_status': code, 'error': json.loads(raw).get('error')})
    callback('ENV-OIDC-CALLBACK-MISSING-BOTH', {})
    callback('ENV-OIDC-CALLBACK-MISSING-STATE', {'code': secrets.token_urlsafe(20)})
    callback('ENV-OIDC-CALLBACK-MISSING-CODE', {'state': fresh_state()})
    callback('ENV-OIDC-CALLBACK-WRONG-STATE', {'state': secrets.token_urlsafe(24), 'code': secrets.token_urlsafe(20)})
    state = fresh_state(); bad_code = secrets.token_urlsafe(20)
    callback('ENV-OIDC-CALLBACK-VALID-STATE-WRONG-CODE', {'state': state, 'code': bad_code})
    callback('ENV-OIDC-CALLBACK-REPLAY-CONSUMED-STATE', {'state': state, 'code': bad_code})
    result['server_error_categories'] = remote('''import json,subprocess
x=json.load(__import__('sys').stdin);p=subprocess.run(['docker','logs','--since',x['since'],x['container']],capture_output=True,text=True);lines=(p.stdout+p.stderr).splitlines();print(json.dumps({'invalid_state_events':sum('invalid oidc state' in l for l in lines),'token_exchange_events':sum('oidc token exchange' in l for l in lines),'raw_log_stored_publicly':False}))
''', {'container': sso['hub_container'], 'since': '5m'})
    result['final_mode'] = ready()
    result['status'] = 'passed' if all(x['status'] == 'PASS' for x in result['cases']) else 'failed'
    result['session_observation_boundary'] = 'Invalid callbacks issue no cookie/token script; unauthenticated /me stays401. Real authorization code success and three roles were verified by root separately; this replay is a state consumed by failed code exchange.'
except Exception as e:
    result['status'] = 'interrupted'; result['error'] = {'type': type(e).__name__, 'message': 'OIDC negative verification failed; private inputs retained'}
finally:
    result['recorded_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat(); save()
    print(json.dumps({'oidc_negative': result['status'], 'case_count': len(result['cases']), 'statuses': {x['id']: x['status'] for x in result['cases']}, 'restored': result.get('restoration', {}).get('config_restored_exactly'), 'final_mode': result.get('final_mode'), 'error_categories': result.get('server_error_categories')}), flush=True)
