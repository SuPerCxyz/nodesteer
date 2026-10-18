#!/usr/bin/env python3
"""Independent re-verification of batch qa-resume-20261017-g4 (G4: E2E-18, AR-04).

Runs AFTER the batch finished, from a different session than the one that executed it.
Read-only against the repair authority (:18080, hard-guarded) and read-only probes of
native-4 docker baseline. Never prints or writes credentials.

Writes: evidence/reverify-independent.json
"""
from __future__ import annotations

import json
import os
import re
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
REQUIRED_PORT = 18080
KEY = str(Path.home() / '.ssh' / 'id_ed25519')
NATIVE4 = '192.168.100.247'
PREFIX = 'qa-g4-74acdeb973df'
GHOST_NODE = 'd9e5bb6b-a4dd-47c9-95f8-b754916c34c8'
GHOST_AGENT = 'agent-a2f5cdb3-2a75-4d47-a034-5109c6c846a0'
E1 = 'f5249cb8-8c7e-496f-88ea-f384eab28621'
E2 = 'fe8c480f-7e5d-4981-a477-2d72bb3da210'
GHOST_TASK = '36b3dcd2-8697-41d2-a8e2-5a595b026fce'
GHOST_SCRIPT = 'eb19e2f2-be67-488d-bfda-3c4e1f316c9f'
GHOST_SCRIPT2 = '23984e5a-ecf5-491b-af97-176e2f29e69d'
GHOST_APP = 'f543feb3-c216-479d-9e6d-75b7e9b93553'
GHOST_ART = 'ff88f9f3-7f7f-47b0-8d17-6ccc01d738e5'
GHOST_TASK2 = '69d7f063-30c8-44d2-83d0-dda00e193976'
BASELINE_NODES = ['03cb50cc-eab6-4afd-bbe0-f73c4e326773', '15961ef7-81b4-43f3-80c8-1b6977ca2aea',
                  '37989297-3bc8-4c0c-b302-7c6efbdba630', '611c02a5-1859-4465-878b-d543b7f6fdec',
                  '91f44866-8a28-435e-bbda-2f2367126c00', 'f6a79d23-2410-450c-ac39-64dd70c9daa1']
PROTECTED = {'91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
             '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
             '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
             '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4'}

checks: list[dict] = []


def ck(name, ok, detail=None):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    return bool(ok)


def gate(path: Path):
    st = path.lstat()
    if path.is_symlink() or not path.is_file() or st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o600:
        raise SystemExit('gate_failed:' + path.name)
    return st


def ssh(host, script, timeout=120):
    cp = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                         '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR', '-i', KEY,
                         'root@' + host, 'bash', '-s'],
                        input=script, capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


# --------------------------------------------------------------- hub client (read-only)
st_conn, st_cred = gate(CONN), gate(CRED)
conn = json.loads(CONN.read_text())
base = (conn.get('hub_url') or '').rstrip('/')
ck('endpoint_is_repair_18080', urllib.parse.urlsplit(base).port == REQUIRED_PORT, base)
if urllib.parse.urlsplit(base).port != REQUIRED_PORT:
    raise SystemExit('endpoint_guard')
cred = json.loads(CRED.read_text())


def req(method, path, body=None, token=None, timeout=25):
    hdrs = {'Accept': 'application/json'}
    data = None
    if token:
        hdrs['Authorization'] = 'Bearer ' + token
    if body is not None:
        data = json.dumps(body).encode()
        hdrs['Content-Type'] = 'application/json'
    r = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, (json.loads(raw) if raw else None)
        except Exception:
            return exc.code, None


code, login = req('POST', '/api/login', {'username': cred['admin_username'],
                                         'password': cred['admin_password']})
ck('login_200', code == 200 and isinstance(login, dict) and bool(login.get('token')), 'http=%s' % code)
token = (login or {}).get('token')

# healthz
try:
    with urllib.request.urlopen(base + '/healthz', timeout=10) as resp:
        hz = resp.status
except urllib.error.HTTPError as exc:
    hz = exc.code
ck('healthz_200', hz == 200, 'http=%s' % hz)

# nodes: no ghost node, no qa-g4-* hostname, baseline set intact, protected healthy
code, nodes = req('GET', '/api/nodes', token=token)
nodes = nodes if isinstance(nodes, list) else []
ids = sorted(n.get('id') for n in nodes)
ck('nodes_list_200', code == 200, 'count=%d' % len(nodes))
ck('ghost_node_absent', GHOST_NODE not in ids)
ck('no_qa_g4_hostname', not [n for n in nodes if PREFIX in (n.get('hostname') or '')])
ck('node_id_set_matches_prebatch_baseline', ids == sorted(BASELINE_NODES),
   {'now': ids, 'baseline': sorted(BASELINE_NODES)})
