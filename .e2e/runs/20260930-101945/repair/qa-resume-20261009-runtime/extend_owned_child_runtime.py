#!/usr/bin/env python3
"""Extend only the previously approved QA transient child Unit, with strict owner checks."""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time

ROOT = pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee')
OWNER_NONCE = '870358cd92ee'
NODE_ID = 'c77b8a84-8085-4ea8-8532-de171761854b'
AGENT_ID = 'agent-426aa507-af4c-4005-858f-ce497d2fc67d'
UNIT = 'nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service'
BINARY = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
BINARY_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
CONFIG = str(ROOT / 'agent.yaml')
EXPECTED_PID = 47582
EXPECTED_STARTTICKS = 129963662


def proc(pid):
    raw = open(f'/proc/{pid}/stat').read()
    rest = raw[raw.rfind(')') + 2:].split()
    status = open(f'/proc/{pid}/status').read()
    uid = int(re.search(r'^Uid:\s+(\d+)', status, re.M).group(1))
    cmdline = open(f'/proc/{pid}/cmdline', 'rb').read()
    argv = [x.decode(errors='replace') for x in cmdline.split(b'\0') if x]
    h = hashlib.sha256()
    with open(f'/proc/{pid}/exe', 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return {'pid': pid, 'state': rest[0], 'starttime_ticks': int(rest[19]),
            'uid': uid, 'argv': argv, 'argv_sha256': hashlib.sha256(cmdline).hexdigest(),
            'exe_path': os.readlink(f'/proc/{pid}/exe'), 'exe_sha256': h.hexdigest(),
            'cgroup': open(f'/proc/{pid}/cgroup').read().strip().splitlines()}


def props():
    p = subprocess.run(['systemctl', 'show', UNIT, '-p', 'LoadState', '-p',
                        'ActiveState', '-p', 'MainPID', '-p', 'ControlGroup',
                        '-p', 'RuntimeMaxUSec'], text=True, capture_output=True,
                       timeout=5, check=True)
    return dict(line.split('=', 1) for line in p.stdout.splitlines() if '=' in line)


def guard():
    owner = json.loads((ROOT / 'owner.json').read_text())
    expected_owner = {'owner_nonce': OWNER_NONCE, 'node_id': NODE_ID,
                      'agent_id': AGENT_ID, 'unit': UNIT,
                      'binary_path': BINARY, 'binary_sha256': BINARY_SHA,
                      'config_path': CONFIG, 'data_dir': str(ROOT / 'state')}
    mismatch = [k for k, v in expected_owner.items() if owner.get(k) != v]
    up = props()
    pid = int(up.get('MainPID', '0'))
    process = proc(pid) if pid else None
    cg = process['cgroup'][0].split(':', 2)[-1] if process and process['cgroup'] else ''
    checks = {
        'owner_marker_exact': not mismatch,
        'unit_loaded_active': up.get('LoadState') == 'loaded' and up.get('ActiveState') == 'active',
        'pid_exact': pid == EXPECTED_PID,
        'starttime_exact': process is not None and process['starttime_ticks'] == EXPECTED_STARTTICKS,
        'uid_root': process is not None and process['uid'] == 0,
        'argv_exact_owned_config': process is not None and process['argv'] == [BINARY, '--config', CONFIG],
        'exe_path_exact': process is not None and process['exe_path'] == BINARY,
        'exe_sha_exact': process is not None and process['exe_sha256'] == BINARY_SHA,
        'cgroup_exact': cg == up.get('ControlGroup') == '/system.slice/' + UNIT,
    }
    return {'valid': all(checks.values()), 'checks': checks,
            'owner_mismatches': mismatch, 'unit': up, 'process': process}


before = guard()
if not before['valid']:
    print(json.dumps({'status': 'GUARD_REJECTED_NO_MUTATION', 'before': before}, sort_keys=True))
    raise SystemExit(3)
if before['unit'].get('RuntimeMaxUSec') not in {'30min', '1h'}:
    print(json.dumps({'status': 'UNEXPECTED_RUNTIME_LIMIT_NO_MUTATION', 'before': before}, sort_keys=True))
    raise SystemExit(4)
if before['unit'].get('RuntimeMaxUSec') == '30min':
    changed = subprocess.run(['systemctl', 'set-property', '--runtime', UNIT,
                              'RuntimeMaxSec=3600'], text=True, capture_output=True,
                             timeout=10)
    if changed.returncode != 0:
        print(json.dumps({'status': 'SET_PROPERTY_REJECTED', 'before': before,
                          'returncode': changed.returncode,
                          'stderr': changed.stderr[-1200:]}, sort_keys=True), flush=True)
        raise SystemExit(6)
after = guard()
if not after['valid'] or after['unit'].get('RuntimeMaxUSec') != '1h':
    print(json.dumps({'status': 'POSTCHECK_FAILED', 'before': before, 'after': after}, sort_keys=True))
    raise SystemExit(5)
print(json.dumps({'status': 'OWNED_CHILD_RUNTIME_EXTENDED',
                  'owner_nonce': OWNER_NONCE, 'unit': UNIT,
                  'runtime_max_before': before['unit'].get('RuntimeMaxUSec'),
                  'runtime_max_after': after['unit'].get('RuntimeMaxUSec'),
                  'unit_properties_after': after['unit'],
                  'process_identity_after': {k: after['process'][k] for k in
                      ['pid', 'starttime_ticks', 'uid', 'argv', 'argv_sha256', 'exe_path', 'exe_sha256', 'cgroup']},
                  'deadline_if_started_2026_10_09T08_18_22Z': '2026-10-09T09:18:22Z',
                  'completed_unix': int(time.time())}, sort_keys=True), flush=True)
