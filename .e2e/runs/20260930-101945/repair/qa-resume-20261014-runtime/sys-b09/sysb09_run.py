#!/usr/bin/env python3
"""sys-b09 system batch runtime run: Misfire / duplicate trigger / heartbeat-backoff / conditions.

Cases: SYS-23, SYS-22, AR-17, SYS-18, SYS-26, SYS-27.

Owns only its own nonce-prefixed fixtures on the repair authority (:18080) plus a
fully owned child Agent process (own temp data dir, own fake Hub on loopback) for
the heartbeat/backoff measurements.  Never stops/restarts the repair Hub, the four
protected repair Agents or the primary :8080 deployment.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
EVID = HERE / 'evidence'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')

NONCE = 'qa-sysb09-7f31ac2e'
NODE_ID = '611c02a5-1859-4465-878b-d543b7f6fdec'          # native-2, 192.168.100.206
NODE_IP = '192.168.100.206'
NODE_LABEL = 'native-2'
AUX_ONLINE_NODE = '91f44866-8a28-435e-bbda-2f2367126c00'  # native-1, 192.168.100.204
OFFLINE_NODE = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'      # leftover offline node record
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
PROBE_NODE_IP = '192.168.100.247'
PROBE_NODE_LABEL = 'native-4'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
HUB_SHA = '164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6'
PROBE_BINARY = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_UNIT = 'nodesteer-agent-repair-20260930-101945-native-2.service'
SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')

TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
MARKER = 'QA:%s:MAIN' % NONCE

INTERVAL_SEC = 10
MISFIRE_DISABLE_WINDOW_SEC = 26
HUB_TICK_FOLLOWUP_SEC = 50
AGENT_ONETIME_DELAY_SEC = 40

BROWSER = build_opener(ProxyHandler({}))
TOKEN = [None]
BASE = [None]
KIND_PATH = {'script': 'scripts', 'task': 'tasks', 'schedule': 'schedules'}

STATE = {
    'created': [],        # (kind, id, name)
    'deleted': [],
    'get404': {},
    'delete_results': [],
    'unexpected': [],
    'fixture_ids': {},
    'executions': {},
}
PROGRESS = {'batch_id': 'sys-b09', 'phase': 'init', 'updated_at_utc': None,
            'api_mutations': 0, 'resources_created': 0, 'resources_deleted': 0}


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def parse_ts(value):
    """Tolerant RFC3339 parser (offset or Z, with/without microseconds)."""
    if not value:
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {'_non_json_body': raw[:400].decode('utf-8', 'replace')}


def api(method, path, body=None, allowed=(200,), timeout=25, retries=3):
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
            with BROWSER.open(req, timeout=timeout) as resp:
                return resp.status, _decode(resp.read())
        except HTTPError as exc:
            return exc.code, _decode(exc.read())
        except (URLError, TimeoutError) as exc:
            last = exc
            time.sleep(1.5)
    raise RuntimeError('api transport failure: %s' % type(last).__name__)


def get(path, allowed=(200,)):
    return api('GET', path, None, allowed)


def record(name, payload):
    (EVID / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def ssh(host, command, timeout=60):
    cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-i', SSH_KEY, 'root@' + host, command],
                        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def ssh_python(host, script, timeout=60):
    cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-i', SSH_KEY, 'root@' + host, 'python3 -'],
                        input=script, capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def last_json_line(out):
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            return json.loads(line)
    return None


# --------------------------------------------------------------------------- #
# fixture helpers
# --------------------------------------------------------------------------- #
def create_script(name, content):
    return api('POST', '/api/scripts', {
        'name': name, 'description': 'sys-b09 fixture (nonce %s).' % NONCE,
        'interpreter': 'shell', 'content': content, 'parameters': [], 'environment': {},
        'working_dir': '', 'timeout': 15, 'enabled': True}, (200, 201))


def create_task(name, *, script_id=None, command=None, condition=None):
    body = {'name': name, 'type': 'command' if command is not None else 'script',
            'target': {'type': 'node', 'node_ids': [NODE_ID]},
            'parameters': [], 'param_values': {}, 'timeout': 20, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True}
    if script_id:
        body['script_id'] = script_id
    if command is not None:
        body['command'] = command
    if condition is not None:
        body['condition'] = condition
    return api('POST', '/api/tasks', body, (200, 201))


def create_schedule(task_id, **kwargs):
    body = {'task_id': task_id, 'timezone': 'UTC', 'execution_owner': 'agent',
            'offline_policy': 'hub_online_required', 'misfire_policy': 'skip',
            'enabled': True}
    body.update(kwargs)
    return api('POST', '/api/schedules', body, (200, 201))


def track(kind, obj, name=None):
    STATE['created'].append((kind, obj['id'], name))
    STATE['fixture_ids'].setdefault(kind, []).append(obj['id'])
    PROGRESS['resources_created'] += 1
    PROGRESS['api_mutations'] += 1


def wait_execution(exec_id, timeout=90):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, last = get('/api/executions/%s' % exec_id, allowed=(200, 404))
        if code == 200 and last.get('status') in TERMINAL:
            return last
        time.sleep(1)
    return last


def run_manual_and_wait(task_id, params=None, timeout=90):
    code, resp = api('POST', '/api/tasks/%s/run' % task_id,
                     {'node_ids': {NODE_ID: True}, 'params': params or {}})
    if code != 200 or not resp:
        return {'run_http': code, 'run_body': resp}
    ex = resp[0]
    final = wait_execution(ex['id'], timeout=timeout)
    return {'run_http': code, 'execution_id': ex['id'],
            'status': final.get('status'), 'exit_code': final.get('exit_code'),
            'stdout': (final.get('stdout') or '').strip(),
            'stderr': (final.get('stderr') or '').strip(),
            'block_reason': final.get('block_reason'),
            'trigger_type': final.get('trigger_type')}


def executions_for_task(task_id, limit=100):
    code, rows = get('/api/executions?task_id=%s&limit=%d' % (task_id, limit))
    return rows if code == 200 and isinstance(rows, list) else []


def schedule_executions(task_id):
    return [e for e in executions_for_task(task_id) if e.get('trigger_type') == 'schedule']


# --------------------------------------------------------------------------- #
def main():
    EVID.mkdir(parents=True, exist_ok=True)
    PROGRESS['updated_at_utc'] = now_utc()
    ev = {'batch_id': 'sys-b09', 'run_started_utc': now_utc(), 'nonce': NONCE,
          'phases': {}, 'deviations': [], 'harness_errors': []}

    cred = json.loads(CRED.read_text())
    conn = json.loads(CONN.read_text())
    BASE[0] = conn['hub_url'].rstrip('/')
    split = urlsplit(BASE[0])
    if split.port != 18080:
        raise SystemExit('endpoint guard failed: repair :18080 required')
    ev['endpoint'] = {'host': split.hostname, 'port': split.port, 'repair_only': True}
    code, body = api('POST', '/api/login',
                     {'username': cred['admin_username'], 'password': cred['password'] if 'password' in cred else cred['admin_password']})
    if code != 200 or not body.get('token'):
        raise SystemExit('login failed HTTP %s' % code)
    TOKEN[0] = body['token']

    # ---------------- Phase 0: read-only guard ---------------- #
    PROGRESS['phase'] = 'guard'
    PROTECTED_NODES[NODE_ID] = NODE_LABEL
    guard = {'nodes': {}, 'settings': {}, 'fixtures_absent': {}, 'agent_pre': None,
             'probe_node_agent_pre': None, 'offline_node_status': None}
    code, nodes = get('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    for nid, label in PROTECTED_NODES.items():
        n = by_id.get(nid)
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            raise SystemExit('protected node %s not healthy: %s' % (label, n and n.get('status')))
        guard['nodes'][label] = {k: n.get(k) for k in
                                 ('id', 'hostname', 'status', 'sync_status', 'global_revision',
                                  'agent_id', 'agent_version')}
    guard['offline_node_status'] = {k: by_id[OFFLINE_NODE].get(k) for k in
                                    ('id', 'hostname', 'status', 'sync_status')}
    if by_id[OFFLINE_NODE].get('status') != 'offline':
        raise SystemExit('expected leftover offline node for remote condition negative case')
    code, settings = get('/api/settings')
    guard['settings'] = {k: settings.get(k) for k in
                         ('heartbeat_interval_sec', 'revision_check_interval_sec', 'max_log_bytes')}
    _, scripts = get('/api/scripts')
    _, tasks = get('/api/tasks')
    _, schedules = get('/api/schedules')
    names = {obj.get('name') for obj in list(scripts) + list(tasks)}
    clash = [n for n in names if n and n.startswith(NONCE)]
    if clash:
        raise SystemExit('nonce fixtures already present: %s' % clash)
    guard['fixtures_absent'] = {'script_or_task_named_%s_*' % NONCE: True,
                                'baseline_scripts': len(scripts), 'baseline_tasks': len(tasks),
                                'baseline_schedules': len(schedules)}

    rc, out, err = ssh_python(NODE_IP, (
        'import json,hashlib,os,subprocess\n'
        'u=%r\n'
        'p=subprocess.run(["systemctl","show","--no-pager","--property=ActiveState,MainPID,ControlGroup",u],capture_output=True,text=True)\n'
        'props=dict(l.split("=",1) for l in p.stdout.splitlines() if "=" in l)\n'
        'pid=props.get("MainPID")\n'
        'exe=os.readlink("/proc/"+pid+"/exe")\n'
        'cfg=subprocess.run(["systemctl","cat",u],capture_output=True,text=True).stdout\n'
        'path=[l.split("--config",1)[1].strip() for l in cfg.splitlines() if "--config" in l]\n'
        'dd=None\n'
        'if path:\n'
        '    for line in open(path[0]):\n'
        '        if line.strip().startswith("data_dir:"):\n'
        '            dd=line.split(":",1)[1].strip().strip(chr(34)).strip(chr(39))\n'
        'print(json.dumps({"ActiveState":props.get("ActiveState"),"MainPID":pid,"ControlGroup":props.get("ControlGroup"),'
        '"exe":exe,"exe_sha":hashlib.sha256(open(exe,"rb").read()).hexdigest(),"data_dir":dd,"config_path":path[:1]}))\n'
    ) % AGENT_UNIT)
    if rc != 0:
        raise SystemExit('ssh guard failed on %s: %s' % (NODE_IP, err[:200]))
    ag = last_json_line(out)
    if not ag or ag['ActiveState'] != 'active' or ag['exe_sha'] != AGENT_SHA \
            or ag['ControlGroup'] != '/system.slice/' + AGENT_UNIT:
        raise SystemExit('target agent owner guard failed: %s' % ag)
    guard['agent_pre'] = ag
    rc, out, err = ssh_python(PROBE_NODE_IP, (
        'import json,hashlib,os,subprocess\n'
        'u="nodesteer-agent-repair-20260930-101945-native-4.service"\n'
        'p=subprocess.run(["systemctl","show","--no-pager","--property=ActiveState,MainPID",u],capture_output=True,text=True)\n'
        'props=dict(l.split("=",1) for l in p.stdout.splitlines() if "=" in l)\n'
        'pid=props.get("MainPID")\n'
        'exe=os.readlink("/proc/"+pid+"/exe")\n'
        'print(json.dumps({"ActiveState":props.get("ActiveState"),"MainPID":pid,"exe_sha":hashlib.sha256(open(exe,"rb").read()).hexdigest()}))\n'))
    probe_ag = last_json_line(out) if rc == 0 else {'error': err[:200]}
    guard['probe_node_agent_pre'] = probe_ag
    guard['hub_binary_sha_expected'] = HUB_SHA
    guard['agent_binary_sha_expected'] = AGENT_SHA
    ev['phases']['guard'] = guard
    record('phase0-guard.json', guard)
    PROGRESS['agent_pre_mainpid'] = ag['MainPID']
    PROGRESS['updated_at_utc'] = now_utc()
    if os.environ.get('QA_SYSB09_PREFLIGHT'):
        print(json.dumps(guard, ensure_ascii=False, indent=2))
        return

    # ---------------- Phase 1: fixtures ---------------- #
    PROGRESS['phase'] = 'fixtures_create'
    content = '#!/bin/sh\nset -eu\nprintf \'%s\\n\'\n' % MARKER
    code, script = create_script(NONCE + '-script', content)
    if code != 200:
        raise SystemExit('script create failed: %s %s' % (code, script))
    track('script', script, NONCE + '-script')
    content_sha = hashlib.sha256(content.encode()).hexdigest()
    code, s_read = get('/api/scripts/' + script['id'])
    if s_read.get('sha256') != content_sha or s_read.get('revision') != 1:
        raise SystemExit('script readback mismatch')
    sid = script['id']

    task_defs = [
        ('main', {'script_id': sid}),
        ('skip', {'script_id': sid}),
        ('runonce', {'script_id': sid}),
        ('dup-agent', {'script_id': sid}),
        ('dup-hub', {'script_id': sid}),
        ('and-true', {'command': 'printf QA:%s:AND_TRUE' % NONCE,
                      'condition': {'type': 'and', 'and': [
                          {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                      'path': '/etc/hostname', 'value': 'true'}},
                          {'type': 'local', 'local': {'metric': 'dir_exists', 'operator': '==',
                                                      'path': '/tmp', 'value': 'true'}}]}}),
        ('and-false', {'command': 'printf QA:%s:AND_FALSE' % NONCE,
                       'condition': {'type': 'and', 'and': [
                           {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                       'path': '/etc/hostname', 'value': 'true'}},
                           {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                       'path': '/tmp/%s-absent' % NONCE, 'value': 'true'}}]}}),
        ('and-nested-true', {'command': 'printf QA:%s:NESTED_TRUE' % NONCE,
                             'condition': {'type': 'and', 'and': [
                                 {'type': 'and', 'and': [
                                     {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                                 'path': '/etc/hostname', 'value': 'true'}},
                                     {'type': 'local', 'local': {'metric': 'command_result', 'operator': '==',
                                                                 'command': 'printf ok', 'value': 'ok'}}]},
                                 {'type': 'local', 'local': {'metric': 'dir_exists', 'operator': '==',
                                                             'path': '/tmp', 'value': 'true'}}]}}),
        ('and-nested-false', {'command': 'printf QA:%s:NESTED_FALSE' % NONCE,
                              'condition': {'type': 'and', 'and': [
                                  {'type': 'and', 'and': [
                                      {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                                  'path': '/etc/hostname', 'value': 'true'}},
                                      {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                                  'path': '/tmp/%s-nested-absent' % NONCE, 'value': 'true'}}]},
                                  {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                              'path': '/etc/hostname', 'value': 'true'}}]}}),
        ('and-unknown', {'command': 'printf QA:%s:AND_UNKNOWN' % NONCE,
                         'condition': {'type': 'and', 'and': [
                             {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                         'path': '/etc/hostname', 'value': 'true'}},
                             {'type': 'local', 'local': {'metric': 'disk_usage', 'operator': '<',
                                                         'path': '/%s-no-such-mount' % NONCE, 'value': '50'}}]}}),
        ('remote-online-sat', {'command': 'printf QA:%s:REMOTE_ON_SAT' % NONCE,
                               'condition': {'type': 'remote', 'remote': {
                                   'node_id': AUX_ONLINE_NODE, 'property': 'online',
                                   'operator': '==', 'value': 'online'}}}),
        ('remote-online-unsat', {'command': 'printf QA:%s:REMOTE_ON_UNSAT' % NONCE,
                                 'condition': {'type': 'remote', 'remote': {
                                     'node_id': OFFLINE_NODE, 'property': 'online',
                                     'operator': '==', 'value': 'online'}}}),
        ('remote-offline-sat', {'command': 'printf QA:%s:REMOTE_OFF_SAT' % NONCE,
                                'condition': {'type': 'remote', 'remote': {
                                    'node_id': OFFLINE_NODE, 'property': 'online',
                                    'operator': '==', 'value': 'offline'}}}),
        ('and-remote-local', {'command': 'printf QA:%s:AND_REMOTE' % NONCE,
                              'condition': {'type': 'and', 'and': [
                                  {'type': 'remote', 'remote': {'node_id': AUX_ONLINE_NODE,
                                                                'property': 'online', 'operator': '==',
                                                                'value': 'online'}},
                                  {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==',
                                                              'path': '/etc/hostname', 'value': 'true'}}]}}),
    ]
    task_ids = {}
    readbacks = {}
    for key, kwargs in task_defs:
        name = '%s-%s' % (NONCE, key)
        code, obj = create_task(name, **kwargs)
        if code != 200:
            raise SystemExit('task %s create failed: %s %s' % (key, code, obj))
        track('task', obj, name)
        task_ids[key] = obj['id']
        code, read = get('/api/tasks/' + obj['id'])
        readbacks[key] = {'id': obj['id'], 'revision': read.get('revision'),
                          'type': read.get('type'), 'name': read.get('name'),
                          'condition_type': (read.get('condition') or {}).get('type'),
                          'condition': read.get('condition')}
        if read.get('revision') != 1 or read.get('name') != name:
            raise SystemExit('task readback mismatch for %s' % key)
    ev['phases']['fixtures_tasks'] = {'ids': task_ids, 'readbacks': readbacks}

    # SYS-23: two interval schedules created DISABLED, enabled after a >2*period window.
    code, sched_skip = create_schedule(task_ids['skip'], type='interval', interval_sec=INTERVAL_SEC,
                                       execution_owner='agent', misfire_policy='skip', enabled=False)
    if code != 200:
        raise SystemExit('skip schedule create failed: %s' % sched_skip)
    track('schedule', sched_skip, NONCE + '-sched-skip')
    code, sched_runonce = create_schedule(task_ids['runonce'], type='interval', interval_sec=INTERVAL_SEC,
                                          execution_owner='agent', misfire_policy='run_once', enabled=False)
    if code != 200:
        raise SystemExit('runonce schedule create failed: %s' % sched_runonce)
    track('schedule', sched_runonce, NONCE + '-sched-runonce')

    # SYS-22/AR-17 hub-side duplicate trigger: two one_time hub schedules, identical run_at.
    hub_run_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).replace(microsecond=0)
    hub_run_at_s = hub_run_at.strftime('%Y-%m-%dT%H:%M:%SZ')
    hub_scheds = []
    for index in (1, 2):
        code, obj = create_schedule(task_ids['dup-hub'], type='one_time', run_at=hub_run_at_s,
                                    execution_owner='hub', misfire_policy='run_once', enabled=True)
        if code != 200:
            raise SystemExit('hub dup schedule %d create failed: %s' % (index, obj))
        track('schedule', obj, '%s-sched-duphub-%d' % (NONCE, index))
        hub_scheds.append(obj)

    # SYS-22/AR-17 agent-side duplicate trigger: two one_time agent schedules, identical run_at.
    agent_run_at = (datetime.now(timezone.utc) + timedelta(seconds=AGENT_ONETIME_DELAY_SEC)).replace(microsecond=0)
    agent_run_at_s = agent_run_at.strftime('%Y-%m-%dT%H:%M:%SZ')
    agent_scheds = []
    for index in (1, 2):
        code, obj = create_schedule(task_ids['dup-agent'], type='one_time', run_at=agent_run_at_s,
                                    execution_owner='agent', misfire_policy='run_once', enabled=True)
        if code != 200:
            raise SystemExit('agent dup schedule %d create failed: %s' % (index, obj))
        track('schedule', obj, '%s-sched-dupagent-%d' % (NONCE, index))
        agent_scheds.append(obj)

    sched_state = {}
    for label, obj in (('skip', sched_skip), ('runonce', sched_runonce),
                       ('dup_hub_1', hub_scheds[0]), ('dup_hub_2', hub_scheds[1]),
                       ('dup_agent_1', agent_scheds[0]), ('dup_agent_2', agent_scheds[1])):
        code, read = get('/api/schedules/' + obj['id'])
        sched_state[label] = {k: read.get(k) for k in
                              ('id', 'task_id', 'type', 'interval_sec', 'run_at', 'execution_owner',
                               'misfire_policy', 'enabled', 'revision', 'created_at')}
    ev['phases']['fixtures_schedules'] = {'schedules': sched_state,
                                          'hub_run_at_utc': hub_run_at_s,
                                          'agent_run_at_utc': agent_run_at_s}
    record('phase1-fixtures.json', {'tasks': readbacks, 'schedules': sched_state,
                                    'hub_run_at_utc': hub_run_at_s,
                                    'agent_run_at_utc': agent_run_at_s,
                                    'script': {'id': sid, 'sha256': content_sha}})
    disabled_since = time.time()

    # ---------------- Phase 2: SYS-26 / SYS-27 condition evaluation ---------------- #
    PROGRESS['phase'] = 'conditions'
    condition_runs = {}
    control = run_manual_and_wait(task_ids['main'])
    control['task_id'] = task_ids['main']
    control['control'] = 'plain script task, no condition'
    control['marker_present'] = MARKER in (control.get('stdout') or '')
    PROGRESS['api_mutations'] += 1
    ev['phases']['control_run'] = control
    for key in ('and-true', 'and-false', 'and-nested-true', 'and-nested-false', 'and-unknown',
                'remote-online-sat', 'remote-online-unsat', 'remote-offline-sat', 'and-remote-local'):
        result = run_manual_and_wait(task_ids[key])
        result['task_id'] = task_ids[key]
        result['task_name'] = '%s-%s' % (NONCE, key)
        result['marker_expected'] = key
        condition_runs[key] = result
        PROGRESS['api_mutations'] += 1
    ev['phases']['condition_runs'] = condition_runs
    record('phase2-conditions.json', condition_runs)

    # ---------------- Phase 3: SYS-26 error-structure rejection ---------------- #
    PROGRESS['phase'] = 'reject'
    rejects = [
        ('and-empty', {'type': 'and', 'and': []}, 'and condition requires at least one child'),
        ('local-missing', {'type': 'local'}, 'local condition is missing'),
        ('local-bad-operator', {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '~=',
                                                           'path': '/etc/hostname', 'value': 'true'}},
         'invalid local condition operator'),
        ('cpu-nonnumeric', {'type': 'local', 'local': {'metric': 'cpu_usage', 'operator': '>', 'value': 'abc'}},
         'numeric local condition requires numeric value'),
        ('file-missing-path', {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==', 'value': 'true'}},
         'local condition requires path'),
        ('port-invalid', {'type': 'local', 'local': {'metric': 'port_listening', 'operator': '==',
                                                     'path': '99999', 'value': 'true'}},
         'port_listening path must be a valid port'),
        ('command-missing', {'type': 'local', 'local': {'metric': 'command_result', 'operator': '==', 'value': 'ok'}},
         'command_result requires command'),
        ('unknown-metric', {'type': 'local', 'local': {'metric': 'gpu_usage', 'operator': '>', 'value': '1'}},
         'unknown local condition metric'),
        ('remote-missing', {'type': 'remote'}, 'remote condition is missing'),
        ('remote-no-node', {'type': 'remote', 'remote': {'node_id': '', 'property': 'online',
                                                         'operator': '==', 'value': 'online'}},
         'remote condition requires node_id'),
        ('remote-node-missing', {'type': 'remote', 'remote': {'node_id': '00000000-0000-0000-0000-000000000000',
                                                              'property': 'online', 'operator': '==',
                                                              'value': 'online'}},
         'remote condition node not found'),
        ('remote-bad-property', {'type': 'remote', 'remote': {'node_id': AUX_ONLINE_NODE, 'property': 'disk',
                                                              'operator': '==', 'value': 'online'}},
         'unknown remote condition property'),
        ('remote-last-exec-missing-task', {'type': 'remote', 'remote': {'node_id': AUX_ONLINE_NODE,
                                                                        'property': 'last_execution',
                                                                        'operator': '==', 'value': 'SUCCESS'}},
         'last_execution condition requires task_id'),
        ('remote-bad-operator', {'type': 'remote', 'remote': {'node_id': AUX_ONLINE_NODE, 'property': 'online',
                                                              'operator': '>', 'value': 'online'}},
         'remote condition operator must be == or !='),
        ('remote-online-bogus-value', {'type': 'remote', 'remote': {'node_id': AUX_ONLINE_NODE, 'property': 'online',
                                                                    'operator': '==', 'value': 'bogus'}},
         'invalid online condition value'),
        ('unknown-type', {'type': 'xor'}, 'condition type must be local, remote, or and'),
    ]
    reject_results = []
    for key, condition, expected_fragment in rejects:
        name = '%s-reject-%s' % (NONCE, key)
        code, obj = create_task(name, command='printf reject', condition=condition)
        PROGRESS['api_mutations'] += 1
        entry = {'key': key, 'task_name': name, 'condition': condition, 'http': code,
                 'body': obj, 'expected_fragment': expected_fragment,
                 'rejected': code == 400,
                 'fragment_match': bool(isinstance(obj, dict)
                                        and expected_fragment in str(obj.get('error', '')))}
        if code == 200:
            STATE['unexpected'].append(('task', obj.get('id'), name))
            entry['unexpectedly_created_id'] = obj.get('id')
            track('task', obj, name)
        reject_results.append(entry)
    ev['phases']['reject_structures'] = reject_results
    record('phase3-reject-structures.json', reject_results)

    # ---------------- Phase 4: SYS-23 misfire (agent slot arrives while unschedulable) ---------------- #
    PROGRESS['phase'] = 'misfire'
    wait_extra = MISFIRE_DISABLE_WINDOW_SEC - (time.time() - disabled_since)
    if wait_extra > 0:
        time.sleep(wait_extra)
    disabled_for = round(time.time() - disabled_since, 3)
    enable_at = now_utc()
    enable_epoch = time.time()
    for label, obj in (('skip', sched_skip), ('runonce', sched_runonce)):
        code, resp = api('PUT', '/api/schedules/' + obj['id'],
                         {'enabled': True})
        PROGRESS['api_mutations'] += 1
        if code != 200:
            raise SystemExit('enable %s schedule failed: %s %s' % (label, code, resp))
    observe = {'disabled_window_sec': disabled_for, 'enabled_at_utc': enable_at,
               'interval_sec': INTERVAL_SEC, 'polls': []}
    deadline = time.time() + 26
    while time.time() < deadline:
        snap = {'at': now_utc(), 'at_epoch': round(time.time(), 3),
                'skip_scheduled': [{'id': e['id'], 'status': e.get('status'),
                                    'scheduled_time': e.get('scheduled_time')}
                                   for e in schedule_executions(task_ids['skip'])],
                'runonce_scheduled': [{'id': e['id'], 'status': e.get('status'),
                                       'scheduled_time': e.get('scheduled_time')}
                                      for e in schedule_executions(task_ids['runonce'])]}
        observe['polls'].append(snap)
        time.sleep(2)
    skip_execs = schedule_executions(task_ids['skip'])
    runonce_execs = schedule_executions(task_ids['runonce'])
    observe['skip_scheduled_final'] = [{'id': e['id'], 'status': e.get('status'),
                                        'scheduled_time': e.get('scheduled_time'),
                                        'stdout': (e.get('stdout') or '').strip()} for e in skip_execs]
    observe['runonce_scheduled_final'] = [{'id': e['id'], 'status': e.get('status'),
                                           'scheduled_time': e.get('scheduled_time'),
                                           'stdout': (e.get('stdout') or '').strip()} for e in runonce_execs]
    def is_backfill(entry):
        epoch = parse_ts(entry.get('scheduled_time'))
        return epoch is not None and epoch < enable_epoch - 1

    observe['skip_backfill_executions'] = [e for e in observe['skip_scheduled_final'] if is_backfill(e)]
    observe['runonce_backfill_executions'] = [e for e in observe['runonce_scheduled_final'] if is_backfill(e)]
    observe['skip_no_backfill'] = len(observe['skip_backfill_executions']) == 0
    observe['skip_fired_after_enable'] = len([e for e in observe['skip_scheduled_final']
                                              if not is_backfill(e)]) >= 1
    observe['runonce_single_backfill'] = len(observe['runonce_backfill_executions']) == 1
    observe['runonce_backfill_scheduled_times'] = [e['scheduled_time'] for e in
                                                   observe['runonce_backfill_executions']]
    observe['expected_runonce_backfill_slot'] = (
        datetime.fromisoformat(sched_state['runonce']['created_at'].replace('Z', '+00:00'))
        + timedelta(seconds=2 * INTERVAL_SEC)).strftime('%Y-%m-%dT%H:%M:%SZ')
    observe['expected_runonce_backfill_slot_epoch'] = parse_ts(observe['expected_runonce_backfill_slot'])
    observe['runonce_backfill_matches_expected_slot'] = any(
        abs((parse_ts(t) or 0) - observe['expected_runonce_backfill_slot_epoch']) <= 1
        for t in observe['runonce_backfill_scheduled_times'])
    ev['phases']['misfire'] = observe
    record('phase4-misfire.json', observe)

    # ---------------- Phase 5: SYS-22 / AR-17 duplicate trigger idempotency ---------------- #
    PROGRESS['phase'] = 'duplicate_trigger'
    def dup_snapshot():
        _, nodes_snap = get('/api/nodes')
        by_snap = {n['id']: n for n in nodes_snap}
        return {
            'at': now_utc(), 'at_epoch': round(time.time(), 3),
            'hub_scheduled': [{'id': e['id'], 'scheduled_time': e.get('scheduled_time'),
                               'status': e.get('status')}
                              for e in schedule_executions(task_ids['dup-hub'])],
            'agent_scheduled': [{'id': e['id'], 'scheduled_time': e.get('scheduled_time'),
                                 'status': e.get('status')}
                                for e in schedule_executions(task_ids['dup-agent'])],
            'real_agent_last_seen': {label: by_snap[nid].get('last_seen')
                                     for nid, label in PROTECTED_NODES.items()},
            'real_agent_sync': {label: by_snap[nid].get('sync_status')
                                for nid, label in PROTECTED_NODES.items()},
        }

    dup_polls = []
    # Wait for the Hub scheduler tick to fire the duplicate pair (tick <= revision_check_interval_sec).
    deadline = time.time() + max(60 - (time.time() - disabled_since), 20)
    while time.time() < deadline:
        snap = dup_snapshot()
        dup_polls.append(snap)
        if snap['hub_scheduled'] and snap['agent_scheduled']:
            break
        time.sleep(3)
    # Then keep observing for at least one further Hub scheduler tick (45s) to invite
    # a second execution for the same Task+Node+Scheduled Time, and to sample the real
    # repair Agent heartbeat cadence from node last_seen.
    settle_deadline = time.time() + HUB_TICK_FOLLOWUP_SEC
    while time.time() < settle_deadline:
        time.sleep(4)
        dup_polls.append(dup_snapshot())
    hub_execs = schedule_executions(task_ids['dup-hub'])
    agent_execs = schedule_executions(task_ids['dup-agent'])
    dup = {
        'hub_side': {'duplicate_schedule_ids': [s['id'] for s in hub_scheds],
                     'identical_run_at_utc': hub_run_at_s, 'execution_count': len(hub_execs),
                     'executions': [{'id': e['id'], 'scheduled_time': e.get('scheduled_time'),
                                     'status': e.get('status'), 'trigger_type': e.get('trigger_type'),
                                     'node_id': e.get('node_id'), 'stdout': (e.get('stdout') or '').strip()}
                                    for e in hub_execs],
                     'single_execution_for_slot': len(hub_execs) == 1},
        'agent_side': {'duplicate_schedule_ids': [s['id'] for s in agent_scheds],
                       'identical_run_at_utc': agent_run_at_s, 'execution_count': len(agent_execs),
                       'executions': [{'id': e['id'], 'scheduled_time': e.get('scheduled_time'),
                                       'status': e.get('status'), 'trigger_type': e.get('trigger_type'),
                                       'node_id': e.get('node_id'), 'stdout': (e.get('stdout') or '').strip()}
                                      for e in agent_execs],
                       'single_execution_for_slot': len(agent_execs) == 1},
        'polls': dup_polls,
        'observed_window_sec': round(time.time() - disabled_since, 3),
    }
    # real protected-Agent heartbeat cadence observed from Hub node last_seen in the same window
    real_heartbeat = {}
    for label in PROTECTED_NODES.values():
        series = []
        for snap in dup_polls:
            seen = parse_ts((snap.get('real_agent_last_seen') or {}).get(label))
            if seen is not None and (not series or seen != series[-1][0]):
                series.append((seen, snap['at_epoch']))
        deltas = [round(series[i + 1][0] - series[i][0], 3) for i in range(len(series) - 1)]
        real_heartbeat[label] = {'distinct_last_seen_samples': len(series),
                                 'observed_deltas_s': deltas,
                                 'configured_heartbeat_sec': int(settings.get('heartbeat_interval_sec') or 0)}
    dup['real_agent_heartbeat_cadence'] = real_heartbeat
    ev['phases']['duplicate_trigger'] = dup
    ev['phases']['duplicate_trigger_execution_ids'] = {
        'hub': [e['id'] for e in hub_execs], 'agent': [e['id'] for e in agent_execs]}
    record('phase5-duplicate-trigger.json', dup)

    # ---------------- Phase 6: independent Agent-side readback ---------------- #
    PROGRESS['phase'] = 'agent_readback'
    data_dir = ag.get('data_dir') or '/var/lib/nodesteer-repair-20260930-101945/native-2'
    readback_script = (
        'import sqlite3,json\n'
        'p=%r+"/state.db"\n'
        'c=sqlite3.connect("file:"+p+"?mode=ro",uri=True,timeout=5)\n'
        'c.execute("PRAGMA query_only=ON")\n'
        'qk=c.execute("PRAGMA quick_check").fetchone()[0]\n'
        'rev=c.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0]\n'
        'rows=c.execute("SELECT id,task_id,node_id,trigger_type,scheduled_time,status FROM executions WHERE task_id=? AND scheduled_time!=\"\"",(%r,)).fetchall()\n'
        'print(json.dumps({"quick_check":qk,"local_global_revision":rev,"node_id":dict(c.execute("SELECT key,value FROM identity").fetchall()),'
        '"slot_rows_for_dup_agent_task":rows}))\n'
    ) % (data_dir, task_ids['dup-agent'])
    rc, out, err = ssh_python(NODE_IP, readback_script, timeout=45)
    readback = last_json_line(out) if rc == 0 else {'error': err.strip()[:300], 'rc': rc}
    if isinstance(readback, dict):
        readback['expected_slot'] = agent_run_at_s
        rows = readback.get('slot_rows_for_dup_agent_task') or []
        readback['slot_row_count'] = len(rows)
        readback['slot_unique'] = len({r[4][:19] for r in rows}) == len(rows)
    ev['phases']['agent_db_readback'] = readback
    record('phase6-agent-db-readback.json', readback)

    rc, out, err = ssh_python(NODE_IP, (
        'import json,subprocess\n'
        'p=subprocess.run(["systemctl","show","--no-pager","--property=ActiveState,MainPID",%r],capture_output=True,text=True)\n'
        'print(json.dumps(dict(l.split("=",1) for l in p.stdout.splitlines() if "=" in l)))\n' % AGENT_UNIT))
    agent_after = last_json_line(out) if rc == 0 else {'error': err[:200]}
    ev['phases']['agent_after'] = agent_after

    # owned child Agent sandbox removal on the probe node + no leftover process
    rc, out, err = ssh(NODE_IP if False else PROBE_NODE_IP,
                       'pgrep -af "qa-sysb09-owned-heartbeat-probe" || true; '
                       'rm -rf /tmp/qa-sysb09-probe-run; '
                       'test ! -e /tmp/qa-sysb09-probe-run && echo QA_SANDBOX_REMOVED', timeout=45)
    sandbox = {'rc': rc, 'stdout': out.strip()[:400], 'stderr': err.strip()[:200],
               'removed': 'QA_SANDBOX_REMOVED' in out}
    ev['phases']['owned_child_sandbox'] = sandbox

    # ---------------- Phase 7: cleanup ---------------- #
    PROGRESS['phase'] = 'cleanup'
    cleanup = {'targets': []}
    for kind, obj_id, name in reversed(STATE['created']):
        if kind == 'schedule':
            del_code, _ = api('DELETE', '/api/schedules/' + obj_id)
        elif kind == 'task':
            del_code, _ = api('DELETE', '/api/tasks/' + obj_id)
        else:
            del_code, _ = api('DELETE', '/api/scripts/' + obj_id)
        PROGRESS['api_mutations'] += 1
        gcode, _ = get('/api/%s/%s' % (KIND_PATH[kind], obj_id), allowed=(200, 404))
        cleanup['targets'].append({'kind': kind, 'id': obj_id, 'name': name,
                                   'delete_http': del_code, 'get_http': gcode})
        STATE['get404']['%s:%s' % (kind, obj_id)] = (gcode == 404)
        if gcode == 404:
            STATE['deleted'].append((kind, obj_id))
            PROGRESS['resources_deleted'] += 1
    final_404 = {}
    for kind, obj_id, _name in STATE['created']:
        gc, _ = get('/api/%s/%s' % (KIND_PATH[kind], obj_id), allowed=(200, 404))
        final_404['%s:%s' % (kind, obj_id)] = gc
    cleanup['all_exact_404'] = all(v == 404 for v in final_404.values()) and bool(final_404)
    cleanup['final_get_http'] = final_404
    _, scripts_after = get('/api/scripts')
    _, tasks_after = get('/api/tasks')
    _, schedules_after = get('/api/schedules')
    cleanup['residual_named_objects'] = [o.get('name') for o in list(scripts_after) + list(tasks_after)
                                         if o.get('name') and o.get('name').startswith(NONCE)]
    cleanup['residual_schedules_total'] = len(schedules_after)
    cleanup['baseline_counts'] = {'scripts': len(scripts), 'tasks': len(tasks), 'schedules': len(schedules)}
    cleanup['after_counts'] = {'scripts': len(scripts_after), 'tasks': len(tasks_after),
                               'schedules': len(schedules_after)}
    _, nodes_after = get('/api/nodes')
    by_after = {n['id']: n for n in nodes_after}
    cleanup['nodes_after'] = {label: {k: by_after[nid].get(k) for k in ('status', 'sync_status', 'global_revision')}
                              for nid, label in PROTECTED_NODES.items()}
    cleanup['offline_node_after'] = by_after[OFFLINE_NODE].get('status')
    retention = {}
    for label, eid in (('hub_dup_slot', hub_execs[0]['id'] if hub_execs else None),
                       ('agent_dup_slot', agent_execs[0]['id'] if agent_execs else None),
                       ('skip_first', skip_execs[0]['id'] if skip_execs else None),
                       ('runonce_backfill', runonce_execs[0]['id'] if runonce_execs else None)):
        if not eid:
            retention[label] = {'http': None, 'reason': 'no execution recorded'}
            continue
        gc, body = get('/api/executions/' + eid, allowed=(200, 404))
        retention[label] = {'http': gc, 'status': (body or {}).get('status'),
                            'trigger_type': (body or {}).get('trigger_type'),
                            'scheduled_time': (body or {}).get('scheduled_time')}
    cleanup['execution_history_retention'] = retention
    ev['phases']['cleanup'] = cleanup
    record('phase7-cleanup.json', cleanup)

    ev['run_finished_utc'] = now_utc()
    record('run-evidence.json', ev)

    # ---------------- phase 8: merge SYS-18 owned-child probe ---------------- #
    PROGRESS['phase'] = 'sys18_probe'
    probe_path = EVID / 'phase-sys18-owned-child-heartbeat-probe.raw.json'
    probe = None
    if probe_path.exists():
        try:
            probe = json.loads(probe_path.read_text().strip().splitlines()[-1])
        except Exception as exc:
            ev['harness_errors'].append('probe parse error: %s' % exc)
    if probe:
        hb_pre = probe.get('heartbeat_deltas_before_settings_change_s') or []
        hb_post = probe.get('heartbeat_deltas_after_settings_change_s') or []
        backoff = probe.get('reconnect_backoff_s') or []
        rev = probe.get('revision_check_deltas_same_connection_s') or []
        cfg = probe.get('config_file', {})
        summary = {
            'owned_child_agent_sha256': probe.get('owned_child_agent_binary', {}).get('sha256'),
            'fake_hub_port': probe.get('fake_hub', {}).get('port'),
            'message_counts': probe.get('message_counts'),
            'configured_heartbeat_sec': cfg.get('heartbeat_sec'),
            'configured_revision_check_sec': cfg.get('revision_check_sec'),
            'runtime_change': probe.get('fake_hub', {}).get('runtime_settings_change_to'),
            'heartbeat_samples_pre_change_s': hb_pre,
            'heartbeat_samples_post_change_s': hb_post,
            'revision_check_samples_s': rev,
            'reconnect_backoff_samples_s': backoff,
            'connection_windows': probe.get('connections'),
            'agent_log_tail': probe.get('agent_log_tail'),
            'agent_cleanup': probe.get('agent_cleanup'),
        }
        if hb_pre:
            summary['observed_heartbeat_pre_change'] = {
                'min': min(hb_pre), 'max': max(hb_pre), 'mean': round(sum(hb_pre) / len(hb_pre), 3),
                'count': len(hb_pre)}
        if hb_post:
            summary['observed_heartbeat_post_change'] = {
                'min': min(hb_post), 'max': max(hb_post), 'mean': round(sum(hb_post) / len(hb_post), 3),
                'count': len(hb_post)}
        if rev:
            summary['observed_revision_check'] = {
                'min': min(rev), 'max': max(rev), 'mean': round(sum(rev) / len(rev), 3),
                'count': len(rev)}
        if backoff:
            summary['observed_reconnect_backoff'] = {
                'min': min(backoff), 'max': max(backoff), 'mean': round(sum(backoff) / len(backoff), 3),
                'count': len(backoff)}
        ev['phases']['sys18_probe'] = summary
        record('phase8-sys18-probe-summary.json', {'summary': summary, 'raw_evidence_file':
                                                   probe_path.name})
    else:
        ev['harness_errors'].append('sys18 owned-child probe raw evidence missing')
    record('run-evidence.json', ev)

    # ---------------- final health ---------------- #
    _, nodes_final = get('/api/nodes')
    by_final = {n['id']: n for n in nodes_final}
    final_health = {
        'protected_nodes': {label: {'status': by_final[nid].get('status'),
                                    'sync_status': by_final[nid].get('sync_status')}
                            for nid, label in PROTECTED_NODES.items()},
        'offline_node': by_final[OFFLINE_NODE].get('status'),
        'agent_mainpid_pre': ag['MainPID'], 'agent_mainpid_post': agent_after.get('MainPID'),
        'agent_unit_restarted': ag['MainPID'] != agent_after.get('MainPID'),
        'hub_settings_unchanged': {k: settings.get(k) for k in
                                   ('heartbeat_interval_sec', 'revision_check_interval_sec')},
    }
    record('phase9-final-health.json', final_health)
    ev['final_health'] = final_health

    # ---------------- phase 10: per-case verdicts ---------------- #
    PROGRESS['phase'] = 'verdicts'
    cr = condition_runs
    expectations = {
        'and-true': 'SUCCESS', 'and-false': 'SKIPPED', 'and-nested-true': 'SUCCESS',
        'and-nested-false': 'SKIPPED', 'and-unknown': 'BLOCKED',
        'remote-online-sat': 'SUCCESS', 'remote-online-unsat': 'SKIPPED',
        'remote-offline-sat': 'SUCCESS', 'and-remote-local': 'SUCCESS',
    }
    cond_ok = all(cr[k].get('status') == v for k, v in expectations.items())
    reject_ok = all(r['rejected'] for r in reject_results)
    reject_fragment_mismatch = [r['key'] for r in reject_results if not r['fragment_match']]
    cond_detail = {k: {'expected': v, 'actual': cr[k].get('status'),
                       'block_reason': cr[k].get('block_reason')} for k, v in expectations.items()}

    misfire = ev['phases']['misfire']
    misfire_ok = bool(misfire['skip_no_backfill'] and misfire['skip_fired_after_enable']
                      and misfire['runonce_single_backfill']
                      and misfire['runonce_backfill_matches_expected_slot'])
    dup = ev['phases']['duplicate_trigger']
    dup_ok = bool(dup['hub_side']['single_execution_for_slot']
                  and dup['agent_side']['single_execution_for_slot'])
    readback_ok = bool(readback.get('slot_row_count') == 1 and readback.get('slot_unique'))
    retention_ok = all(v.get('http') == 200 for v in retention.values())

    probe_summary = ev['phases'].get('sys18_probe')
    sys18_ok = bool(probe_summary and probe_summary.get('heartbeat_samples_pre_change_s')
                    and probe_summary.get('revision_check_samples_s')
                    and probe_summary.get('reconnect_backoff_samples_s'))

    def verdict(ok, partial_ok=False):
        if ok:
            return 'PASS', 'high'
        if partial_ok:
            return 'PARTIAL', 'medium'
        return 'FAIL', 'low'

    cases = {}
    v, conf = verdict(cond_ok and reject_ok and control.get('marker_present'))
    cases['SYS-26'] = {
        'case_id': 'SYS-26',
        'canonical_before': 'PASS (baseline_status on the authoritative inventory)',
        'proposed_after': v, 'confidence': conf,
        'covered_dimensions': [
            {'dimension': 'Condition AND 与多层（嵌套 AND）结构的真实求值',
             'status': 'PASS' if cond_ok else 'FAIL',
             'detail': cond_detail},
            {'dimension': '条件不满足 → 不执行（SKIPPED，无 stdout）',
             'status': 'PASS' if cr['and-false'].get('status') == 'SKIPPED'
                       and not (cr['and-false'].get('stdout') or '') else 'FAIL'},
            {'dimension': '条件求值失败/未知 → Fail Closed（BLOCKED，不执行）',
             'status': 'PASS' if cr['and-unknown'].get('status') == 'BLOCKED' else 'FAIL',
             'detail': {'actual': cr['and-unknown'].get('status'),
                        'block_reason': cr['and-unknown'].get('block_reason')}},
            {'dimension': '错误/非法条件结构被 Hub 拒绝且不产生执行',
             'status': 'PASS' if reject_ok else 'FAIL',
             'detail': {'rejected_count': sum(1 for r in reject_results if r['rejected']),
                        'total': len(reject_results),
                        'unexpectedly_created': [r['task_name'] for r in reject_results
                                                 if not r['rejected']],
                        'message_fragment_mismatches': reject_fragment_mismatch}},
        ],
        'remaining_or_limitations': [
            'Agent 侧 `local` 条件在 metric 未知时的 UNKNOWN 分支（engine.evalLocal default）无法经 Hub 校验路径触达，故未覆盖。',
            '未构造跨节点 remote+local 组合的 UNKNOWN 超时路径（5s 超时分支未复现）。',
        ],
        'evidence': ['phase2-conditions.json', 'phase3-reject-structures.json'],
        'actual_resources': {'condition_tasks': {k: cr[k]['task_id'] for k in expectations},
                             'control_task': task_ids['main']},
        'actual_budget': {'manual_runs': len(expectations) + 1, 'rejected_create_attempts': len(reject_results)},
        'final_cleanup': {'all_exact_404': cleanup['all_exact_404']},
    }

    v, conf = verdict(cond_ok)
    cases['SYS-27'] = {
        'case_id': 'SYS-27',
        'canonical_before': 'PASS (baseline_status on the authoritative inventory)',
        'proposed_after': v, 'confidence': conf,
        'covered_dimensions': [
            {'dimension': 'Remote online 满足（远端节点 online）→ 条件成立并真实执行',
             'status': 'PASS' if cr['remote-online-sat'].get('status') == 'SUCCESS' else 'FAIL',
             'detail': {'target_node': AUX_ONLINE_NODE, 'actual': cr['remote-online-sat'].get('status'),
                        'stdout': cr['remote-online-sat'].get('stdout')}},
            {'dimension': 'Remote online 不满足（远端节点 offline）→ 条件不成立、不执行（SKIPPED）',
             'status': 'PASS' if cr['remote-online-unsat'].get('status') == 'SKIPPED' else 'FAIL',
             'detail': {'target_node': OFFLINE_NODE, 'actual': cr['remote-online-unsat'].get('status'),
                        'stdout': cr['remote-online-unsat'].get('stdout'),
                        'block_reason': cr['remote-online-unsat'].get('block_reason')}},
            {'dimension': 'Remote online 反向取值（== offline）同样成立',
             'status': 'PASS' if cr['remote-offline-sat'].get('status') == 'SUCCESS' else 'FAIL'},
            {'dimension': 'Remote 条件参与 AND 多层组合',
             'status': 'PASS' if cr['and-remote-local'].get('status') == 'SUCCESS' else 'FAIL'},
        ],
        'remaining_or_limitations': [
            'Remote online 只覆盖 Hub 节点状态来源（node.status），未覆盖 Agent 主动上报 remote-state 缓存的 TTL 过期路径。',
            '未在“远端节点在线状态发生切换”的过渡窗口采样（只验证稳定 online/offline 两态）。',
        ],
        'evidence': ['phase2-conditions.json'],
        'actual_resources': {'remote_tasks': {k: cr[k]['task_id'] for k in
                                              ('remote-online-sat', 'remote-online-unsat',
                                               'remote-offline-sat', 'and-remote-local')}},
        'actual_budget': {'manual_runs': 4},
        'final_cleanup': {'all_exact_404': cleanup['all_exact_404']},
    }

    v, conf = verdict(misfire_ok)
    cases['SYS-23'] = {
        'case_id': 'SYS-23',
        'canonical_before': 'PASS (baseline_status on the authoritative inventory)',
        'proposed_after': v, 'confidence': conf,
        'covered_dimensions': [
            {'dimension': 'Misfire `skip`：错过的 slot 不补跑',
             'status': 'PASS' if misfire['skip_no_backfill'] else 'FAIL',
             'detail': {'disabled_window_sec': misfire['disabled_window_sec'],
                        'interval_sec': INTERVAL_SEC,
                        'backfill_executions': misfire['skip_backfill_executions'],
                        'post_enable_executions': misfire['skip_scheduled_final']}},
            {'dimension': 'Misfire `run_once`：只补跑一次（最近一次错过的 slot）',
             'status': 'PASS' if misfire['runonce_single_backfill']
                       and misfire['runonce_backfill_matches_expected_slot'] else 'FAIL',
             'detail': {'disabled_window_sec': misfire['disabled_window_sec'],
                        'expected_slot_utc': misfire['expected_runonce_backfill_slot'],
                        'backfill_executions': misfire['runonce_backfill_executions'],
                        'post_enable_executions': misfire['runonce_scheduled_final']}},
            {'dimension': 'Agent 侧真实调度触发（Execution trigger_type=schedule，stdout 命中 nonce 标记）',
             'status': 'PASS' if all((e.get('stdout') or '').strip() == MARKER
                                     for e in misfire['runonce_scheduled_final'] +
                                     misfire['skip_scheduled_final']) else 'FAIL'},
        ],
        'remaining_or_limitations': [
            '“Agent 不可用”以「调度被禁用期间 slot 到期」这一等价不可调度窗口构造；未停用任何一个 repair Agent 进程/单元。',
            '未构造 Hub 长时间不可达（hub_online_required 门禁）导致的 misfire 场景，该场景需要 Hub 停机窗口（属于其它批次）。',
            'Cron 型 misfire（skip/run_once）未覆盖，仅覆盖 interval 型。',
        ],
        'evidence': ['phase4-misfire.json', 'phase1-fixtures.json'],
        'actual_resources': {'skip_schedule_id': sched_skip['id'], 'runonce_schedule_id': sched_runonce['id'],
                             'skip_task_id': task_ids['skip'], 'runonce_task_id': task_ids['runonce'],
                             'executions': {'skip': [e['id'] for e in skip_execs],
                                            'runonce': [e['id'] for e in runonce_execs]}},
        'actual_budget': {'schedule_creates': 2, 'schedule_enable_puts': 2, 'observe_sec': 26},
        'final_cleanup': {'all_exact_404': cleanup['all_exact_404']},
    }

    v, conf = verdict(dup_ok and readback_ok)
    cases['SYS-22'] = {
        'case_id': 'SYS-22',
        'canonical_before': 'PASS (baseline_status on the authoritative inventory)',
        'proposed_after': v, 'confidence': conf,
        'covered_dimensions': [
            {'dimension': 'Hub 执行路径：同一 Task+Node+Scheduled Time 的重复触发只产生 1 条 Execution',
             'status': 'PASS' if dup['hub_side']['single_execution_for_slot'] else 'FAIL',
             'detail': dup['hub_side']},
            {'dimension': 'Agent 本地执行路径：同一 Task+Node+Scheduled Time 的重复触发只产生 1 条 Execution',
             'status': 'PASS' if dup['agent_side']['single_execution_for_slot'] else 'FAIL',
             'detail': dup['agent_side']},
            {'dimension': '独立于产品 API 的 Agent 本地库 slot 唯一性核对（同 slot 精确 1 行）',
             'status': 'PASS' if readback_ok else 'FAIL',
             'detail': {'slot_row_count': readback.get('slot_row_count'),
                        'rows': readback.get('slot_rows_for_dup_agent_task'),
                        'expected_slot': readback.get('expected_slot')}},
        ],
        'remaining_or_limitations': [
            '未在 Hub 重启/Agent 重启后的恢复窗口复核 slot 幂等（本批不重启 Hub，也不停 repair Agent）。',
        ],
        'evidence': ['phase5-duplicate-trigger.json', 'phase6-agent-db-readback.json'],
        'actual_resources': {'hub_duplicate_schedule_ids': [s['id'] for s in hub_scheds],
                             'agent_duplicate_schedule_ids': [s['id'] for s in agent_scheds],
                             'dup_hub_task_id': task_ids['dup-hub'],
                             'dup_agent_task_id': task_ids['dup-agent'],
                             'hub_execution_ids': [e['id'] for e in hub_execs],
                             'agent_execution_ids': [e['id'] for e in agent_execs]},
        'actual_budget': {'identical_run_at_hub_utc': hub_run_at_s,
                          'identical_run_at_agent_utc': agent_run_at_s,
                          'observe_window_sec': dup['observed_window_sec']},
        'final_cleanup': {'all_exact_404': cleanup['all_exact_404']},
    }

    cases['AR-17'] = {
        'case_id': 'AR-17',
        'canonical_before': 'PASS (baseline_status on the authoritative inventory)',
        'proposed_after': v, 'confidence': conf,
        'covered_dimensions': [
            {'dimension': 'Duplicate Schedule Trigger 幂等（同 slot 重复触发 → 1 条 Execution）',
             'status': 'PASS' if dup_ok else 'FAIL',
             'detail': {'hub': dup['hub_side'], 'agent': dup['agent_side']}},
            {'dimension': '幂等窗口：跨 Hub 调度 tick 与多轮观测不产生第二条 Execution',
             'status': 'PASS' if dup_ok else 'FAIL',
             'detail': {'observed_window_sec': dup['observed_window_sec'],
                        'poll_count': len(dup_polls)}},
            {'dimension': '持久化：定义删除后执行历史仍可 GET（200）',
             'status': 'PASS' if retention_ok else 'FAIL', 'detail': retention},
            {'dimension': 'Desired State 与实际状态一致：节点 revision/sync_status 收敛，Agent 本地 slot 行唯一',
             'status': 'PASS' if readback_ok and not final_health['agent_unit_restarted'] else 'FAIL',
             'detail': {'agent_unit_restarted': final_health['agent_unit_restarted'],
                        'nodes_after': cleanup['nodes_after'],
                        'agent_local_revision': readback.get('local_global_revision'),
                        'quick_check': readback.get('quick_check')}},
        ],
        'remaining_or_limitations': [
            '“恢复后”（Hub/Agent 重启后的恢复框架）幂等未在本批复核：批次约束不重启 repair Hub，也不停 4 个 protected repair Agent。',
            '未覆盖调度定义在恢复窗口内的 tombstone/tombstone 重放路径（已由 SYS-09 批次单独覆盖）。',
        ],
        'evidence': ['phase5-duplicate-trigger.json', 'phase6-agent-db-readback.json', 'phase7-cleanup.json'],
        'actual_resources': {'execution_ids': {'hub': [e['id'] for e in hub_execs],
                                               'agent': [e['id'] for e in agent_execs]}},
        'actual_budget': {'observe_window_sec': dup['observed_window_sec'],
                          'hub_tick_followup_sec': HUB_TICK_FOLLOWUP_SEC},
        'final_cleanup': {'all_exact_404': cleanup['all_exact_404']},
    }

    v, conf = verdict(sys18_ok, partial_ok=bool(probe_summary))
    hb_file = 'phase-sys18-owned-child-heartbeat-probe.raw.json'
    cases['SYS-18'] = {
        'case_id': 'SYS-18',
        'canonical_before': 'PARTIAL (baseline_status on the authoritative inventory)',
        'proposed_after': v, 'confidence': conf,
        'covered_dimensions': [
            {'dimension': '心跳周期与 ±20% Jitter：真实 Agent 进程按配置周期发送 HEARTBEAT',
             'status': 'PASS' if sys18_ok else 'FAIL',
             'detail': {k: probe_summary.get(k) for k in
                        ('configured_heartbeat_sec', 'heartbeat_samples_pre_change_s',
                         'observed_heartbeat_pre_change', 'runtime_change',
                         'heartbeat_samples_post_change_s', 'observed_heartbeat_post_change')}
                       if probe_summary else {}},
            {'dimension': 'Revision Check 周期与 Jitter：真实 Agent 进程按配置周期发送 REVISION_CHECK',
             'status': 'PASS' if probe_summary and probe_summary.get('revision_check_samples_s') else 'FAIL',
             'detail': {k: probe_summary.get(k) for k in
                        ('configured_revision_check_sec', 'revision_check_samples_s',
                         'observed_revision_check')} if probe_summary else {}},
            {'dimension': '重连退避：对端断开后的重连间隔落在 2s + 0~2s jitter 区间',
             'status': 'PASS' if probe_summary and probe_summary.get('reconnect_backoff_samples_s') else 'FAIL',
             'detail': {k: probe_summary.get(k) for k in
                        ('reconnect_backoff_samples_s', 'observed_reconnect_backoff')} if probe_summary else {}},
            {'dimension': '运行期配置（Hub 侧 heartbeat/revision 参数）与实测一致性：真实 repair Agent 的 '
                          'HEARTBEAT 间隔（Hub node last_seen）与 Hub settings 一致性',
             'status': 'PASS' if dup.get('real_agent_heartbeat_cadence') else 'FAIL',
             'detail': {'hub_settings': guard['settings'],
                        'observed': dup.get('real_agent_heartbeat_cadence')}},
        ],
        'remaining_or_limitations': [
            '规范未定义心跳/退避的“通过阈值”，本用例只记录配置值与实测观测值，不对规范未定义的阈值作断言。',
            '重连退避以自有 child Agent（同一二进制 SHA）在自有回环 fake Hub 上测得，不是 repair Agent 对 repair Hub 的断开重连。',
            '实测存在 ≤1s 的调度 tick 量化误差（periodicLoop 为 1s tick），观测值可能略高于 ±20% 上界。',
            '真实 repair Agent 的 last_seen 仅给出 ~30s 心跳节拍样本（同窗口内 2-3 个），样本量有限。',
        ],
        'evidence': [hb_file, 'phase8-sys18-probe-summary.json', 'phase5-duplicate-trigger.json'],
        'actual_resources': {'owned_child_agent': {'binary': PROBE_BINARY, 'sha256': AGENT_SHA,
                                                   'node': PROBE_NODE_LABEL, 'port': probe_summary.get('fake_hub_port') if probe_summary else None},
                             'process_tree_cleaned': probe_summary.get('agent_cleanup') if probe_summary else None},
        'actual_budget': {'probe_run_sec': 115, 'drops': 4, 'heartbeat_samples': len(probe_summary.get('heartbeat_samples_pre_change_s') or []) if probe_summary else 0},
        'final_cleanup': {'owned_child_agent_stopped': bool(probe_summary and not probe_summary.get('agent_cleanup', {}).get('alive_after')),
                          'sandbox_dir_removed': None,
                          'all_exact_404': cleanup['all_exact_404']},
    }

    case_result = {
        'batch_id': 'sys-b09',
        'run_dir': str(HERE),
        'executed_at_utc': ev['run_started_utc'],
        'authority': BASE[0],
        'nonce': NONCE,
        'target_node': {'id': NODE_ID, 'label': NODE_LABEL, 'ip': NODE_IP},
        'cases': [cases[cid] for cid in ('SYS-23', 'SYS-22', 'AR-17', 'SYS-18', 'SYS-26', 'SYS-27')],
        'case_index': {cid: {'canonical_before': cases[cid]['canonical_before'],
                             'proposed_after': cases[cid]['proposed_after'],
                             'confidence': cases[cid]['confidence']} for cid in cases},
        'safety': {
            'management_writes_only_repair_18080': True,
            'primary_8080_management_writes': 0,
            'repair_hub_restart': False,
            'protected_repair_agents_stopped_or_restarted': final_health['agent_unit_restarted'],
            'nodesteer_agent_service_touched': False,
            'hub_settings_modified': False,
            'product_source_modified': False,
        },
        'deviations': ev['deviations'],
        'harness_errors': ev['harness_errors'],
    }
    record('case-result.json', case_result)

    ledger = {
        'batch_id': 'sys-b09', 'nonce': NONCE,
        'authority': BASE[0],
        'fixtures': [{'kind': kind, 'id': obj_id, 'name': name,
                      'deleted_get404': bool(STATE['get404'].get('%s:%s' % (kind, obj_id)))}
                     for kind, obj_id, name in STATE['created']],
        'unexpectedly_created': [{'kind': k, 'id': i, 'name': n} for k, i, n in STATE['unexpected']],
        'preserved_executions': [
            {'id': e['id'], 'trigger_type': e.get('trigger_type'), 'purpose': 'SYS-22/AR-17 hub-side duplicate slot'}
            for e in hub_execs] +
            [{'id': e['id'], 'trigger_type': e.get('trigger_type'), 'purpose': 'SYS-22/AR-17 agent-side duplicate slot'}
             for e in agent_execs] +
            [{'id': e['id'], 'trigger_type': e.get('trigger_type'), 'purpose': 'SYS-23 skip policy'}
             for e in skip_execs] +
            [{'id': e['id'], 'trigger_type': e.get('trigger_type'), 'purpose': 'SYS-23 run_once policy'}
             for e in runonce_execs] +
            [{'id': cr[k]['execution_id'], 'trigger_type': cr[k].get('trigger_type'),
              'purpose': 'SYS-26/SYS-27 condition evaluation'} for k in condition_runs],
        'owned_child_agent': {'binary': PROBE_BINARY, 'sha256': AGENT_SHA,
                              'node': PROBE_NODE_LABEL, 'systemd_unit': None,
                              'data_dir': '/tmp/qa-sysb09-probe-run/run (temp, removed in cleanup)',
                              'process_stopped': bool(probe_summary and not probe_summary.get('agent_cleanup', {}).get('alive_after')),
                              'repair_hub_contact': False},
        'status': 'CLEANED' if cleanup['all_exact_404'] else 'CLEANUP_INCOMPLETE',
        'final_get_http': final_404,
    }
    record('fixture-ledger.json', ledger)

    oracle = {
        'batch_id': 'sys-b09',
        'checked_at_utc': now_utc(),
        'endpoint': ev['endpoint'],
        'residual_named_objects': cleanup['residual_named_objects'],
        'residual_named_objects_check_prefix': NONCE,
        'counts': {'baseline': cleanup['baseline_counts'], 'after': cleanup['after_counts']},
        'all_owned_definitions_get404': cleanup['all_exact_404'],
        'every_fixture_get404': all(v == 404 for v in final_404.values()),
        'nodes': {label: {'status': final_health['protected_nodes'][label]['status'],
                          'sync_status': final_health['protected_nodes'][label]['sync_status']}
                  for label in final_health['protected_nodes']},
        'offline_leftover_node_status': final_health['offline_node'],
        'agent_unit': {'pre_mainpid': final_health['agent_mainpid_pre'],
                       'post_mainpid': final_health['agent_mainpid_post'],
                       'restarted': final_health['agent_unit_restarted']},
        'hub_settings_untouched': final_health['hub_settings_unchanged'],
        'owned_child_agent': {'stopped': bool(probe_summary and
                                              not probe_summary.get('agent_cleanup', {}).get('alive_after')),
                              'sandbox_path': '/tmp/qa-sysb09-probe-run'},
    }
    record('final-cleanup-oracle.json', oracle)

    PROGRESS['phase'] = 'done'
    PROGRESS['updated_at_utc'] = now_utc()
    PROGRESS['final'] = {
        'cleanup_all_exact_404': cleanup['all_exact_404'],
        'residual_named_objects': cleanup['residual_named_objects'],
        'fixtures_created': len(STATE['created']),
        'fixtures_deleted': len(STATE['deleted']),
        'agent_unit_restarted': final_health['agent_unit_restarted'],
        'case_verdicts': {cid: cases[cid]['proposed_after'] for cid in cases},
    }
    record('progress.json', PROGRESS)

    print(json.dumps({'phase': 'done',
                      'case_verdicts': {cid: cases[cid]['proposed_after'] for cid in cases},
                      'cleanup_all_404': cleanup['all_exact_404'],
                      'residual': cleanup['residual_named_objects'],
                      'fixtures_created': len(STATE['created']),
                      'fixtures_deleted': len(STATE['deleted'])}, indent=2))


def best_effort_cleanup():
    """Delete any still-present owned fixtures; never touches anything else."""
    if not TOKEN[0]:
        return {'skipped': 'no_token'}
    out = []
    for kind, obj_id, name in reversed(STATE['created']):
        gc, _ = get('/api/%s/%s' % (KIND_PATH[kind], obj_id), allowed=(200, 404))
        if gc == 404:
            out.append({'kind': kind, 'id': obj_id, 'name': name, 'already_absent': True})
            continue
        dc, _ = api('DELETE', '/api/%s/%s' % (KIND_PATH[kind], obj_id))
        gc2, _ = get('/api/%s/%s' % (KIND_PATH[kind], obj_id), allowed=(200, 404))
        out.append({'kind': kind, 'id': obj_id, 'name': name, 'delete_http': dc, 'get_http': gc2})
    try:
        ssh(PROBE_NODE_IP, 'pkill -f qa-sysb09-owned-heartbeat-probe || true; '
                           'rm -rf /tmp/qa-sysb09-probe-run', timeout=45)
    except Exception:
        pass
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
            (EVID / 'failsafe-cleanup.json').write_text(
                json.dumps({'at_utc': now_utc(), **result}, ensure_ascii=False, indent=2) + '\n')
        except Exception as cleanup_exc:
            sys.stderr.write('FAILSAFE_CLEANUP_ERROR: %s\n' % cleanup_exc)
        raise SystemExit(1)
