#!/usr/bin/env python3
"""Task B: full endpoint authz matrix.

Enumerates every endpoint registered in internal/hub/api/api.go Routes(),
then probes each (method x role x {existing id, nonexistent id}) and records
status/Allow/error body. Verdicts: 401/403 correctness, existence-oracle
consistency for unauthorized roles, 405+Allow on unsupported methods,
no 5xx / raw SQL text.
"""
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qalib import Client, load_creds, multipart  # noqa: E402

WORK = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(WORK, 'authz-matrix.json')
HUB_DIR = '/tmp/nodesteer-qa-20261018-stress-authz'
HUB_HOST = '192.168.100.209'

creds = load_creds()
ROLE_ADMIN, ROLE_OP, ROLE_VIEWER = 'administrator', 'operator', 'viewer'
NX = 'qa-nonexistent-' + uuid.uuid4().hex[:12]

admin = Client(creds['host'], creds['port'])
admin.login(creds['admin_username'], creds['admin_password'])

state = {'roles': {}, 'fixtures': {}, 'rows': [], 'method_probes': [],
         'findings': [], 'summary': {}}


def save():
    with open(OUT, 'w') as f:
        json.dump(state, f, indent=1)


def jb(r):
    try:
        return json.loads(r.body)
    except Exception:
        return {}


def err_of(r):
    if r.status < 400:
        return None
    j = jb(r)
    if isinstance(j, dict) and j.get('error'):
        return j['error']
    return r.snippet(120)


def find_id(list_path, key, value):
    data = jb(admin.request('GET', list_path))
    if isinstance(data, dict):
        for k in ('tasks', 'nodes', 'items'):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and item.get(key) == value:
            return item.get('id')
    return None


# ---------- fixtures ----------
fx = {}
for uname, role in [('qa-authz-operator', 'operator'), ('qa-authz-viewer', 'viewer'),
                    ('qa-authz-user-x', 'viewer')]:
    r = admin.request('POST', '/api/users',
                      {'username': uname, 'password': 'QaAuthz!' + uname[-4:], 'role': role})
    if r.status == 400 and 'already exists' in r.body:
        r2 = admin.request('GET', '/api/users')
        uid = [u['id'] for u in jb(r2) if u['username'] == uname][0]
        fx[uname] = uid
        continue
    assert r.status == 200, r.snippet()
    fx[uname] = jb(r)['id']

for nm, ip in [('qa-authz-node-x', '10.96.1.1'), ('qa-authz-node-y', '10.96.1.2'),
               ('qa-authz-node-r', '10.96.1.3'), ('qa-authz-node-d', '10.96.1.4')]:
    r = admin.request('POST', '/api/nodes/enrollment', {'node_name': nm, 'node_ip': ip})
    assert r.status == 200, r.snippet()
    fx[nm] = jb(r)['node_id']

def ensure_task(name):
    r = admin.request('POST', '/api/tasks', {
        'name': name, 'type': 'command', 'command': 'true',
        'target': {'type': 'label', 'label_key': 'qa-authz'}, 'enabled': False})
    if r.status == 200:
        return jb(r)['id']
    got = find_id('/api/tasks', 'name', name)
    assert got, 'task fixture failed: %s' % r.snippet()
    return got

fx['task-x'] = ensure_task('qa-authz-task-x')
fx['task-host'] = ensure_task('qa-authz-task-host')

r = admin.request('POST', '/api/scripts', {
    'name': 'qa-authz-script-x', 'interpreter': 'shell', 'content': 'echo qa-authz', 'enabled': True})
if r.status == 200:
    fx['script-x'] = jb(r)['id']
else:
    fx['script-x'] = find_id('/api/scripts', 'name', 'qa-authz-script-x')
assert fx.get('script-x'), 'script fixture failed'

r = admin.request('POST', '/api/schedules', {
    'task_id': fx['task-host'], 'type': 'interval', 'interval_sec': 7200, 'timezone': 'UTC',
    'execution_owner': 'hub', 'offline_policy': 'hub_online_required',
    'misfire_policy': 'skip', 'enabled': False})
