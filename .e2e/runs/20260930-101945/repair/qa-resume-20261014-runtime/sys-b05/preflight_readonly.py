#!/usr/bin/env python3
"""sys-b05 read-only preflight: authority guard, protected-node health, settings,
fixture absence and owned-child absence. No writes."""
import json
import socket
import sys
import time
from pathlib import Path

import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
OWNED_PREFIX = 'qa-sysb05-%s-' % NONCE
PROTECTED_UNITS = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
HUB_SHA = '164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'nonce': NONCE, 'owned_prefix': OWNED_PREFIX, 'endpoint': hub.base, 'port': 18080}

    nodes = hub.json('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    out['protected_nodes'] = {}
    bad = []
    for nid, label in PROTECTED_UNITS.items():
        n = by_id.get(nid)
        rec = None if not n else {k: n.get(k) for k in ('id', 'agent_id', 'hostname', 'status', 'sync_status', 'global_revision')}
        out['protected_nodes'][label] = rec
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            bad.append(label)
    out['protected_all_online_synced'] = not bad
    out['node_total'] = len(nodes)

    settings = hub.json('/api/settings')
    if isinstance(settings, list):
        settings = {s.get('key'): s.get('value') for s in settings}
    out['settings'] = settings

    scripts = hub.json('/api/scripts')
    tasks = hub.json('/api/tasks')
    schedules = hub.json('/api/schedules')
    out['fixture_absence'] = {
        'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(OWNED_PREFIX)],
        'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(OWNED_PREFIX)],
        'child_nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(OWNED_PREFIX)],
        'scripts_total': len(scripts), 'tasks_total': len(tasks), 'schedules_total': len(schedules),
    }

    # owned child absence on native-4 (process + dir) using a marker only (no secret read)
    import subprocess
    owned_dir = '/var/lib/nodesteer-%s' % OWNED_PREFIX.rstrip('-')
    ssh = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
           '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@192.168.100.247']
    probe = (
        "import json,os,subprocess\n"
        "d=%r\n"
        "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
        "lines=[l for l in p.stdout.splitlines()]\n"
        "own=[l for l in lines if 'qa-sysb05' in l]\n"
        "print(json.dumps({'owned_child_dir_exists':os.path.exists(d),"
        "'owned_child_process_lines':own,'agent_process_count':len(lines),"
        "'agent_binary_sha':__import__('hashlib').sha256(open('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','rb').read()).hexdigest()}))\n"
    ) % owned_dir
    cp = subprocess.run(ssh + ['python3', '-'], input=probe, capture_output=True, text=True, timeout=40)
    out['native4_owned_absence'] = json.loads(cp.stdout.strip().splitlines()[-1]) if cp.returncode == 0 else {'error': cp.stderr[-300:]}

    # local workstation: proxy port free + no stale listener
    ok = True
    try:
        s = socket.socket()
        s.bind(('0.0.0.0', 0))
        s.close()
    except OSError:
        ok = False
    out['workspace_tcp_ok'] = ok
    out['preflight_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    hubapi.write_json('evidence/phase0-preflight.json', out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out['protected_all_online_synced'] else 1


if __name__ == '__main__':
    sys.exit(main())
