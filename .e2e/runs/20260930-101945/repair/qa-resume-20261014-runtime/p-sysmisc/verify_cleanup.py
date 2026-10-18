#!/usr/bin/env python3
"""Independent cleanup oracle for the p-sysmisc batch (separate process, fresh reads).

Re-derives every cleanup claim from scratch: fresh Hub login, fresh Hub REST calls,
fresh read-only Hub SQLite, fresh SSH into native-1, and a local port re-check. It
never trusts the run script's own in-process state.
"""
import json
import subprocess
import time
from pathlib import Path

import hubapi
import hubdb

HERE = Path(__file__).resolve().parent
RUN = json.loads((HERE / 'run-evidence.json').read_text())
STATE = RUN['state']
PREFIX = RUN['prefix']
NONCE = RUN['nonce']
PROTECTED_NODES = ['91f44866-8a28-435e-bbda-2f2367126c00', '611c02a5-1859-4465-878b-d543b7f6fdec',
                   '37989297-3bc8-4c0c-b302-7c6efbdba630', '03cb50cc-eab6-4afd-bbe0-f73c4e326773']
NATIVE1 = '192.168.100.204'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def main():
    hub = hubapi.Hub()
    hub.login()

    exact = {}
    for kind, oid, _name in STATE.get('created', []):
        code, _ = hub.get('/api/%ss/%s' % (kind, hub.quote(oid)))
        exact[oid] = {'kind': kind, 'get_http': code, 'expected': 404}
    for label, n in STATE.get('nodes', {}).items():
        code, _ = hub.get('/api/nodes/' + hub.quote(n['id']))
        exact[n['id']] = {'kind': 'node', 'get_http': code, 'expected': 404}

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
    nodes_by_id = {n['id']: n for n in nodes}
    protected, ok = {}, True
    for nid in PROTECTED_NODES:
        n = nodes_by_id.get(nid)
        protected[nid] = None if not n else {'hostname': n.get('hostname'), 'status': n.get('status'),
                                            'sync_status': n.get('sync_status')}
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            ok = False

    ledger = json.loads((HERE / 'fixture-ledger.json').read_text())
    ports = [p.get('listen_port') for p in ledger.get('proxies', []) if p.get('listen_port')]
    ss = subprocess.run(['ss', '-ltn'], capture_output=True, text=True).stdout
    ports_released = all((':%d' % p) not in ss for p in ports) if ports else True
    harness_procs = subprocess.run(
        ['bash', '-lc', "ps -eo args | grep -E 'psysmisc|psm-.*frame_proxy' | grep -v grep || true"],
        capture_output=True, text=True).stdout.strip().splitlines()

    ssh = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
           '-o', 'ControlMaster=auto', '-o', 'ControlPath=/tmp/opencode/psm-native1-ssh-%r@%h:%p',
           '-o', 'ControlPersist=120',
           '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + NATIVE1]
    remote = (
        "import json,os,subprocess,hashlib\n"
        "out={}\n"
        "out['roots']={}\n"
        "for lab in ('a','b','c'):\n"
        "    r='/var/lib/nodesteer-qa-psm-%s-'+lab\n"
        "    out['roots'][lab]={'root':r,'exists':os.path.isdir(r),'data_mount':os.path.ismount(r+'/data')}\n"
        "p=subprocess.run(['pgrep','-af','nodesteer-qa-psm'],capture_output=True,text=True)\n"
        "out['owned_processes']=[l for l in p.stdout.splitlines() if l.strip()]\n"
        "m=subprocess.run(['mount'],capture_output=True,text=True).stdout\n"
        "out['psm_mounts']=[l for l in m.splitlines() if 'nodesteer-qa-psm' in l]\n"
        "out['agent_binary_sha256']=hashlib.sha256(open('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','rb').read()).hexdigest()\n"
        "for k,unit in (('primary','nodesteer-agent.service'),('repair','nodesteer-agent-repair-20260930-101945-native-1.service')):\n"
        "    u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',unit],capture_output=True,text=True)\n"
        "    out[k+'_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
        "print(json.dumps(out))\n"
    ) % NONCE
    cp = subprocess.run(ssh + ['python3', '-'], input=remote, capture_output=True, text=True, timeout=90)
    host = json.loads([l for l in cp.stdout.splitlines() if l.strip()][-1])

    health = {'repair_18080': hub.raw('GET', '/healthz', None, timeout=8)[0], 'primary_8080': None}
    try:
        import urllib.request
        with urllib.request.urlopen('http://192.168.100.209:8080/healthz', timeout=8) as resp:
            health['primary_8080'] = resp.status
    except Exception as exc:
        health['primary_8080'] = 'error:%s' % type(exc).__name__

    db = hubdb.snapshot([oid for _, oid, _ in STATE.get('created', [])])
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
        'remaining_harness_processes_on_workstation': harness_procs,
        'native1': host,
        'native1_clean': (not any(v['exists'] for v in host['roots'].values())
                          and not any(v['data_mount'] for v in host['roots'].values())
                          and not host['owned_processes'] and not host['psm_mounts']),
        'native1_protected_units_active': (host['primary_unit'].get('ActiveState') == 'active'
                                          and host['repair_unit'].get('ActiveState') == 'active'),
        'native1_agent_binary_sha_matches': host['agent_binary_sha256'] == AGENT_SHA,
        'healthz': health,
        'hub_db_quick_check': db['quick_check'],
        'hub_global_revision': db['revision_sequence'],
        'hub_leftover_tombstones_for_owned': [
            {'object_id': c['object_id'], 'object_type': c['object_type'], 'operation': c['operation'],
             'global_revision': c['global_revision']}
            for c in db['owned_changes'] if c['operation'] == 'delete'],
        'other_batches_nodes_untouched': [
            {'id': n['id'], 'hostname': n.get('hostname'), 'status': n.get('status'),
             'sync_status': n.get('sync_status')}
            for n in nodes if (n.get('hostname') or '').startswith('qa-')
            and n['id'] not in PROTECTED_NODES and not (n.get('hostname') or '').startswith(PREFIX)],
        'ok': None,
    }
    out['ok'] = (out['exact_all_404'] and out['hub_prefix_leftover_clear'] and ok and ports_released
                 and out['native1_clean'] and out['native1_protected_units_active']
                 and out['native1_agent_binary_sha_matches'] and db['quick_check'] == 'ok'
                 and health['repair_18080'] == 200 and health['primary_8080'] == 200)
    hubapi.write_json('evidence/phase12-independent-cleanup-verification.json', out)
    hubapi.write_json('final-cleanup-oracle.json', out)
    print(json.dumps({'ok': out['ok'], 'exact_all_404': out['exact_all_404'],
                      'leftover_clear': out['hub_prefix_leftover_clear'],
                      'ports_released': ports_released, 'native1_clean': out['native1_clean'],
                      'protected_ok': ok, 'healthz': health}, ensure_ascii=False, indent=2))
    return 0 if out['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