assert r.status == 200, r.snippet()
fx['schedule-x'] = jb(r)['id']

r = admin.request('POST', '/api/groups', {'name': 'qa-authz-group-x', 'type': 'static'})
if r.status == 200:
    fx['group-x'] = jb(r)['id']
else:
    fx['group-x'] = find_id('/api/groups', 'name', 'qa-authz-group-x')
assert fx.get('group-x'), 'group fixture failed'

r = admin.request('POST', '/api/applications', {'name': 'qa-authz-app-x'})
if r.status == 200:
    fx['app-x'] = jb(r)['id']
else:
    fx['app-x'] = find_id('/api/applications', 'name', 'qa-authz-app-x')
assert fx.get('app-x'), 'app fixture failed'

for key, dst in [('xfer-r', None), ('xfer-c', None)]:
    r = admin.request('POST', '/api/transfers', {
        'source_node_id': fx['qa-authz-node-x'], 'source_path': '/tmp/qa-authz-src-' + key,
        'targets': [{'node_id': fx['qa-authz-node-y'], 'destination_path': '/tmp/qa-authz-dst-' + key}]})
    assert r.status == 200, r.snippet()
    fx[key] = jb(r)['id']

raw, ct = multipart({'name': 'qa-authz-art-x', 'version': '1.0.0', 'architecture': 'amd64'},
                    'qa-authz.bin', b'qa-authz-artifact-content')
r = admin.request('POST', '/api/artifacts', raw_body=raw, content_type=ct)
assert r.status == 200, r.snippet()
fx['artifact-x'] = jb(r)['id']

# execution fixture inserted directly into the isolated instance DB (no agent available)
exec_id = 'qa-authz-exec-' + uuid.uuid4().hex[:8]
sql = ("import sqlite3,time;c=sqlite3.connect('%s/data/hub.db');"
       "c.execute(\"INSERT INTO executions (id,task_id,task_revision,script_id,script_revision,node_id,"
       "trigger_type,scheduled_time,start_time,end_time,status,exit_code,stdout,stderr,stdout_truncated,"
       "stderr_truncated,offline,block_reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\","
       "(%r,%r,1,'',0,%r,'manual','','','','SUCCEEDED',0,'ok','',0,0,0,'',time.strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ',time.gmtime())));"
       "c.commit();print('inserted')") % (HUB_DIR, exec_id, fx['task-x'], fx['qa-authz-node-x'])
cp = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'root@' + HUB_HOST, 'python3 -c %s' % json.dumps(sql)],
                    capture_output=True, text=True, timeout=60)
assert 'inserted' in cp.stdout, (cp.stdout, cp.stderr)
fx['exec-x'] = exec_id
r = admin.request('GET', '/api/executions/%s' % exec_id)
assert r.status == 200, 'execution fixture not readable: %s %s' % (r.status, r.snippet())
state['fixtures'] = fx
save()

# ---------- role sessions ----------
for role, uname in [(ROLE_ADMIN, creds['admin_username']), (ROLE_OP, 'qa-authz-operator'),
                    (ROLE_VIEWER, 'qa-authz-viewer')]:
    c = Client(creds['host'], creds['port'])
    pw = creds['admin_password'] if role == ROLE_ADMIN else 'QaAuthz!' + uname[-4:]
    c.login(uname, pw)
    state['roles'][role] = {'user': uname}
    globals()['client_' + role] = c
save()

CLIENTS = {ROLE_ADMIN: client_administrator, ROLE_OP: client_operator,
           ROLE_VIEWER: client_viewer, 'anon': Client(creds['host'], creds['port'])}


def P(name, method, perm, build, variants=('existing', 'nonexistent'), agent=False):
    """build(variant) -> (path, body, headers)"""
    return {'name': name, 'method': method, 'perm': perm, 'build': build,
            'variants': list(variants), 'agent': agent}


def ident(key, variant):
    return fx[key] if variant == 'existing' else NX


