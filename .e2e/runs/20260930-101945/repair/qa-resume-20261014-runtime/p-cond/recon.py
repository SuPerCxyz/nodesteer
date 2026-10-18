#!/usr/bin/env python3
"""P-COND read-only recon: authority guard, node inventory, clock skew, tooling."""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
BROWSER = build_opener(ProxyHandler({}))
TOKEN = [None]
BASE = [None]
SSH_N1 = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
          '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@192.168.100.204']


def api(method, path, body=None, allowed=(200,)):
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if TOKEN[0]:
        headers['Authorization'] = 'Bearer ' + TOKEN[0]
    try:
        req = Request(BASE[0] + path, data=data, headers=headers, method=method)
        with BROWSER.open(req, timeout=20) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {'_non_json': raw[:120].decode('utf-8', 'replace')}
    except HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {'_raw': raw[:200].decode('utf-8', 'replace')}


def ssh(cmd, timeout=25):
    cp = subprocess.run(SSH_N1 + [cmd], capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def main():
    cred = json.loads(CRED.read_text())
    conn = json.loads(CONN.read_text())
    BASE[0] = conn['hub_url'].rstrip('/')
    sp = urlsplit(BASE[0])
    out = {'hub_url': BASE[0], 'port': sp.port, 'guard_18080': sp.port == 18080,
           'recon_at_utc': datetime.now(timezone.utc).isoformat()}
    code, body = api('POST', '/api/login',
                     {'username': cred['admin_username'], 'password': cred['admin_password']})
    out['login_http'] = code
    TOKEN[0] = (body or {}).get('token')
    out['login_ok'] = bool(TOKEN[0])

    code, nodes = api('GET', '/api/nodes')
    out['nodes_http'] = code
    out['nodes'] = [{'id': n.get('id'), 'hostname': n.get('hostname'), 'status': n.get('status'),
                     'sync_status': n.get('sync_status'), 'agent_id': n.get('agent_id'),
                     'agent_version': n.get('agent_version'), 'last_seen': n.get('last_seen'),
                     'global_revision': n.get('global_revision')} for n in (nodes or [])]

    # settings read-only
    code, settings = api('GET', '/api/settings')
    out['settings_http'] = code
    out['settings_keys'] = sorted((settings or {}).keys()) if isinstance(settings, dict) else settings

    # tasks / scripts / schedules inventory (read-only; count owned-prefix objects)
    for kind in ('tasks', 'scripts', 'schedules'):
        c, lst = api('GET', '/api/%s' % kind)
        out['%s_http' % kind] = c
        out['%s_total' % kind] = len(lst or [])
        out['%s_pcond_owned' % kind] = [x.get('name') for x in (lst or [])
                                        if 'qa-pcond-' in (x.get('name') or '')]
        out['%s_existing_qa_prefix' % kind] = sorted({(x.get('name') or '')[:24]
                                                     for x in (lst or [])
                                                     if (x.get('name') or '').startswith('qa-')})[:40]

    # execution status vocabulary sanity: latest 3 executions
    c, exs = api('GET', '/api/executions?limit=3')
    out['executions_http'] = c
    out['execution_sample_fields'] = sorted((exs or [{}])[0].keys()) if exs else []

    # clock skew: hub vs native-1, interleaved same-source measurement
    samples = []
    for i in range(5):
        t0 = time.time()
        req = Request(BASE[0] + '/healthz', method='GET')
        with BROWSER.open(req, timeout=10) as resp:
            date_hdr = resp.headers.get('Date')
        t1 = time.time()
        hub_epoch = datetime.strptime(date_hdr, '%a, %d %b %Y %H:%M:%S %Z').replace(
            tzinfo=timezone.utc).timestamp()
        rc, so, se = ssh('date -u +%s.%N')
        t2 = time.time()
        native_epoch = float(so.strip()) if rc == 0 else None
        samples.append({'round': i, 'hub_epoch': hub_epoch, 'native_epoch': native_epoch,
                        'hub_minus_native_s': (None if native_epoch is None
                                               else round(hub_epoch - native_epoch, 4)),
                        'hub_rtt_s': round(t1 - t0, 4), 'ssh_rtt_s': round(t2 - t1, 4)})
    out['clock_samples'] = samples

    # native-1 tooling / state
    rc, so, se = ssh('ls -d /var/lib/nodesteer-repair-20260930-101945/native-1 2>&1; '
                     'echo "---df"; df -h / | tail -1; '
                     'echo "---uptime"; uptime -p; '
                     'echo "---ps agent"; pgrep -a -f nodesteer-agent | head -5')
    out['native1'] = {'rc': rc, 'stdout': so, 'stderr': se}

    # hub process / version evidence
    (HERE / 'evidence' / 'recon.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
