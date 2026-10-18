#!/usr/bin/env python3
"""qa-resume-20261017-fixall-verify: final cleanup + oracles.

Stops the owned child agents, removes owned host dirs, deletes every remaining
owned fixture through the product path, verifies GET 404 per fixture (or records
the honest reason it cannot be deleted), and re-checks that nothing protected
was touched.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402
import hostlib  # noqa: E402
import regression_api as A  # noqa: E402

EV = HERE / 'evidence'
os.umask(0o077)
hub = hubclient.Hub()


def main():
    hub.login()
    st = A.ST
    prefix = A.P
    out = {'at_utc': A.now_utc(), 'steps': {}}
    # ---------------- 1. stop owned child agents
    out['steps']['stop_children'] = {}
    for key in list(st['children'].keys()):
        ch = A.child_of(key)
        try:
            out['steps']['stop_children'][key] = ch.stop()
        except Exception as exc:  # noqa
            out['steps']['stop_children'][key] = {'error': str(exc)}
    time.sleep(3)
    # ---------------- 2. delete every node record whose hostname is owned
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    owned_nodes = [n for n in rows if (n.get('hostname') or '').startswith(prefix)]
    out['steps']['owned_nodes_found'] = [{'id': n['id'], 'hostname': n['hostname'],
                                          'status': n.get('status')} for n in owned_nodes]
    out['steps']['node_delete'] = {}
    for n in owned_nodes:
        c, b = hub.delete('/api/nodes/' + n['id'])
        out['steps']['node_delete'][n['hostname']] = {'http': c, 'body': b}
        time.sleep(0.5)
    time.sleep(2)
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    leftovers = [n['hostname'] for n in rows if (n.get('hostname') or '').startswith(prefix)]
    out['steps']['owned_nodes_left'] = leftovers
    # ---------------- 3. remaining list fixtures via product path
    out['steps']['fixture_delete'] = {}
    ids = st['ids']
    for kind, path_tpl in (('task', '/api/tasks/%s'), ('schedule', '/api/schedules/%s'),
                           ('artifact_A1', '/api/artifacts/%s')):
        fid = ids.get(kind)
        if not fid:
            continue
        c, b = hub.delete(path_tpl % fid)
        out['steps']['fixture_delete'][kind] = {'id': fid, 'http': c, 'body': b}
    # ---------------- 4. remove owned host dirs (processes already stopped)
    out['steps']['host_cleanup'] = {}
    for key in ('a', 'b', 'd'):
        ch = A.child_of(key)
        try:
            out['steps']['host_cleanup'][key] = ch.remove()
        except Exception as exc:  # noqa
            out['steps']['host_cleanup'][key] = {'error': str(exc)}
    ch_c = A.child_of('c')
    try:
        out['steps']['host_cleanup']['c'] = ch_c.remove()
    except Exception as exc:  # noqa
        out['steps']['host_cleanup']['c'] = {'error': str(exc)}
    # owned unit / opt dir residue on n1 and n3
    out['steps']['host_residue'] = {}
    for key, host_key in (('n1', 'n1'), ('n3', 'n3')):
        ch = hostlib.OwnedChild(st['nonce'], host_key, '-a' if host_key == 'n1' else '-c')
        r = ch.remote_py(
            "import json,os,subprocess\n"
            "prefix=%(p)r\n"
            "out={'etc_units':[n for n in sorted(os.listdir('/etc/systemd/system')) if prefix in n],"
            "'wants':[n for n in sorted(os.listdir('/etc/systemd/system/multi-user.target.wants')) if prefix in n] if os.path.isdir('/etc/systemd/system/multi-user.target.wants') else [],"
            "'var_lib':[n for n in sorted(os.listdir('/var/lib')) if prefix in n],"
            "'opt':[n for n in sorted(os.listdir('/opt')) if prefix in n],"
            "'procs':[l for l in subprocess.run(['ps','-eo','pid,args','--no-headers'],capture_output=True,text=True).stdout.splitlines() if prefix in l]}\n"
            "print(json.dumps(out))\n" % {'p': prefix})
        out['steps']['host_residue'][key] = r
    # ---------------- 5. protected-state re-check
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
    A.ev_write('final-cleanup-raw.json', out)
    # ---------------- 6. oracles + ledger
    ledger = {'batch': 'qa-resume-20261017-fixall-verify', 'nonce': st['nonce'],
              'at_utc': A.now_utc(), 'fixtures': []}
    for kind, fid, name in st['fixtures']:
        entry = {'kind': kind, 'id': fid, 'name': name, 'cleanup': None}
        if kind == 'node':
            c, _ = hub.get('/api/nodes/' + fid)
            entry['cleanup'] = {'via': 'UI 删除节点 / API DELETE', 'after_get_http': c,
                                'get404': c == 404}
        elif kind == 'application':
            c, _ = hub.get('/api/applications/' + fid)
            entry['cleanup'] = {'via': 'UI 删除应用', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'script':
            c, _ = hub.get('/api/scripts/' + fid)
            entry['cleanup'] = {'via': 'UI 删除脚本', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'group':
            c, _ = hub.get('/api/groups/' + fid)
            entry['cleanup'] = {'via': 'UI 删除分组', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'artifact':
            c, _ = hub.get('/api/artifacts/' + fid)
            entry['cleanup'] = {'via': 'UI/API 删除发布包', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'task':
            c, _ = hub.get('/api/tasks/' + fid)
            entry['cleanup'] = {'via': 'API DELETE', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'schedule':
            c, _ = hub.get('/api/schedules/' + fid)
            entry['cleanup'] = {'via': 'API DELETE', 'after_get_http': c, 'get404': c == 404}
        elif kind == 'transfer':
            c, b = hub.get('/api/transfers/' + fid)
            status = (b or {}).get('status') if c == 200 else None
            entry['cleanup'] = {'via': '产品路径无删除接口（终态记录保留为历史）',
                                'after_get_http': c, 'status': status,
                                'terminal': status in ('SUCCESS', 'FAILED', 'CANCELED')}
        ledger['fixtures'].append(entry)
    ledger['re_enrolled_nodes_note'] = (
        '删除节点记录后，仍在运行的 owned child Agent 会按产品既有能力自动重新纳管（生成新 node id），'
        '清理阶段先停止子进程再删除全部 %s* 节点记录。' % prefix)
    A.ev_write('fixture-ledger.json', ledger)
    oracle = {'batch': 'qa-resume-20261017-fixall-verify', 'at_utc': A.now_utc(),
              'hub_prefix_nodes_left': leftovers,
              'residue': {k: out['steps'][k] for k in out['steps'] if k.startswith('residue_')},
              'host_residue': out['steps']['host_residue'],
              'protected_nodes': repair,
              'verdict': {}}
    oracle['verdict'] = {
        'no_owned_nodes_left': not leftovers,
        'no_owned_list_fixtures_left': all(not v for k, v in oracle['residue'].items()),
        'no_owned_host_units_or_dirs': all(
            not v.get('etc_units') and not v.get('var_lib') and not v.get('opt') and not v.get('procs')
            for v in out['steps']['host_residue'].values()),
        'repair_nodes_untouched_online': len(repair) == 4 and all(
            v['status'] == 'online' and v['sync_status'] == 'synced' for v in repair.values()),
    }
    A.ev_write('final-cleanup-oracle.json', oracle)
    print(json.dumps(oracle['verdict'], indent=1))


if __name__ == '__main__':
    main()
