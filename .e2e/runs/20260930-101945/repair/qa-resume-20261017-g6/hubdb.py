"""Read-only Hub SQLite oracle for the p-sched batch (adapted from p-sched/hubdb.py).

Talks to the repair Hub VM (192.168.100.209) read-only over SSH: a one-shot
committed-state snapshot and a coarse clock-offset probe. The Hub database is
opened with mode=ro and PRAGMA query_only=ON; nothing is ever written and no
credentials are read here.
"""
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

HUB_HOST = '192.168.100.209'
HUB_DB = '/var/lib/nodesteer-hub-repair-20260930-101945/hub.db'
os.makedirs('/tmp/opencode', exist_ok=True)
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=8', '-o', 'ControlMaster=auto',
       '-o', 'ControlPath=/tmp/opencode/g6-hub-ssh-%r@%h:%p',
       '-o', 'ControlPersist=600',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HUB_HOST]

_RO_HEAD = (
    "import sqlite3, json, sys\n"
    "c = sqlite3.connect('file:" + HUB_DB + "?mode=ro', uri=True)\n"
    "c.execute('PRAGMA query_only=ON')\n"
)

_SNAPSHOT_BODY = '''
ids = [x for x in (sys.argv[1] if len(sys.argv) > 1 else '').split(',') if x]
ph = ','.join(['?'] * len(ids)) if ids else "''"
def q(sql):
    return list(c.execute(sql, ids))
out = {}
out['revision_sequence'] = c.execute('SELECT value FROM revision_sequence LIMIT 1').fetchone()[0]
out['settings'] = dict(c.execute("SELECT key,value FROM settings WHERE key IN ('heartbeat_interval_sec','revision_check_interval_sec','changelog_window')"))
out['recent_changes'] = [{'global_revision': r[0], 'object_type': r[1], 'object_id': r[2],
                          'object_revision': r[3], 'operation': r[4], 'created_at': r[5]}
                         for r in c.execute('SELECT global_revision,object_type,object_id,object_revision,operation,created_at FROM revision_changes ORDER BY global_revision DESC LIMIT 30')]
out['owned_changes'] = [{'global_revision': r[0], 'object_type': r[1], 'object_id': r[2],
                         'object_revision': r[3], 'operation': r[4]}
                        for r in q('SELECT global_revision,object_type,object_id,object_revision,operation FROM revision_changes WHERE object_id IN (%s) ORDER BY global_revision' % ph)]
out['owned_scripts'] = [{'id': r[0], 'name': r[1], 'revision': r[2], 'sha256': r[3],
                         'enabled': bool(r[4]), 'timeout': r[5]}
                        for r in q('SELECT id,name,revision,sha256,enabled,timeout FROM scripts WHERE id IN (%s)' % ph)]
out['owned_tasks'] = [{'id': r[0], 'name': r[1], 'revision': r[2], 'definition': json.loads(r[3])}
                      for r in q('SELECT id,name,revision,definition_json FROM tasks WHERE id IN (%s)' % ph)]
out['owned_schedules'] = [{'id': r[0], 'task_id': r[1], 'revision': r[2], 'enabled': bool(r[3]),
                           'definition': json.loads(r[4])}
                          for r in q('SELECT id,task_id,revision,enabled,definition_json FROM schedules WHERE id IN (%s)' % ph)]
out['agent_sync_state'] = [{'node_id': r[0], 'global_revision': r[1], 'last_sync': r[2], 'sync_status': r[3]}
                           for r in c.execute('SELECT * FROM agent_sync_state')]
out['nodes'] = [{'id': r[0], 'hostname': r[1], 'status': r[2], 'sync_status': r[3], 'global_revision': r[4]}
                for r in c.execute('SELECT id,hostname,status,sync_status,global_revision FROM nodes')]
out['revision_changes_total'] = c.execute('SELECT count(*) FROM revision_changes').fetchone()[0]
out['quick_check'] = c.execute('PRAGMA quick_check').fetchone()[0]
print(json.dumps(out))
'''


def _run_remote(program, args=(), timeout=90):
    cp = subprocess.run(SSH + ['python3', '-'] + list(args), input=program,
                        capture_output=True, text=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError('hub_db_ssh_failed: %s' % (cp.stderr[-400:] or cp.stdout[-400:]))
    return cp.stdout


def _last_json(out):
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError('hub_db_no_output')
    return json.loads(lines[-1])


def snapshot(owned_ids=None):
    """Committed-state read; owned_ids = script/task/schedule ids of interest."""
    owned_ids = owned_ids or []
    out = _run_remote(_RO_HEAD + _SNAPSHOT_BODY, args=[','.join(owned_ids)])
    return _last_json(out)


def clock_offset(samples=8):
    """Estimate (hub_clock - workstation_clock) in seconds via SSH round trips."""
    offs, rtts = [], []
    for _ in range(samples):
        t0 = time.time()
        out = _run_remote("import time\nprint(time.time())\n", timeout=30)
        t1 = time.time()
        remote = float(out.strip().splitlines()[-1])
        offs.append(remote - (t0 + t1) / 2.0)
        rtts.append(t1 - t0)
    return {
        'samples': samples,
        'offset_sec_median': statistics.median(offs),
        'offset_sec_min': min(offs),
        'offset_sec_max': max(offs),
        'offset_sec_halfspread': (max(offs) - min(offs)) / 2.0,
        'rtt_sec_min': min(rtts),
        'rtt_sec_median': statistics.median(rtts),
    }