TASK_BODY = {'name': 'qa-authz-task-x', 'type': 'command', 'command': 'true',
             'target': {'type': 'label', 'label_key': 'qa-authz'}, 'enabled': False}

SPECS = [
    P('login_post', 'POST', 'public', lambda v: ('/api/login', {'username': 'nobody-' + uuid.uuid4().hex[:6], 'password': 'x'}, None), variants=('-',)),
    P('me_get', 'GET', 'read', lambda v: ('/api/me', None, None), variants=('-',)),
    P('logout_post', 'POST', 'read', lambda v: ('/api/logout', None, None), variants=('-',)),
    P('logout_get', 'GET', 'read', lambda v: ('/api/logout', None, None), variants=('-',)),
    P('oidc_state_get', 'GET', 'public', lambda v: ('/api/oidc/state', None, None), variants=('-',)),
    P('oidc_login_get', 'GET', 'public', lambda v: ('/api/oidc/login', None, None), variants=('-',)),
    P('oidc_callback_get', 'GET', 'public', lambda v: ('/api/oidc/callback?state=x&code=y', None, None), variants=('-',)),
    P('agent_binary_head', 'HEAD', 'public', lambda v: ('/api/agent/binary?architecture=amd64', None, None), variants=('-',)),
    P('agent_binary_badarch', 'GET', 'public', lambda v: ('/api/agent/binary?architecture=mips', None, None), variants=('-',)),
    P('healthz_get', 'GET', 'public', lambda v: ('/healthz', None, None), variants=('-',)),
    P('readyz_get', 'GET', 'public', lambda v: ('/readyz', None, None), variants=('-',)),
    P('metrics_get', 'GET', 'read', lambda v: ('/metrics', None, None), variants=('-',)),

    P('nodes_get', 'GET', 'read', lambda v: ('/api/nodes', None, None), variants=('-',)),
    P('nodes_post', 'POST', 'write',
      lambda v: ('/api/nodes', {'id': ident('qa-authz-node-x', v), 'status': 'offline'}, None)),
    P('nodes_upgrade_post', 'POST', 'run',
      lambda v: ('/api/nodes/upgrade', {'node_ids': [ident('qa-authz-node-x', v)]}, None)),
    P('nodes_enrollment_get', 'GET', 'admin',
      lambda v: ('/api/nodes/enrollment?node_name=qa-authz-probe&node_ip=10.96.9.9', None, None), variants=('-',)),
    P('node_get', 'GET', 'read', lambda v: ('/api/nodes/%s' % ident('qa-authz-node-x', v), None, None)),
    P('node_put', 'PUT', 'write',
      lambda v: ('/api/nodes/%s' % ident('qa-authz-node-x', v), {'labels': {'qa': '1'}}, None)),
    P('node_post', 'POST', 'write',
      lambda v: ('/api/nodes/%s' % ident('qa-authz-node-x', v), {'labels': {'qa': '2'}}, None)),
    P('node_delete', 'DELETE', 'admin', lambda v: ('/api/nodes/%s' % ident('qa-authz-node-d', v), None, None)),
    P('node_revoke_post', 'POST', 'admin',
      lambda v: ('/api/nodes/%s/revoke-credential' % ident('qa-authz-node-r', v), None, None)),

    P('transfers_get', 'GET', 'read', lambda v: ('/api/transfers', None, None), variants=('-',)),
    P('transfers_post', 'POST', 'admin', lambda v: ('/api/transfers', {
        'source_node_id': ident('qa-authz-node-x', v), 'source_path': '/tmp/qa-authz-post-%s' % v,
        'targets': [{'node_id': fx['qa-authz-node-y'], 'destination_path': '/tmp/qa-authz-post-dst-%s' % v}]}, None)),
    P('transfer_get', 'GET', 'read', lambda v: ('/api/transfers/%s' % ident('xfer-r', v), None, None)),
    P('transfer_retry_post', 'POST', 'admin', lambda v: ('/api/transfers/%s/retry' % ident('xfer-r', v), None, None)),
    P('transfer_cancel_post', 'POST', 'admin', lambda v: ('/api/transfers/%s/cancel' % ident('xfer-c', v), None, None)),

    P('groups_get', 'GET', 'read', lambda v: ('/api/groups', None, None), variants=('-',)),
    P('groups_post', 'POST', 'write', lambda v: ('/api/groups', {'name': 'qa-authz-group-new-%s' % uuid.uuid4().hex[:8], 'type': 'static'}, None), variants=('-',)),
    P('group_get', 'GET', 'read', lambda v: ('/api/groups/%s' % ident('group-x', v), None, None)),
    P('group_put', 'PUT', 'write', lambda v: ('/api/groups/%s' % ident('group-x', v), {'name': 'qa-authz-group-x', 'description': 'upd', 'type': 'static'}, None)),
    P('group_delete', 'DELETE', 'write', lambda v: ('/api/groups/%s' % ident('group-x', v), None, None)),

    P('scripts_get', 'GET', 'read', lambda v: ('/api/scripts', None, None), variants=('-',)),
    P('scripts_post', 'POST', 'write', lambda v: ('/api/scripts', {'name': 'qa-authz-script-new-%s' % uuid.uuid4().hex[:8], 'interpreter': 'shell', 'content': 'echo new', 'enabled': True}, None), variants=('-',)),
    P('script_get', 'GET', 'read', lambda v: ('/api/scripts/%s' % ident('script-x', v), None, None)),
    P('script_put', 'PUT', 'write', lambda v: ('/api/scripts/%s' % ident('script-x', v), {'name': 'qa-authz-script-x', 'interpreter': 'shell', 'content': 'echo updated', 'enabled': True}, None)),
    P('script_revisions_get', 'GET', 'read', lambda v: ('/api/scripts/%s/revisions' % ident('script-x', v), None, None)),
    P('script_revision_get', 'GET', 'read', lambda v: ('/api/scripts/%s/revisions/1' % ident('script-x', v), None, None)),
    P('script_delete', 'DELETE', 'write', lambda v: ('/api/scripts/%s' % ident('script-x', v), None, None)),

    P('schedules_get', 'GET', 'read', lambda v: ('/api/schedules', None, None), variants=('-',)),
    P('schedules_post', 'POST', 'write', lambda v: ('/api/schedules', {'task_id': fx['task-host'], 'type': 'interval', 'interval_sec': 7300, 'timezone': 'UTC', 'execution_owner': 'hub', 'offline_policy': 'hub_online_required', 'misfire_policy': 'skip', 'enabled': False}, None), variants=('-',)),
    P('schedule_get', 'GET', 'read', lambda v: ('/api/schedules/%s' % ident('schedule-x', v), None, None)),
    P('schedule_put', 'PUT', 'write', lambda v: ('/api/schedules/%s' % ident('schedule-x', v), {'task_id': fx['task-host'], 'type': 'interval', 'interval_sec': 7400, 'timezone': 'UTC', 'execution_owner': 'hub', 'offline_policy': 'hub_online_required', 'misfire_policy': 'skip', 'enabled': False}, None)),
    P('schedule_delete', 'DELETE', 'write', lambda v: ('/api/schedules/%s' % ident('schedule-x', v), None, None)),

    P('tasks_get', 'GET', 'read', lambda v: ('/api/tasks', None, None), variants=('-',)),
    P('tasks_post', 'POST', 'write', lambda v: ('/api/tasks', {'name': 'qa-authz-task-new-%s' % uuid.uuid4().hex[:8], 'type': 'command', 'command': 'true', 'target': {'type': 'label', 'label_key': 'qa-authz'}, 'enabled': False}, None), variants=('-',)),
    P('task_get', 'GET', 'read', lambda v: ('/api/tasks/%s' % ident('task-x', v), None, None)),
    P('task_put', 'PUT', 'write', lambda v: ('/api/tasks/%s' % ident('task-x', v), TASK_BODY, None)),
    P('task_run_post', 'POST', 'run', lambda v: ('/api/tasks/%s/run' % ident('task-x', v), {}, None)),
    P('task_delete', 'DELETE', 'write', lambda v: ('/api/tasks/%s' % ident('task-x', v), None, None)),

    P('artifacts_get', 'GET', 'read', lambda v: ('/api/artifacts', None, None), variants=('-',)),
    P('artifacts_post', 'POST', 'write', lambda v: ('/api/artifacts', {'__multipart__': True}, None), variants=('-',)),
    P('artifact_get', 'GET', 'read', lambda v: ('/api/artifacts/%s' % ident('artifact-x', v), None, None)),
    P('artifact_download_user', 'GET', 'read', lambda v: ('/api/artifacts/%s/download' % ident('artifact-x', v), None, None)),
    P('artifact_download_agent_header', 'GET', 'agent',
      lambda v: ('/api/artifacts/%s/download' % ident('artifact-x', v), None, {'X-NodeSteer-Agent-Token': creds['registration_token']})),
    P('artifact_download_agent_query', 'GET', 'agent',
      lambda v: ('/api/artifacts/%s/download?agent_token=%s' % (ident('artifact-x', v), creds['registration_token']), None, None)),
    P('artifact_delete', 'DELETE', 'write', lambda v: ('/api/artifacts/%s' % ident('artifact-x', v), None, None)),

    P('apps_get', 'GET', 'read', lambda v: ('/api/applications', None, None), variants=('-',)),
    P('apps_post', 'POST', 'write', lambda v: ('/api/applications', {'name': 'qa-authz-app-new-%s' % uuid.uuid4().hex[:8]}, None), variants=('-',)),
    P('app_get', 'GET', 'read', lambda v: ('/api/applications/%s' % ident('app-x', v), None, None)),
    P('app_put', 'PUT', 'write', lambda v: ('/api/applications/%s' % ident('app-x', v), {'name': 'qa-authz-app-x'}, None)),
    P('app_state_get', 'GET', 'read', lambda v: ('/api/applications/%s/state' % ident('app-x', v), None, None)),
    P('app_executions_get', 'GET', 'read', lambda v: ('/api/applications/%s/executions' % ident('app-x', v), None, None)),
    P('app_revisions_get', 'GET', 'read', lambda v: ('/api/applications/%s/revisions' % ident('app-x', v), None, None)),
    P('app_nodes_get', 'GET', 'read', lambda v: ('/api/applications/%s/nodes' % ident('app-x', v), None, None)),
    P('app_assign_post', 'POST', 'write', lambda v: ('/api/applications/%s/assign' % ident('app-x', v), {'node_ids': [fx['qa-authz-node-x']]}, None)),
    P('app_deploy_post', 'POST', 'run', lambda v: ('/api/applications/%s/deploy' % ident('app-x', v), {'node_ids': [fx['qa-authz-node-x']], 'operation': 'deploy'}, None)),
    P('app_delete', 'DELETE', 'write', lambda v: ('/api/applications/%s' % ident('app-x', v), None, None)),

    P('executions_get', 'GET', 'read', lambda v: ('/api/executions', None, None), variants=('-',)),
    P('execution_get', 'GET', 'read', lambda v: ('/api/executions/%s' % ident('exec-x', v), None, None)),
    P('execution_logs_get', 'GET', 'read', lambda v: ('/api/executions/%s/logs' % ident('exec-x', v), None, None)),
    P('execution_cancel_post', 'POST', 'run', lambda v: ('/api/executions/%s/cancel' % ident('exec-x', v), None, None)),

    P('audit_get', 'GET', 'read', lambda v: ('/api/audit', None, None), variants=('-',)),
    P('settings_get', 'GET', 'read', lambda v: ('/api/settings', None, None), variants=('-',)),
    P('settings_put', 'PUT', 'write', lambda v: ('/api/settings', {'heartbeat_interval_sec': '45'}, None), variants=('-',)),

    P('users_get', 'GET', 'admin', lambda v: ('/api/users', None, None), variants=('-',)),
    P('users_post', 'POST', 'admin', lambda v: ('/api/users', {'username': 'qa-authz-created-%s' % uuid.uuid4().hex[:8], 'password': 'QaAuthz!created', 'role': 'viewer'}, None), variants=('-',)),
    P('user_put', 'PUT', 'admin', lambda v: ('/api/users/%s' % ident('qa-authz-user-x', v), {'role': 'viewer'}, None)),
    P('user_delete_probe', 'DELETE', 'admin', lambda v: ('/api/users/%s' % ident('qa-authz-user-x', v), None, None)),
]


