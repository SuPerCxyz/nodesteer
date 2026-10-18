#!/usr/bin/env python3
"""Independent cleanup oracle for the qa-resume-20261017-g6 batch.

Separate process, fresh reads only: it never trusts run_g6.py's in-process
state.  The Hub API is queried directly, native-1 is inspected over SSH, the
workstation's listening ports are re-checked, the Hub SQLite is re-opened
read-only and the retained (undeletable) history is re-enumerated.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import hubapi
import hubdb

HERE = Path(__file__).resolve().parent
RUN = json.loads((HERE / 'run-evidence.json').read_text())
STATE = RUN['state']
PREFIX = RUN['prefix']
NODE_HOST = '192.168.100.204'
HUB_HOST = '192.168.100.209'
PROTECTED_PREFIX = 'qa-repair-20260930-101945-native-'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def main():
    hub = hubapi.Hub()
    hub.login()
    delete_results = STATE.get('delete_results', {})
    exact = {}
    for oid, meta in delete_results.items():
        kind = meta['kind']
        code, body = hub.get('/api/%ss/%s' % (kind, hub.quote(oid)))
        exact[oid] = {'kind': kind, 'get_http': code, 'expected': 404}
    scripts = hub.json('/api/scripts')
    tasks = hub.json('/api/tasks')
    schedules = hub.json('/api/schedules')
    nodes = hub.json('/api/nodes')
    groups = hub.json('/api/groups')
    apps = hub.json('/api/applications')
    leftovers = {
        'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
        'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'schedules': [s['id'] for s in schedules if (s.get('name') or '').startswith(PREFIX)],
        'groups': [g['id'] for g in groups if (g.get('name') or '').startswith(PREFIX)],
        'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
        'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
    }
    protected, ok = {}, True
    for n in nodes:
        host = n.get('hostname') or n['id']
        if host.startswith(PREFIX):
            continue
        is_protected = host.startswith(PROTECTED_PREFIX)
        protected[host] = {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                           'protected_repair_native': is_protected}
        if is_protected and (n.get('status') != 'online' or n.get('sync_status') != 'synced'):
            ok = False

    ports = []
    ledger_path = HERE / 'fixture-ledger.json'
    if ledger_path.exists():
        led = json.loads(ledger_path.read_text())
        ports = [p.get('listen_port') for p in led.get('proxies', []) if p.get('listen_port')]
    if not ports:
        ports = [STATE.get('proxy_port')] if STATE.get('proxy_port') else []
    ss = subprocess.run(['ss', '-ltn'], capture_output=True, text=True).stdout
    ports_released = all((': %d' % p) not in ss and (':%d ' % p) not in ss and (':%d\n' % p) not in ss
                         for p in ports) if ports else None

    ssh = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
           '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + NODE_HOST]
    remote = (
        "import json,os,subprocess,hashlib,glob\n"
        "out={}\n"
        "out['owned_root_glob']=sorted(glob.glob('/var/lib/nodesteer-qa-g6-*'))\n"
        "p=subprocess.run(['pgrep','-af','nodesteer-qa-g6'],capture_output=True,text=True)\n"
        "out['g6_processes']=[l for l in p.stdout.splitlines() if l.strip()]\n"
        "g=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
        "out['agent_processes']=[l for l in g.stdout.splitlines() if l.strip()]\n"
        "out['agent_binary_sha256']=hashlib.sha256(open('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','rb').read()).hexdigest()\n"
        "for k,unit in (('primary_agent','nodesteer-agent.service'),('repair_agent','nodesteer-agent-repair-20260930-101945-native-1.service')):\n"
        "    u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',unit],capture_output=True,text=True)\n"
        "    out[k]=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
        "print(json.dumps(out))\n"
    )
    cp = subprocess.run(ssh + ['python3', '-'], input=remote, capture_output=True, text=True, timeout=90)
    host = json.loads([l for l in cp.stdout.splitlines() if l.strip()][-1])

    health = {'repair_18080': hub.raw('GET', '/healthz', None, timeout=8)[0], 'primary_8080': None}
    try:
        import urllib.request
        with urllib.request.urlopen('http://%s:8080/healthz' % HUB_HOST, timeout=8) as resp:
            health['primary_8080'] = resp.status
    except Exception as exc:
        health['primary_8080'] = 'error:%s' % type(exc).__name__
    db = hubdb.snapshot([oid for oid, m in delete_results.items() if m['kind'] == 'script'])
    subprocess.run(['ssh', '-O', 'exit', '-o',
                    'ControlPath=/tmp/opencode/g6-hub-ssh-root@192.168.100.209:22',
                    'root@192.168.100.209'], capture_output=True, text=True, timeout=20)
    time.sleep(1.5)
    ps_out = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True, text=True).stdout
    procs = [l.strip() for l in ps_out.splitlines()
             if any(t in l for t in ('run_g6.py', 'frame_proxy.py', 'child_agent.py'))]
    mux = [l.strip() for l in ps_out.splitlines() if 'g6-hub-ssh' in l]

    # retained (undeletable) history re-read from scratch
    transfers = hub.json('/api/transfers')
    mine_tr = [t for t in transfers if (t.get('source_path') or '').startswith('/var/lib/nodesteer-qa-g6-')
               or any((x.get('destination_path') or '').startswith('/var/lib/nodesteer-qa-g6-')
                      for x in t.get('targets') or [])]
    # every node id this batch ever owned (main run + the three focused
    # reproductions + the aborted first run), so the retained Execution rows can be
    # enumerated even though the task rows themselves are deleted.
    batch_node_ids = {label: rec['id'] for label, rec in (STATE.get('nodes') or {}).items()}
    for name in ('repro-xfer-second-node.json', 'repro-xfer-frame-ledger.json', 'repro-xfer-liveness.json'):
        p = HERE / 'evidence' / name
        if p.exists():
            try:
                for label, rec in (json.loads(p.read_text()).get('children') or {}).items():
                    batch_node_ids['%s:%s' % (name.split('-')[2], label)] = rec['node_id']
            except Exception:
                pass
    # first (aborted) run's owned nodes, recorded here because that run's evidence
    # file was overwritten by the graded re-run
    for label, nid in (('run1:a', '2660ab86-e22e-42bb-9199-fd88c17c0d83'),
                       ('run1:b', '8d6f768b-a5f0-4538-8009-6fe50b4c73a8'),
                       ('run1:c', '84ad1726-d2ef-43e1-8a50-8e9477297f60')):
        batch_node_ids[label] = nid
    retained_exec = []
    for key, eid in (STATE.get('executions') or {}).items():
        for e in (eid if isinstance(eid, list) else [eid]):
            code, d = hub.get('/api/executions/' + hub.quote(e))
            ec, ch = hub.get('/api/executions/' + hub.quote(e) + '/logs')
            retained_exec.append({'purpose': key, 'execution_id': e, 'get_http': code,
                                  'status': (d or {}).get('status') if isinstance(d, dict) else None,
                                  'log_chunks': len(ch) if isinstance(ch, list) else None})
    retained_by_node = {}
    total_by_node = 0
    for label, nid in batch_node_ids.items():
        code, rows = hub.get('/api/executions?node_id=%s&limit=500' % hub.quote(nid))
        rows = rows if isinstance(rows, list) else []
        total_by_node += len(rows)
        retained_by_node[label] = {'node_id': nid, 'http': code, 'count': len(rows),
                                   'statuses': sorted({r.get('status') for r in rows})}
    out = {
        'at_utc': now_utc(),
        'verifier': 'independent process; fresh login, fresh Hub/host/SQLite reads',
        'owned_prefix': PREFIX,
        'exact_get_404': exact,
        'exact_all_404': all(v['get_http'] == 404 for v in exact.values()) if exact else None,
        'hub_prefix_leftover': leftovers,
        'hub_prefix_leftover_clear': not any(leftovers.values()),
        'protected_nodes': protected,
        'protected_nodes_online_synced': ok,
        'operator_proxy_ports': ports,
        'operator_proxy_ports_released': ports_released,
        'remaining_harness_processes_on_workstation': procs,
        'ssh_control_mux_paths_present': mux,
        'no_harness_processes_and_no_mux': (not procs) and (not mux),
        'native1': host,
        'native1_clean': (not host['owned_root_glob'] and not host['g6_processes']),
        'native1_protected_units_active': (host['primary_agent'].get('ActiveState') == 'active'
                                           and host['repair_agent'].get('ActiveState') == 'active'),
        'native1_agent_binary_sha_matches_run': host['agent_binary_sha256'] == STATE.get('agent_binary_sha256'),
        'healthz': health,
        'hub_db_quick_check': db['quick_check'],
        'hub_global_revision_before': STATE.get('hub_revision_before'),
        'hub_global_revision_after': db['revision_sequence'],
        'retained_history': {
            'transfers_total_on_hub': len(transfers),
            'transfers_created_by_batch': len(mine_tr),
            'transfers': [{'id': t['id'], 'status': t['status'], 'source_path': t.get('source_path'),
                           'targets': [{'node_id': x['node_id'], 'destination_path': x['destination_path'],
                                        'status': x['status']} for x in t.get('targets') or []]}
                          for t in mine_tr],
            'executions': retained_exec,
            'execution_rows_created_by_batch': len(retained_exec),
            'executions_by_batch_node': retained_by_node,
            'execution_rows_created_by_batch_all_runs': total_by_node,
        },
        'ok': None,
    }
    out['ok'] = (out['exact_all_404'] is True and out['hub_prefix_leftover_clear'] and ok
                 and ports_released is not False and out['native1_clean']
                 and out['no_harness_processes_and_no_mux'] and out['native1_protected_units_active']
                 and db['quick_check'] == 'ok' and health['repair_18080'] == 200
                 and health['primary_8080'] == 200)
    hubapi.write_json(str(HERE / 'final-cleanup-oracle.json'), out)
    hubapi.write_json(str(HERE / 'evidence' / 'phase9-independent-cleanup-verification.json'), out)
    print(json.dumps({'ok': out['ok'], 'exact_all_404': out['exact_all_404'],
                      'leftover_clear': out['hub_prefix_leftover_clear'], 'ports_released': ports_released,
                      'native1_clean': out['native1_clean'], 'protected_ok': ok, 'healthz': health,
                      'retained_transfers': len(mine_tr), 'retained_executions': len(retained_exec)},
                     ensure_ascii=False, indent=2))
    return 0 if out['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
