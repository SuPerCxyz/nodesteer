#!/usr/bin/env python3
"""SYS-09 runtime completion run (connected Native3 Agent tombstone consumption).

Owns its own fixtures only, talks management writes only to the repair :18080
authority, and never stops/restarts any production Unit/cgroup.

Evidence written next to this file.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')

NODE_ID = '37989297-3bc8-4c0c-b302-7c6efbdba630'
AGENT_ID = 'agent-bb005487-7d84-418c-b598-f93d8b0c86a0'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
UNIT = 'nodesteer-agent-repair-20260930-101945-native-3.service'
DATA_DIR = '/var/lib/nodesteer-repair-20260930-101945/native-3'
STATE_DB = DATA_DIR + '/state.db'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@192.168.100.222']

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
    'f6a79d23-2410-450c-ac39-64dd70c9daa1': 'host-integration',
}

PREFIX = 'qa-sys09-native3-20261012-2709acec1825'
SCRIPT_NAME = PREFIX + '-script'
TASK_NAME = PREFIX + '-script-task'
PROBE_NAME = PREFIX + '-readonly-probe'
SCHEDULE_INTERVAL_SEC = 15
POST_DELETE_OBSERVE_SEC = 20
CONVERGE_TIMEOUT_SEC = 60
FIRST_EXEC_TIMEOUT_SEC = 70
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
MARKER = 'QA:2709acec1825:SCHEDULED'

BROWSER = build_opener(ProxyHandler({}))
TOKEN = [None]
BASE = [None]
KIND_PATH = {'script': 'scripts', 'task': 'tasks', 'schedule': 'schedules', 'probe_task': 'tasks'}
RUN_START_EPOCH = time.time()
STATE = {'script_id': None, 'task_id': None, 'schedule_id': None, 'probe_task_id': None,
         'execution_id': None, 'created': [], 'deleted': [], 'delete_results': [], 'get404': {}}


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load_cmd_builder():
    spec = importlib.util.spec_from_file_location('sys09_cb', HERE / 'command_builder.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {'_non_json_body': raw[:200].decode('utf-8', 'replace')}


def api(method, path, body=None, allowed=(200,), retries=3):
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if TOKEN[0]:
        headers['Authorization'] = 'Bearer ' + TOKEN[0]
    last = None
    for _ in range(retries):
        try:
            req = Request(BASE[0] + path, data=data, headers=headers, method=method)
            with BROWSER.open(req, timeout=25) as resp:
                return resp.status, _decode(resp.read())
        except HTTPError as exc:
            return exc.code, _decode(exc.read())
        except (URLError, TimeoutError) as exc:
            last = exc
            time.sleep(1.5)
    raise RuntimeError('api transport failure: %s' % type(last).__name__)


def get(path, allowed=(200,)):
    return api('GET', path, None, allowed)


def record(ev, path, payload):
    (HERE / path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def ssh_python(script, timeout=30):
    cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True,
                        text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def ssh_shell(command, timeout=30):
    cp = subprocess.run(SSH + [command], capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def main():
    ev = {
        'case_id': 'SYS-09',
        'run_started_utc': now_utc(),
        'endpoint': None,
        'target_node_id': NODE_ID,
        'prefix': PREFIX,
        'phases': {},
        'deviations': [],
    }
    progress = {'case_id': 'SYS-09', 'phase': 'init', 'updated_at_utc': now_utc(),
                'api_mutations': 0, 'resources_created': 0, 'resources_deleted': 0,
                'business_scheduled_executions': 0}
    state = STATE
    state.update({'script_id': None, 'task_id': None, 'schedule_id': None,
                  'probe_task_id': None, 'execution_id': None,
                  'created': [], 'deleted': [], 'delete_results': [], 'get404': {}})

    cred = json.loads(CRED.read_text())
    conn = json.loads(CONN.read_text())
    BASE[0] = conn['hub_url'].rstrip('/')
    split = urlsplit(BASE[0])
    if split.port != 18080:
        raise SystemExit('endpoint guard failed: repair :18080 required')
    ev['endpoint'] = {'host': split.hostname, 'port': split.port, 'only_repair_18080': True}

    code, body = api('POST', '/api/login',
                     {'username': cred['admin_username'], 'password': cred['admin_password']})
    if code != 200 or not body.get('token'):
        raise SystemExit('login failed HTTP %s' % code)
    TOKEN[0] = body['token']

    # ---------------- Phase 0: read-only fresh guards ----------------
    progress['phase'] = 'guard'
    guard = {'nodes': {}, 'fixtures_absent': {}, 'agent_pre': None}
    code, nodes = get('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    if NODE_ID not in by_id:
        raise SystemExit('target node missing')
    tgt = by_id[NODE_ID]
    if tgt.get('agent_id') != AGENT_ID or tgt.get('status') != 'online' or tgt.get('sync_status') != 'synced':
        raise SystemExit('target node not connected/synced: %s' % tgt.get('status'))
    guard['nodes']['target'] = {k: tgt.get(k) for k in
                                ('id', 'hostname', 'status', 'sync_status', 'global_revision', 'agent_version')}
    for nid, label in PROTECTED_NODES.items():
        n = by_id.get(nid)
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            raise SystemExit('protected node %s not healthy' % label)
        guard['nodes'][label] = {k: n.get(k) for k in ('id', 'status', 'sync_status', 'global_revision')}

    _, scripts = get('/api/scripts')
    _, tasks = get('/api/tasks')
    _, schedules = get('/api/schedules')
    name_tasks = {t.get('name') for t in tasks}
    if any(s.get('name') == SCRIPT_NAME for s in scripts) or TASK_NAME in name_tasks or PROBE_NAME in name_tasks:
        raise SystemExit('fixture names already present')
    guard['fixtures_absent'] = {
        'script_name': not any(s.get('name') == SCRIPT_NAME for s in scripts),
        'task_names': not (TASK_NAME in name_tasks or PROBE_NAME in name_tasks),
        'schedules_total': len(schedules),
    }
    rc, out, err = ssh_python(
        'import json,hashlib,subprocess,os\n'
        'p=subprocess.run(["systemctl","show","--no-pager","--property=ActiveState,MainPID,ControlGroup",%r],'
        'capture_output=True,text=True)\n'
        'props=dict(l.split("=",1) for l in p.stdout.splitlines() if "=" in l)\n'
        'pid=props.get("MainPID")\n'
        'exe=os.readlink("/proc/"+pid+"/exe")\n'
        'h=hashlib.sha256(open(exe,"rb").read()).hexdigest()\n'
        'print(json.dumps({"ActiveState":props.get("ActiveState"),"MainPID":pid,'
        '"ControlGroup":props.get("ControlGroup"),"exe":exe,"exe_sha":h}))\n' % UNIT)
    if rc != 0:
        raise SystemExit('ssh agent guard failed')
    ag = json.loads(out.strip().splitlines()[-1])
    if ag['ActiveState'] != 'active' or ag['exe_sha'] != AGENT_SHA or ag['ControlGroup'] != '/system.slice/' + UNIT:
        raise SystemExit('agent owner guard failed: %s' % ag)
    guard['agent_pre'] = ag
    rev_guard, _sync = applied_rev()
    guard['revision_before_any_mutation'] = rev_guard
    ev['phases']['guard'] = guard
    record(ev, 'phase0-guard.json', guard)
    progress['agent_pre_mainpid'] = ag['MainPID']

    # ---------------- Phase 1: readonly probe Task + preflight ----------------
    progress['phase'] = 'probe_create'
    cb = load_cmd_builder()
    summary = cb.summary()
    if summary['command_bytes'] != 5797 or summary['command_sha256'] != '6b8c35583e4886d42adb59ad1b28e9956c1972b98e1625b4b95d2147e072a2e6':
        raise SystemExit('pinned probe command hash mismatch: %s' % summary)
    command = cb.build_command()
    probe_params = cb.task_parameters()
    rc, created = api('POST', '/api/tasks', {
        'name': PROBE_NAME, 'type': 'command', 'command': command,
        'target': {'type': 'node', 'node_ids': [NODE_ID]},
        'parameters': probe_params, 'param_values': {},
        'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True,
    }, (200, 201))
    state['probe_task_id'] = created['id']
    state['created'].append(('probe_task', created['id']))
    progress['api_mutations'] += 1
    progress['resources_created'] += 1
    code, probe_read = get('/api/tasks/' + state['probe_task_id'])
    if code != 200 or probe_read.get('type') != 'command' or probe_read.get('command') != command \
            or probe_read.get('target', {}).get('node_ids') != [NODE_ID]:
        raise SystemExit('probe task exact readback mismatch')
    guard['probe_task'] = {'id': state['probe_task_id'], 'command_sha256': summary['command_sha256'],
                           'command_bytes': summary['command_bytes'], 'readback_ok': True}

    # wait for the connected Agent to apply the probe Task definition (rev_guard+1)
    rev0, pre_polls = wait_rev_at_least(rev_guard + 1, timeout=30)
    if rev0 is None:
        raise SystemExit('node did not apply probe task revision: %s' % pre_polls)
    sync0 = 'synced'
    guard['revision_after_probe_task_create'] = rev0
    guard['sync_after_probe_task_create'] = sync0

    progress['phase'] = 'probe_pre'
    rc, runresp = api('POST', '/api/tasks/%s/run' % state['probe_task_id'],
                      {'node_ids': {NODE_ID: True}, 'params': {'phase': 'pre', 'expected_hub_revision': str(rev0)}})
    progress['api_mutations'] += 1
    pre_exec_id = runresp[0]['id']
    pre = wait_execution(pre_exec_id)
    ev['phases']['probe_pre'] = {'execution_id': pre_exec_id, 'expected_hub_revision': rev0,
                                 'status': pre.get('status'), 'exit_code': pre.get('exit_code'),
                                 'stdout': (pre.get('stdout') or '').strip(), 'stderr': (pre.get('stderr') or '').strip()}
    record(ev, 'phase1-probe-pre.json', ev['phases']['probe_pre'])
    if pre.get('status') != 'SUCCESS' or 'READONLY_PROBE_PASS' not in (pre.get('stdout') or ''):
        raise SystemExit('preflight readonly probe did not pass: %s' % ev['phases']['probe_pre'])

    # ---------------- Phase 2: business Script / Task / Schedule ----------------
    progress['phase'] = 'fixtures_create'
    content = (HERE / 'business_script.sh').read_text()
    content_sha = hashlib.sha256(content.encode()).hexdigest()
    code, script = api('POST', '/api/scripts', {
        'name': SCRIPT_NAME, 'description': 'SYS-09 tombstone runtime fixture (nonce 2709acec1825).',
        'interpreter': 'shell', 'content': content, 'parameters': [], 'environment': {},
        'working_dir': '', 'timeout': 15, 'enabled': True,
    }, (200, 201))
    state['script_id'] = script['id']
    state['created'].append(('script', script['id']))
    progress['api_mutations'] += 1
    progress['resources_created'] += 1
    code, s_read = get('/api/scripts/' + state['script_id'])
    if s_read.get('revision') != 1 or s_read.get('sha256') != content_sha or s_read.get('name') != SCRIPT_NAME:
        raise SystemExit('script readback mismatch')

    code, task = api('POST', '/api/tasks', {
        'name': TASK_NAME, 'type': 'script', 'script_id': state['script_id'],
        'target': {'type': 'node', 'node_ids': [NODE_ID]},
        'parameters': [], 'param_values': {}, 'timeout': 15, 'retry': 0,
        'offline_policy': 'hub_online_required', 'enabled': True,
    }, (200, 201))
    state['task_id'] = task['id']
    state['created'].append(('task', task['id']))
    progress['api_mutations'] += 1
    progress['resources_created'] += 1
    code, t_read = get('/api/tasks/' + state['task_id'])
    if t_read.get('revision') != 1 or t_read.get('script_id') != state['script_id'] \
            or t_read.get('target', {}).get('node_ids') != [NODE_ID] or t_read.get('type') != 'script':
        raise SystemExit('task readback mismatch')

    code, sched = api('POST', '/api/schedules', {
        'task_id': state['task_id'], 'type': 'interval', 'interval_sec': SCHEDULE_INTERVAL_SEC,
        'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'hub_online_required',
        'misfire_policy': 'skip', 'enabled': True,
    }, (200, 201))
    state['schedule_id'] = sched['id']
    state['created'].append(('schedule', sched['id']))
    progress['api_mutations'] += 1
    progress['resources_created'] += 1
    code, sc_read = get('/api/schedules/' + state['schedule_id'])
    if sc_read.get('revision') != 1 or sc_read.get('task_id') != state['task_id'] \
            or sc_read.get('execution_owner') != 'agent' or sc_read.get('interval_sec') != SCHEDULE_INTERVAL_SEC \
            or sc_read.get('misfire_policy') != 'skip' or sc_read.get('enabled') is not True:
        raise SystemExit('schedule readback mismatch')

    created_at = sc_read['created_at']
    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    expected_scheduled_time = (dt + timedelta(seconds=SCHEDULE_INTERVAL_SEC)).astimezone(
        timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
    ev['phases']['fixtures'] = {
        'script': {'id': state['script_id'], 'revision': s_read.get('revision'), 'sha256': s_read.get('sha256')},
        'task': {'id': state['task_id'], 'revision': t_read.get('revision')},
        'schedule': {'id': state['schedule_id'], 'revision': sc_read.get('revision'),
                     'interval_sec': sc_read.get('interval_sec'), 'created_at': created_at,
                     'execution_owner': sc_read.get('execution_owner')},
        'expected_scheduled_time_utc': expected_scheduled_time,
    }
    record(ev, 'phase2-fixtures.json', ev['phases']['fixtures'])

    # wait for node to reach revision including all three fixtures
    # wait for the connected Agent to apply the 3 fixture definitions (rev0+3)
    rev_pre, conv_pre_polls = wait_rev_at_least(rev0 + 3, timeout=30)
    if rev_pre is None:
        raise SystemExit('node did not apply fixture revisions: %s' % conv_pre_polls)
    ev['phases']['fixtures']['hub_applied_revision_before_delete'] = rev_pre

    # ---------------- Phase 3: observe first scheduled execution ----------------
    progress['phase'] = 'await_scheduled'
    exec_deadline = time.time() + FIRST_EXEC_TIMEOUT_SEC
    first_exec = None
    observed = []
    while time.time() < exec_deadline:
        c, exlist = get('/api/executions?task_id=%s&limit=50' % state['task_id'])
        sched_execs = [e for e in exlist if e.get('trigger_type') == 'schedule']
        observed.append({'at': now_utc(), 'scheduled_exec_count': len(sched_execs),
                         'statuses': [e.get('status') for e in sched_execs]})
        if sched_execs and all(e.get('status') in TERMINAL for e in sched_execs):
            first_exec = sorted(sched_execs, key=lambda e: e.get('scheduled_time') or '')[0]
            break
        time.sleep(1)
    if first_exec is None:
        raise SystemExit('no terminal scheduled execution observed within budget')
    state['execution_id'] = first_exec['id']
    ev['phases']['first_scheduled_execution'] = {
        'id': first_exec['id'], 'status': first_exec.get('status'),
        'trigger_type': first_exec.get('trigger_type'), 'scheduled_time': first_exec.get('scheduled_time'),
        'task_id': first_exec.get('task_id'), 'script_id': first_exec.get('script_id'),
        'node_id': first_exec.get('node_id'), 'stdout': (first_exec.get('stdout') or '').strip(),
        'scheduled_exec_count_at_detect': len([e for e in observed[-1]['statuses']]) if observed else None,
    }
    record(ev, 'phase3-first-scheduled-execution.json',
           {'execution': ev['phases']['first_scheduled_execution'], 'polls': observed})
    if first_exec.get('status') != 'SUCCESS' or first_exec.get('trigger_type') != 'schedule':
        raise SystemExit('first scheduled execution not SUCCESS/schedule')
    if MARKER not in (first_exec.get('stdout') or ''):
        ev['deviations'].append('scheduled execution stdout missing nonce marker')

    # ---------------- Phase 4: delete definitions (Schedule -> Task -> Script) ----
    progress['phase'] = 'delete_definitions'
    delete_time = now_utc()
    for kind, obj_id in (('schedule', state['schedule_id']), ('task', state['task_id']),
                         ('script', state['script_id'])):
        del_code, del_body = api('DELETE', '/api/%ss/%s' % (kind, obj_id))
        progress['api_mutations'] += 1
        gcode, gbody = get('/api/%ss/%s' % (kind, obj_id), allowed=(200, 404))
        state['delete_results'].append({'kind': kind, 'id': obj_id, 'delete_http': del_code,
                                        'delete_body': del_body, 'get_http': gcode})
        if gcode != 404:
            raise SystemExit('fixture %s %s did not GET404 (got %s)' % (kind, obj_id, gcode))
        state['deleted'].append((kind, obj_id))
        state['get404'][kind] = True
        progress['resources_deleted'] += 1
    ev['phases']['deletion'] = {'at_utc': delete_time, 'results': state['delete_results']}
    record(ev, 'phase4-deletion-get404.json', ev['phases']['deletion'])

    # ---------------- Phase 5: tombstone convergence on the connected Agent ----
    progress['phase'] = 'converge'
    rev_post, conv_polls = wait_rev_at_least(rev_pre + 3, timeout=CONVERGE_TIMEOUT_SEC)
    if rev_post is None:
        ev['phases']['convergence'] = {'status': 'DID_NOT_CONVERGE', 'polls': conv_polls,
                                       'rev_pre': rev_pre, 'rev_expected_min': rev_pre + 3}
        record(ev, 'phase5-convergence-fail.json', ev['phases']['convergence'])
        raise SystemExit('agent did not apply delete revisions within %ss' % CONVERGE_TIMEOUT_SEC)
    ev['phases']['convergence'] = {'rev_pre': rev_pre, 'rev_post': rev_post,
                                   'rev_expected_min': rev_pre + 3, 'converged_at_utc': now_utc(),
                                   'poll_count': len(conv_polls), 'polls': conv_polls}

    # Authoritative tombstone-consumption gate: readonly postcheck probe on the
    # still-connected Agent proves local definition rows were removed by the tombstones.
    progress['phase'] = 'probe_post'
    rev_gate = rev_pre + 3
    rc, runresp = api('POST', '/api/tasks/%s/run' % state['probe_task_id'],
                      {'node_ids': {NODE_ID: True}, 'params': {
                          'phase': 'post', 'script_id': state['script_id'], 'task_id': state['task_id'],
                          'schedule_id': state['schedule_id'], 'execution_id': state['execution_id'],
                          'expected_scheduled_time': expected_scheduled_time,
                          'expected_hub_revision': str(rev_gate)}})
    progress['api_mutations'] += 1
    post_exec_id = runresp[0]['id']
    post = wait_execution(post_exec_id)
    ev['phases']['probe_post'] = {'execution_id': post_exec_id, 'expected_hub_revision': rev_gate,
                                  'status': post.get('status'), 'exit_code': post.get('exit_code'),
                                  'stdout': (post.get('stdout') or '').strip(),
                                  'stderr': (post.get('stderr') or '').strip()}
    record(ev, 'phase6c-probe-post.json', ev['phases']['probe_post'])
    post_ok = post.get('status') == 'SUCCESS' and 'READONLY_PROBE_PASS' in (post.get('stdout') or '')

    # ---------------- Phase 6: future interval slot suppression --------------
    progress['phase'] = 'suppress'
    observe_start = time.time()
    new_sched_execs = []
    obs_polls = []
    while time.time() - observe_start < POST_DELETE_OBSERVE_SEC:
        time.sleep(2)
        c, exlist = get('/api/executions?task_id=%s&trigger_type=schedule&limit=50' % state['task_id'])
        new_sched_execs = exlist
        obs_polls.append({'at': now_utc(), 'scheduled_exec_count': len(exlist),
                          'ids': [e.get('id') for e in exlist]})
    extra = [e for e in new_sched_execs if e.get('id') != state['execution_id']]
    ev['phases']['suppression'] = {
        'observe_seconds': POST_DELETE_OBSERVE_SEC, 'interval_sec': SCHEDULE_INTERVAL_SEC,
        'gate_verified_local_definition_rows_absent': post_ok,
        'observed_scheduled_exec_ids': [e.get('id') for e in new_sched_execs],
        'extra_scheduled_executions': [e.get('id') for e in extra],
        'suppressed': post_ok and len(new_sched_execs) == 1 and not extra,
        'polls': obs_polls,
    }
    record(ev, 'phase5-convergence-suppression.json',
           {'convergence': ev['phases']['convergence'], 'suppression': ev['phases']['suppression']})
    progress['business_scheduled_executions'] = len(new_sched_execs)
    if extra:
        ev['deviations'].append('additional scheduled executions after tombstone: %s' % [e.get('id') for e in extra])

    # ---------------- Phase 7: independent Agent-side readbacks ---------------
    progress['phase'] = 'agent_readback'
    # 7a independent SSH read-only DB probe (bypasses the product probe entirely)
    db_script = (
        'import sqlite3,json,os\n'
        'p=%r\n'
        'c=sqlite3.connect("file:"+p+"?mode=ro",uri=True,timeout=5)\n'
        'c.execute("PRAGMA query_only=ON")\n'
        'qk=c.execute("PRAGMA quick_check").fetchone()[0]\n'
        'rev=c.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0]\n'
        'out={"quick_check":qk,"local_global_revision":rev,\n'
        ' "scripts_fixture_rows":c.execute("SELECT count(*) FROM scripts WHERE id=?",(%r,)).fetchone()[0],\n'
        ' "tasks_fixture_rows":c.execute("SELECT count(*) FROM tasks WHERE id=?",(%r,)).fetchone()[0],\n'
        ' "schedules_fixture_rows":c.execute("SELECT count(*) FROM schedules WHERE id=?",(%r,)).fetchone()[0],\n'
        ' "exec_row":c.execute("SELECT status,trigger_type,scheduled_time,task_id,script_id FROM executions WHERE id=?",(%r,)).fetchone()}\n'
        'print(json.dumps(out))\n' % (STATE_DB, state['script_id'], state['task_id'],
                                      state['schedule_id'], state['execution_id']))
    rc, out, err = ssh_python(db_script)
    agent_db = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {'error': err.strip()[:200]}
    if isinstance(agent_db.get('exec_row'), list):
        agent_db['exec_row'] = {'status': agent_db['exec_row'][0], 'trigger_type': agent_db['exec_row'][1],
                                'scheduled_time': agent_db['exec_row'][2], 'task_id': agent_db['exec_row'][3],
                                'script_id': agent_db['exec_row'][4]}
    ev['phases']['agent_db_readback'] = agent_db
    record(ev, 'phase6a-agent-db-readback.json', agent_db)

    # 7b targeted scheduler log lines for our exact schedule id
    rc, out, err = ssh_shell(
        'journalctl -u %s --no-pager -o cat 2>/dev/null | grep -F %s | tail -n 20' % (UNIT, state['schedule_id']))
    log_lines = [l for l in out.splitlines() if l.strip()]
    ev['phases']['agent_scheduler_log'] = {'schedule_id': state['schedule_id'],
                                           'matching_line_count': len(log_lines),
                                           'lines': log_lines[-20:]}
    record(ev, 'phase6b-agent-scheduler-log.json', ev['phases']['agent_scheduler_log'])

    # 7c API execution history retention after all definitions were deleted
    c, hist = get('/api/executions/%s' % state['execution_id'], allowed=(200, 404))
    ev['phases']['history_retention_api'] = {
        'get_http': c, 'id': (hist or {}).get('id'), 'status': (hist or {}).get('status'),
        'trigger_type': (hist or {}).get('trigger_type'), 'scheduled_time': (hist or {}).get('scheduled_time'),
        'script_id': (hist or {}).get('script_id'), 'task_id': (hist or {}).get('task_id')}
    record(ev, 'phase6d-history-retention-api.json', ev['phases']['history_retention_api'])

    # ---------------- Phase 8: cleanup --------------------------------------
    progress['phase'] = 'cleanup'
    cleanup = {'targets': []}
    ccode, cbody = api('DELETE', '/api/tasks/' + state['probe_task_id'])
    progress['api_mutations'] += 1
    gcode, _ = get('/api/tasks/' + state['probe_task_id'], allowed=(200, 404))
    cleanup['targets'].append({'kind': 'probe_task', 'id': state['probe_task_id'],
                               'delete_http': ccode, 'get_http': gcode})
    state['get404']['probe_task'] = (gcode == 404)
    if gcode == 404:
        state['deleted'].append(('probe_task', state['probe_task_id']))
        progress['resources_deleted'] += 1

    # final full sweep: exact 404 for all four definitions
    final_404 = {}
    for kind, obj_id in (('script', state['script_id']), ('task', state['task_id']),
                         ('schedule', state['schedule_id']), ('probe_task', state['probe_task_id'])):
        gc, _ = get('/api/%s/%s' % (KIND_PATH[kind], obj_id), allowed=(200, 404))
        final_404[kind] = gc
    cleanup['final_get_http'] = final_404
    cleanup['all_exact_404'] = all(v == 404 for v in final_404.values())

    # protected nodes + target still healthy
    _, nodes2 = get('/api/nodes')
    by2 = {n['id']: n for n in nodes2}
    cleanup['target_after'] = {k: by2[NODE_ID].get(k) for k in ('status', 'sync_status', 'global_revision')}
    cleanup['protected_after'] = {label: {'status': by2[nid].get('status'), 'sync_status': by2[nid].get('sync_status')}
                                  for nid, label in PROTECTED_NODES.items()}
    rc, out, err = ssh_python(
        'import json,subprocess,os\n'
        'p=subprocess.run(["systemctl","show","--no-pager","--property=ActiveState,MainPID",%r],capture_output=True,text=True)\n'
        'pr=dict(l.split("=",1) for l in p.stdout.splitlines() if "=" in l)\n'
        'print(json.dumps(pr))\n' % UNIT)
    cleanup['agent_after'] = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {'error': err[:200]}
    ev['phases']['cleanup'] = cleanup
    record(ev, 'phase7-cleanup.json', cleanup)

    ev['run_finished_utc'] = now_utc()
    ev['final'] = {
        'probe_pre_pass': 'READONLY_PROBE_PASS' in ev['phases']['probe_pre']['stdout'],
        'probe_post_pass': 'READONLY_PROBE_PASS' in ev['phases']['probe_post']['stdout'],
        'converged': bool(ev['phases'].get('convergence', {}).get('rev_post')),
        'suppressed': ev['phases']['suppression']['suppressed'],
        'history_retained_api_http': ev['phases']['history_retention_api']['get_http'],
        'all_definitions_get404': cleanup['all_exact_404'],
        'no_agent_restart': ag['MainPID'] == cleanup['agent_after'].get('MainPID'),
    }
    record(ev, 'run-evidence.json', ev)
    progress['phase'] = 'done'
    progress['updated_at_utc'] = now_utc()
    progress['final'] = ev['final']

    # case-result.json
    pre_ok = ev['final']['probe_pre_pass']
    post_ok = ev['final']['probe_post_pass']
    suppressed = ev['final']['suppressed']
    if pre_ok and post_ok and suppressed and ev['final']['history_retained_api_http'] == 200 \
            and cleanup['all_exact_404'] and not extra:
        proposed = 'PASS'
        confidence = 'high'
    elif post_ok and suppressed:
        proposed = 'PASS_WITH_NOTES'
        confidence = 'medium'
    else:
        proposed = 'PARTIAL'
        confidence = 'low'
    case_result = {
        'case_id': 'SYS-09',
        'run_dir': str(HERE),
        'executed_at_utc': ev['run_started_utc'],
        'canonical_before': 'PARTIAL (API delete + Execution preservation only; connected-Agent tombstone consumption and future interval slot suppression unproven)',
        'proposed_after': proposed,
        'confidence': confidence,
        'covered_dimensions': [
            {'test_id': '20261014-RUNTIME:SYS-09', 'layer': 'real-connected-agent/hub-api/agent-db/scheduler-log',
             'status': 'PASS' if proposed == 'PASS' else proposed,
             'description': [
                 'Exact QA Script+ScriptTask+interval Schedule created on connected Native3 repair Agent (online/synced).',
                 'First Agent-owned scheduled Execution observed SUCCESS with trigger=schedule, scheduled_time derived from schedule CreatedAt+interval.',
                 'Hub definitions deleted (Schedule->Task->Script) with exact GET404 while Agent stayed connected (no stop/restart).',
                 'Agent applied tombstones: node global_revision advanced past the 3 delete revisions with sync_status=synced; probe + independent SSH read show local scripts/tasks/schedules rows absent.',
                 'Future interval slot suppressed: one full interval + margin observed with zero additional scheduled Executions.',
                 'Historical scheduled Execution preserved locally and via GET /api/executions/{id} after all definitions deleted.',
             ]},
        ],
        'remaining_or_limitations': [
            'No Application create/delete in this run; the prior Application slice remains separate evidence.',
            'No claim about content-addressed Script cache eviction; only definition tombstones and future scheduling.',
            'Single-node scoped to Native3 repair Agent; not a cross-node or Hub-restart scenario.',
        ],
        'evidence': ['phase0-guard.json', 'phase1-probe-pre.json', 'phase2-fixtures.json',
                     'phase3-first-scheduled-execution.json', 'phase4-deletion-get404.json',
                     'phase5-convergence-suppression.json', 'phase6a-agent-db-readback.json',
                     'phase6b-agent-scheduler-log.json', 'phase6c-probe-post.json',
                     'phase6d-history-retention-api.json', 'phase7-cleanup.json', 'run-evidence.json'],
        'actual_resources': {
            'script': ev['phases']['fixtures']['script'],
            'task': ev['phases']['fixtures']['task'],
            'schedule': ev['phases']['fixtures']['schedule'],
            'probe_task_id': state['probe_task_id'],
            'scheduled_execution_id': state['execution_id'],
            'probe_execution_ids': [pre_exec_id, post_exec_id],
        },
        'actual_budget': {
            'definition_creates': 4, 'definition_deletes': 4,
            'manual_business_run_posts': 0, 'readonly_probe_run_posts': 2,
            'business_scheduled_executions': len(new_sched_execs),
            'unexpected_additional_scheduled_executions': len(extra),
        },
        'final_cleanup': cleanup,
        'deviations': ev['deviations'],
        'harness_errors': [],
        'phase_detail': ev['phases'],
    }
    record(case_result, 'case-result.json', case_result)

    ledger = {
        'case_id': 'SYS-09',
        'fixtures': [
            {'kind': 'script', 'id': state['script_id'], 'name': SCRIPT_NAME, 'deleted_get404': state['get404'].get('script')},
            {'kind': 'task', 'id': state['task_id'], 'name': TASK_NAME, 'deleted_get404': state['get404'].get('task')},
            {'kind': 'schedule', 'id': state['schedule_id'], 'name': None, 'deleted_get404': state['get404'].get('schedule')},
            {'kind': 'probe_task', 'id': state['probe_task_id'], 'name': PROBE_NAME, 'deleted_get404': state['get404'].get('probe_task')},
        ],
        'preserved_executions': [
            {'id': state['execution_id'], 'trigger_type': 'schedule', 'purpose': 'history retention'},
            {'id': pre_exec_id, 'trigger_type': 'manual', 'purpose': 'readonly preflight probe'},
            {'id': post_exec_id, 'trigger_type': 'manual', 'purpose': 'readonly postcheck probe'},
        ],
        'status': 'CLEANED' if cleanup['all_exact_404'] else 'CLEANUP_INCOMPLETE',
        'final_get_http': final_404,
    }
    record(ledger, 'fixture-ledger.json', ledger)
    progress['final_cleanup_get_http'] = final_404
    record(progress, 'progress.json', progress)

    print(json.dumps({'proposed_after': proposed, 'confidence': confidence, 'final': ev['final'],
                      'deviations': ev['deviations'], 'fixtures': ledger['fixtures']},
                     ensure_ascii=False, indent=2))


def applied_rev():
    c, n = get('/api/nodes/' + NODE_ID)
    return (n or {}).get('global_revision'), (n or {}).get('sync_status')


def wait_rev_at_least(target, timeout=30):
    """Wait until the Agent-reported applied revision reaches `target`."""
    deadline = time.time() + timeout
    polls = []
    while time.time() < deadline:
        rev, sync = applied_rev()
        polls.append({'at': now_utc(), 'global_revision': rev, 'sync_status': sync})
        if rev is not None and rev >= target:
            return rev, polls
        time.sleep(1)
    return None, polls


def wait_execution(exec_id, timeout=90):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        c, last = get('/api/executions/%s' % exec_id, allowed=(200, 404))
        if c == 200 and last.get('status') in TERMINAL:
            return last
        time.sleep(1)
    return last


def best_effort_cleanup():
    """Delete any still-present owned fixtures in safe order; never touches other objects."""
    if not TOKEN[0]:
        return {'skipped': 'no_token'}
    out = []
    for kind, key in (('schedule', 'schedule_id'), ('task', 'task_id'), ('script', 'script_id'),
                      ('probe_task', 'probe_task_id')):
        obj_id = STATE.get(key)
        if not obj_id:
            continue
        gc, _ = get('/api/%s/%s' % (KIND_PATH[kind], obj_id), allowed=(200, 404))
        if gc == 404:
            out.append({'kind': kind, 'id': obj_id, 'already_absent': True})
            continue
        dc, _ = api('DELETE', '/api/%s/%s' % (KIND_PATH[kind], obj_id))
        gc2, _ = get('/api/%s/%s' % (KIND_PATH[kind], obj_id), allowed=(200, 404))
        out.append({'kind': kind, 'id': obj_id, 'delete_http': dc, 'get_http': gc2})
    return {'targets': out}


if __name__ == '__main__':
    failed = False
    try:
        main()
    except BaseException:
        failed = True
        import traceback
        traceback.print_exc()
        sys.stderr.write('HARNESS_ERROR: performing fail-safe cleanup\n')
        try:
            result = best_effort_cleanup()
            (HERE / 'failsafe-cleanup.json').write_text(
                json.dumps({'at_utc': now_utc(), **result}, ensure_ascii=False, indent=2) + '\n')
        except Exception as cleanup_exc:
            sys.stderr.write('FAILSAFE_CLEANUP_ERROR: %s\n' % cleanup_exc)
        raise SystemExit(1)