def authorized(perm, role):
    if perm in ('public', 'probe'):
        return True
    if role == 'anon':
        return False
    if perm == 'read':
        return True
    if perm == 'agent':
        return True
    if perm == 'write' or perm == 'admin':
        return role == ROLE_ADMIN
    if perm == 'run':
        return role in (ROLE_ADMIN, ROLE_OP)
    return False


def do_request(client, method, path, body, headers):
    if isinstance(body, dict) and body.get('__multipart__'):
        raw, ct = multipart({'name': 'qa-authz-art-new-%s' % uuid.uuid4().hex[:8],
                             'version': '1.0.0', 'architecture': 'amd64'},
                            'qa-authz-new.bin', b'qa-authz-new-artifact')
        return client.request(method, path, raw_body=raw, content_type=ct, headers=headers)
    return client.request(method, path, body, headers=headers)


for spec in SPECS:
    for role in (ROLE_ADMIN, ROLE_OP, ROLE_VIEWER, 'anon'):
        client = CLIENTS[role]
        for variant in spec['variants']:
            path, body, headers = spec['build'](variant)
            if spec['name'] == 'logout_post' and role != 'anon':
                fresh = Client(creds['host'], creds['port'])
                uname = state['roles'][role]['user']
                pw = creds['admin_password'] if role == ROLE_ADMIN else 'QaAuthz!' + uname[-4:]
                fresh.login(uname, pw)
                client = fresh
            r = do_request(client, spec['method'], path, body, headers)
            state['rows'].append({
                'spec': spec['name'], 'perm': spec['perm'], 'method': spec['method'],
                'role': role, 'variant': variant, 'path': path,
                'status': r.status, 'allow': r.allow, 'sql': r.sql_hit,
                'error': (jb(r).get('error') if r.status >= 400 else None) or
                         (r.snippet(120) if r.status >= 400 else None),
            })
        save()
    print('[matrix] %s done (%d rows)' % (spec['name'], len(state['rows'])), flush=True)

