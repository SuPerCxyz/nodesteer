"""qa-resume-20261016-cr1-verify shared helpers.

Repair-environment scope: hub :18080 on 192.168.100.209, 4 native agent nodes.
Only reads local agent state DBs and owned fixture paths; never touches the
primary hub unit (:8080) or the original nodesteer-agent.service units.
"""
import json
import os
import subprocess
import time
from pathlib import Path

os.umask(0o077)

SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')
HUB_HOST = '192.168.100.209'
NODES = {'native-1': '192.168.100.204', 'native-2': '192.168.100.206',
         'native-3': '192.168.100.222', 'native-4': '192.168.100.247'}
HUB_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
HUB_BIN = '/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard'
AGENT_UNIT_FMT = 'nodesteer-agent-repair-20260930-101945-%s.service'
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
PRIMARY_UNIT = 'nodesteer-hub.service'
PRIMARY_AGENT_UNIT = 'nodesteer-agent.service'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def ssh(host, cmd, timeout=90, check=False):
    cp = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                         '-i', SSH_KEY, 'root@' + host, cmd],
                        capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError('ssh_failed %s: rc=%s err=%s' % (host, cp.returncode, cp.stderr[-300:]))
    return cp.returncode, cp.stdout, cp.stderr


def scp_up(local, host, remote, timeout=180):
    cp = subprocess.run(['scp', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                         '-i', SSH_KEY, local, 'root@%s:%s' % (host, remote)],
                        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def unit_pid(host, unit):
    rc, out, _ = ssh(host, 'systemctl show -p MainPID --value %s' % unit)
    return out.strip() if rc == 0 else ''


def unit_state(host, unit):
    rc, out, _ = ssh(host, 'systemctl show --no-pager -p ActiveState,SubState,MainPID,UnitFileState %s' % unit)
    return dict(kv.split('=', 1) for kv in out.splitlines() if '=' in kv)


def sha256(host, path):
    rc, out, _ = ssh(host, 'sha256sum %s 2>/dev/null' % path)
    return out.split()[0] if rc == 0 and out.strip() else None


def exe_sha(host, pid):
    rc, out, _ = ssh(host, 'sha256sum /proc/%s/exe 2>/dev/null; readlink /proc/%s/exe' % (pid, pid))
    lines = [l for l in out.splitlines() if l.strip()]
    sha = lines[0].split()[0] if lines else None
    target = lines[1] if len(lines) > 1 else None
    return sha, target


def curl_code(host, path, port=18080):
    rc, out, _ = ssh(host, 'curl -s -o /dev/null -w "%%{http_code}" http://127.0.0.1:%d%s' % (port, path))
    return out.strip()


def agent_db_path(node, config_hint=None):
    """Resolve the running agent's state.db via /proc fd (no config file read)."""
    unit = AGENT_UNIT_FMT % node
    host = NODES[node]
    pid = unit_pid(host, unit)
    if not pid:
        return None
    rc, out, _ = ssh(host, 'ls -l /proc/%s/fd 2>/dev/null | grep -o "/[^ ]*state.db" | head -1' % pid)
    return out.strip() or None


def agent_db_rows(node, sql, db=None, timeout=90):
    host = NODES[node]
    script = ('python3 - <<PYEOF\nimport json,sqlite3\n'
              "c=sqlite3.connect('file:%s?mode=ro', uri=True, timeout=15)\n"
              "c.row_factory=sqlite3.Row\n"
              "rows=[dict(r) for r in c.execute(%r)]\n"
              "print(json.dumps(rows))\nPYEOF" % (db, sql))
    last = None
    for attempt in range(4):
        rc, out, err = ssh(host, script, timeout=timeout)
        if rc == 0 and out.strip():
            try:
                return json.loads(out.strip().split('\n')[-1])
            except Exception as exc:  # noqa
                last = {'error': 'decode:%s:%s' % (exc, out[-200:])}
        else:
            last = {'error': (err or out)[-300:]}
        time.sleep(1.5)
    return last


def agent_db_exec(node, sql, db=None, timeout=90):
    """Write one statement into the agent state DB (only used while the agent is stopped)."""
    host = NODES[node]
    script = ('python3 - <<PYEOF\nimport json,sqlite3\n'
              "c=sqlite3.connect(%r, timeout=10)\n"
              "cur=c.execute(%r)\n"
              "c.commit()\n"
              "print(json.dumps({'rowcount': cur.rowcount}))\n"
              "c.close()\nPYEOF" % (db, sql))
    rc, out, err = ssh(host, script, timeout=timeout)
    if rc != 0:
        return {'error': (err or out)[-300:]}
    return json.loads(out.strip().split('\n')[-1])


def wait_node(hub, node_id, timeout=150):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, body = hub.get('/api/nodes/' + node_id)
        if code == 200 and isinstance(body, dict):
            last = body
            if body.get('status') == 'online' and body.get('sync_status') == 'synced':
                return body
        time.sleep(1.5)
    return last


def wait_exec(hub, eid, timeout=240):
    deadline = time.time() + timeout
    ex = {}
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/' + eid)
        if code == 200 and ex.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED',
                                                'CANCELED', 'TIMED_OUT'):
            return ex
        time.sleep(0.8)
    return ex or {}

def ev_dir():
    return Path(__file__).resolve().parent.parent


def ev_write(name, value):
    path = Path(ev_dir()) / name
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)
    return str(path)
