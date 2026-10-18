#!/usr/bin/env python3
"""Independent cleanup oracle for the qa-resume-20261016-g23 batch (separate
process, fresh reads).  It never trusts the run script's in-process state: the
Hub API is queried directly, the native-2 host is inspected over SSH, the
operator-side listening ports are re-checked and the Hub read-only SQLite is
re-opened.
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
NODE_HOST = '192.168.100.206'
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
        code, _ = hub.get('/api/%ss/%s' % (kind, hub.quote(oid)))
        exact[oid] = {'kind': kind, 'get_http': code, 'expected': 404}
    scripts = hub.json('/api/scripts')
    tasks = hub.json('/api/tasks')
    schedules = hub.json('/api/schedules')
    nodes = hub.json('/api/nodes')
    leftovers = {
        'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
        'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'schedules': [s['id'] for s in schedules if (s.get('name') or '').startswith(PREFIX)],
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
    ss = subprocess.run(['ss', '-ltn'], capture_output=True, text=True).stdout
    ports_released = all((': %d' % p) not in ss and (':%d ' % p) not in ss and (':%d\n' % p) not in ss
                         for p in ports) if ports else None

    ssh = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
           '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + NODE_HOST]
    remote = (
        "import json,os,subprocess,hashlib,glob\n"
        "out={}\n"
        "root='/var/lib/nodesteer-qa-g23-%s-a'\n" % RUN['nonce'] +
        "out['root']=root\n"
        "out['root_exists']=os.path.exists(root)\n"
        "out['owned_root_glob']=glob.glob('/var/lib/nodesteer-qa-g23-*')\n"
        "p=subprocess.run(['pgrep','-af','nodesteer-qa-g23'],capture_output=True,text=True)\n"
        "out['g23_processes']=[l for l in p.stdout.splitlines() if l.strip()]\n"
        "g=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
        "out['agent_processes']=[l for l in g.stdout.splitlines() if l.strip()]\n"
        "out['agent_binary_sha256']=hashlib.sha256(open('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','rb').read()).hexdigest()\n"
        "for k,unit in (('primary_agent','nodesteer-agent.service'),('repair_agent','nodesteer-agent-repair-20260930-101945-native-2.service')):\n"
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
    # close the read-only helper's own ssh control master before probing for leftovers,
    # so the workstation probe reflects only batch-owned processes
    subprocess.run(['ssh', '-O', 'exit', '-o',
                    'ControlPath=/tmp/opencode/g23-hub-ssh-root@192.168.100.209:22',
                    'root@192.168.100.209'], capture_output=True, text=True, timeout=20)
    time.sleep(1.5)
    ps_out = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True, text=True).stdout
    procs = [l.strip() for l in ps_out.splitlines()
             if any(t in l for t in ('run_g23.py', 'frame_proxy.py', 'child_agent.py'))]
    mux = [l.strip() for l in ps_out.splitlines() if 'g23-hub-ssh' in l]
    retained = []
    for key, tid in (STATE.get('fixtures') or {}).items():
        if not key.startswith('task_') or not isinstance(tid, str):
            continue
        code, body = hub.get('/api/executions?task_id=%s&limit=500' % hub.quote(tid))
        rows = body if isinstance(body, list) else []
        retained.append({'purpose': key, 'task_id': tid, 'get_http': code, 'count': len(rows),
                         'statuses': sorted({r.get('status') for r in rows})})
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
        'native2': host,
        'native2_clean': (not host['root_exists'] and not host['owned_root_glob']
                          and not host['g23_processes']),
        'native2_protected_units_active': (host['primary_agent'].get('ActiveState') == 'active'
                                           and host['repair_agent'].get('ActiveState') == 'active'),
        'native2_agent_binary_sha_matches_run': host['agent_binary_sha256'] == STATE.get('agent_binary_sha256'),
        'healthz': health,
        'hub_db_quick_check': db['quick_check'],
        'hub_global_revision_after': db['revision_sequence'],
        'hub_global_revision_before': STATE.get('hub_revision_before'),
        'hub_leftover_tombstones_for_owned': [
            {'object_id': c['object_id'], 'object_type': c['object_type'], 'operation': c['operation'],
             'global_revision': c['global_revision']}
            for c in db['owned_changes'] if c['operation'] == 'delete'],
        'retained_execution_history': retained,
        'ok': None,
    }
    out['ok'] = (out['exact_all_404'] is True and out['hub_prefix_leftover_clear'] and ok
                 and ports_released is not False and out['native2_clean'] and out['no_harness_processes_and_no_mux']
                 and out['native2_protected_units_active'] and db['quick_check'] == 'ok'
                 and health['repair_18080'] == 200 and health['primary_8080'] == 200)
    hubapi.write_json(str(HERE / 'final-cleanup-oracle.json'), out)
    hubapi.write_json(str(HERE / 'evidence' / 'phase11-independent-cleanup-verification.json'), out)
    print(json.dumps({'ok': out['ok'], 'exact_all_404': out['exact_all_404'],
                      'leftover_clear': out['hub_prefix_leftover_clear'], 'ports_released': ports_released,
                      'native2_clean': out['native2_clean'], 'protected_ok': ok, 'healthz': health},
                     ensure_ascii=False, indent=2))
    return 0 if out['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
