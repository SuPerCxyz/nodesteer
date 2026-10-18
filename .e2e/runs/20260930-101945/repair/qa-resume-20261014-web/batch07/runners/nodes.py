#!/usr/bin/env python3
"""Read/write helpers for the batch07 nodes.

Scope: node-side operations are limited to native-3 (192.168.100.222) plus
read-only inspection of the repair Hub host (192.168.100.209) storage and the
Hub's HTTP/DB readbacks.  Never reads agent config files and never touches the
protected Hub/Agent services.
"""
import json
import subprocess
import time

SSH_OPTS = ['-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
NATIVE3 = '192.168.100.222'
HUB = '192.168.100.209'
AGENT_DATA = '/var/lib/nodesteer-repair-20260930-101945/native-3'
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
HUB_DATA = '/var/lib/nodesteer-hub-repair-20260930-101945'
HUB_ARTIFACTS = HUB_DATA + '/artifacts'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def ssh(host, script, timeout=120, via_stdin=True):
    cmd = ['ssh'] + SSH_OPTS + ['root@' + host]
    if via_stdin:
        cp = subprocess.run(cmd + ['bash -s'], input=script, capture_output=True, text=True, timeout=timeout)
    else:
        cp = subprocess.run(cmd + [script], capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def n3(script, timeout=120):
    return ssh(NATIVE3, script, timeout=timeout)


def hubsh(script, timeout=120):
    return ssh(HUB, script, timeout=timeout)


def n3_out(script, timeout=120):
    rc, out, err = n3(script, timeout=timeout)
    return {'rc': rc, 'stdout': out.strip(), 'stderr': err.strip()[-400:]}


def hubsh_out(script, timeout=120):
    rc, out, err = hubsh(script, timeout=timeout)
    return {'rc': rc, 'stdout': out.strip(), 'stderr': err.strip()[-400:]}


def n3_file_state(path):
    """Return existence, size, mode, sha256, mtime and symlink target for one path."""
    script = """
p=%s
if [ -L "$p" ]; then echo '{"kind":"symlink","target":"%s"}'; fi
if [ ! -e "$p" ] && [ ! -L "$p" ]; then echo '{"exists":false}'; exit 0; fi
printf '{"exists_true":true'
printf ',"size":%%s' "$(stat -c %%s "$p" 2>/dev/null)"
printf ',"mode":%%s' "$(stat -c %%a "$p" 2>/dev/null)"
printf ',"mtime":%%s' "$(stat -c %%Y "$p" 2>/dev/null)"
printf ',"sha256":"%%s"' "$(sha256sum "$p" 2>/dev/null | cut -d" " -f1)"
if [ -L "$p" ]; then printf ',"symlink_target":"%%s"' "$(readlink "$p")"; fi
printf '}'
""" % (json.dumps(path), '')
    return n3_out(script)


def n3_dir_listing(path, pattern='*'):
    script = 'ls -la %s/%s 2>/dev/null || true' % (json.dumps(path)[1:-1], pattern)
    return n3_out(script)


def agent_cache_state():
    """Agent artifact cache + .tmp observation (read-only, no config read)."""
    script = """
d=%s
echo "DATA_DIR=$d"
echo "--artifacts"
ls -la "$d/artifacts" 2>/dev/null
echo "--tmp_dir"
ls -la "$d/tmp" 2>/dev/null
echo "--dot_tmp_in_cache"
ls -la "$d/artifacts"/*.tmp 2>/dev/null || echo "none"
echo "--cache_sha"
for f in "$d/artifacts"/*; do [ -f "$f" ] && printf '%%s %%s %%s\\n' "$(basename $f)" "$(stat -c %%s $f)" "$(sha256sum $f | cut -d' ' -f1)"; done 2>/dev/null
""" % AGENT_DATA
    return n3_out(script)


def hub_transfer_storage():
    return hubsh('ls -la %s/file-transfers/ 2>/dev/null; echo "--artifacts"; ls -la %s/ 2>/dev/null' % (HUB_DATA, HUB_ARTIFACTS))


def agent_db_artifacts():
    """Agent SQLite artifact registry (python sqlite3, read-only copy)."""
    script = """
python3 - <<'PY'
import sqlite3, shutil, tempfile, os, json
src='%s/state.db'
tmp=tempfile.mktemp(suffix='.db')
shutil.copy(src, tmp)
for ext in ('-wal','-shm'):
    if os.path.exists(src+ext): shutil.copy(src+ext, tmp+ext)
c=sqlite3.connect(tmp)
out={}
try:
    out['tables']=[r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    try:
        out['artifacts']=[dict(zip([d[0] for d in cur.description], r)) for cur in [c.execute('select * from artifacts')] for r in cur.fetchall()]
    except Exception as e:
        out['artifacts_error']=str(e)
    out['quick_check']=c.execute('pragma quick_check').fetchone()[0]
except Exception as e:
    out['error']=str(e)
finally:
    c.close()
    for p in (tmp, tmp+'-wal', tmp+'-shm'):
        try: os.remove(p)
        except Exception: pass
print(json.dumps(out, ensure_ascii=False))
PY
""" % AGENT_DATA
    rc, out, err = n3(script, timeout=60)
    if rc != 0:
        return {'rc': rc, 'error': err[-300:]}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return {'raw': out[:800]}


def agent_logs(since='15 min ago', grep='artifact|prefetch|transfer|delivery'):
    script = "journalctl -u nodesteer-agent-repair-20260930-101945-native-3.service --since %s --no-pager 2>/dev/null | grep -Ei %s | tail -40" % (
        json.dumps(since), json.dumps(grep))
    return n3_out(script)
