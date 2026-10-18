#!/usr/bin/env python3
"""Batch07 Agent artifact-cache stages (P16-09 / P16-10) on native-3.

Owned fixtures: <=1 application, <=1 artifact (both deleted at the end).
Faults are injected at the Hub artifact storage layer on the fixture's own
content-addressed blob (<artifact_dir>/<sha>) and are always restored:
  - content-append  -> download completes but SHA256 verification fails
  - rename-aside    -> download endpoint returns 404
  - truncate mid-download -> download is interrupted while streaming
"""
import json
import os
import subprocess
import time
from pathlib import Path

import hub
import nodes
from run_b07 import N3DIR, NATIVE3, NONCE, PREFIX, rec, save_state, now, sha_of, BASELINE_CACHE_SHA

HERE = Path(__file__).resolve().parent
AGENT_DATA = '/var/lib/nodesteer-repair-20260930-101945/native-3'
CACHE = AGENT_DATA + '/artifacts'
HUB_ART = nodes.HUB_ARTIFACTS
WATCH = N3DIR + '/tmp-watch.jsonl'
WATCH_PID = N3DIR + '/tmp-watch.pid'


def sh(script, timeout=120):
    return nodes.n3_out(script, timeout=timeout)


def cache_listing():
    return sh('ls -la %s/ 2>/dev/null; echo "--tmp--"; ls -la %s/*.tmp 2>/dev/null || echo none' % (CACHE, CACHE))


def stop_watcher():
    return sh('pkill -f watch_tmp.py 2>/dev/null; sleep 0.2; echo stopped; ls -la %s 2>/dev/null | tail -3' % WATCH)


def start_watcher(sha, seconds=180):
    """Copy the watcher to the node and start it detached with setsid."""
    src = (HERE / 'watch_tmp.py').read_text()
    sh('cat > %s/watch_tmp.py <<\'PYEOF\'\n%s\nPYEOF\nchmod 700 %s/watch_tmp.py'
       % (N3DIR, src, N3DIR))
    sh('rm -f %s %s' % (WATCH, WATCH_PID))
    start = sh('setsid nohup python3 %s/watch_tmp.py %s %s %s %s >/dev/null 2>&1 < /dev/null & echo $! > %s'
               % (N3DIR, AGENT_DATA, sha, WATCH, seconds, WATCH_PID))
    time.sleep(0.8)
    check = sh('ls -la %s 2>/dev/null || echo "LOG_MISSING"; pgrep -f watch_tmp.py | head -2' % WATCH)
    return {'start': start, 'check': check,
            'log_ok': 'LOG_MISSING' not in (check.get('stdout') or '')}


def read_watch():
    r = sh('cat %s 2>/dev/null || echo "<no watch log>"' % WATCH)
    events = []
    for line in r['stdout'].splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    return {'raw': r['stdout'][:6000], 'events': events}


def agent_log(grep='artifact'):
    return nodes.n3_out(
        'journalctl -u nodesteer-agent-repair-20260930-101945-native-3.service --since "30 min ago" '
        '--no-pager 2>/dev/null | grep -Ei %s | tail -25' % json.dumps(grep))


def trigger_prefetch(h, app_id, node_id, mode='assign'):
    """Desired-state change that makes the agent prefetch the app artifact."""
    if mode == 'reassign':
        h.post('/api/applications/%s/assign' % app_id, {'node_ids': [node_id], 'remove': True})
        time.sleep(1.0)
    return h.post('/api/applications/%s/assign' % app_id, {'node_ids': [node_id]})


def hub_blob(sha):
    return '%s/%s' % (HUB_ART, sha)


def hub_blob_sha(sha):
    r = nodes.hubsh_out('sha256sum %s 2>/dev/null | cut -d" " -f1; stat -c "%%s" %s' % (hub_blob(sha), hub_blob(sha)))
    return r['stdout']


def wait_cache_state(sha, want, timeout=60):
    """wait until <sha> exists (want='file') or <sha>.tmp is gone (want='no_tmp')"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = sh('test -e %s/%s && echo FILE || echo NOFILE; test -e %s/%s.tmp && echo TMP || echo NOTMP'
               % (CACHE, sha, CACHE, sha))
        txt = r['stdout']
        if want == 'file' and 'FILE' in txt.split('\n')[0]:
            return txt
        if want == 'no_tmp' and 'NOTMP' in txt:
            return txt
        time.sleep(0.5)
    return r['stdout']
