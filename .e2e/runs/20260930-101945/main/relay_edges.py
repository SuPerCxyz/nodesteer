"""真实 Relay 身份、分段暂存与 Native 目标文件保护检查。"""
import hashlib
import json
import shlex
import subprocess
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json').read_text())
BASE = CFG['base_url']
ADMIN = CFG['admin_token']
SOURCE = '811d72ef-75f8-47ba-84de-8cb1e6974315'
TARGET = '1c59cf12-97ef-412f-9a06-aea04e6423c1'
OTHER = 'd77e2440-919f-4431-a488-733817dcd809'
PREFIX = '/tmp/qa-20260930-101945-main-relay-' + uuid.uuid4().hex[:8]
ROWS = []
TRANSFER = None


def record(case, ok, **detail):
    row = {'case_id': case, 'classification': 'REAL_API_GUEST', 'status': 'PASS' if ok else 'FAIL', 'detail': detail}
    ROWS.append(row)
    (ROOT / 'relay-edge-results.json').write_text(json.dumps(ROWS, ensure_ascii=False, indent=2) + '\n')
    print(case, row['status'], flush=True)


def guest(node_id, script):
    node = next(n for n in CFG['nodes'] if n['id'] == node_id)
    p = subprocess.run(node['executor_argv'], input=script, text=True, capture_output=True, timeout=25)
    if p.returncode:
        raise RuntimeError('owned guest check failed, exit ' + str(p.returncode))
    return p.stdout


def hub(script):
    node = next(n for n in CFG['nodes'] if n['id'] == SOURCE)
    argv = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'LogLevel=ERROR',
            '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-i', node['ssh_identity_file'],
            CFG['hub']['ssh_user'] + '@' + CFG['hub']['ip'], 'sh', '-s']
    p = subprocess.run(argv, input=script, text=True, capture_output=True, timeout=25)
    if p.returncode:
        raise RuntimeError('owned Hub file check failed, exit ' + str(p.returncode))
    return p.stdout


def identity(node):
    return json.loads(guest(node, """python3 - <<'PY'
import sqlite3,json
c=sqlite3.connect('file:/var/lib/nodesteer/state.db?mode=ro',uri=True)
print(json.dumps(dict(c.execute("select key,value from identity where key in ('agent_id','credential')"))))
PY
"""))


def api(method, path, headers=None, **kw):
    return requests.request(method, BASE + path, headers=headers or {'Authorization': 'Bearer ' + ADMIN}, timeout=20, **kw)


def agent_headers(data):
    return {'X-NodeSteer-Agent-ID': data['agent_id'], 'X-NodeSteer-Agent-Token': data['credential']}


def upload(headers, body, expected, name):
    r = api('POST', '/agent/transfers/' + TRANSFER + '/upload', headers, data=body)
    record(name, r.status_code == expected, actual_http=r.status_code, expected_http=expected, response=r.json())
    return r


def state():
    r = api('GET', '/api/transfers/' + TRANSFER)
    r.raise_for_status()
    return r.json()


def wait_terminal():
    end = time.monotonic() + 35
    while time.monotonic() < end:
        d = state()
        if d['status'] in ['FAILED', 'SUCCESS', 'CANCELED']:
            return d
        time.sleep(.4)
    raise RuntimeError('owned Relay terminal state timed out')


