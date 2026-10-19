#!/usr/bin/env python3
"""Task A: concurrent stress on every write endpoint of the isolated hub.

16 concurrent x 3 rounds per endpoint, unique qa-stress payloads, mixed
read+write phase, integrity read-back, 5xx/SQL-leak capture with hub log.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qalib import Client, fire, load_creds, multipart, new_uid  # noqa: E402

WORK = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(WORK, 'stress-results.json')
HUB_DIR = '/tmp/nodesteer-qa-20261018-stress-authz'
HUB_HOST = '192.168.100.209'

creds = load_creds()
admin = Client(creds['host'], creds['port'])
admin.login(creds['admin_username'], creds['admin_password'])

state = {
    'instance': {'base_url': creds['base_url'], 'port': creds['port']},
    'batches': [],
    'five_xx': [],
    'sql_leaks': [],
    'anomalies': [],
    'hub_log_fetches': [],
}
created = defaultdict(dict)   # batch -> (round, i) -> dict(id=..., name=..., extra)
log_fetched_for_error = False


def fetch_hub_log(tag, tail=300):
    try:
        cp = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
             'root@' + HUB_HOST, 'tail -n %d %s/hub.log' % (tail, HUB_DIR)],
            capture_output=True, text=True, timeout=60)
        lines = cp.stdout.strip().splitlines()
        state['hub_log_fetches'].append({'tag': tag, 'lines': len(lines)})
        return lines
    except Exception as exc:  # noqa
        state['hub_log_fetches'].append({'tag': tag, 'error': str(exc)})
        return []


def note_error(r, batch, payload):
    entry = {
        'batch': batch, 'method': r.method, 'path': r.path, 'status': r.status,
        'body': r.snippet(600), 'payload': payload, 'ms': round(r.ms, 1),
        'allow': r.allow,
    }
    if r.status >= 500:
        state['five_xx'].append(entry)
        note_error.last5xx = entry
    if r.sql_hit:
        entry['sql_marker'] = r.sql_hit
        state['sql_leaks'].append(entry)


def run_batch(name, n, rounds, fn, expect=200):
    """fn(round_no, i, uid) -> (Resp, created_record_or_None)"""
    for rnd in range(rounds):
        def one(i):
            uid = '%s-r%d-i%d-%s' % (name, rnd, i, uuid.uuid4().hex[:6])
            r, rec = fn(rnd, i, uid)
            return r, rec, uid
        results = fire(n, one)
        dist = Counter()
        lat = []
        samples = []
        errs = []
        for i, (r, rec, uid) in enumerate(results):
            dist[r.status] += 1
            lat.append(r.ms)
            if rec is not None:
                created[name][(rnd, i)] = rec
            if r.status != expect or r.sql_hit:
                samples.append({'status': r.status, 'path': r.path, 'body': r.snippet(400)})
                note_error(r, name, uid)
        lat.sort()
        entry = {
            'batch': name, 'round': rnd, 'n': n,
            'status_dist': {str(k): v for k, v in sorted(dist.items())},
            'max_ms': round(max(lat), 1) if lat else None,
            'p95_ms': round(lat[int(len(lat) * 0.95) - 1], 1) if lat else None,
            'non_expect_samples': samples[:6],
            'errors': len(errs),
        }
        state['batches'].append(entry)
        print('[batch] %-22s r%d dist=%s max=%.0fms' % (
            name, rnd, dict(dist), max(lat) if lat else 0), flush=True)
        save()


_raw_run_batch = run_batch


def run_batch(*a, **kw):
    return guarded('batch:' + str(a[0]) if a else 'batch', _raw_run_batch, *a, **kw)


def save():
    with open(OUT, 'w') as f:
        json.dump(state, f, indent=1)


def guarded(label, fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as exc:  # noqa
        state['anomalies'].append({'phase': label, 'error': repr(exc)})
        save()
        print('[anomaly] %s: %r' % (label, exc), flush=True)
        return None


def jbody(r):
    return r.json() or {}


# ---------- fixtures ----------
def setup():
    fx = {}
    r = admin.request('POST', '/api/nodes/enrollment',
                      {'node_name': 'qa-stress-src', 'node_ip': '10.99.0.1'})
    assert r.status == 200, r.snippet()
    fx['node_src'] = jbody(r)['node_id']
    r = admin.request('POST', '/api/nodes/enrollment',
                      {'node_name': 'qa-stress-dst', 'node_ip': '10.99.0.2'})
    assert r.status == 200, r.snippet()
    fx['node_dst'] = jbody(r)['node_id']
    assert fx['node_src'] != fx['node_dst'], 'fixture nodes must differ'
    r = admin.request('POST', '/api/tasks', {
        'name': 'qa-fixture-sched-host', 'type': 'command', 'command': 'true',
        'target': {'type': 'label', 'label_key': 'qa-stress'}, 'enabled': False})
    assert r.status == 200, r.snippet()
    fx['task_sched'] = jbody(r)['id']
    state['fixtures'] = fx
    save()
    return fx


fx = setup()

# ---------- creates ----------
def make_create(batch, method, path, build):
    def fn(rnd, i, uid):
        body = build(rnd, i, uid)
        r = admin.request(method, path, body)
        rec = None
        if r.status == 200:
            j = jbody(r)
            rec = {'id': j.get('id') or j.get('node_id'), 'name': body.get('name') or body.get('username') or body.get('node_name')}
        return r, rec
    return fn


run_batch('tasks_create', 16, 3, make_create('tasks_create', 'POST', '/api/tasks',
    lambda rnd, i, uid: {'name': 'qa-stress-task-' + uid, 'type': 'command',
                         'command': 'true', 'target': {'type': 'label', 'label_key': 'qa-stress'},
                         'enabled': False}))

run_batch('scripts_create', 16, 3, make_create('scripts_create', 'POST', '/api/scripts',
    lambda rnd, i, uid: {'name': 'qa-stress-script-' + uid, 'interpreter': 'shell',
                         'content': 'echo ' + uid, 'enabled': True}))

run_batch('schedules_create', 16, 3, make_create('schedules_create', 'POST', '/api/schedules',
    lambda rnd, i, uid: {'task_id': fx['task_sched'], 'type': 'interval', 'interval_sec': 3600,
                         'timezone': 'UTC', 'execution_owner': 'hub',
                         'offline_policy': 'hub_online_required', 'misfire_policy': 'skip',
                         'enabled': False}))

run_batch('groups_create', 16, 3, make_create('groups_create', 'POST', '/api/groups',
    lambda rnd, i, uid: {'name': 'qa-stress-group-' + uid, 'type': 'static'}))

run_batch('apps_create', 16, 3, make_create('apps_create', 'POST', '/api/applications',
    lambda rnd, i, uid: {'name': 'qa-stress-app-' + uid}))

run_batch('users_create', 16, 3, make_create('users_create', 'POST', '/api/users',
    lambda rnd, i, uid: {'username': 'qa-stress-user-' + uid,
                         'password': 'QaStress!' + uid[-8:], 'role': 'viewer'}))

run_batch('nodes_enroll', 16, 3, make_create('nodes_enroll', 'POST', '/api/nodes/enrollment',
    lambda rnd, i, uid: {'node_name': 'qa-stress-node-' + uid,
                         'node_ip': '10.99.%d.%d' % (rnd + 1, i + 1)}))

run_batch('transfers_create', 16, 3, make_create('transfers_create', 'POST', '/api/transfers',
    lambda rnd, i, uid: {'source_node_id': fx['node_src'], 'source_path': '/tmp/qa-stress-src-' + uid,
                         'targets': [{'node_id': fx['node_dst'],
                                      'destination_path': '/tmp/qa-stress-dst-' + uid}]}))


def artifact_upload(rnd, i, uid):
    fields = {'name': 'qa-stress-art-' + uid, 'version': '1.0.0', 'architecture': 'amd64'}
    content = ('qa-stress-payload-%s' % uid).encode() * 8
    raw, ct = multipart(fields, 'qa-stress-%s.bin' % uid, content)
    r = admin.request('POST', '/api/artifacts', raw_body=raw, content_type=ct)
    rec = None
    if r.status == 200:
        rec = {'id': jbody(r).get('id'), 'name': fields['name']}
    return r, rec


run_batch('artifacts_upload', 16, 3, artifact_upload)

# ---------- updates ----------
def put_path(batch_prefix, rec):
    seg = {'tasks': 'tasks', 'scripts': 'scripts', 'schedules': 'schedules',
           'groups': 'groups', 'apps': 'applications', 'users': 'users'}[batch_prefix]
    return '/api/%s/%s' % (seg, rec['id'])


def run_update(name, build):
    def fn(rnd, i, uid):
        rec = created[name.replace('_update', '_create')][(rnd, i)]
        body = build(rnd, i, uid, rec)
        r = admin.request('PUT', put_path(name.split('_')[0], rec), body)
        return r, None
    guarded(name, run_batch, name, 16, 3, fn)


run_update('tasks_update', lambda rnd, i, uid, rec: {
    'name': 'qa-stress-task-u-' + uid, 'type': 'command', 'command': 'true',
    'target': {'type': 'label', 'label_key': 'qa-stress'}, 'enabled': False})

run_update('scripts_update', lambda rnd, i, uid, rec: {
    'name': rec['name'], 'interpreter': 'shell', 'content': 'echo updated-' + uid, 'enabled': True})

run_update('schedules_update', lambda rnd, i, uid, rec: {
    'task_id': fx['task_sched'], 'type': 'interval', 'interval_sec': 1800 + i,
    'timezone': 'UTC', 'execution_owner': 'hub', 'offline_policy': 'hub_online_required',
    'misfire_policy': 'skip', 'enabled': False})

run_update('groups_update', lambda rnd, i, uid, rec: {
    'name': rec['name'], 'description': 'qa-stress-upd-' + uid, 'type': 'static'})

run_update('apps_update', lambda rnd, i, uid, rec: {
    'name': rec['name']})

run_update('users_update', lambda rnd, i, uid, rec: {
    'role': 'operator' if i % 2 == 0 else 'viewer'})

# node update via POST /api/nodes {id,status}
run_batch('nodes_post_status', 16, 3, lambda rnd, i, uid: (
    lambda rec: (admin.request('POST', '/api/nodes', {'id': rec['id'], 'status': 'offline'}), None)[0:2]
)(created['nodes_enroll'][(rnd, i)]))

# node labels via POST /api/nodes/{id}
run_batch('nodes_labels_update', 16, 3, lambda rnd, i, uid: (
    admin.request('POST', '/api/nodes/%s' % created['nodes_enroll'][(rnd, i)]['id'],
                  {'labels': {'qa-stress': uid}}), None))

# transfers retry (pending, no sha -> stays pending)
run_batch('transfers_retry', 16, 3, lambda rnd, i, uid: (
    admin.request('POST', '/api/transfers/%s/retry' % created['transfers_create'][(rnd, i)]['id']), None))

# settings update
def settings_fn(rnd, i, uid):
    val = 30 + rnd * 50 + i
    r = admin.request('PUT', '/api/settings', {'heartbeat_interval_sec': str(val)})
    return r, None


written_settings = set()
for rnd in range(3):
    for i in range(16):
        written_settings.add(30 + rnd * 50 + i)
run_batch('settings_update', 16, 3, settings_fn)

# node revoke-credential (admin) - distinct node per request
run_batch('nodes_revoke', 16, 3, lambda rnd, i, uid: (
    admin.request('POST', '/api/nodes/%s/revoke-credential' % created['nodes_enroll'][(rnd, i)]['id']), None))

# ---------- deletes ----------
def run_delete(name, prefix):
    def fn(rnd, i, uid):
        rec = created[name.replace('_delete', '_create')][(rnd, i)]
        return admin.request('DELETE', put_path(prefix, rec)), None
    guarded(name, run_batch, name, 16, 3, fn)


run_delete('tasks_delete', 'tasks')
run_delete('scripts_delete', 'scripts')
run_delete('schedules_delete', 'schedules')
run_delete('groups_delete', 'groups')
run_delete('apps_delete', 'apps')

run_batch('nodes_delete', 16, 3, lambda rnd, i, uid: (
    admin.request('DELETE', '/api/nodes/%s' % created['nodes_enroll'][(rnd, i)]['id']), None))

run_batch('artifacts_delete', 16, 3, lambda rnd, i, uid: (
    admin.request('DELETE', '/api/artifacts/%s' % created['artifacts_upload'][(rnd, i)]['id']), None))

# transfers cancel
run_batch('transfers_cancel', 16, 3, lambda rnd, i, uid: (
    admin.request('POST', '/api/transfers/%s/cancel' % created['transfers_create'][(rnd, i)]['id']), None))

save()

# ---------- mixed read + write ----------
mixed = {'writers': defaultdict(Counter), 'readers': defaultdict(Counter), 'duration_s': 0,
         'write_samples': [], 'read_samples': []}
stop = threading.Event()
write_errors = []


def writer(kind):
    n = 0
    while not stop.is_set():
        n += 1
        uid = 'qa-mixed-%s-%d-%s' % (kind, n, uuid.uuid4().hex[:6])
        try:
            if kind == 'tasks':
                r1 = admin.request('POST', '/api/tasks', {
                    'name': uid, 'type': 'command', 'command': 'true',
                    'target': {'type': 'label', 'label_key': 'qa-stress'}, 'enabled': False})
                mixed['writers'][kind][r1.status] += 1
                tid = jbody(r1).get('id')
                if tid:
                    r2 = admin.request('PUT', '/api/tasks/%s' % tid, {
                        'name': uid + '-u', 'type': 'command', 'command': 'true',
                        'target': {'type': 'label', 'label_key': 'qa-stress'}, 'enabled': False})
                    mixed['writers'][kind][r2.status] += 1
                    r3 = admin.request('DELETE', '/api/tasks/%s' % tid)
                    mixed['writers'][kind][r3.status] += 1
            elif kind == 'scripts':
                r1 = admin.request('POST', '/api/scripts', {
                    'name': uid, 'interpreter': 'shell', 'content': 'echo x', 'enabled': True})
                mixed['writers'][kind][r1.status] += 1
                sid = jbody(r1).get('id')
                if sid:
                    r2 = admin.request('DELETE', '/api/scripts/%s' % sid)
                    mixed['writers'][kind][r2.status] += 1
            elif kind == 'settings':
                r = admin.request('PUT', '/api/settings', {'heartbeat_interval_sec': str(60 + n % 30)})
                mixed['writers'][kind][r.status] += 1
            elif kind == 'nodes':
                r1 = admin.request('POST', '/api/nodes/enrollment',
                                   {'node_name': uid,
                                    'node_ip': '10.98.%d.%d' % ((n // 200) % 250 + 1, (n % 200) + 1)})
                mixed['writers'][kind][r1.status] += 1
                nid = jbody(r1).get('node_id')
                if nid:
                    r2 = admin.request('DELETE', '/api/nodes/%s' % nid)
                    mixed['writers'][kind][r2.status] += 1
        except Exception as exc:  # noqa
            write_errors.append('%s: %s' % (kind, exc))


READ_PATHS = ['/api/tasks', '/api/scripts', '/api/nodes', '/api/executions?limit=20',
              '/api/audit?limit=20', '/api/settings', '/api/me', '/api/groups']


def reader(slot):
    n = 0
    while not stop.is_set():
        path = READ_PATHS[(slot + n) % len(READ_PATHS)]
        n += 1
        r = admin.request('GET', path)
        mixed['readers']['slot%d' % slot][r.status] += 1
        if r.status != 200 or r.sql_hit:
            mixed['read_samples'].append({'path': path, 'status': r.status, 'body': r.snippet(300)})
            note_error(r, 'mixed_read', path)


t0 = time.time()
threads = [threading.Thread(target=writer, args=(k,)) for k in ('tasks', 'scripts', 'settings', 'nodes')]
threads += [threading.Thread(target=reader, args=(s,)) for s in range(6)]
for t in threads:
    t.start()
time.sleep(8)
stop.set()
for t in threads:
    t.join(timeout=30)
mixed['duration_s'] = round(time.time() - t0, 2)
mixed['writers'] = {k: dict(v) for k, v in mixed['writers'].items()}
mixed['readers'] = {k: dict(v) for k, v in mixed['readers'].items()}
mixed['write_errors'] = write_errors
state['mixed'] = mixed
print('[mixed] writers=%s readers=%s' % (mixed['writers'], mixed['readers']), flush=True)
save()

# ---------- integrity read-back ----------
integrity = {'checks': [], 'failures': []}


def check(name, ok, detail):
    integrity['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
    if not ok:
        integrity['failures'].append({'check': name, 'detail': detail})
    print('[integrity] %-38s %s %s' % (name, 'OK' if ok else 'FAIL', detail), flush=True)


def list_of(path):
    r = admin.request('GET', path)
    return r.status, jbody(r)


# remaining stress resources
st, tasks = list_of('/api/tasks')
tasks = tasks if isinstance(tasks, list) else tasks.get('tasks', [])
leftover_tasks = [t for t in tasks if str(t.get('name', '')).startswith('qa-stress-task')]
names = [t['name'] for t in leftover_tasks]
check('tasks: all qa-stress deleted', len(leftover_tasks) == 0, 'leftover=%d' % len(leftover_tasks))
check('tasks: no duplicate names', len(names) == len(set(names)), 'n=%d uniq=%d' % (len(names), len(set(names))))

st, scripts = list_of('/api/scripts')
scripts = scripts if isinstance(scripts, list) else []
leftover_scripts = [s for s in scripts if str(s.get('name', '')).startswith('qa-stress-script')]
check('scripts: all qa-stress deleted', len(leftover_scripts) == 0, 'leftover=%d' % len(leftover_scripts))

st, scheds = list_of('/api/schedules')
scheds = scheds if isinstance(scheds, list) else []
sched_ids = {r['id'] for r in created['schedules_create'].values()}
leftover_scheds = [s for s in scheds if s.get('id') in sched_ids]
check('schedules: all qa-stress deleted', len(leftover_scheds) == 0, 'leftover=%d' % len(leftover_scheds))

st, groups = list_of('/api/groups')
groups = groups if isinstance(groups, list) else []
leftover_groups = [g for g in groups if str(g.get('name', '')).startswith('qa-stress-group')]
check('groups: all qa-stress deleted', len(leftover_groups) == 0, 'leftover=%d' % len(leftover_groups))

st, apps = list_of('/api/applications')
apps = apps if isinstance(apps, list) else []
leftover_apps = [a for a in apps if str(a.get('name', '')).startswith('qa-stress-app')]
check('applications: all qa-stress deleted', len(leftover_apps) == 0, 'leftover=%d' % len(leftover_apps))

st, users = list_of('/api/users')
users = users if isinstance(users, list) else []
stress_users = [u for u in users if str(u.get('username', '')).startswith('qa-stress-user')]
unames = [u['username'] for u in stress_users]
check('users: 48 present', len(stress_users) == 48, 'count=%d' % len(stress_users))
check('users: no duplicate usernames', len(unames) == len(set(unames)), 'n=%d uniq=%d' % (len(unames), len(set(unames))))
bad_roles = []
for u in stress_users:
    suffix = u['username'].rsplit('-i', 1)[-1]
    try:
        i = int(suffix.split('-')[0])
    except Exception:
        bad_roles.append((u['username'], u['role'], 'unparsed'))
        continue
    want = 'operator' if i % 2 == 0 else 'viewer'
    if u['role'] != want:
        bad_roles.append((u['username'], u['role'], want))
check('users: role updates intact', len(bad_roles) == 0, 'mismatch=%d %s' % (len(bad_roles), bad_roles[:5]))

st, nodes = list_of('/api/nodes')
nodes = nodes if isinstance(nodes, list) else nodes.get('nodes', [])
leftover_nodes = [n for n in nodes if str(n.get('hostname', '')).startswith('qa-stress-node')]
check('nodes: all qa-stress deleted', len(leftover_nodes) == 0, 'leftover=%d' % len(leftover_nodes))

st, transfers = list_of('/api/transfers')
transfers = transfers if isinstance(transfers, list) else []
tids = {r['id'] for r in created['transfers_create'].values()}
mine = [t for t in transfers if t.get('id') in tids]
bad = [t for t in mine if t.get('status') != 'CANCELED' or len(t.get('targets') or []) != 1
       or (t.get('targets') or [{}])[0].get('status') != 'CANCELED']
check('transfers: 48 canceled & intact', len(mine) == 48 and len(bad) == 0,
      'found=%d bad=%d' % (len(mine), len(bad)))

st, settings = list_of('/api/settings')
settings = settings if isinstance(settings, dict) else {}
hb = settings.get('heartbeat_interval_sec')
mixed_settings = set(range(60, 90))  # mixed writer writes 60+n%30
check('settings: value is one written', hb is not None and int(hb) in (written_settings | mixed_settings),
      'value=%s' % hb)

st, arts = list_of('/api/artifacts')
arts = arts if isinstance(arts, list) else []
leftover_arts = [a for a in arts if str(a.get('name', '')).startswith('qa-stress-art')]
check('artifacts: all qa-stress deleted', len(leftover_arts) == 0, 'leftover=%d' % len(leftover_arts))

# deleted-id sampling -> 404
import random
random.seed(42)
sample = random.sample(list(created['tasks_create'].values()), min(6, len(created['tasks_create'])))
bad = []
for rec in sample:
    r = admin.request('GET', '/api/tasks/%s' % rec['id'])
    if r.status != 404:
        bad.append((rec['id'], r.status))
check('tasks: deleted ids read back 404', len(bad) == 0, 'bad=%s' % bad)

state['integrity'] = integrity

# ---------- summary ----------
five = state['five_xx']
summary = {
    'total_batches': len(state['batches']),
    'five_xx_count': len(five),
    'sql_leak_count': len(state['sql_leaks']),
    'integrity_failures': len(integrity['failures']),
    'batch_status_union': dict(Counter(
        s for b in state['batches'] for s, c in b['status_dist'].items() for _ in range(c))),
}
state['summary'] = summary
save()

# hub log copy
lines = fetch_hub_log('final')
with open(os.path.join(WORK, 'hub.log'), 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(json.dumps(summary, indent=1))
print('DONE stress')
