#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: item 9 supplement — runtime terminal-freeze probe.

Replays an upload POST against the agent-facing upload endpoint of owned SUCCESS/
FAILED transfers (authenticated as the owned source agent). The terminal guard
must reject with 410 Gone and must not change the transfer state. The agent
credential is read read-only from the hub DB and is never written to evidence.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import finallib as L  # noqa: E402
import regression_go2 as A  # noqa: E402

EV = HERE / 'evidence'
os.umask(0o077)

HUB_DB = '/var/lib/nodesteer-hub-repair-20260930-101945/hub.db'


def agent_cred_via_ssh(node_id):
    script = (
        "import sqlite3, json\n"
        "c = sqlite3.connect('file:%s?mode=ro', uri=True)\n"
        "row = c.execute('SELECT value FROM settings WHERE key=?', ('cred:' + %r,)).fetchone()\n"
        "print(json.dumps({'cred': row[0] if row else None}))\n" % (HUB_DB, node_id))
    import subprocess
    cp = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                         '-i', str(Path.home() / '.ssh' / 'id_ed25519'),
                         'root@' + L.HUB_HOST, 'python3 -'],
                        input=script, capture_output=True, text=True, timeout=60)
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    if cp.returncode != 0 or not lines:
        raise RuntimeError('cred_read_failed rc=%s' % cp.returncode)
    return json.loads(lines[-1]).get('cred')


def probe(tid, agent_id, cred, expect_status):
    body = b''
    req = urllib.request.Request(
        'http://192.168.100.209:18080/agent/transfers/%s/upload' % tid, data=body, method='POST',
        headers={'X-NodeSteer-Agent-ID': agent_id, 'X-NodeSteer-Agent-Token': cred,
                 'Content-Type': 'application/octet-stream'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8', 'replace')


def main():
    A.hub.login()
    out = {'at_utc': A.now_utc(), 'case': ['P16-TERMINAL-UPLOAD-REPLAY']}
    g8 = json.loads((EV / 'phase08-g8-terminal.json').read_text())
    node_a = A.ST['children']['a']
    cred = agent_cred_via_ssh(node_a['node_id'])
    out['cred_read'] = bool(cred)
    frozen_before = L.ssh(L.HUB_HOST, "journalctl -u %s --since '30 min ago' -o cat --no-pager | grep -c 'terminal state frozen' || true" % L.HUB_UNIT)[1].strip()
    probes = {}
    for name, tid, expect in (('success_transfer', g8['success_transfer']['id'], 'SUCCESS'),
                              ('failed_transfer', g8['failed_transfer']['id'], 'FAILED')):
        c, b = probe(tid, node_a['agent_id'], cred, expect)
        probes[name] = {'http': c, 'body': b[:160], 'expected_terminal': expect}
        # state must be unchanged
        cc, t = A.hub.get('/api/transfers/' + tid)
        probes[name]['status_after'] = (t or {}).get('status')
    # control: invalid credential
    c, b = probe(g8['success_transfer']['id'], node_a['agent_id'], 'wrong-token-control', 'auth')
    probes['invalid_credential_control'] = {'http': c, 'body': b[:120]}
    frozen_after = L.ssh(L.HUB_HOST, "journalctl -u %s --since '30 min ago' -o cat --no-pager | grep -c 'terminal state frozen' || true" % L.HUB_UNIT)[1].strip()
    out['probes'] = probes
    out['journal_terminal_frozen'] = {'before': frozen_before, 'after': frozen_after}
    ok = (out['cred_read']
          and probes['success_transfer']['http'] == 410
          and 'terminal state SUCCESS' in probes['success_transfer']['body']
          and probes['success_transfer']['status_after'] == 'SUCCESS'
          and probes['failed_transfer']['http'] == 410
          and 'terminal state FAILED' in probes['failed_transfer']['body']
          and probes['failed_transfer']['status_after'] == 'FAILED'
          and probes['invalid_credential_control']['http'] == 401)
    A.record('P16-TERMINAL-UPLOAD-REPLAY', 'terminal', 'FIXED' if ok else 'STILL_BROKEN',
             ('运行时终态写拒绝：SUCCESS/FAILED 传输的续传 POST（agent 认证）均 410 Gone '
              '（terminal state 文案），状态保持终态不变；无效凭据 401 对照；'
              'Hub 日志 terminal state frozen %s→%s' % (frozen_before, frozen_after)) if ok
             else '仍复现：%s' % out['probes'],
             ['evidence/phase09b-upload-replay.json'])
    A.ev_write('phase09b-upload-replay.json', out)
    A.dump_results()
    print(json.dumps({'ok': ok, 'probes': {k: {'http': v['http'], 'status_after': v.get('status_after')}
                                           for k, v in probes.items()}}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