payload = b'qa-relay-boundary-real-file-' + uuid.uuid4().hex.encode()
sha = hashlib.sha256(payload).hexdigest()
ids = {key: identity(key) for key in [SOURCE, TARGET, OTHER]}
sentinel = 'owned-old-file-' + uuid.uuid4().hex
setup = 'set -eu\nmkdir ' + shlex.quote(PREFIX) + '\nprintf %s ' + shlex.quote(sentinel) + ' > ' + shlex.quote(PREFIX + '/old') + '\nln -s old ' + shlex.quote(PREFIX + '/destination') + '\n'
guest(TARGET, setup)
try:
    r = api('POST', '/api/transfers', json={'source_node_id': SOURCE, 'source_path': PREFIX + '/absent-source',
        'targets': [{'node_id': TARGET, 'destination_path': PREFIX + '/destination'}]})
    r.raise_for_status()
    TRANSFER = r.json()['id']
    (ROOT / 'relay-edge-ledger.json').write_text(json.dumps({'transfer_id': TRANSFER, 'target_node_id': TARGET, 'owned_path': PREFIX, 'status': 'active'}, indent=2) + '\n')
    initial = wait_terminal()
    record('MAIN-RELAY-SOURCE-ERROR-TERMINAL', initial['status'] == 'FAILED' and all(t['status'] == 'FAILED' for t in initial['targets']), transfer_id=TRANSFER, actual=initial)
    sh = agent_headers(ids[SOURCE])
    th = agent_headers(ids[TARGET])
    oh = agent_headers(ids[OTHER])
    normal = {**sh, 'X-NodeSteer-File-Size': str(len(payload)), 'X-NodeSteer-File-Mode': '0644'}
    upload({**th, 'X-NodeSteer-File-Size': str(len(payload))}, payload, 403, 'MAIN-RELAY-WRONG-SOURCE')
    for label, value in [('ABSENT', ''), ('NEGATIVE', '-1'), ('OVER-LIMIT', str((10 << 30) + 1)), ('TEXT', 'not-a-number')]:
        upload({**normal, 'X-NodeSteer-File-Size': value}, payload, 400, 'MAIN-RELAY-SIZE-' + label)
    upload({**normal, 'X-NodeSteer-File-Mode': 'not-octal'}, payload, 400, 'MAIN-RELAY-MODE-INVALID')
    upload({**normal, 'Content-Range': 'bad-range'}, payload, 400, 'MAIN-RELAY-RANGE-INVALID')
    upload({**normal, 'Content-Range': f'bytes 1-{len(payload)-1}/{len(payload)}'}, payload[1:], 409, 'MAIN-RELAY-OFFSET-MISMATCH')
    upload(normal, payload[:-1], 400, 'MAIN-RELAY-LENGTH-MISMATCH')
    first = payload[:13]
    partial = upload(normal, iter([first]), 409, 'MAIN-RELAY-PARTIAL-UPLOAD')
    local = json.loads(hub("python3 - <<'PY'\nimport json,pathlib\np=pathlib.Path(" + repr(CFG['hub']['data_dir'] + '/file-transfers/' + TRANSFER) + ")\nprint(json.dumps({'part_exists':p.with_suffix('.part').exists(),'part_size':p.with_suffix('.part').stat().st_size if p.with_suffix('.part').exists() else None,'blob_exists':p.with_suffix('.blob').exists()}))\nPY\n"))
    staged = state()
    record('MAIN-RELAY-PARTIAL-NOT-PUBLISHED', local == {'part_exists': True, 'part_size': len(first), 'blob_exists': False} and not staged.get('sha256') and partial.json().get('next_offset') == len(first), hub_files=local, transfer_status=staged['status'])
    for label, hdr in [('SOURCE', sh), ('OTHER', oh), ('TARGET', th)]:
        rr = api('GET', '/agent/transfers/' + TRANSFER + '/download', hdr)
        record('MAIN-RELAY-PARTIAL-DOWNLOAD-' + label, rr.status_code == 404, actual_http=rr.status_code)
    offset = len(first)
    upload({**normal, 'Content-Range': f'bytes {offset}-{len(payload)-1}/{len(payload)}'}, payload[offset:], 200, 'MAIN-RELAY-RESUME-UPLOAD')
    final = wait_terminal()
    record('MAIN-RELAY-COMPLETED-METADATA', final.get('sha256') == sha and final['size'] == len(payload), expected_sha256=sha, actual_sha256=final.get('sha256'), size=final['size'])
    for label, hdr, code in [('SOURCE', sh, 403), ('OTHER', oh, 403), ('TARGET', th, 200)]:
        rr = api('GET', '/agent/transfers/' + TRANSFER + '/download', hdr)
        record('MAIN-RELAY-DOWNLOAD-OWNER-' + label, rr.status_code == code and (code != 200 or rr.content == payload), actual_http=rr.status_code, bytes_match=rr.content == payload if code == 200 else None)
    before = final.get('sha256')
    upload(normal, b'duplicate-must-not-replace', 200, 'MAIN-RELAY-UPLOAD-IDEMPOTENT')
    record('MAIN-RELAY-DUPLICATE-PRESERVES-SHA', state().get('sha256') == before, actual_sha256=state().get('sha256'))
    files = json.loads(guest(TARGET, "python3 - <<'PY'\nimport json,pathlib,hashlib\np=pathlib.Path(" + repr(PREFIX) + ")\nprint(json.dumps({'is_symlink':(p/'destination').is_symlink(),'old':(p/'old').read_text(),'tmp':list(x.name for x in p.glob('.nodesteer-transfer-*'))}))\nPY\n"))
    record('MAIN-RELAY-NATIVE-SYMLINK-PROTECTION', final['status'] == 'FAILED' and files == {'is_symlink': True, 'old': sentinel, 'tmp': []}, target_result=final['targets'], destination=files)
finally:
    if TRANSFER:
        rr = api('POST', '/api/transfers/' + TRANSFER + '/cancel')
        record('MAIN-RELAY-CANCEL-EXACT', rr.status_code == 200 and rr.json().get('status') == 'CANCELED', transfer_id=TRANSFER, actual_http=rr.status_code)
        cleaned = hub('test ! -e ' + shlex.quote(CFG['hub']['data_dir'] + '/file-transfers/' + TRANSFER + '.part') + ' && test ! -e ' + shlex.quote(CFG['hub']['data_dir'] + '/file-transfers/' + TRANSFER + '.blob') + '\n')
        record('MAIN-RELAY-HUB-BLOB-CLEANUP', cleaned == '', transfer_id=TRANSFER)
    guest(TARGET, 'set -eu\nrm -- ' + shlex.quote(PREFIX + '/destination') + ' ' + shlex.quote(PREFIX + '/old') + '\nrmdir -- ' + shlex.quote(PREFIX) + '\n')
    if TRANSFER:
        (ROOT / 'relay-edge-ledger.json').write_text(json.dumps({'transfer_id': TRANSFER, 'target_node_id': TARGET, 'owned_path': PREFIX, 'status': 'cleaned', 'retained': 'API transfer/Execution/Audit history only'}, indent=2) + '\n')
    record('MAIN-RELAY-GUEST-CLEANUP', True, path=PREFIX)