prot = {}
for n in nodes:
    if n.get('id') in PROTECTED:
        prot[PROTECTED[n['id']]] = {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                    'global_revision': n.get('global_revision'),
                                    'agent_version': n.get('agent_version'),
                                    'deployment_mode': n.get('deployment_mode')}
ck('protected_4_online_synced',
   len(prot) == 4 and all(v['status'] == 'online' and v['sync_status'] == 'synced' for v in prot.values()),
   prot)

# collections: prefix must be absent everywhere; ghost ids absent
collections = {
    'tasks': '/api/tasks', 'scripts': '/api/scripts', 'applications': '/api/applications',
    'artifacts': '/api/artifacts', 'schedules': '/api/schedules',
}
ghost_ids = {GHOST_NODE, GHOST_TASK, GHOST_TASK2, GHOST_SCRIPT, GHOST_SCRIPT2, GHOST_APP, GHOST_ART}
coll_report = {}
for key, path in collections.items():
    code, body = req('GET', path, token=token)
    rows = body if isinstance(body, list) else (body or {}).get(key) or (body or {}).get('rows') or []
    rows = rows if isinstance(rows, list) else []
    pref = [r for r in rows if isinstance(r, dict) and PREFIX in json.dumps(r, ensure_ascii=False)]
    gid = [r for r in rows if isinstance(r, dict) and r.get('id') in ghost_ids]
    coll_report[key] = {'http': code, 'count': len(rows), 'prefix_hits': len(pref), 'ghost_id_hits': len(gid)}
    ck('collection_%s_no_prefix_no_ghost' % key, code == 200 and not pref and not gid, coll_report[key])
    if pref:
        ck('collection_%s_prefix_detail' % key, False,
           [{k: v for k, v in r.items() if k in ('id', 'name', 'hostname')} for r in pref])

# executions: retained history (unavoidable). Register counts, do NOT treat as residue.
code, execs = req('GET', '/api/executions', token=token)
execs = execs if isinstance(execs, list) else (execs or {}).get('executions') or []
execs = execs if isinstance(execs, list) else []
owned_exec = [e for e in execs if isinstance(e, dict) and GHOST_TASK in json.dumps(e)]
hist_ids = {e.get('id') for e in owned_exec if isinstance(e, dict)}
ck('retained_history_both_executions_present', E1 in hist_ids and E2 in hist_ids,
   {'retained_owned_executions': len(owned_exec), 'ids': sorted(hist_ids)})
term = []
for eid in (E1, E2):
    code, e = req('GET', '/api/executions/' + eid, token=token)
    term.append({'id': eid, 'http': code, 'status': (e or {}).get('status'),
                 'exit_code': (e or {}).get('exit_code'),
                 'script_id': (e or {}).get('script_id'), 'node_id': (e or {}).get('node_id'),
                 'task_id': (e or {}).get('task_id')})
ck('retained_executions_successful_and_owned',
   all(t['http'] == 200 and t['status'] == 'SUCCESS' for t in term)
   and all(t['task_id'] == GHOST_TASK and t['node_id'] == GHOST_NODE for t in term), term)

# --------------------------------------------------------------- remote baseline (read-only)
rc, out, err = ssh(NATIVE4, r'''
echo "VERSION=$(docker version --format '{{.Server.Version}}')"
echo "CONTAINERS=$(docker ps -a -q | wc -l)"
echo "IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' | sort | tr '\n' ',')"
echo "VOLUMES=$(docker volume ls -q | wc -l)"
echo "NETWORKS=$(docker network ls --format '{{.Name}}' | sort | tr '\n' ',')"
echo "QADIRS=$(ls -d /opt/*qa* /var/lib/docker/volumes/*qa-g4* 2>/dev/null | tr '\n' ',')"
echo "QAPROCS=$(pgrep -af 'qa-g4' | wc -l)"
echo "UNIT=$(systemctl is-active nodesteer-agent-repair-20260930-101945-native-4.service)"
echo "ROOTFREE=$(df -h / | tail -1 | awk '{print $4}')"
''')
rmt = dict(l.split('=', 1) for l in out.splitlines() if '=' in l)
ck('native4_ssh_ok', rc == 0, err[-200:] if rc else None)
ck('native4_docker_usable', rmt.get('VERSION') == '29.4.0-ce', rmt.get('VERSION'))
ck('native4_zero_containers', rmt.get('CONTAINERS') == '0', rmt.get('CONTAINERS'))
ck('native4_zero_volumes', rmt.get('VOLUMES') == '0', rmt.get('VOLUMES'))
ck('native4_networks_default_only', rmt.get('NETWORKS') == 'bridge,host,none,', rmt.get('NETWORKS'))
ck('native4_no_qa_dirs_no_qa_vols', rmt.get('QADIRS') == '', rmt.get('QADIRS'))
ck('native4_no_qa_procs', rmt.get('QAPROCS') == '0', rmt.get('QAPROCS'))
ck('native4_protected_unit_active', rmt.get('UNIT') == 'active', rmt.get('UNIT'))
ck('pinned_image_removed_from_native4',
   'nodesteer-qa/agent-host' not in (rmt.get('IMAGES') or ''), rmt.get('IMAGES'))