# ---------- method probes (unsupported method -> 405 + Allow) ----------
PROBES = [
    ('probe_patch_tasks', 'PATCH', '/api/tasks', ROLE_ADMIN),
    ('probe_patch_task_id', 'PATCH', '/api/tasks/%s' % fx['task-x'], ROLE_ADMIN),
    ('probe_get_task_run', 'GET', '/api/tasks/%s/run' % fx['task-host'], ROLE_ADMIN),
    ('probe_patch_users', 'PATCH', '/api/users', ROLE_ADMIN),
    ('probe_patch_user_id', 'PATCH', '/api/users/%s' % fx['qa-authz-user-x'], ROLE_ADMIN),
    ('probe_delete_user_id', 'DELETE', '/api/users/%s' % fx['qa-authz-user-x'], ROLE_ADMIN),
    ('probe_patch_settings', 'PATCH', '/api/settings', ROLE_ADMIN),
    ('probe_patch_audit', 'PATCH', '/api/audit', ROLE_ADMIN),
    ('probe_patch_group_id', 'PATCH', '/api/groups/%s' % fx['group-x'], ROLE_ADMIN),
    ('probe_patch_script_id', 'PATCH', '/api/scripts/%s' % fx['script-x'], ROLE_ADMIN),
    ('probe_patch_schedule_id', 'PATCH', '/api/schedules/%s' % fx['schedule-x'], ROLE_ADMIN),
    ('probe_patch_transfer_id', 'PATCH', '/api/transfers/%s' % fx['xfer-r'], ROLE_ADMIN),
    ('probe_patch_artifact_id', 'PATCH', '/api/artifacts/%s' % fx['artifact-x'], ROLE_ADMIN),
    ('probe_patch_execution_id', 'PATCH', '/api/executions/%s' % fx['exec-x'], ROLE_ADMIN),
    ('probe_delete_execution_id', 'DELETE', '/api/executions/%s' % fx['exec-x'], ROLE_ADMIN),
    ('probe_patch_nodes', 'PATCH', '/api/nodes', ROLE_ADMIN),
    ('probe_patch_node_id', 'PATCH', '/api/nodes/%s' % fx['qa-authz-node-x'], ROLE_ADMIN),
    ('probe_patch_nodes_upgrade', 'PATCH', '/api/nodes/upgrade', ROLE_ADMIN),
    ('probe_patch_transfers', 'PATCH', '/api/transfers', ROLE_ADMIN),
    ('probe_get_login', 'GET', '/api/login', ROLE_ADMIN),
    ('probe_patch_login', 'PATCH', '/api/login', ROLE_ADMIN),
    ('probe_delete_app_revisions', 'DELETE', '/api/applications/%s/revisions' % fx['app-x'], ROLE_ADMIN),
    ('probe_delete_app_nodes', 'DELETE', '/api/applications/%s/nodes' % fx['app-x'], ROLE_ADMIN),
    ('probe_patch_app_state', 'PATCH', '/api/applications/%s/state' % fx['app-x'], ROLE_ADMIN),
    ('probe_get_revoke', 'GET', '/api/nodes/%s/revoke-credential' % fx['qa-authz-node-r'], ROLE_ADMIN),
    ('probe_get_transfer_cancel', 'GET', '/api/transfers/%s/cancel' % fx['xfer-c'], ROLE_ADMIN),
    ('probe_get_execution_cancel', 'GET', '/api/executions/%s/cancel' % fx['exec-x'], ROLE_ADMIN),
    ('probe_get_app_assign', 'GET', '/api/applications/%s/assign' % fx['app-x'], ROLE_ADMIN),
    ('probe_get_app_deploy', 'GET', '/api/applications/%s/deploy' % fx['app-x'], ROLE_ADMIN),
    ('probe_anon_patch_tasks', 'PATCH', '/api/tasks', 'anon'),
    ('probe_viewer_patch_tasks', 'PATCH', '/api/tasks', ROLE_VIEWER),
    ('probe_patch_oidc_state', 'PATCH', '/api/oidc/state', ROLE_ADMIN),
]

