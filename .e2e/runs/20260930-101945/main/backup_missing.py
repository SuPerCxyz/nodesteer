"""删除唯一测试升级的单个备份，核验明确失败；仅自有App/Unit。"""
import base64
import concurrent.futures
import hashlib
import json
import shlex
import time
import uuid

import requests
import native_faults as f

login = requests.post(f.BASE + '/api/login', json={'username': f.CFG['admin_username'], 'password': f.CFG['admin_password']}, timeout=20)
login.raise_for_status()
f.HEADERS = {'Authorization': 'Bearer ' + login.json()['token']}
prefix = 'qa-20260930-101945-main-backup-' + uuid.uuid4().hex[:8]
directory, unit = '/tmp/' + prefix, prefix + '.service'
ledger = []
row = {'case_id': 'MAIN-BACKUP-MISSING', 'classification': 'REAL_API_AGENT', 'status': 'RUNNING'}
app = None
try:
    v1 = base64.b64decode(f.remote('base64 /bin/sh\n'))
    for version, data in [('v1', v1), ('v2', v1 + b'\nqa-missing-backup-v2\n')]:
        r = requests.post(f.BASE + '/api/artifacts', headers=f.HEADERS, data={'name': prefix + '-' + version, 'version': version, 'architecture': 'amd64'}, files={'file': ('fixture.bin', data)}, timeout=25)
        r.raise_for_status()
        artifact = r.json()
        ledger.append({'kind': 'artifacts', 'id': artifact['id'], 'status': 'created'})
    app = f.api('POST', '/api/applications', {'name': prefix, 'version': 'v1', 'artifact_id': ledger[0]['id'], 'binary_path': directory + '/app-bin', 'config_path': directory + '/app.conf', 'config': 'config-v1\n', 'unit_name': unit, 'arguments': ['-c', 'exec sleep 60'], 'health_check': {'type': 'systemd', 'timeout': 1, 'attempts': 2, 'interval': 1}})
    ledger.append({'kind': 'applications', 'id': app['id'], 'status': 'created'})
    f.api('POST', '/api/applications/' + app['id'] + '/assign', {'node_ids': [f.NODE_ID]})
    f.ready()
    initial = f.terminal(f.deploy(app))
    assert initial['status'] == 'SUCCESS', 'owned baseline deployment failed'
    row['baseline'] = f.state(app, directory, unit)
    assert row['baseline']['binary_sha256'] == hashlib.sha256(v1).hexdigest()
    app.update(version='v2', artifact_id=ledger[1]['id'], config='config-v2\n', health_check={'type': 'command', 'target': 'sleep 2; false', 'timeout': 3, 'attempts': 2, 'interval': 1})
    app = f.api('PUT', '/api/applications/' + app['id'], app)
    watch = directory + '/backup-watch'
    monitor = '''import sqlite3,time,pathlib,json
app=%r; watch=%r
c=sqlite3.connect('file:/var/lib/nodesteer/state.db?mode=ro',uri=True,timeout=.2)
pathlib.Path(watch).write_text('ready');end=time.monotonic()+20;out={'observed':False}
while time.monotonic()<end:
 r=c.execute('select id,phase,backup_path,backup_config_path,backup_unit_path from deployments where application_id=? and phase=? order by started_at desc limit 1',(app,'VERIFYING')).fetchone()
 if r:
  p=pathlib.Path(r[2]);assert str(p).startswith('/var/lib/nodesteer/applications/backup-') and p.is_file();original=p.read_bytes();p.unlink();out={'observed':True,'deployment_id':r[0],'phase':r[1],'removed_binary_backup':str(p),'config_backup':r[3],'unit_backup':r[4],'original_backup_sha256':__import__('hashlib').sha256(original).hexdigest()};break
 time.sleep(.005)
print(json.dumps(out))
''' % (app['id'], watch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(f.remote, "python3 - <<'PY'\n" + monitor + '\nPY\n', 30)
        end = time.monotonic() + 6
        while time.monotonic() < end:
            if f.remote('test -f ' + shlex.quote(watch) + ' && echo ready || true\n') == 'ready':
                break
            time.sleep(.2)
        result = f.terminal(f.deploy(app, 'upgrade'), timeout=45)
        injection = json.loads(future.result())
    actual = f.state(app, directory, unit)
    states = f.api('GET', '/api/applications/' + app['id'] + '/state')
    row.update(injection=injection, execution=result, application_states=states, actual=actual)
    row['status'] = 'PASS' if injection['observed'] and result['status'] == 'FAILED' and all(s['health'] == 'unhealthy' and 'rollback health check also failed' in s.get('error', '') for s in states) and actual['unit_state'] != 'active' else 'FAIL'
    row['limitation'] = '该例验证缺失备份不会误报SUCCESS/healthy；缺失备份无法恢复旧Binary，实际新Binary仍在，但Unit未active，记录真实物理状态。'
finally:
    for item in reversed(ledger):
        try:
            f.api('DELETE', '/api/' + item['kind'] + '/' + item['id'])
            item['status'] = 'deleted'
        except Exception as error:
            item['status'] = 'cleanup_failed'
            item['error_type'] = type(error).__name__
    paths = []
    for journal in row.get('actual', {}).get('journal', []):
        paths += [p for p in journal[2:] if p]
    cleanup = 'systemctl stop ' + shlex.quote(unit) + ' 2>/dev/null || true\n'
    cleanup += 'rm -f -- ' + ' '.join(shlex.quote(p) for p in [directory + '/app-bin', directory + '/app.conf', directory + '/backup-watch', '/etc/systemd/system/' + unit] + paths) + '\n'
    cleanup += 'systemctl daemon-reload\nsystemctl reset-failed ' + shlex.quote(unit) + ' 2>/dev/null || true\nrmdir -- ' + shlex.quote(directory) + '\n'
    f.remote(cleanup)
    row['cleanup'] = ledger
    (f.ROOT / 'backup-missing-result.json').write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
    requests.post(f.BASE + '/api/logout', headers=f.HEADERS, timeout=20)
    print(row['case_id'], row['status'], flush=True)
