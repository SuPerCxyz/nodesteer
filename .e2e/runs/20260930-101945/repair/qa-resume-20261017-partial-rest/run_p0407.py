#!/usr/bin/env python3
"""P04-07 closure: non-admin POST /api/nodes/enrollment -> 403 direct assertion.

Runs a disposable isolated hub instance on .209:18087 using the same repair
binary, with independent config/data_dir, owned admin+operator+viewer accounts.
Instance is destroyed at the end (kill + rm + port re-probe).
Secrets are generated locally and passed via stdin only; never persisted.
"""
import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib as L  # noqa: E402

ISO_PORT = 18087
ISO_DIR = '/tmp/nodesteer-iso-prst-' + L.NONCE
ISO_UI = 'http://%s:%d' % (L.HOST, ISO_PORT)
BIN = '/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard'
# [p] trick keeps the remote shell's own cmdline from matching its own pgrep
PGREP_SAFE = 'nodesteer-hub-static-login-guard --config /tmp/nodesteer-iso-[p]rst-' + L.NONCE

ADMIN_USER = 'iso-prst-' + L.NONCE + '-admin'
OP_USER = 'iso-prst-' + L.NONCE + '-operator'
VIEW_USER = 'iso-prst-' + L.NONCE + '-viewer'
ADMIN_PW = secrets.token_urlsafe(18)
OP_PW = secrets.token_urlsafe(18)
VIEW_PW = secrets.token_urlsafe(18)
REG_TOKEN = secrets.token_urlsafe(24)

pid = None
ev = {
    'case_id': 'P04-07',
    'at_utc': L.now_utc(),
    'isolated_instance': {
        'host': L.HOST, 'port': ISO_PORT, 'binary': BIN,
        'data_dir': ISO_DIR + '/data', 'config': ISO_DIR + '/hub.yaml',
        'purpose': 'owned+disposable authority for non-admin role 403 assertion',
        'secrets_policy': 'admin/user passwords + registration token generated in-memory, passed via stdin, never persisted',
    },
    'authority_18080_untouched_check': {},
    'steps': {},
}


