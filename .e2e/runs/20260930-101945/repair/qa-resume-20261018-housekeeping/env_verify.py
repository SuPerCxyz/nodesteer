#!/usr/bin/env python3
"""qa-resume-20261018-housekeeping: repair-environment final verification (read-only).

Task 4: :18080 healthz/readyz, 4 nodes online/synced on final build, backups
exist with sha (first 16), primary :8080 hub + agent units untouched, no VM
reboot traces, host residue rescan for owned objects.

Writes evidence/env-verify-raw.json.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
os.umask(0o077)
sys.path.insert(0, str(HERE.parent / 'qa-resume-20261018-final-verify' / 'evidence' / 'tools'))
import hubclient  # noqa: E402

KEY = str(Path.home() / '.ssh' / 'id_ed25519')
HUB = '192.168.100.209'
NODES = {'native-1': '192.168.100.204', 'native-2': '192.168.100.206',
         'native-3': '192.168.100.222', 'native-4': '192.168.100.247'}
LABEL = 'dddee4f0-e2e-repair-20261018-final'
HUB_BAK = '/opt/nodesteer-repair-20260930-101945/backup-20261018-final/nodesteer-hub-static-login-guard.bak-20261018-final'
AGENT_BAK = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent.bak-20261018-final'
DEPLOY_START = '2026-10-18T04:40:28Z'


def ssh(ip, cmd, timeout=60):
    cp = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                         '-i', KEY, 'root@' + ip, cmd],
                        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout.strip(), cp.stderr.strip()


def http_code(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception as exc:
        return 'ERR:%s' % type(exc).__name__


def main():
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    # ---------- 1. :18080 healthz/readyz (workstation direct + hub loopback)
    out['health'] = {
        'workstation': {'healthz': http_code('http://192.168.100.209:18080/healthz'),
                        'readyz': http_code('http://192.168.100.209:18080/readyz')},
    }
    rc, so, _ = ssh(HUB, 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18080/healthz; echo; curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18080/readyz; echo')
    out['health']['hub_loopback'] = so.splitlines()
    # ---------- 2. nodes via API
    hub = hubclient.Hub()
    hub.login()
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    out['nodes'] = {}
    for n in rows:
        out['nodes'][n.get('hostname')] = {
            'id': n.get('id'), 'status': n.get('status'), 'sync_status': n.get('sync_status'),
            'agent_version': n.get('agent_version'), 'deployment_mode': n.get('deployment_mode'),
        }
    # ---------- 3. backups
    out['backups'] = {'hub': {}, 'agents': {}}
    rc, so, _ = ssh(HUB, 'ls -la %s && sha256sum %s' % (HUB_BAK, HUB_BAK))
    out['backups']['hub'] = {'path': HUB_BAK, 'ls': so.splitlines()[:3]}
    sha = ''
    for line in so.splitlines():
        if len(line.split()) == 2 and len(line.split()[0]) == 64:
            sha = line.split()[0]
    out['backups']['hub']['sha256'] = sha
    out['backups']['hub']['sha256_first16'] = sha[:16]
    for name, ip in NODES.items():
        rc, so, _ = ssh(ip, 'ls -la %s && sha256sum %s' % (AGENT_BAK, AGENT_BAK))
        sha = ''
        for line in so.splitlines():
            if len(line.split()) == 2 and len(line.split()[0]) == 64:
                sha = line.split()[0]
        out['backups']['agents'][name] = {'path': AGENT_BAK, 'sha256': sha, 'sha256_first16': sha[:16],
                                          'ls_first_line': (so.splitlines() or [''])[0]}
    # ---------- 4. primary untouched + boot times
    out['primary'] = {'hub_209': {}, 'agents': {}, 'boot': {}}
    rc, so, _ = ssh(HUB, 'systemctl show --no-pager -p Id,ActiveState,SubState,MainPID,UnitFileState nodesteer-hub.service')
    out['primary']['hub_209'] = dict(kv.split('=', 1) for kv in so.splitlines() if '=' in kv)
    for name, ip in NODES.items():
        rc, so, _ = ssh(ip, 'systemctl show --no-pager -p Id,ActiveState,SubState,MainPID,UnitFileState nodesteer-agent.service')
        out['primary']['agents'][name] = dict(kv.split('=', 1) for kv in so.splitlines() if '=' in kv)
    for name, ip in [('hub-209', HUB)] + list(NODES.items()):
        rc, so, _ = ssh(ip, 'uptime -s; cat /proc/uptime')
        lines = so.splitlines()
        out['primary']['boot'][name] = {'uptime_s': lines[0] if lines else '', 'proc_uptime': lines[1] if len(lines) > 1 else ''}
    # ---------- 5. residue rescan (targeted, owned prefixes)
    out['residue'] = {}
    for name, ip in [('hub-209', HUB)] + list(NODES.items()):
        script = (
            "import json,os,subprocess\n"
            "out={}\n"
            "out['etc_qa_units']=[n for n in sorted(os.listdir('/etc/systemd/system')) if n.startswith('qa-')]\n"
            "out['etc_qa_units_nodesteer']=[n for n in sorted(os.listdir('/etc/systemd/system')) if 'nodesteer-qa-' in n]\n"
            "out['opt_qa']=[n for n in sorted(os.listdir('/opt')) if n.startswith('qa-') or n.startswith('nodesteer-qa-')]\n"
            "out['varlib_qa']=[n for n in sorted(os.listdir('/var/lib')) if n.startswith('qa-') or n.startswith('nodesteer-qa-')]\n"
            "p=subprocess.run(['ps','-eo','pid,args','--no-headers'],capture_output=True,text=True)\n"
            "out['procs_qa']=[l for l in p.stdout.splitlines() if ('qa-' in l or 'qa_' in l) and 'procs_qa' not in l and 'python3 -' not in l]\n"
            "s=subprocess.run(['ss','-tlnp'],capture_output=True,text=True)\n"
            "out['ports_nodesteer']=[l for l in s.stdout.splitlines() if 'nodesteer' in l]\n"
            "print(json.dumps(out))\n")
        cp = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             '-i', KEY, 'root@' + ip, 'python3', '-'],
                            input=script, capture_output=True, text=True, timeout=90)
        lines = [l for l in cp.stdout.splitlines() if l.strip()]
        try:
            out['residue'][name] = json.loads(lines[-1])
        except Exception as exc:
            out['residue'][name] = {'error': 'parse_%s' % type(exc).__name__, 'tail': cp.stdout[-300:]}
    (EV / 'env-verify-raw.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    # ---------- summary
    nodes_ok = all(v['status'] == 'online' and v['sync_status'] == 'synced' and v['agent_version'] == LABEL
                   for k, v in out['nodes'].items() if k.startswith('qa-repair'))
    print(json.dumps({
        'health': out['health'],
        'nodes_repair_ok': nodes_ok,
        'backups': {k: {kk: vv for kk, vv in v.items() if kk in ('sha256_first16',)} for k, v in out['backups']['agents'].items()},
        'hub_bak_first16': out['backups']['hub'].get('sha256_first16'),
        'primary_hub_pid': out['primary']['hub_209'].get('MainPID'),
        'primary_agent_pids': {k: v.get('MainPID') for k, v in out['primary']['agents'].items()},
        'boot': {k: v['uptime_s'] for k, v in out['primary']['boot'].items()},
        'residue': out['residue'],
    }, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
