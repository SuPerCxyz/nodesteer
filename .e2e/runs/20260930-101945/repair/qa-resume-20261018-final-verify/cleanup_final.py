#!/usr/bin/env python3
"""qa-resume-20261018-final-verify: final cleanup + oracles.

Stops the owned child agents, removes owned host dirs, deletes every remaining
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
import regression_go as A  # noqa: E402

EV = HERE / 'evidence'
os.umask(0o077)
hub = hubclient.Hub()


def main():
    hub.login()
    st = A.ST
    prefix = A.P
    out = {'at_utc': A.now_utc(), 'steps': {}}
    # ---------------- 1. stop owned child agents + mock WS leftovers
    out['steps']['stop_children'] = {}
    for key in list(st['children'].keys()):
        ch = A.child_of(key)
        try:
            out['steps']['stop_children'][key] = ch.stop()
        except Exception as exc:  # noqa
            out['steps']['stop_children'][key] = {'error': str(exc)}
    time.sleep(2)
    # kill any leftover mock ws process in the owned g root
    if 'g' in st['children']:
        ch_g = A.child_of('g')
        try:
            out['steps']['mock_kill'] = ch_g.remote_py(
                "import json,os,signal,subprocess,time\n"
                "p=subprocess.run(['pgrep','-af','mock_ws.py'],capture_output=True,text=True)\n"
                "lines=[l for l in p.stdout.splitlines() if %(root)r in l]\n"
                "pids=[int(l.split()[0]) for l in lines]\n"
                "for pid in pids:\n"
                "    try: os.kill(pid, signal.SIGKILL)\n"
                "    except Exception: pass\n"
                "print(json.dumps({'killed':pids}))\n" % {'root': ch_g.root})
        except Exception as exc:  # noqa
            out['steps']['mock_kill'] = {'error': str(exc)}
    # ---------------- 2. delete schedules -> tasks -> scripts -> app -> artifacts (reference order)
    ids = st['ids']
    out['steps']['fixture_delete'] = {}

    def delete(kind, path_tpl, fid):
        if not fid:
            return
        c, b = hub.delete(path_tpl % fid)
        out['steps']['fixture_delete'][kind + ':' + fid[:8]] = {'id': fid, 'http': c,
                                                               'body': (b if isinstance(b, str) else json.dumps(b, ensure_ascii=False))[:160]}
        time.sleep(0.4)

    for kind in ('schedule', 'schedule_interval'):
        delete(kind, '/api/schedules/%s', ids.get(kind))
    for kind in ('task', 'task_disabled', 'task_x16', 'task_and'):
        delete(kind, '/api/tasks/%s', ids.get(kind))
    delete('script_x16', '/api/scripts/%s', ids.get('script_x16'))
    delete('app', '/api/applications/%s', ids.get('app'))
    for kind in ('artifact_A1', 'artifact_A2'):
        delete(kind, '/api/artifacts/%s', ids.get(kind))
    # ---------------- 3. delete owned nodes (children + enrollment fixtures)
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    owned_nodes = [n for n in rows if (n.get('hostname') or '').startswith(prefix)]
    out['steps']['owned_nodes_found'] = [{'id': n['id'], 'hostname': n['hostname'],
                                          'status': n.get('status')} for n in owned_nodes]
    out['steps']['node_delete'] = {}
    for n in owned_nodes:
        c, b = hub.delete('/api/nodes/' + n['id'])
        out['steps']['node_delete'][n['hostname']] = {'http': c, 'body': b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)}
        time.sleep(0.5)
    time.sleep(2)
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    leftovers = [n['hostname'] for n in rows if (n.get('hostname') or '').startswith(prefix)]
    out['steps']['owned_nodes_left'] = leftovers
    # ---------------- 4. users: product path has no DELETE (405 by design); rotate passwords
    out['steps']['user_cleanup'] = {}
    for key, uname in (('user_operator', prefix + 'role-operator'),
                       ('user_viewer', prefix + 'role-viewer'),
                       ('user_dup', prefix + 'dup')):
        fid = ids.get(key)
        if not fid:
            continue
        c405, h405, b405 = hub.raw_text('DELETE', '/api/users/%s' % fid)
        new_pw = secrets.token_urlsafe(18)
        c, b = hub.put('/api/users/%s' % fid, {'password': new_pw})
        out['steps']['user_cleanup'][key] = {
            'id': fid, 'name': uname, 'delete_http': c405, 'delete_body': b405[:120],
            'password_rotated_http': c,
            'note': '产品路径无用户删除接口（DELETE 405，仅 PUT 改角色/密码）；密码已轮换为随机值'}
    # ---------------- 5. remove owned host dirs
    out['steps']['host_cleanup'] = {}
    for key in list(st['children'].keys()):
        ch = A.child_of(key)
        try:
            out['steps']['host_cleanup'][key] = ch.remove()
        except Exception as exc:  # noqa
            out['steps']['host_cleanup'][key] = {'error': str(exc)}
    # ---------------- 6. host residue scan (owned prefixes on n1/n2/n3)
    out['steps']['host_residue'] = {}
    for host_key in ('n1', 'n2', 'n3'):
        ch = hostlib.OwnedChild(st['nonce'], host_key, '-scan')
        try:
            r = ch.remote_py(
                "import json,os,subprocess\n"
                "prefix='qa-final'\n"
                "out={'etc_units':[n for n in sorted(os.listdir('/etc/systemd/system')) if prefix in n],"
                "'wants':[n for n in sorted(os.listdir('/etc/systemd/system/multi-user.target.wants')) if prefix in n] if os.path.isdir('/etc/systemd/system/multi-user.target.wants') else [],"
                "'var_lib':[n for n in sorted(os.listdir('/var/lib')) if prefix in n],"
                "'opt':[n for n in sorted(os.listdir('/opt')) if prefix in n],"
                "'procs':[l for l in subprocess.run(['ps','-eo','pid,args','--no-headers'],capture_output=True,text=True).stdout.splitlines() if prefix in l and 'scan' not in l]}\n"
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
        out['steps']['residue_' + name] = [n for n in names if (n or '').startswith(prefix)]
    # transfers: no delete API -> record terminal history honestly
    c, transfers = hub.get('/api/transfers')
    owned_tr = [t for t in (transfers or []) if t.get('id') in
                {ids.get('transfer_happy'), ids.get('transfer_big')} or
                any((t.get('source_path') or '').startswith('/var/lib/nodesteer-' + prefix) for t in [t])]
    out['steps']['owned_transfers'] = [
        {'id': t.get('id'), 'status': t.get('status'), 'source_path': t.get('source_path'),
         'targets': [{'node_id': x.get('node_id'), 'status': x.get('status')} for x in (t.get('targets') or [])]}
        for t in owned_tr]
    A.ev_write('final-cleanup-raw.json', out)
    # ---------------- 8. oracles + ledger
    ledger = {'batch': 'qa-resume-20261018-final-verify', 'nonce': st['nonce'],
              'at_utc': A.now_utc(), 'build_label': A.LABEL, 'authority': 'http://192.168.100.209:18080',
              'fixtures': []}
    for kind, fid, name in st['fixtures']:
        entry = {'kind': kind, 'id': fid, 'name': name, 'cleanup': None}
        if kind == 'node':
            c, _ = hub.get('/api/nodes/' + fid)
            entry['cleanup'] = {'via': 'API DELETE /api/nodes/{id}', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'application':
            c, _ = hub.get('/api/applications/' + fid)
            entry['cleanup'] = {'via': 'API DELETE', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'script':
            c, _ = hub.get('/api/scripts/' + fid)
            entry['cleanup'] = {'via': 'API DELETE', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'artifact':
            c, _ = hub.get('/api/artifacts/' + fid)
            entry['cleanup'] = {'via': 'API DELETE', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'task':
            c, _ = hub.get('/api/tasks/' + fid)
            entry['cleanup'] = {'via': 'API DELETE', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'schedule':
            c, _ = hub.get('/api/schedules/' + fid)
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
    ]
    A.ev_write('fixture-ledger.json', ledger)
    oracle = {'batch': 'qa-resume-20261018-final-verify', 'at_utc': A.now_utc(),
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
    oracle['verdict'] = {
        'no_owned_nodes_left': not leftovers,
        'no_owned_list_fixtures_left': all(not v for k, v in oracle['residue'].items()),
        'owned_transfers_terminal_history_only': all(t.get('status') in ('SUCCESS', 'FAILED', 'CANCELED')
                                                     for t in out['steps']['owned_transfers']),
        'no_owned_host_units_or_dirs_or_procs': all(
            not v.get('etc_units') and not v.get('var_lib') and not v.get('opt') and not v.get('procs')
            for v in out['steps']['host_residue'].values()),
        'repair_nodes_untouched_online': len(repair) == 4 and all(
            v['status'] == 'online' and v['sync_status'] == 'synced' for v in repair.values()),
        'repair_nodes_on_final_build': all(v['agent_version'] == A.LABEL for v in repair.values()),
        'primary_hub_pid_unchanged': primary_now.get('MainPID') == pre['primary_hub_untouched']['state'].get('MainPID'),
        'primary_agent_units_unchanged': all(
            agents_now[n].get('MainPID') == pre['primary_agent_unit_snapshot']['states'][n].get('MainPID')
            for n in agents_now),
        'repair_hub_on_final_build': oracle['repair_hub_now']['disk_sha256'] == A.HUB_SHA
                                     and oracle['repair_hub_now']['healthz'] == '200'
                                     and oracle['repair_hub_now']['readyz'] == '200',
        'repair_agents_on_final_build': all(v['disk_sha256'] == A.AGENT_SHA for v in oracle['repair_agents_now'].values()),
    }
    A.ev_write('final-cleanup-oracle.json', oracle)
    print(json.dumps(oracle['verdict'], indent=1))


if __name__ == '__main__':
    main()
