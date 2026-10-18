#!/usr/bin/env python3
"""qa-resume-20261017-fixall-verify: final oracle rewrite after residue fix-ups."""
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
import fixalllib as L  # noqa: E402
import regression_api as A  # noqa: E402

os.umask(0o077)
hub = hubclient.Hub()


def main():
    hub.login()
    st = A.ST
    prefix = A.P
    out = {'at_utc': A.now_utc()}
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    out['owned_nodes_left'] = [n['hostname'] for n in rows if (n.get('hostname') or '').startswith(prefix)]
    out['all_nodes'] = sorted(n['hostname'] for n in rows)
    out['repair_nodes'] = {n['hostname']: {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                           'agent_version': n.get('agent_version')}
                           for n in rows if (n.get('hostname') or '').startswith('qa-repair')}
    out['residue'] = {}
    for name, path in (('applications', '/api/applications'), ('scripts', '/api/scripts'),
                       ('tasks', '/api/tasks'), ('groups', '/api/groups'),
                       ('artifacts', '/api/artifacts'), ('transfers', '/api/transfers')):
        c, b = hub.get(path)
        lst = b if isinstance(b, list) else (b.get(name) if isinstance(b, dict) else [])
        hits = []
        for x in lst or []:
            blob = json.dumps(x, ensure_ascii=False)
            if prefix in blob:
                hits.append({'id': x.get('id'), 'status': x.get('status'), 'name': x.get('name')})
        out['residue'][name] = hits
    # schedules: name field is empty by design; match via task_id/expression/known ids
    c, scheds = hub.get('/api/schedules')
    out['residue']['schedules'] = [{'id': s.get('id'), 'task_id': s.get('task_id')}
                                   for s in (scheds or [])]
    # host residue on n1/n3 (owned prefix units/dirs/procs)
    out['host_residue'] = {}
    for host_key, tag in (('n1', '-a'), ('n3', '-c')):
        ch = hostlib.OwnedChild(st['nonce'], host_key, tag)
        out['host_residue'][host_key] = ch.remote_py(
            "import json,os,subprocess\n"
            "prefix=%(p)r\n"
            "out={'etc_units':[n for n in sorted(os.listdir('/etc/systemd/system')) if prefix in n],"
            "'wants':[n for n in sorted(os.listdir('/etc/systemd/system/multi-user.target.wants')) if prefix in n] if os.path.isdir('/etc/systemd/system/multi-user.target.wants') else [],"
            "'var_lib':[n for n in sorted(os.listdir('/var/lib')) if prefix in n],"
            "'opt':[n for n in sorted(os.listdir('/opt')) if prefix in n],"
            "'procs':[l for l in subprocess.run(['ps','-eo','pid,args','--no-headers'],capture_output=True,text=True).stdout.splitlines() if prefix in l],"
            "'etc_nodesteer':[n for n in sorted(os.listdir('/etc')) if 'qa-fixall' in n]}\n"
            "print(json.dumps(out))\n" % {'p': prefix})
    # protected state: primary hub/agents from the deploy pre-state evidence
    ev = json.loads((HERE / 'deploy-evidence.json').read_text())
    pre = ev['pre_deploy']
    out['primary_hub_now'] = L.unit_state(L.HUB_HOST, L.PRIMARY_UNIT)
    out['primary_hub_pid_before'] = pre['primary_hub_untouched']['state'].get('MainPID')
    out['primary_agent_units_now'] = {n: L.unit_state(L.NODES[n], L.PRIMARY_AGENT_UNIT) for n in L.NODES}
    out['primary_agent_units_before'] = pre['primary_agent_unit_snapshot']['states']
    out['repair_hub_now'] = {'state': L.unit_state(L.HUB_HOST, L.HUB_UNIT),
                             'disk_sha256': L.sha256(L.HUB_HOST, L.HUB_BIN),
                             'healthz': L.curl_code(L.HUB_HOST, '/healthz'),
                             'readyz': L.curl_code(L.HUB_HOST, '/readyz')}
    out['repair_agents_now'] = {n: {'disk_sha256': L.sha256(L.NODES[n], L.AGENT_BIN),
                                    'state': L.unit_state(L.NODES[n], L.AGENT_UNIT_FMT % n)}
                                for n in L.NODES}
    verdict = {
        'no_owned_nodes_left': not out['owned_nodes_left'],
        'no_owned_list_fixtures_left': all(not v for k, v in out['residue'].items() if k != 'transfers'),
        'transfers_are_terminal_history_only': all(t.get('status') in ('SUCCESS', 'FAILED', 'CANCELED')
                                                   for t in out['residue']['transfers']),
        'no_owned_host_units_or_dirs_or_procs': all(
            not v.get('etc_units') and not v.get('var_lib') and not v.get('opt') and not v.get('procs')
            and not v.get('etc_nodesteer') for v in out['host_residue'].values()),
        'repair_nodes_untouched_online': len(out['repair_nodes']) == 4 and all(
            v['status'] == 'online' and v['sync_status'] == 'synced' for v in out['repair_nodes'].values()),
        'primary_hub_pid_unchanged': out['primary_hub_now'].get('MainPID') == out['primary_hub_pid_before'],
        'primary_agent_units_unchanged': all(
            out['primary_agent_units_now'][n].get('MainPID') == out['primary_agent_units_before'][n].get('MainPID')
            for n in out['primary_agent_units_now']),
        'repair_hub_on_fixall_build': out['repair_hub_now']['disk_sha256'] == A.HUB_SHA
                                       and out['repair_hub_now']['healthz'] == '200'
                                       and out['repair_hub_now']['readyz'] == '200',
        'repair_agents_on_fixall_build': all(v['disk_sha256'] == A.AGENT_SHA for v in out['repair_agents_now'].values()),
    }
    out['verdict'] = verdict
    A.ev_write('final-cleanup-oracle.json', out)
    print(json.dumps(verdict, indent=1))


if __name__ == '__main__':
    main()