for name, method, path, role in PROBES:
    r = CLIENTS[role].request(method, path)
    state['method_probes'].append({
        'name': name, 'method': method, 'path': path, 'role': role,
        'status': r.status, 'allow': r.allow, 'sql': r.sql_hit,
        'error': err_of(r),
    })
    print('[probe] %-32s %s -> %s allow=%s' % (name, method, r.status, r.allow), flush=True)
save()

# ---------- verdicts ----------
findings = state['findings']
five = [row for row in state['rows'] if row['status'] >= 500]
for row in five:
    findings.append({'id': 'F-5XX-%s-%s-%s' % (row['spec'], row['role'], row['variant']),
                     'severity': 'P1', 'kind': '5xx', 'spec': row['spec'], 'role': row['role'],
                     'variant': row['variant'], 'status': row['status'], 'error': row['error']})
sql_rows = [row for row in state['rows'] if row['sql']]
for row in sql_rows:
    findings.append({'id': 'F-SQLLEAK-%s-%s-%s' % (row['spec'], row['role'], row['variant']),
                     'severity': 'P2', 'kind': 'sql_text_leak', 'spec': row['spec'], 'role': row['role'],
                     'variant': row['variant'], 'status': row['status'], 'error': row['error'],
                     'sql_marker': row['sql']})
