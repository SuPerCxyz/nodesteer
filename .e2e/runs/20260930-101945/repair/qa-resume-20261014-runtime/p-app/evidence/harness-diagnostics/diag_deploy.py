#!/usr/bin/env python3
"""Focused diagnostic: deploy one owned application and inspect the owned child's
SQLite journal, unit registry and agent log directly."""
import json
import time

import hostlib
import hubapi
import run_papp

NONCE = run_papp.NONCE
PREFIX = run_papp.PREFIX
h = hubapi.Hub()
h.login()
out = {'at_utc': hostlib.now_utc()}

TABLES = ("identity", "sync_state", "applications", "artifacts", "executions", "deployments",
          "unit_registry", "tasks", "schedules")


def probe(ch):
    return ch.remote_py(
        "import json,os,sqlite3\n"
        "data=%r\n"
        "db=os.path.join(data,'state.db')\n"
        "out={'db':db,'exists':os.path.isfile(db)}\n"
        "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5)\n"
        "out['journal_mode']=c.execute('PRAGMA journal_mode').fetchone()[0]\n"
        "out['tables']=[r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")]\n"
        "out['counts']={}\n"
        "for t in %r:\n"
        "    try: out['counts'][t]=c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]\n"
        "    except Exception as e: out['counts'][t]='ERR:'+type(e).__name__\n"
        "out['deployments']=c.execute('SELECT id,application_id,from_version,to_version,phase,backup_path FROM deployments').fetchall()\n"
        "out['unit_registry']=c.execute('SELECT * FROM unit_registry').fetchall()\n"
        "out['executions']=[list(r)[:8] for r in c.execute('SELECT id,task_id,application_id,application_version,application_operation,application_health,status,block_reason FROM executions ORDER BY start_time DESC LIMIT 5')]\n"
        "out['identity']=c.execute('SELECT key,value FROM identity').fetchall()\n"
        "out['wal']=[n for n in os.listdir(data) if n.startswith('state.db')]\n"
        "print(json.dumps(out,default=str))\n" % (ch.data, list(TABLES)))


def hoststate(ch, unit):
    return ch.remote_py(
        "import json,os,subprocess\n"
        "u=%r\n"
        "r=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,SubState,MainPID,UnitFileState',u],capture_output=True,text=True)\n"
        "print(json.dumps({'unit':dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x),"
        "'unit_file':os.path.lexists('/etc/systemd/system/'+u),"
        "'proc':[l for l in subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True).stdout.splitlines()]},default=str))\n" % unit)


# ---- 1. owned child
name = PREFIX + 'diag'
ip = '%sdiag.invalid' % PREFIX
code, enroll = h.post('/api/nodes/enrollment',
                      {'node_name': name, 'node_ip': ip, 'hub_address': run_papp.HUB})
print('enroll', code, enroll.get('node_id'))
nid = enroll['node_id']
ch = hostlib.OwnedChild(NONCE, '', nid, enroll['agent_id'], name, ip,
                        h.cred['registration_token'])
started = ch.write_config_and_start(run_papp.WS)
out['child'] = {'root': ch.root, 'data': ch.data, 'started': started}
deadline = time.time() + 90
node = None
while time.time() < deadline:
    c2, node = h.get('/api/nodes/' + h.quote(nid))
    if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
        break
    time.sleep(1)
out['node'] = {k: node.get(k) for k in ('id', 'status', 'sync_status', 'global_revision')}
out['probe_child_idle'] = probe(ch)

# ---- 2. artifact + application
content = run_papp.app_script('diag')
code, art = h.upload_artifact(PREFIX + 'diag-payload', 'v1', 'diag.bin', content)
out['artifact'] = {'id': art['id'], 'sha': art['sha256']}
defn = {'name': PREFIX + 'diagapp', 'description': 'diag', 'artifact_id': art['id'], 'version': '1.0',
        'binary_path': run_papp.BINARY, 'unit_name': run_papp.UNIT,
        'arguments': ['--profile=v1'], 'environment': {'APP_PROFILE': 'v1'},
        'config': 'APP_MODE=v1\n', 'config_path': run_papp.CONFIG,
        'health_check': run_papp.Run.health()}
code, app = h.post('/api/applications', defn)
appid = app['id']
out['app'] = {'id': appid, 'create_http': code}
print('app', appid)
out['assign_http'] = h.post('/api/applications/%s/assign' % h.quote(appid),
                            {'node_ids': [nid]})[0]
time.sleep(25)
out['probe_after_assign'] = probe(ch)
out['host_after_assign'] = hoststate(ch, run_papp.UNIT)

# ---- 3. explicit deploy
code, execs = h.post('/api/applications/%s/deploy' % h.quote(appid),
                     {'node_ids': [nid], 'operation': 'deploy'})
print('deploy', code, execs)
exec_id = execs[0]['id'] if isinstance(execs, list) and execs else None
deadline = time.time() + 120
ex = None
while time.time() < deadline:
    c3, ex = h.get('/api/executions/' + h.quote(exec_id))
    if ex and ex.get('status') in ('SUCCESS', 'FAILED', 'CANCELED', 'TIMED_OUT', 'BLOCKED', 'SKIPPED'):
        break
    time.sleep(1)
out['execution'] = ex
time.sleep(3)
out['probe_after_deploy'] = probe(ch)
out['host_after_deploy'] = hoststate(ch, run_papp.UNIT)
out['executions_api'] = h.get('/api/applications/%s/executions' % h.quote(appid))[1]
out['app_state_api'] = h.get('/api/applications/%s/state' % h.quote(appid))[1]
log = ch.log_tail(20000)['tail']
out['agent_log_tail'] = log[-6000:]

# ---- 4. cleanup
out['cleanup'] = {'remove': ch.remove()}
out['cleanup']['app_delete'] = h.delete('/api/applications/' + h.quote(appid))[0]
out['cleanup']['artifact_delete'] = h.delete('/api/artifacts/' + h.quote(art['id']))[0]
out['cleanup']['node_delete'] = h.delete('/api/nodes/' + h.quote(nid))[0]
out['host_baseline_after'] = hostlib.OwnedChild(NONCE).host_baseline()
hubapi.write_json('diag-result.json', out)
print(json.dumps({'probe_after_deploy_counts': (out['probe_after_deploy'] or {}).get('counts'),
                  'deployments': (out['probe_after_deploy'] or {}).get('deployments'),
                  'unit_registry': (out['probe_after_deploy'] or {}).get('unit_registry'),
                  'exec_status': (ex or {}).get('status'),
                  'log_clean': (out['host_baseline_after']['root_exists'] is False)},
                 ensure_ascii=False, indent=1))
print('--- log tail ---')
print(out['agent_log_tail'][-3000:])