def ssh_run(cmd, input_text=None, timeout=60, check=True):
    cp = subprocess.run(['ssh', '-i', L.SSH_KEY, '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=no',
                         L.SSH_DEST, cmd], input=input_text, capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError('ssh_failed rc=%s cmd=%s err=%s' % (cp.returncode, cmd[:80], (cp.stderr or '')[-300:]))
    return cp


def http(method, url, body=None, token=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {'Accept': 'application/json'}
    if data:
        hdrs['Content-Type'] = 'application/json'
    if token:
        hdrs['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw or b'null')
            except Exception:
                return resp.status, raw.decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b'null')
        except Exception:
            return exc.code, raw.decode('utf-8', 'replace')


def record(name, ok, detail):
    ev['steps'][name] = {'ok': bool(ok), 'detail': detail}
    print(('[OK]  ' if ok else '[FAIL]') + ' ' + name + ' :: ' + json.dumps(detail, ensure_ascii=False)[:400], flush=True)


try:
    repair_before = http('GET', L.UI + '/healthz')
    ev['authority_18080_untouched_check']['before'] = {'http': repair_before[0], 'body': repair_before[1]}

    pre = ssh_run('ss -ltn | grep -c ":%d " || true; ls -l %s' % (ISO_PORT, BIN))
    lines = [l for l in pre.stdout.strip().splitlines() if l.strip()]
    port_free = lines[0].strip() == '0'
    record('port_18087_free_preflight', port_free, {'ss_grep_count': lines[0].strip(), 'binary': lines[-1][:140]})
    if not port_free:
        raise RuntimeError('port 18087 not free, aborting isolated instance')

    cfg = (
        'web_addr: ":%d"\n' % ISO_PORT +
        'gateway_addr: ":%d"\n' % ISO_PORT +
        'base_url: "%s"\n' % ISO_UI +
        'registration_token: "%s"\n' % REG_TOKEN +
        'data_dir: "%s/data"\n' % ISO_DIR +
        'artifact_dir: "%s/data/artifacts"\n' % ISO_DIR +
        'admin_username: "%s"\n' % ADMIN_USER +
        'admin_password: "%s"\n' % ADMIN_PW +
        'session_ttl: "2h"\n'
    )
    setup = ssh_run('mkdir -p %s/data/artifacts && umask 077 && cat > %s/hub.yaml && ls -l %s/hub.yaml' % (ISO_DIR, ISO_DIR, ISO_DIR), input_text=cfg)
    record('config_written_0600', 'hub.yaml' in setup.stdout, {'stdout_tail': setup.stdout.strip().splitlines()[-1][:160]})

    launcher = (
        'cd %s\n'
        'setsid %s --config %s/hub.yaml > %s/hub.log 2>&1 < /dev/null &\n'
        'echo $! > %s/hub.pid\n'
        'sleep 1\n'
        'echo "pidfile=$(cat %s/hub.pid)"\n'
    ) % (ISO_DIR, BIN, ISO_DIR, ISO_DIR, ISO_DIR, ISO_DIR)
    start = ssh_run('sh -s', input_text=launcher)
    m = re.search(r'pidfile=(\d+)', start.stdout)
    pidfile_pid = int(m.group(1)) if m else None
    record('instance_launched', bool(pidfile_pid), {'pidfile_pid': pidfile_pid})

    health = None
    for _ in range(40):
        time.sleep(0.75)
        try:
            hc, hb = http('GET', ISO_UI + '/healthz')
            if hc == 200:
                health = {'http': hc, 'body': hb}
                break
        except Exception:
            pass
    record('instance_healthz', bool(health), health or {'error': 'timeout'})
    if not health:
        raise RuntimeError('isolated instance did not become healthy')

    # authoritative pid from the listening socket
    ss = ssh_run("ss -ltnp | grep ':%d '" % ISO_PORT, check=False)
    pm = re.search(r'pid=(\d+)', ss.stdout)
    pid = int(pm.group(1)) if pm else pidfile_pid
    cmdline = ssh_run('tr "\\0" " " < /proc/%d/cmdline 2>/dev/null | head -c 220' % pid, check=False).stdout.strip()
    record('instance_pid_resolved', bool(pid) and 'nodesteer-hub-static-login-guard' in cmdline,
           {'pid': pid, 'pidfile_pid': pidfile_pid, 'cmdline_has_binary': 'nodesteer-hub-static-login-guard' in cmdline,
            'listening': ':%d' % ISO_PORT in ss.stdout})

    c, login_body = http('POST', ISO_UI + '/api/login', {'username': ADMIN_USER, 'password': ADMIN_PW})
    admin_token = login_body.get('token') if isinstance(login_body, dict) else None
    record('admin_login', c == 200 and bool(admin_token), {'http': c, 'token_present': bool(admin_token)})

    for role, user, pw in [('operator', OP_USER, OP_PW), ('viewer', VIEW_USER, VIEW_PW)]:
        c, b = http('POST', ISO_UI + '/api/users', {'username': user, 'password': pw, 'role': role}, token=admin_token)
        record('create_%s_user' % role, c == 200, {'http': c, 'role': role, 'username': user,
                                                  'response_username': (b or {}).get('username') if isinstance(b, dict) else None})

    for role, user, pw in [('operator', OP_USER, OP_PW), ('viewer', VIEW_USER, VIEW_PW)]:
        c, login_body = http('POST', ISO_UI + '/api/login', {'username': user, 'password': pw})
        tok = login_body.get('token') if isinstance(login_body, dict) else None
        mc, me = http('GET', ISO_UI + '/api/me', token=tok)
        rc, nodes = http('GET', ISO_UI + '/api/nodes', token=tok)
        ec, eb = http('POST', ISO_UI + '/api/nodes/enrollment', {
            'node_name': 'iso-%s-probe' % role, 'node_ip': '192.0.2.201', 'hub_address': ISO_UI}, token=tok)
        me_role = (me or {}).get('Role') if isinstance(me, dict) else None
        if me_role is None and isinstance(me, dict):
            me_role = me.get('role')
        record('%s_session_valid' % role, c == 200 and mc == 200 and me_role == role,
               {'login_http': c, 'me_http': mc, 'me_role': me_role, 'me_keys': sorted(me.keys()) if isinstance(me, dict) else None})
        record('%s_read_allowed' % role, rc == 200, {'get_nodes_http': rc, 'nodes_count': len(nodes) if isinstance(nodes, list) else None})
        record('%s_enrollment_POST_403' % role, ec == 403 and (eb or {}).get('error') == 'permission denied',
               {'http': ec, 'body': eb, 'endpoint': 'POST /api/nodes/enrollment'})
        gc, gb = http('GET', ISO_UI + '/api/nodes/enrollment?node_name=probe&node_ip=192.0.2.201', token=tok)
        record('%s_enrollment_GET_403' % role, gc == 403 and (gb or {}).get('error') == 'permission denied',
               {'http': gc, 'body': gb, 'endpoint': 'GET /api/nodes/enrollment'})

    ac, ab = http('POST', ISO_UI + '/api/nodes/enrollment', {
        'node_name': 'iso-%s-adminnode' % L.NONCE, 'node_ip': '192.0.2.202', 'hub_address': ISO_UI}, token=admin_token)
    native_cmd = (ab or {}).get('native', '') if isinstance(ab, dict) else ''
    node_id = (ab or {}).get('node_id', '') if isinstance(ab, dict) else ''
    record('admin_enrollment_200_positive_control', ac == 200 and bool(native_cmd) and REG_TOKEN in native_cmd,
           {'http': ac, 'response_keys': sorted(ab.keys()) if isinstance(ab, dict) else None,
            'native_command_present': bool(native_cmd), 'registration_token_embedded': REG_TOKEN in native_cmd,
            'command_sha256_prefix': hashlib.sha256(native_cmd.encode()).hexdigest()[:16],
            'command_persisted': False,
            'redaction': 'command text contains the registration token and was NOT persisted'})
    if node_id:
        dc, _ = http('DELETE', ISO_UI + '/api/nodes/' + node_id, token=admin_token)
        gc2, _ = http('GET', ISO_UI + '/api/nodes/' + node_id, token=admin_token)
        record('admin_enrollment_node_cleanup', gc2 == 404, {'delete_http': dc, 'get_after_http': gc2})

    ev['verdict'] = 'PASS' if all(s['ok'] for s in ev['steps'].values()) else 'FAIL'
    L.save('p0407-isolated.json', ev)

finally:
    teardown = {'at_utc': L.now_utc()}
    try:
        if pid:
            kill = ssh_run('kill %d 2>/dev/null || true; sleep 1.5; kill -0 %d 2>/dev/null && { kill -9 %d; sleep 1; }; kill -0 %d 2>/dev/null && echo STILL_ALIVE || echo GONE' % (pid, pid, pid, pid), check=False)
            teardown['kill_result'] = kill.stdout.strip().splitlines()[-1] if kill.stdout.strip() else ''
            teardown['process_gone'] = 'GONE' in kill.stdout
        else:
            teardown['process_gone'] = None
        leftover = ssh_run('pgrep -f "%s" | head -3 || true' % PGREP_SAFE, check=False)
        teardown['pgrep_leftover'] = leftover.stdout.strip()
        teardown['no_leftover_process'] = leftover.stdout.strip() == ''
        port = ssh_run('ss -ltn | grep -c ":%d " || true' % ISO_PORT, check=False)
        teardown['port_grep_after'] = port.stdout.strip().splitlines()[0].strip()
        teardown['port_closed'] = teardown['port_grep_after'] == '0'
        rm = ssh_run('rm -rf %s && (test -e %s && echo LEFT || echo REMOVED)' % (ISO_DIR, ISO_DIR), check=False)
        teardown['dir_removed'] = 'REMOVED' in rm.stdout
        after = http('GET', L.UI + '/healthz')
        ev['authority_18080_untouched_check']['after'] = {'http': after[0], 'body': after[1]}
        teardown['repair_18080_health_after'] = ev['authority_18080_untouched_check']['after']
    except Exception as exc:
        teardown['exception'] = str(exc)
    ev['teardown'] = teardown
    ev['teardown_ok'] = bool(teardown.get('process_gone') and teardown.get('port_closed') and teardown.get('dir_removed') and teardown.get('no_leftover_process'))
    L.save('p0407-isolated.json', ev)

    L.save('instance-ledger.json', {
        'batch': 'qa-resume-20261017-partial-rest',
        'nonce': L.NONCE,
        'instances': [{
            'kind': 'isolated-disposable-hub',
            'host': L.HOST, 'port': ISO_PORT,
            'binary': BIN,
            'data_dir': ISO_DIR + '/data', 'config': ISO_DIR + '/hub.yaml',
            'purpose': 'P04-07 non-admin enrollment 403 assertion',
            'resolved_pid': pid,
            'teardown': teardown,
            'status': 'destroyed' if ev.get('teardown_ok') else 'TEARDOWN_INCOMPLETE',
        }],
        'repair_authority_18080': ev.get('authority_18080_untouched_check'),
    })

print('P04-07 verdict:', ev.get('verdict'), 'teardown_ok:', ev.get('teardown_ok'), flush=True)