probe_sql = [p for p in state['method_probes'] if p['sql']]
for row in probe_sql:
    findings.append({'id': 'F-SQLLEAK-%s' % row['name'], 'severity': 'P2', 'kind': 'sql_text_leak',
                     'spec': row['name'], 'role': row['role'], 'status': row['status'],
                     'error': row['error'], 'sql_marker': row['sql']})

# privilege escalation / oracle checks
by_spec = {}
for row in state['rows']:
    by_spec.setdefault(row['spec'], []).append(row)

oracle_mismatches = []
for spec in SPECS:
    name = spec['name']
    rows = by_spec.get(name, [])
    for role in (ROLE_ADMIN, ROLE_OP, ROLE_VIEWER, 'anon'):
        rrows = [r for r in rows if r['role'] == role]
        for r in rrows:
            if r['status'] == 200 and not authorized(spec['perm'], role):
                findings.append({'id': 'F-ESC-%s-%s' % (name, role), 'severity': 'P0',
                                 'kind': 'privilege_escalation', 'spec': name, 'role': role,
                                 'variant': r['variant'], 'status': r['status'], 'error': r['error']})
        ex = [r for r in rrows if r['variant'] == 'existing']
        nx = [r for r in rrows if r['variant'] == 'nonexistent']
        if ex and nx and not authorized(spec['perm'], role):
            if ex[0]['status'] != nx[0]['status']:
                oracle_mismatches.append({'spec': name, 'role': role,
                                          'existing_status': ex[0]['status'],
                                          'nonexistent_status': nx[0]['status'],
                                          'perm': spec['perm'],
                                          'existing_error': ex[0]['error'],
                                          'nonexistent_error': nx[0]['error']})
        if role == 'anon' and spec['perm'] not in ('public',) and rrows:
            bad = [r for r in rrows if r['status'] != 401]
            for r in bad:
                findings.append({'id': 'F-ANON-%s-%s' % (name, r['variant']), 'severity': 'P1',
                                 'kind': 'anon_not_401', 'spec': name, 'variant': r['variant'],
                                 'status': r['status'], 'error': r['error']})
