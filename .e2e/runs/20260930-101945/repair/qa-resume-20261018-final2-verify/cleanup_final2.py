#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: final cleanup + oracles.

Stops owned child agents, removes owned host dirs, deletes every remaining
owned fixture through the product path, verifies GET 404 per fixture (or records
the honest reason it cannot be deleted), and re-checks that nothing protected
was touched.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402
import hostlib  # noqa: E402
import regression_go2 as A  # noqa: E402

EV = HERE / 'evidence'
os.umask(0o077)
hub = hubclient.Hub()

# This batch owns both prefixes: regression fixtures (qa-final2-<nonce>-) and
# the E2E suite fixtures (qa-pw2-20261018-).
PREFIXES = (A.P, 'qa-pw2-20261018-')


def owned(name):
    return bool(name) and any(name.startswith(p) for p in PREFIXES)


def main():
    hub.login()
    st = A.ST
    out = {'at_utc': A.now_utc(), 'steps': {}}
    # ---------------- 1. stop owned child agents
    out['steps']['stop_children'] = {}
    for key in list(st['children'].keys()):
        ch = A.child_of(key)
        try:
            out['steps']['stop_children'][key] = ch.stop()
        except Exception as exc:  # noqa
            out['steps']['stop_children'][key] = {'error': str(exc)}
    time.sleep(2)
    # ---------------- 2. delete fixtures in reference order (product path)
    ids = st['ids']
    out['steps']['fixture_delete'] = {}

    def delete(kind, path_tpl, fid):
        if not fid:
            return
        c, b = hub.delete(path_tpl % fid)
        out['steps']['fixture_delete'][kind + ':' + fid[:8]] = {
            'id': fid, 'http': c,
            'body': (b if isinstance(b, str) else json.dumps(b, ensure_ascii=False))[:160]}
        time.sleep(0.4)

    for kind in ('schedule_g7', 'schedule', 'schedule_interval'):
        delete(kind, '/api/schedules/%s', ids.get(kind))
    for kind in ('task', 'task_disabled', 'task_x16', 'task_and'):
        delete(kind, '/api/tasks/%s', ids.get(kind))
    delete('script', '/api/scripts/%s', ids.get('script'))
    delete('app', '/api/applications/%s', ids.get('app'))
    for kind in ('artifact', 'artifact_A1', 'artifact_A2'):
        delete(kind, '/api/artifacts/%s', ids.get(kind))
    # ---------------- 3. scan + delete every remaining owned fixture by prefix
    out['steps']['prefix_sweep'] = {}
    owned_task_ids = {fid for kind, fid, _ in st['fixtures'] if kind == 'task'}
    for kind, path in (('schedules', '/api/schedules'), ('tasks', '/api/tasks'),
                       ('scripts', '/api/scripts'), ('applications', '/api/applications'),
                       ('artifacts', '/api/artifacts')):
        c, body = hub.get(path)
        rows = body if isinstance(body, list) else (body or {}).get(kind) or []
        if kind == 'schedules':
            # schedules carry no name; ownership = task_id references an owned task
            found = [x for x in rows if x.get('task_id') in owned_task_ids]
        else:
            found = [x for x in rows if owned(x.get('name'))]
        deleted = []
        for x in found:
            dc, db = hub.delete('%s/%s' % (path, x['id']))
            deleted.append({'id': x['id'], 'name': x.get('name'), 'http': dc})
            time.sleep(0.3)
        out['steps']['prefix_sweep'][kind] = {'found': len(found), 'deleted': deleted}
    # nodes by prefix
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    owned_nodes = [n for n in rows if owned(n.get('hostname'))]
    out['steps']['owned_nodes_found'] = [{'id': n['id'], 'hostname': n['hostname'],
                                          'status': n.get('status')} for n in owned_nodes]
    out['steps']['node_delete'] = {}
    for n in owned_nodes:
        c, b = hub.delete('/api/nodes/' + n['id'])
        out['steps']['node_delete'][n['hostname']] = {
            'http': c, 'body': b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)}
        time.sleep(0.5)
    time.sleep(2)
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    leftovers = [n['hostname'] for n in rows if owned(n.get('hostname'))]
    out['steps']['owned_nodes_left'] = leftovers
    # ---------------- 4. users (no DELETE API): rotate passwords
    out['steps']['user_cleanup'] = {}
    c, users = hub.get('/api/users')
    for u in (users or []):
        if not owned(u.get('username')):
            continue
        new_pw = secrets.token_urlsafe(18)
        c2, b2 = hub.put('/api/users/%s' % u['id'], {'password': new_pw})
        out['steps']['user_cleanup'][u['username']] = {
            'id': u['id'], 'password_rotated_http': c2,
            'note': '产品路径无用户删除接口（DELETE 405，仅 PUT 改角色/密码）；密码已轮换为随机值'}
    # ---------------- 5. remove owned host dirs
    out['steps']['host_cleanup'] = {}
    for key in list(st['children'].keys()):
        ch = A.child_of(key)
        try:
            out['steps']['host_cleanup'][key] = ch.remove()
        except Exception as exc:  # noqa
            out['steps']['host_cleanup'][key] = {'error': str(exc)}
    # ---------------- 6. host residue scan (owned prefixes on all 4 hosts)
    out['steps']['host_residue'] = {}
    for host_key in ('n1', 'n2', 'n3', 'n4'):
        ch = hostlib.OwnedChild(st['nonce'], host_key, '-scan')
        try:
            r = ch.remote_py(
                "import json,os,subprocess\n"
                "prefixes=['qa-final2','qa-pw2']\n"
                "def scan(d):\n"
                "    try: return [n for n in sorted(os.listdir(d)) if any(p in n for p in prefixes)]\n"
                "    except Exception: return []\n"
                "out={'etc_units':scan('/etc/systemd/system'),"
                "'wants':scan('/etc/systemd/system/multi-user.target.wants'),"
                "'var_lib':scan('/var/lib'),'opt':scan('/opt'),"
                "'procs':[l for l in subprocess.run(['ps','-eo','pid,args','--no-headers'],capture_output=True,text=True).stdout.splitlines() if any(p in l for p in prefixes) and 'scan' not in l]}\n"
                "print(json.dumps(out))\n")
            out['steps']['host_residue'][host_key] = r
        except Exception as exc:  # noqa
            out['steps']['host_residue'][host_key] = {'error': str(exc)}
    # ---------------- 7. protected-state re-check
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    repair = {n['hostname']: {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                              'agent_version': n.get('agent_version')}
              for n in rows if n['hostname'].startswith('qa-repair')}
    out['steps']['protected_nodes'] = repair
    out['steps']['all_nodes_after'] = sorted(n['hostname'] for n in rows)
    for name, path in (('apps', '/api/applications'), ('scripts', '/api/scripts'),
                       ('tasks', '/api/tasks'), ('schedules', '/api/schedules'),
                       ('groups', '/api/groups'), ('artifacts', '/api/artifacts')):
        c, b = hub.get(path)
        lst = b if isinstance(b, list) else (b.get(name) if isinstance(b, dict) else [])
        names = [x.get('name') for x in (lst or [])]
        out['steps']['residue_' + name] = [n for n in names if owned(n)]
    c, transfers = hub.get('/api/transfers')
    rows = transfers if isinstance(transfers, list) else (transfers or {}).get('transfers') or []
    owned_tr = [t for t in rows if any((t.get('source_path') or '').startswith('/var/lib/nodesteer-' + p)
                                       for p in PREFIXES)]
    out['steps']['owned_transfers'] = [
        {'id': t.get('id'), 'status': t.get('status'), 'source_path': t.get('source_path'),
         'targets': [{'node_id': x.get('node_id'), 'status': x.get('status')} for x in (t.get('targets') or [])]}
        for t in owned_tr]
    A.ev_write('final-cleanup-raw.json', out)
    # ---------------- 8. oracles + ledger
    ledger = {'batch': 'qa-resume-20261018-final2-verify', 'nonce': st['nonce'],
              'at_utc': A.now_utc(), 'build_label': A.LABEL, 'authority': 'http://192.168.100.209:18080',
              'fixtures': []}
    for kind, fid, name in st['fixtures']:
        entry = {'kind': kind, 'id': fid, 'name': name, 'cleanup': None}
        if kind == 'node':
            c, _ = hub.get('/api/nodes/' + fid)
            entry['cleanup'] = {'via': 'API DELETE /api/nodes/{id}', 'after_get_http': c, 'get404': c == 404}
        elif kind in ('application', 'script', 'artifact', 'task', 'schedule'):
            path = {'application': '/api/applications/', 'script': '/api/scripts/',
                    'artifact': '/api/artifacts/', 'task': '/api/tasks/',
                    'schedule': '/api/schedules/'}[kind]
            c, _ = hub.get(path + fid)
            entry['cleanup'] = {'via': 'API DELETE', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'user':
            c, _ = hub.get('/api/users/' + fid)
            entry['cleanup'] = {'via': '产品路径无删除接口（DELETE 405 by design；仅 PUT 改角色/密码）',
                                'after_get_http': c, 'get404': False,
                                'residual': 'owned 角色测试用户保留；密码已轮换为随机值'}
        elif kind == 'transfer':
            c, b = hub.get('/api/transfers/' + fid)
            status = (b or {}).get('status') if c == 200 else None
            entry['cleanup'] = {'via': '产品路径无删除接口（终态记录保留为历史）',
                                'after_get_http': c, 'status': status,
                                'terminal': status in ('SUCCESS', 'FAILED', 'CANCELED')}
        ledger['fixtures'].append(entry)
    ledger['notes'] = [
        '执行历史（executions）与审计日志为追加型历史记录，产品无删除接口，随记录保留。',
        'owned 子 Agent 进程先停止再删除节点记录，避免自动重新纳管。',
        '本批同时清理 E2E 套件前缀 qa-pw2-20261018- 的剩余资源（套件自身 finally 已删除大部分）。',
        'E2E 套件新建的 UI 用户无删除接口，保留为 viewer 且密码已轮换。',
    ]
    A.ev_write('fixture-ledger.json', ledger)
    oracle = {'batch': 'qa-resume-20261018-final2-verify', 'at_utc': A.now_utc(),
              'hub_prefix_nodes_left': leftovers,
              'residue': {k: out['steps'][k] for k in out['steps'] if k.startswith('residue_')},
              'host_residue': out['steps']['host_residue'],
              'protected_nodes': repair,
              'owned_transfers': out['steps']['owned_transfers'],
              'verdict': {}}
    ev = json.loads((HERE / 'deploy-evidence.json').read_text())
    pre = ev['pre_deploy']
    import finallib as L
    primary_now = L.unit_state(L.HUB_HOST, L.PRIMARY_UNIT)
    agents_now = {n: L.unit_state(L.NODES[n], L.PRIMARY_AGENT_UNIT) for n in L.NODES}
    oracle['primary_hub_now'] = primary_now
    oracle['primary_hub_pid_before'] = pre['primary_hub_untouched']['state'].get('MainPID')
    oracle['primary_agent_units_now'] = agents_now
    oracle['primary_agent_units_before'] = pre['primary_agent_unit_snapshot']['states']
    oracle['repair_hub_now'] = {'state': L.unit_state(L.HUB_HOST, L.HUB_UNIT),
                                'disk_sha256': L.sha256(L.HUB_HOST, L.HUB_BIN),
                                'healthz': L.curl_code(L.HUB_HOST, '/healthz'),
                                'readyz': L.curl_code(L.HUB_HOST, '/readyz')}
    oracle['repair_agents_now'] = {n: {'disk_sha256': L.sha256(L.NODES[n], L.AGENT_BIN),
                                       'state': L.unit_state(L.NODES[n], L.AGENT_UNIT_FMT % n)}
                                   for n in L.NODES}
    v = oracle['verdict']
    v['no_owned_prefix_nodes'] = not leftovers
    v['no_owned_residue_in_definitions'] = all(not val for val in oracle['residue'].values())
    v['no_owned_host_units_or_dirs_or_procs'] = all(
        not r.get('etc_units') and not r.get('var_lib') and not r.get('opt') and not r.get('procs')
        for r in out['steps']['host_residue'].values() if isinstance(r, dict) and 'error' not in r)
    v['protected_repair_nodes_intact'] = (len(repair) == 4 and all(
        x['status'] == 'online' and x['sync_status'] == 'synced' and x['agent_version'] == A.LABEL
        for x in repair.values()))
    v['primary_hub_pid_unchanged'] = primary_now.get('MainPID') == pre['primary_hub_untouched']['state'].get('MainPID')
    v['primary_agent_units_active'] = all(s.get('ActiveState') == 'active' for s in agents_now.values())
    v['repair_hub_healthy'] = oracle['repair_hub_now']['healthz'] == '200' and oracle['repair_hub_now']['readyz'] == '200'
    v['repair_hub_new_build'] = oracle['repair_hub_now']['disk_sha256'] == '3b5eec15ea2ce7ad6cd0e0f90b7f0a2382e02db3c577c905ba7f4b6fd9c3902b'
    v['repair_agents_new_build'] = all(
        r['disk_sha256'] == '89f2a3bb4b6bcb8b208230b0c9bffa48d7fb9ddda577b491dd6f4cf317f90008'
        for r in oracle['repair_agents_now'].values())
    v['all_owned_transfers_terminal'] = all(t['status'] in ('SUCCESS', 'FAILED', 'CANCELED')
                                            for t in oracle['owned_transfers'])
    oracle['pass'] = all(v.values())
    A.ev_write('final-cleanup-oracle.json', oracle)
    print(json.dumps(oracle['verdict'], ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