# source image on native-2 untouched (read-only provenance of the import)
rc2, out2, _ = ssh('192.168.100.206',
                   "docker image inspect --format '{{.Id}}' nodesteer-qa/agent-host:f174890c3846 2>&1 | head -1")
ck('native2_source_image_still_present_readonly',
   'sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44' in out2, out2.strip())

# --------------------------------------------------------------- local hygiene
proc = subprocess.run(['pgrep', '-af', PREFIX], capture_output=True, text=True)
loc = [l for l in proc.stdout.splitlines() if 'pgrep' not in l]
ck('no_local_batch_processes', not loc, loc)
ck('credentials_file_perm_0600',
   stat.S_IMODE(st_cred.st_mode) == 0o600 and stat.S_IMODE(st_conn.st_mode) == 0o600,
   {'cred_mode': oct(stat.S_IMODE(st_cred.st_mode)), 'conn_mode': oct(stat.S_IMODE(st_conn.st_mode))})

# secret leak scan over the whole batch directory (values never printed)
secrets = [v for k, v in cred.items() if isinstance(v, str) and len(v) >= 8]
hits = []
for p in HERE.rglob('*'):
    if p.is_file() and '__pycache__' not in p.parts:
        try:
            blob = p.read_text(errors='ignore')
        except Exception:
            continue
        for s in secrets:
            if s in blob:
                hits.append(p.relative_to(HERE).as_posix())
ck('no_credential_values_in_batch_dir', not hits, hits)

# screenshots are real PNGs
pngs = sorted(EV.glob('ui*.png'))
shot = []
for p in pngs:
    head = p.read_bytes()[:32]
    w = int.from_bytes(head[16:20], 'big') if head[:8] == b'\x89PNG\r\n\x1a\n' else 0
    h = int.from_bytes(head[20:24], 'big') if head[:8] == b'\x89PNG\r\n\x1a\n' else 0
    shot.append({'file': p.name, 'png': head[:8] == b'\x89PNG\r\n\x1a\n', 'w': w, 'h': h,
                 'bytes': p.stat().st_size})
ck('ui_screenshots_are_real_pngs', len(shot) == 7 and all(s['png'] and s['w'] > 300 for s in shot), shot)

# claimed deliverables present and parseable
deliv = {}
for name in ('case-result.json', 'progress.json', 'fixture-ledger.json', 'final-cleanup-oracle.json'):
    p = HERE / name
    try:
        deliv[name] = {'exists': p.is_file(), 'bytes': p.stat().st_size,
                       'json_ok': isinstance(json.loads(p.read_text()), (dict, list))}
    except Exception as e:
        deliv[name] = {'exists': p.is_file(), 'error': type(e).__name__}
ck('deliverables_present_and_parseable', all(v.get('json_ok') for v in deliv.values()), deliv)

# --------------------------------------------------------------- report
ok = [c for c in checks if c['ok']]
res = {
    'batch_id': 'qa-resume-20261017-g4',
    'kind': 'independent_reverification',
    'reverified_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'reverified_by': 'separate session (post-completion, read-only); did not re-run the batch',
    'authority': base,
    'primary_8080_contacted': False,
    'summary': {'checks': len(checks), 'passed': len(ok), 'failed': len(checks) - len(ok)},
    'checks': checks,
    'live_state': {
        'protected_nodes': prot,
        'native4': rmt,
        'collections': coll_report,
        'retained_owned_executions': len(owned_exec),
        'retained_execution_ids': sorted(hist_ids),
        'ui_screenshots': shot,
        'deliverables': deliv,
    },
}
( EV / 'reverify-independent.json').write_text(json.dumps(res, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'passed': len(ok), 'total': len(checks),
                  'failed': [c['check'] for c in checks if not c['ok']]}, ensure_ascii=False))