for om in oracle_mismatches:
    findings.append({'id': 'F-ORACLE-%s-%s' % (om['spec'], om['role']), 'severity': 'P2',
                     'kind': 'existence_oracle', **om})
for spec in SPECS:
    if spec['perm'] not in ('write', 'admin'):
        continue
    if 'nonexistent' not in spec['variants']:
        continue
    for r in by_spec.get(spec['name'], []):
        if r['variant'] == 'nonexistent' and authorized(spec['perm'], r['role']) and r['status'] in (200, 201, 204):
            findings.append({'id': 'F-SILENT-%s-%s' % (spec['name'], r['role']), 'severity': 'P3',
                             'kind': 'silent_success_on_missing_id', 'spec': spec['name'],
                             'role': r['role'], 'status': r['status'], 'path': r['path'],
                             'error': r['error']})
state['oracle_mismatches'] = oracle_mismatches

# method probe verdicts
probe_findings = []
for p in state['method_probes']:
    if p['status'] != 405:
        probe_findings.append({'id': 'F-METHOD-%s' % p['name'], 'severity': 'P3',
                               'kind': 'method_not_405', 'probe': p['name'], 'method': p['method'],
                               'path': p['path'], 'status': p['status'], 'error': p['error']})
    elif not p['allow']:
        probe_findings.append({'id': 'F-ALLOW-%s' % p['name'], 'severity': 'P3',
                               'kind': '405_missing_allow', 'probe': p['name'], 'method': p['method'],
                               'path': p['path'], 'status': p['status'], 'error': p['error']})
findings.extend(probe_findings)
state['probe_findings'] = probe_findings

state['summary'] = {
    'rows': len(state['rows']),
    'five_xx': len(five),
    'sql_leaks': len(sql_rows) + len(probe_sql),
    'oracle_mismatches': len(oracle_mismatches),
    'escalations': len([f for f in findings if f['kind'] == 'privilege_escalation']),
    'anon_issues': len([f for f in findings if f['kind'] == 'anon_not_401']),
    'method_probe_deviations': len(probe_findings),
    'findings_total': len(findings),
}
save()
print(json.dumps(state['summary'], indent=1))
print('DONE authz')
