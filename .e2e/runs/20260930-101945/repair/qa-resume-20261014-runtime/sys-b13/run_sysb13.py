#!/usr/bin/env python3
"""sys-b13 runtime batch: Docker Agent enrollment / restart persistence /
missing persistent volume / Native-Docker shared Agent core.

Cases: E2E-17, AR-03, AR-05, INV-13.

Authority: repair http://192.168.100.209:18080 only.
Target node VM: repair native-4 (192.168.100.247) only.
Image: pinned sha256:ecd4dd9f... (loaded onto native-4's daemon from native-2
via a read-only `docker save` stream; no pull/rebuild/retag).

Run one phase at a time:
  python3 run_sysb13.py --phase preflight
  ...
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hubapi  # noqa: E402
import n4  # noqa: E402

# ---------------------------------------------------------------- constants
NONCE = 'e7ec52cebad9'
OWNER = 'qa-sysb13-' + NONCE
ROOT = '/opt/' + OWNER
NET = 'nodesteer-qa-sysb13-' + NONCE
VOL_B = 'qa-sysb13-' + NONCE + '-data-b'
NODE_A = OWNER + '-run'          # docker run enrollment
NODE_B = OWNER + '-compose'      # compose enrollment
CONT_A = NODE_A
CONT_B = NODE_B
PROJ = OWNER
IMAGE = 'sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44'
IMAGE_TAG = 'nodesteer-qa/agent-host:f174890c3846'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
NATIVE4_NODE = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
NATIVE4_AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
NATIVE4_AGENT_DB = '/var/lib/nodesteer-repair-20260930-101945/native-4/state.db'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
SCRIPT_NAME = OWNER + '-script'
TASK_NAME = OWNER + '-task-a'
TASK_B_NAME = OWNER + '-task-b'
TASK_NATIVE_NAME = OWNER + '-task-native'
SCHED_NAME = OWNER + '-disabled-schedule'
MARKER_A = OWNER + '-marker'
STATE_PATH = HERE / 'state.json'
EV = HERE / 'evidence'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'TIMED_OUT', 'CANCELED'}

DATA_A = ROOT + '/data-a'
DB_A = DATA_A + '/state.db'
DATA_B_VOL_MP = '/var/lib/docker/volumes/' + VOL_B + '/_data'
DB_B = DATA_B_VOL_MP + '/state.db'
CONFIG_A = ROOT + '/private/agent-a.yaml'
CONFIG_B = ROOT + '/private/agent-b.yaml'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def ev(name, value):
    path = EV / (name + '.json')
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return value


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {'nonce': NONCE, 'owner': OWNER, 'created_at_utc': now_utc(), 'hub_objects': {},
            'nodes': {}, 'containers': {}, 'volume_b': VOL_B, 'net': NET, 'root': ROOT,
            'image_source': {'loaded_from': 'native-2 (192.168.100.206) read-only docker save stream',
                             'reason': 'pinned image absent on native-4 daemon; no pull/rebuild/retag',
                             'loaded_at_utc': now_utc()}}


def save_state(state):
    tmp = STATE_PATH.with_name(STATE_PATH.name + '.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    os.replace(tmp, STATE_PATH)


def hub():
    h = hubapi.Hub()
    h.login()
    return h


def node_by_name(h, name):
    code, rows = h.get('/api/nodes')
    if code != 200 or not isinstance(rows, list):
        raise RuntimeError('nodes_list_failed_http_%s' % code)
    matches = [r for r in rows if isinstance(r, dict) and r.get('hostname') == name]
    if len(matches) > 1:
        raise RuntimeError('node_name_not_unique:' + name)
    return matches[0] if matches else None


def wait_node_online(h, node_id, name, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        code, last = h.get('/api/nodes/' + node_id)
        if code == 200 and last.get('status') == 'online' and last.get('sync_status') == 'synced':
            if last.get('hostname') != name:
                raise RuntimeError('node_hostname_mismatch')
            return last
        time.sleep(0.5)
    raise RuntimeError('node_not_online_synced: %s last=%s' % (name, json.dumps(last, ensure_ascii=False)[:400]))


def wait_node_by_name(h, name, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = node_by_name(h, name)
        if row:
            return wait_node_online(h, row['id'], name, timeout=max(1, deadline - time.monotonic()))
        time.sleep(0.5)
    raise RuntimeError('node_hello_timeout:' + name)


def run_task_to_terminal(h, task_id, timeout=120):
    code, started = h.post('/api/tasks/' + task_id + '/run', {'params': {}})
    if isinstance(started, list):
        execs = started
    elif isinstance(started, dict):
        execs = started.get('executions') or ([started] if started.get('id') else None)
    else:
        execs = None
    if code not in (200, 201) or not execs or len(execs) != 1:
        raise RuntimeError('task_run_shape_invalid http=%s body=%s' % (code, json.dumps(started)[:300]))
    eid = execs[0]['id']
    deadline = time.monotonic() + timeout
    term = None
    while time.monotonic() < deadline:
        code, cur = h.get('/api/executions/' + eid)
        if code != 200:
            raise RuntimeError('execution_readback_http_%s' % code)
        if cur.get('status') in TERMINAL:
            term = cur
            break
        time.sleep(0.5)
    if term is None:
        raise RuntimeError('execution_not_terminal:' + eid)
    code, logs = h.get('/api/executions/' + eid + '/logs')
    chunks = logs.get('logs') if isinstance(logs, dict) else logs
    stdout = ''
    if isinstance(chunks, list):
        for x in sorted((c for c in chunks if isinstance(c, dict) and c.get('stream') == 'stdout'),
                        key=lambda c: (c.get('seq', 0), c.get('created_at', ''))):
            stdout += x.get('chunk', '')
    return {'execution_id': eid, 'terminal': term, 'stdout': stdout}


# ------------------------------------------------------------ remote helpers
def remote_db(dbpath, note=None):
    code = r'''
import json,os,sqlite3,shutil,tempfile,hashlib
p = __DBPATH__
res = {"db_path": p}
try:
    st = os.stat(p)
    res.update({"exists": True, "dev": st.st_dev, "inode": st.st_ino, "size": st.st_size,
                "uid": st.st_uid, "gid": st.st_gid, "mode": oct(st.st_mode & 0o777)})
except FileNotFoundError:
    res["exists"] = False
    print(json.dumps(res)); raise SystemExit
def open_con(path):
    con = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=1")
    return con
try:
    con = open_con(p)
    res["opened_mode"] = "ro"
except Exception as e:
    tmp = tempfile.mkdtemp()
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(p + suf):
            shutil.copy2(p + suf, os.path.join(tmp, os.path.basename(p) + suf))
    con = sqlite3.connect(os.path.join(tmp, os.path.basename(p)))
    res["opened_mode"] = "copied:" + type(e).__name__
try:
    res["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
    res["tables"] = sorted(r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    rows = list(con.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
    res["schema_sha256"] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    res["schema_rows"] = len(rows)
    for q, key in __QUERIES__:
        try:
            cur = con.execute(q)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()]
            if key == "identity":
                for r in rows:
                    if r and r[0] == "credential":
                        r[1] = "<redacted-sha256:" + hashlib.sha256(str(r[1]).encode()).hexdigest()[:16] + ">"
            res[key] = {"columns": cols, "rows": rows}
        except Exception as e3:
            res[key] = {"error": type(e3).__name__}
finally:
    con.close()
print(json.dumps(res))
'''
    code = code.replace('__DBPATH__', json.dumps(dbpath)).replace('__QUERIES__', json.dumps(QUERIES))
    return n4.rpy(code.strip())


QUERIES = [
    ("SELECT key,value FROM identity ORDER BY key", "identity"),
    ("SELECT global_revision,last_sync FROM sync_state", "sync_state"),
    ("SELECT id,name,revision,sha256,length(content) FROM scripts ORDER BY id", "scripts"),
    ("SELECT id,revision,enabled FROM tasks ORDER BY id", "tasks"),
    ("SELECT id,status,exit_code,stdout,script_id,script_revision,node_id,trigger_type,synced,offline FROM executions ORDER BY start_time DESC LIMIT 12", "executions"),
    ("SELECT sha256,path FROM artifacts ORDER BY sha256 LIMIT 12", "artifacts"),
]


def db_summary(dbpath):
    return remote_db(dbpath)


# ------------------------------------------------------------------ phases
def phase_preflight(state):
    h = hub()
    code, nodes = h.get('/api/nodes')
    baseline = {}
    for nid, nm in PROTECTED_NODES.items():
        row = [x for x in nodes if isinstance(x, dict) and x.get('id') == nid]
        if not row:
            raise RuntimeError('protected_node_missing:' + nid)
        r = row[0]
        baseline[nid] = {'name': nm, 'hostname': r.get('hostname'), 'status': r.get('status'),
                         'sync_status': r.get('sync_status'), 'deployment_mode': r.get('deployment_mode'),
                         'agent_id': r.get('agent_id'), 'global_revision': r.get('global_revision')}
        if r.get('status') != 'online' or r.get('sync_status') != 'synced':
            raise RuntimeError('protected_node_not_online_synced:' + nm)
    baseline_ids = sorted(x['id'] for x in nodes if isinstance(x, dict))
    # fixture name collisions
    code, scripts = h.get('/api/scripts')
    sc = scripts.get('scripts') if isinstance(scripts, dict) else scripts
    code2, tasks = h.get('/api/tasks')
    ts = tasks.get('tasks') if isinstance(tasks, dict) else tasks
    code3, schs = h.get('/api/schedules')
    sch = schs.get('schedules') if isinstance(schs, dict) else schs
    collisions = {
        'script': any(isinstance(x, dict) and x.get('name') == SCRIPT_NAME for x in (sc or [])),
        'task': any(isinstance(x, dict) and x.get('name') in (TASK_NAME, TASK_NATIVE_NAME, TASK_B_NAME) for x in (ts or [])),
        'schedule': any(isinstance(x, dict) and x.get('name') == SCHED_NAME for x in (sch or [])),
        'node_a': any(isinstance(x, dict) and x.get('hostname') == NODE_A for x in nodes),
        'node_b': any(isinstance(x, dict) and x.get('hostname') == NODE_B for x in nodes),
    }
    if any(collisions.values()):
        raise RuntimeError('fixture_name_collision:' + json.dumps(collisions))
    rc, ver, _ = n4.rsh('docker version --format "{{.Server.Version}}"; docker compose version --short; sha256sum ' + NATIVE4_AGENT_BIN)
    img = n4.image_inspect(IMAGE)
    imgs = n4.docker(['images', '-a', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}'])[1].strip().splitlines()
    conts = [l for l in n4.docker(['ps', '-a', '--format', '{{.ID}} {{.Names}}'])[1].strip().splitlines() if l]
    vols = [l for l in n4.docker(['volume', 'ls', '--format', '{{.Name}}'])[1].strip().splitlines() if l]
    nets = [l for l in n4.docker(['network', 'ls', '--format', '{{.Name}}'])[1].strip().splitlines() if l]
    root_rc, _, _ = n4.rsh('test -e ' + shlex.quote(ROOT), check=False)
    occupied_guard = {
        'image_digest_matches': bool(img and img.get('Id') == IMAGE),
        'root_absent': root_rc != 0,
        'net_absent': NET not in nets,
        'vol_b_absent': VOL_B not in vols,
        'cont_a_absent': not any(CONT_A in l for l in conts),
        'cont_b_absent': not any(CONT_B in l for l in conts),
    }
    if not occupied_guard['image_digest_matches']:
        raise RuntimeError('pinned_image_missing_or_digest_mismatch_on_native4')
    if not all(v for k, v in occupied_guard.items() if k != 'image_digest_matches'):
        raise RuntimeError('owned_resource_name_collision:' + json.dumps(occupied_guard))
    ev('phase0-preflight', {'captured_at_utc': now_utc(), 'hub_baseline': baseline,
                            'baseline_node_ids': baseline_ids,
                            'fixture_name_collisions': collisions,
                            'native4': {'docker_version': ver.strip().splitlines()[:3],
                                        'image_id': img.get('Id'), 'image_tags': img.get('RepoTags'),
                                        'images': imgs, 'containers': conts, 'volumes': vols,
                                        'networks': nets},
                            'occupied_guard': occupied_guard,
                            'authority_port': 18080})
    state['baseline_node_ids'] = baseline_ids
    state['protected_before'] = baseline
    save_state(state)
    return {'status': 'PREFLIGHT_OK', 'protected_online_synced': 4}


def _write_configs(state):
    """Create owned root/private dirs + both agent configs. Secret never leaves remote 0600 file."""
    h = hub()
    token = h.cred['registration_token']

    def cfg(node_name):
        return ('hub_url: "ws://192.168.100.209:18080/"\n'
                'registration_token: ' + json.dumps(token) + '\n'
                'node_name: ' + json.dumps(node_name) + '\n'
                'deployment_mode: "docker"\n'
                'data_dir: "/var/lib/nodesteer"\n')

    # write configs via remote python to keep the token out of argv/logs
    code = r'''
import json, os, stat
root = %s
for d in (root, root + "/private", %s):
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
os.chmod(root, 0o700)
for path, content in ((%s, %s), (%s, %s)):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, 65534, 65534)
        os.fchmod(fd, 0o600)
        os.write(fd, content.encode())
    finally:
        os.close(fd)
    st = os.stat(path)
    assert stat.S_IMODE(st.st_mode) == 0o600 and st.st_uid == 65534, path
print(json.dumps({"written": [%s, %s], "root_ok": True}))
''' % (json.dumps(ROOT), json.dumps(DATA_A), json.dumps(CONFIG_A), json.dumps(cfg(NODE_A)),
       json.dumps(CONFIG_B), json.dumps(cfg(NODE_B)), json.dumps(CONFIG_A), json.dumps(CONFIG_B))
    r = n4.rpy(code.strip())
    n4.rsh('chown 0:0 ' + shlex.quote(ROOT) + '; chmod 700 ' + shlex.quote(ROOT)
           + '; chown 65534:65534 ' + shlex.quote(ROOT + '/private') + '; chmod 700 ' + shlex.quote(ROOT + '/private')
           + '; chown 65534:65534 ' + shlex.quote(DATA_A) + '; chmod 700 ' + shlex.quote(DATA_A))
    state['config_written'] = True
    save_state(state)
    return r


def phase_run_enroll(state):
    """E2E-17 part 1: docker run enrollment (resumable)."""
    h = hub()
    _write_configs(state)
    # owned bridge (reuse a previously created owned network)
    rc, netid, _ = n4.docker(['network', 'inspect', NET, '-f', '{{.Id}}'], check=False)
    netid = netid.strip()
    if rc != 0 or not re.fullmatch(r'[0-9a-f]{64}', netid):
        rc, out, err = n4.docker(['network', 'create', '--driver', 'bridge', '--label',
                                  'nodesteer.e2e.owner=' + OWNER, NET], check=False)
        if rc != 0 or not re.fullmatch(r'[0-9a-f]{64}', out.strip()):
            raise RuntimeError('network_create_failed:' + out + err)
        netid = out.strip()
    n4.rsh('chown 65534:65534 ' + shlex.quote(DATA_A) + '; chmod 700 ' + shlex.quote(DATA_A))
    rc, cid, _ = n4.docker(['inspect', '-f', '{{.Id}}', CONT_A], check=False)
    cid = cid.strip()
    if rc != 0 or not re.fullmatch(r'[0-9a-f]{64}', cid):
        cid = n4.docker(['run', '-d', '--name', CONT_A, '--label', 'nodesteer.e2e.owner=' + OWNER,
                         '--label', 'nodesteer.e2e.method=docker-run',
                         '--user', '65534:65534', '--cap-drop', 'ALL', '--read-only',
                         '--security-opt', 'no-new-privileges', '--network', NET,
                         '--mount', 'type=bind,src=' + CONFIG_A + ',dst=/run/nodesteer/agent.yaml,readonly',
                         '--mount', 'type=bind,src=' + DATA_A + ',dst=/var/lib/nodesteer',
                         '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16m', IMAGE], timeout=60)[1].strip()
    if not re.fullmatch(r'[0-9a-f]{64}', cid):
        raise RuntimeError('container_a_run_failed:' + cid)
    insp = n4.container_inspect(cid)
    if not insp or not insp.get('State', {}).get('Running'):
        logs = n4.docker(['logs', cid], check=False)[1]
        raise RuntimeError('container_a_not_running:' + logs[-500:])
    node = wait_node_by_name(h, NODE_A, timeout=120)
    # verify container-side deployment detection
    rc, det, _ = n4.docker(['exec', CONT_A, '/bin/sh', '-c',
                            'test -f /.dockerenv && echo dockerenv-present'], check=False)
    state['nodes']['A'] = {'node_id': node['id'], 'agent_id': node['agent_id'], 'container_id': cid,
                           'network_id': netid, 'node': node}
    state['containers']['A'] = {'id': cid, 'name': CONT_A, 'method': 'docker-run'}
    save_state(state)
    ev('phase1-e2e17-docker-run', {'captured_at_utc': now_utc(), 'method': 'docker run',
                                   'container_id': cid, 'network_id': netid,
                                   'container_state': {'running': insp['State']['Running'],
                                                       'pid': insp['State']['Pid'],
                                                       'started_at': insp['State']['StartedAt'],
                                                       'image': insp.get('Image'),
                                                       'user': insp['Config'].get('User'),
                                                       'readonly': insp['HostConfig'].get('ReadonlyRootfs'),
                                                       'devices': insp['HostConfig'].get('Devices'),
                                                       'dockerenv_probe': det.strip()},
                                   'hub_node': {k: node.get(k) for k in
                                                ('id', 'agent_id', 'hostname', 'deployment_mode', 'host_integration',
                                                 'status', 'sync_status', 'global_revision', 'capabilities', 'agent_version')},
                                   'db_after_hello': db_summary(DB_A)})
    return {'status': 'DOCKER_RUN_ENROLLED', 'node_id': node['id'], 'container_id': cid}


def _create_script_and_task(h, state, task_name, target_node_ids, description):
    content = "printf '%s' " + shlex.quote(MARKER_A)
    exp_sha = hashlib.sha256(content.encode()).hexdigest()
    code, scripts = h.get('/api/scripts')
    sc = scripts.get('scripts') if isinstance(scripts, dict) else scripts
    existing = [x for x in (sc or []) if isinstance(x, dict) and x.get('name') == SCRIPT_NAME]
    if existing:
        if existing[0].get('sha256') != exp_sha:
            raise RuntimeError('existing_script_sha_mismatch')
        script = existing[0]
    else:
        body = {'name': SCRIPT_NAME, 'description': description, 'interpreter': 'shell',
                'content': content, 'parameters': [], 'environment': {}, 'working_dir': '',
                'timeout': 30, 'enabled': True}
        code, script = h.post('/api/scripts', body)
        if code not in (200, 201) or not isinstance(script, dict) or not script.get('id'):
            raise RuntimeError('script_create_failed:%s' % json.dumps(script)[:200])
        if script.get('sha256') != exp_sha or script.get('revision') != 1:
            raise RuntimeError('script_response_mismatch')
    code, tasks = h.get('/api/tasks')
    ts = tasks.get('tasks') if isinstance(tasks, dict) else tasks
    if any(isinstance(x, dict) and x.get('name') == task_name for x in (ts or [])):
        raise RuntimeError('task_name_collision')
    task_body = {'name': task_name, 'type': 'script',
                 'target': {'type': 'node', 'node_ids': list(target_node_ids)},
                 'parameters': [], 'param_values': {}, 'script_id': script['id'], 'timeout': 30,
                 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    code, task = h.post('/api/tasks', task_body)
    if code not in (200, 201) or not isinstance(task, dict) or not task.get('id'):
        raise RuntimeError('task_create_failed:%s' % json.dumps(task)[:200])
    state['hub_objects'].setdefault('scripts', {})[script['id']] = SCRIPT_NAME
    state['hub_objects'].setdefault('tasks', {})[task['id']] = task_name
    save_state(state)
    return {'script_id': script['id'], 'script_sha256': exp_sha,
            'script_revision': script.get('revision'), 'task_id': task['id'],
            'task_targets': list(target_node_ids), 'content': content}


def phase_ar03(state):
    """AR-03: docker restart persistence + idempotency + desired==actual."""
    h = hub()
    a = state['nodes']['A']
    node_id, agent_id = a['node_id'], a['agent_id']
    cid = a['container_id']
    # create the owned script/task pair once, run once
    if not state['hub_objects'].get('script_a'):
        fixture = _create_script_and_task(h, state, TASK_NAME, [node_id],
                                          'owned sys-b13 docker persistence fixture')
        state['hub_objects']['script_a'] = fixture
        save_state(state)
    fixture = state['hub_objects']['script_a']
    pre_node = wait_node_online(h, node_id, NODE_A)
    run1 = run_task_to_terminal(h, fixture['task_id'])
    if run1['terminal'].get('status') != 'SUCCESS' or run1['stdout'] != MARKER_A:
        raise RuntimeError('fixture_run1_not_success:%s' % json.dumps(run1)[:300])
    pre_db = db_summary(DB_A)
    pre_execs = {r[0]: r for r in pre_db['executions']['rows']}

    # real container restart
    insp_before = n4.container_inspect(cid)
    n4.docker(['restart', cid], timeout=90)
    insp_after = n4.container_inspect(cid)
    if not insp_after.get('State', {}).get('Running'):
        raise RuntimeError('container_not_running_after_restart')
    node_after = wait_node_online(h, node_id, NODE_A, timeout=120)
    post_db = db_summary(DB_A)
    post_execs = {r[0]: r for r in post_db['executions']['rows']}

    checks = {
        'node_id_unchanged': node_after['id'] == node_id,
        'agent_id_unchanged': node_after.get('agent_id') == agent_id,
        'db_dev_inode_unchanged': (pre_db['dev'], pre_db['inode']) == (post_db['dev'], post_db['inode']),
        'db_quick_check_ok_before': pre_db.get('quick_check') == 'ok',
        'db_quick_check_ok_after': post_db.get('quick_check') == 'ok',
        'revision_not_regressed': post_db['sync_state']['rows'][0][0] >= pre_db['sync_state']['rows'][0][0],
        'container_pid_changed': insp_before['State']['Pid'] != insp_after['State']['Pid'],
        'execution_rows_preserved': all(k in post_execs for k in pre_execs),
        'execution_count_unchanged': len(pre_execs) == len(post_execs),
        'fixture_execution_still_success':
            post_execs.get(run1['execution_id'], [None])[1] == 'SUCCESS' if post_execs.get(run1['execution_id']) else False,
        'desired_script_present_and_synced':
            any(r[3] == fixture['script_sha256'] and r[2] == 1 for r in post_db['scripts']['rows']),
        'fixture_execution_occurrences_exactly_once':
            sum(1 for r in post_db['executions']['rows'] if r[0] == run1['execution_id']) == 1,
        'restart_created_no_new_execution':
            len(post_execs) == len(pre_execs),
    }
    ev('phase2-ar03-restart', {'captured_at_utc': now_utc(), 'node_id': node_id, 'agent_id': agent_id,
                               'container_id': cid, 'fixture': {k: fixture[k] for k in
                                                                ('script_id', 'task_id', 'script_sha256')},
                               'execution_id': run1['execution_id'],
                               'restart': {'pid_before': insp_before['State']['Pid'],
                                           'pid_after': insp_after['State']['Pid'],
                                           'restart_count_before': insp_before['RestartCount'],
                                           'restart_count_after': insp_after['RestartCount'],
                                           'started_at_before': insp_before['State']['StartedAt'],
                                           'started_at_after': insp_after['State']['StartedAt']},
                               'hub_execution': {'status': run1['terminal'].get('status'),
                                                 'stdout_api': run1['stdout']},
                               'pre_db': pre_db, 'post_db': post_db, 'checks': checks})
    if not all(checks.values()):
        raise RuntimeError('ar03_checks_failed:' + json.dumps({k: v for k, v in checks.items() if not v}))
    state['nodes']['A']['pre_restart_db'] = pre_db
    state['hub_objects']['script_a']['execution_id'] = run1['execution_id']
    save_state(state)
    return {'status': 'AR03_PASS', 'checks': checks}


def phase_compose(state):
    """E2E-17 part 2: docker compose enrollment (owned named volume)."""
    h = hub()
    # create owned named volume and align ownership to the agent uid
    rc, out, err = n4.docker(['volume', 'create', '--label', 'nodesteer.e2e.owner=' + OWNER, VOL_B], check=False)
    if rc != 0:
        raise RuntimeError('volume_create_failed:' + err)
    n4.rsh('chown 65534:65534 ' + shlex.quote(DATA_B_VOL_MP) + '; chmod 700 ' + shlex.quote(DATA_B_VOL_MP))
    compose = (
        "services:\n"
        "  agent-b:\n"
        "    image: " + IMAGE + "\n"
        "    container_name: " + CONT_B + "\n"
        "    user: \"65534:65534\"\n"
        "    cap_drop: [ALL]\n"
        "    read_only: true\n"
        "    security_opt:\n"
        "      - no-new-privileges\n"
        "    networks:\n"
        "      - b13net\n"
        "    volumes:\n"
        "      - " + CONFIG_B + ":/run/nodesteer/agent.yaml:ro\n"
        "      - b13data:/var/lib/nodesteer\n"
        "    tmpfs:\n"
        "      - /tmp:rw,noexec,nosuid,nodev,size=16m\n"
        "    labels:\n"
        "      nodesteer.e2e.owner: \"" + OWNER + "\"\n"
        "      nodesteer.e2e.method: \"compose\"\n"
        "volumes:\n"
        "  b13data:\n"
        "    name: " + VOL_B + "\n"
        "    labels:\n"
        "      nodesteer.e2e.owner: \"" + OWNER + "\"\n"
        "networks:\n"
        "  b13net:\n"
        "    name: " + NET + "\n"
        "    external: true\n")
    cpath = ROOT + '/compose.yaml'
    code = ("import json\n"
            "open(%s,'w').write(%s)\n"
            "print(json.dumps({'compose_path': %s}))\n") % (json.dumps(cpath), json.dumps(compose), json.dumps(cpath))
    n4.rpy(code)
    rc, out, err = n4.docker(['compose', '-p', PROJ, '-f', cpath, 'up', '-d'], timeout=120, check=False)
    if rc != 0:
        raise RuntimeError('compose_up_failed:' + out[-400:] + err[-600:])
    _, ps, _ = n4.docker(['compose', '-p', PROJ, '-f', cpath, 'ps', '--format', 'json'], timeout=60, check=False)
    node = wait_node_by_name(h, NODE_B, timeout=120)
    rc, cid, _ = n4.docker(['inspect', '-f', '{{.Id}}', CONT_B], timeout=60, check=False)
    cid = cid.strip()
    insp = n4.container_inspect(cid)
    if not insp or not insp.get('State', {}).get('Running'):
        logs = n4.docker(['logs', CONT_B], check=False)[1]
        raise RuntimeError('compose_container_not_running:' + logs[-500:])
    state['nodes']['B'] = {'node_id': node['id'], 'agent_id': node['agent_id'], 'container_id': cid,
                           'volume': VOL_B, 'compose_path': cpath, 'node': node}
    state['containers']['B'] = {'id': cid, 'name': CONT_B, 'method': 'compose', 'project': PROJ}
    save_state(state)
    ev('phase3-e2e17-compose', {'captured_at_utc': now_utc(), 'method': 'docker compose',
                                'compose_project': PROJ, 'compose_file': cpath, 'compose_ps': ps.strip(),
                                'container_id': cid, 'volume': VOL_B,
                                'volume_mountpoint': DATA_B_VOL_MP,
                                'container_state': {'running': insp['State']['Running'], 'pid': insp['State']['Pid'],
                                                    'image': insp.get('Image'), 'user': insp['Config'].get('User'),
                                                    'readonly': insp['HostConfig'].get('ReadonlyRootfs')},
                                'compose_labels': insp['Config'].get('Labels'),
                                'hub_node': {k: node.get(k) for k in
                                             ('id', 'agent_id', 'hostname', 'deployment_mode', 'host_integration',
                                              'status', 'sync_status', 'global_revision', 'capabilities', 'agent_version')},
                                'db_after_hello': db_summary(DB_B)})
    return {'status': 'COMPOSE_ENROLLED', 'node_id': node['id'], 'container_id': cid}


def phase_ar05(state):
    """AR-05: persistent volume missing -> explicit failure (no fake success),
    then restore -> idempotent + persistent + desired state consistent."""
    h = hub()
    b = state['nodes']['B']
    node_id, agent_id = b['node_id'], b['agent_id']
    cpath = b['compose_path']
    pre = db_summary(DB_B)
    # backup the volume content before destroying it
    n4.rsh('rm -rf ' + shlex.quote(ROOT + '/volb-backup') + '; mkdir -p ' + shlex.quote(ROOT + '/volb-backup')
           + '; cp -a ' + shlex.quote(DATA_B_VOL_MP) + '/. ' + shlex.quote(ROOT + '/volb-backup') + '/')
    rc, cid_before, _ = n4.docker(['inspect', '-f', '{{.Id}}', CONT_B], check=False)
    # detach the compose service and destroy the volume
    rc, out, err = n4.docker(['compose', '-p', PROJ, '-f', cpath, 'down'], timeout=90, check=False)
    if rc != 0:
        raise RuntimeError('compose_down_failed:' + err[-400:])
    rc, out, err = n4.docker(['volume', 'rm', VOL_B], timeout=60, check=False)
    vol_rm_ok = rc == 0 and not n4.exists('volume', VOL_B)
    if not vol_rm_ok:
        raise RuntimeError('volume_rm_failed:' + out + err)
    # bring compose back up: docker recreates an EMPTY (root-owned) volume => missing state
    rc, out, err = n4.docker(['compose', '-p', PROJ, '-f', cpath, 'up', '-d'], timeout=120, check=False)
    if rc != 0:
        raise RuntimeError('compose_reup_failed:' + err[-400:])
    time.sleep(8)
    rc, cid_after, _ = n4.docker(['inspect', '-f', '{{.Id}}', CONT_B], check=False)
    insp = n4.container_inspect(cid_after.strip()) if cid_after.strip() else None
    logs = n4.docker(['logs', CONT_B], check=False)
    missing_obs = {
        'container_id_after_recreate': cid_after.strip(),
        'container_recreated_new_id': cid_after.strip() != cid_before.strip(),
        'volume_recreated_by_compose': n4.exists('volume', VOL_B),
        'container_running': bool(insp and insp.get('State', {}).get('Running')),
        'container_exit_code': (insp or {}).get('State', {}).get('ExitCode'),
        'container_status': (insp or {}).get('State', {}).get('Status'),
        'docker_logs': (logs[1] or logs[2])[-1500:],
        'db_path': DATA_B_VOL_MP,
    }
    db_after = db_summary(DB_B)
    missing_obs['db_after_missing_volume'] = db_after
    # Hub-side: node must not claim a healthy synced state; and there must be no fake SUCCESS
    time.sleep(5)
    code, node_now = h.get('/api/nodes/' + node_id)
    missing_obs['hub_node_after_missing_volume'] = {k: node_now.get(k) for k in
                                                   ('id', 'agent_id', 'status', 'sync_status', 'global_revision',
                                                    'last_seen')} if isinstance(node_now, dict) else node_now
    no_fake_success = not (missing_obs['container_running'] and db_after.get('exists')
                           and db_after.get('quick_check') == 'ok' and db_after.get('identity'))
    missing_obs['negative_no_fake_success'] = no_fake_success

    # ------- recovery: restore the exact volume content -------
    n4.docker(['compose', '-p', PROJ, '-f', cpath, 'down'], timeout=90, check=False)
    n4.docker(['volume', 'rm', '-f', VOL_B], timeout=60, check=False)
    n4.docker(['volume', 'create', '--label', 'nodesteer.e2e.owner=' + OWNER, VOL_B], timeout=60)
    n4.rsh('chown 65534:65534 ' + shlex.quote(DATA_B_VOL_MP) + '; chmod 700 ' + shlex.quote(DATA_B_VOL_MP)
           + '; cp -a ' + shlex.quote(ROOT + '/volb-backup/.') + ' ' + shlex.quote(DATA_B_VOL_MP) + '/'
           + '; chown -R 65534:65534 ' + shlex.quote(DATA_B_VOL_MP))
    n4.docker(['compose', '-p', PROJ, '-f', cpath, 'up', '-d'], timeout=120)
    node_rec = wait_node_online(h, node_id, NODE_B, timeout=120)
    rec_db = db_summary(DB_B)
    # post-recovery real execution: proves the recovered agent actually works and the
    # Hub Desired State is applied (not just a reconnected socket).
    if 'script_b' not in state['hub_objects']:
        bfix = _create_script_and_task(h, state, TASK_B_NAME, [node_id],
                                       'owned sys-b13 volume-recovery execution')
        state['hub_objects']['script_b'] = bfix
        save_state(state)
    brun = run_task_to_terminal(h, state['hub_objects']['script_b']['task_id'], timeout=120)
    post_exec = {'execution_id': brun['execution_id'], 'status': brun['terminal'].get('status'),
                 'stdout_api': brun['stdout'], 'script_id': state['hub_objects']['script_b']['script_id'],
                 'ok': brun['terminal'].get('status') == 'SUCCESS' and brun['stdout'] == MARKER_A}
    post_exec_db = db_summary(DB_B)
    checks = {
        'recovery_node_id_same': node_rec['id'] == node_id,
        'recovery_agent_id_same': node_rec.get('agent_id') == agent_id,
        'recovery_db_quick_check_ok': rec_db.get('quick_check') == 'ok',
        'recovery_revision_not_regressed': rec_db['sync_state']['rows'][0][0] >= pre['sync_state']['rows'][0][0],
        'recovery_identity_same': rec_db.get('identity') == pre.get('identity'),
        'recovery_no_duplicate_executions':
            len(rec_db.get('executions', {}).get('rows', [])) == len(pre.get('executions', {}).get('rows', [])),
        'recovery_desired_state_matches':
            [r[1] for r in rec_db.get('scripts', {}).get('rows', [])] == [r[1] for r in pre.get('scripts', {}).get('rows', [])],
    }
    # A destroyed+recreated named volume legitimately gets a new host inode; that is an
    # observation, not a pass/fail criterion. The preserved criterion is content identity.
    volume_inode_observation = {
        'pre_dev': pre.get('dev'), 'pre_inode': pre.get('inode'),
        'recovery_dev': rec_db.get('dev'), 'recovery_inode': rec_db.get('inode'),
        'expected_new_inode_after_volume_recreate': True,
        'content_identity_preserved': rec_db.get('identity') == pre.get('identity'),
    }
    ev('phase4-ar05-missing-volume', {'captured_at_utc': now_utc(), 'node_id': node_id, 'agent_id': agent_id,
                                      'volume': VOL_B, 'pre_db': pre,
                                      'missing_volume_observation': missing_obs,
                                      'volume_inode_observation': volume_inode_observation,
                                      'recovery_db': rec_db, 'recovery_checks': checks,
                                      'post_execution_db': post_exec_db,
                                      'post_recovery_execution': post_exec,
                                      'recovery_node': {k: node_rec.get(k) for k in
                                                        ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                                         'global_revision')}})
    if not no_fake_success:
        raise RuntimeError('ar05_negative_expectation_failed: missing volume produced a fake success')
    if not post_exec.get('ok'):
        raise RuntimeError('ar05_post_recovery_execution_failed:' + json.dumps(post_exec)[:300])
    if not all(checks.values()):
        raise RuntimeError('ar05_recovery_checks_failed:' + json.dumps({k: v for k, v in checks.items() if not v}))
    state['nodes']['B'] = {**b, 'container_id': n4.docker(['inspect', '-f', '{{.Id}}', CONT_B])[1].strip()}
    state['containers']['B']['id'] = state['nodes']['B']['container_id']
    save_state(state)
    return {'status': 'AR05_PASS', 'negative_ok': no_fake_success, 'checks': checks}


def phase_inv13(state):
    """INV-13: Native and Docker Agents share one Agent Core."""
    h = hub()
    a = state['nodes']['A']
    node_id = a['node_id']
    # docker-side binary / CLI (resolve the container process image through /proc/<pid>/root)
    docker_probe = n4.rsh(
        'pid=$(docker inspect -f "{{.State.Pid}}" ' + shlex.quote(CONT_A) + '); '
        'echo "exe_symlink=$(readlink /proc/$pid/exe)"; '
        'echo "binary_sha=$(sha256sum /proc/$pid/root/opt/nodesteer/nodesteer-agent | cut -d" " -f1)"; '
        'docker exec ' + shlex.quote(CONT_A) + ' /opt/nodesteer/nodesteer-agent --help > /tmp/b13-help-a 2>&1; '
        'echo "help_body_sha=$(tail -n +2 /tmp/b13-help-a | sha256sum | cut -d" " -f1)"; '
        'echo "help_line1=$(head -1 /tmp/b13-help-a)"; rm -f /tmp/b13-help-a')[1].strip()
    # native-side binary/CLI on the same physical host (protected native-4 agent)
    native_probe = n4.rsh('echo "binary_sha=$(sha256sum ' + NATIVE4_AGENT_BIN + ' | cut -d" " -f1)"; '
                          + NATIVE4_AGENT_BIN + ' --help > /tmp/b13-help-n 2>&1; '
                          'echo "help_body_sha=$(tail -n +2 /tmp/b13-help-n | sha256sum | cut -d" " -f1)"; '
                          'echo "help_line1=$(head -1 /tmp/b13-help-n)"; rm -f /tmp/b13-help-n')[1].strip()

    def kv(probe):
        out = {}
        for l in probe.splitlines():
            if '=' in l:
                k, v = l.split('=', 1)
                out[k] = v
        return out

    dp, npb = kv(docker_probe), kv(native_probe)
    # same fixture script content: already run on docker A; run identical content on native-4
    if 'script_native' not in state['hub_objects']:
        nfix = _create_script_and_task(h, state, TASK_NATIVE_NAME, [NATIVE4_NODE],
                                       'owned sys-b13 native shared-core comparison')
        state['hub_objects']['script_native'] = nfix
        save_state(state)
    nfix = state['hub_objects']['script_native']
    nrun = run_task_to_terminal(h, nfix['task_id'])
    arun_exec = state['hub_objects']['script_a']
    ddb = db_summary(DB_A)
    docker_row = next((r for r in ddb['executions']['rows'] if r[4] == arun_exec['script_id']), None)
    schema_docker = ddb.get('tables')
    schema_native = db_summary(NATIVE4_AGENT_DB)
    # Hub-side capability/version comparison for both deployment modes
    code, n4node = h.get('/api/nodes/' + NATIVE4_NODE)
    _, danode = h.get('/api/nodes/' + node_id)
    checks = {
        'docker_container_binary_sha_equal_pinned': dp.get('binary_sha') == AGENT_SHA,
        'native_binary_sha_equal_pinned': npb.get('binary_sha') == AGENT_SHA,
        'cli_help_body_identical': dp.get('help_body_sha') == npb.get('help_body_sha'),
        'cli_help_differs_only_argv0_line': dp.get('help_line1', '').startswith('Usage of')
            and npb.get('help_line1', '').startswith('Usage of'),
        'native_execution_success': nrun['terminal'].get('status') == 'SUCCESS' and nrun['stdout'] == MARKER_A,
        'docker_execution_success': docker_row is not None and docker_row[1] == 'SUCCESS' and docker_row[3] == MARKER_A,
        'same_execution_journal_columns': ddb['executions']['columns'] == schema_native['executions']['columns'],
        'schema_tables_equal': schema_docker == schema_native.get('tables'),
        'schema_sha_equal': ddb.get('schema_sha256') == schema_native.get('schema_sha256'),
        'capability_keys_equal': sorted((n4node.get('capabilities') or {}).keys())
            == sorted((danode.get('capabilities') or {}).keys()),
        'same_agent_version_field': n4node.get('agent_version') == danode.get('agent_version'),
        'deployment_modes_distinct': n4node.get('deployment_mode') == 'native'
            and danode.get('deployment_mode') == 'docker',
    }
    ev('phase5-inv13-shared-core', {'captured_at_utc': now_utc(),
                                    'pinned_image_agent_sha256': AGENT_SHA,
                                    'docker_probe': dp, 'native_probe': npb,
                                    'docker_node': {'node_id': node_id, 'deployment_mode': danode.get('deployment_mode'),
                                                    'agent_version': danode.get('agent_version'),
                                                    'capabilities': danode.get('capabilities')},
                                    'native_node': {'node_id': NATIVE4_NODE, 'deployment_mode': n4node.get('deployment_mode'),
                                                    'agent_version': n4node.get('agent_version'),
                                                    'capabilities': n4node.get('capabilities')},
                                    'docker_execution': {'execution_id': arun_exec.get('execution_id'),
                                                         'script_id': arun_exec['script_id'], 'db_row': docker_row},
                                    'native_execution': {'execution_id': nrun['execution_id'],
                                                         'status': nrun['terminal'].get('status'),
                                                         'stdout_api': nrun['stdout'],
                                                         'script_id': nfix['script_id']},
                                    'schema_docker_tables': schema_docker,
                                    'schema_native_tables': schema_native.get('tables'),
                                    'schema_docker_sha256': ddb.get('schema_sha256'),
                                    'schema_native_sha256': schema_native.get('schema_sha256'),
                                    'checks': checks})
    failed = [k for k, v in checks.items() if v is False]
    if failed:
        raise RuntimeError('inv13_checks_failed:' + json.dumps(failed))
    return {'status': 'INV13_PASS', 'checks': checks}


def phase_cleanup(state):
    h = hub()
    oracle = {'captured_at_utc': now_utc(), 'step': 'cleanup', 'hub': {}, 'runtime': {}, 'protected': {}}
    # 1. Hub objects: schedules, tasks (before scripts), scripts, then nodes
    code, schs = h.get('/api/schedules')
    sch_rows = schs.get('schedules') if isinstance(schs, dict) else schs
    for s in (sch_rows or []):
        if isinstance(s, dict) and s.get('name') == SCHED_NAME:
            dcode, _ = h.delete('/api/schedules/' + s['id'])
            gcode, _ = h.get('/api/schedules/' + s['id'])
            oracle['hub'].setdefault('deleted', []).append({'type': 'schedule', 'id': s['id'], 'delete': dcode, 'get_after': gcode})
    code, objs = h.get('/api/tasks')
    rows = objs.get('tasks') if isinstance(objs, dict) else objs
    for o in (rows or []):
        if isinstance(o, dict) and o.get('name') in (TASK_NAME, TASK_NATIVE_NAME, TASK_B_NAME):
            dcode, _ = h.delete('/api/tasks/' + o['id'])
            gcode, _ = h.get('/api/tasks/' + o['id'])
            oracle['hub'].setdefault('deleted', []).append({'type': 'task', 'id': o['id'], 'name': o.get('name'), 'delete': dcode, 'get_after': gcode})
    code, objs = h.get('/api/scripts')
    rows = objs.get('scripts') if isinstance(objs, dict) else objs
    for o in (rows or []):
        if isinstance(o, dict) and o.get('name') == SCRIPT_NAME:
            dcode, _ = h.delete('/api/scripts/' + o['id'])
            gcode, _ = h.get('/api/scripts/' + o['id'])
            oracle['hub'].setdefault('deleted', []).append({'type': 'script', 'id': o['id'], 'name': SCRIPT_NAME, 'delete': dcode, 'get_after': gcode})
    for key in ('A', 'B'):
        nid = state['nodes'].get(key, {}).get('node_id')
        if nid:
            dcode, _ = h.delete('/api/nodes/' + nid)
            gcode, _ = h.get('/api/nodes/' + nid)
            oracle['hub'].setdefault('deleted_nodes', []).append({'key': key, 'id': nid, 'delete': dcode, 'get_after': gcode})
    # 2. runtime: compose down, containers by id, network, volume, root
    b = state['nodes'].get('B', {})
    if b.get('compose_path'):
        n4.docker(['compose', '-p', PROJ, '-f', b['compose_path'], 'down', '-v'], timeout=120, check=False)
    removed = []
    for key in ('A', 'B'):
        c = state['containers'].get(key)
        if not c:
            continue
        cid = c['id']
        insp = n4.container_inspect(cid)
        if insp:
            n4.docker(['stop', cid], timeout=60, check=False)
            n4.docker(['rm', '-f', cid], timeout=60, check=False)
            removed.append({'key': key, 'id': cid, 'removed': n4.container_inspect(cid) is None})
    if not n4.exists('container', CONT_A):
        pass
    n4.docker(['network', 'rm', NET], timeout=60, check=False)
    n4.docker(['volume', 'rm', '-f', VOL_B], timeout=60, check=False)
    # remove owned root (record dev/inode first)
    root_stat = None
    rc, out, _ = n4.rsh('stat -c "%d %i %U %a" ' + shlex.quote(ROOT), check=False)
    if rc == 0:
        root_stat = out.strip()
        n4.rsh('rm -rf ' + shlex.quote(ROOT))
    oracle['runtime'] = {
        'removed_containers': removed,
        'containers_absent': not n4.exists('container', CONT_A) and not n4.exists('container', CONT_B),
        'network_absent': not n4.exists('network', NET),
        'volume_absent': not n4.exists('volume', VOL_B),
        'root_absent': n4.rsh('test -e ' + shlex.quote(ROOT), check=False)[0] != 0,
        'root_before_removal': root_stat,
        'pinned_image_present': bool(n4.image_inspect(IMAGE)),
        'other_images': n4.docker(['images', '-a', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}'])[1].strip().splitlines(),
        'remaining_containers': [l for l in n4.docker(['ps', '-a', '--format', '{{.ID}} {{.Names}}'])[1].strip().splitlines() if l],
        'remaining_volumes': [l for l in n4.docker(['volume', 'ls', '--format', '{{.Name}}'])[1].strip().splitlines() if l],
        'remaining_networks': [l for l in n4.docker(['network', 'ls', '--format', '{{.Name}}'])[1].strip().splitlines() if l],
    }
    # 3. protected nodes still healthy
    code, nodes = h.get('/api/nodes')
    prot = {}
    for nid, nm in PROTECTED_NODES.items():
        r = [x for x in nodes if isinstance(x, dict) and x.get('id') == nid]
        prot[nm] = {'status': r[0].get('status'), 'sync_status': r[0].get('sync_status')} if r else 'MISSING'
    oracle['protected'] = prot
    ev('phase6-cleanup', oracle)
    ev('final-cleanup-oracle', oracle)
    state['cleanup'] = oracle
    save_state(state)
    ok = (oracle['runtime']['containers_absent'] and oracle['runtime']['network_absent']
          and oracle['runtime']['volume_absent'] and oracle['runtime']['root_absent']
          and all(v.get('status') == 'online' and v.get('sync_status') == 'synced' for v in prot.values()))
    return {'status': 'CLEANUP_OK' if ok else 'CLEANUP_INCOMPLETE', 'oracle': oracle}


PHASES = {
    'preflight': phase_preflight,
    'run-enroll': phase_run_enroll,
    'ar03': phase_ar03,
    'compose': phase_compose,
    'ar05': phase_ar05,
    'inv13': phase_inv13,
    'cleanup': phase_cleanup,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', required=True, choices=list(PHASES))
    args = ap.parse_args()
    state = load_state()
    EV.mkdir(parents=True, exist_ok=True)
    res = PHASES[args.phase](state)
    save_state(state)
    print(json.dumps({'phase': args.phase, 'result': res}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
