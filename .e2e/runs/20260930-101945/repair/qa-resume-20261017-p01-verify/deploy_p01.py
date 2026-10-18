#!/usr/bin/env python3
"""qa-resume-20261017-p01-verify: deploy the P0-1 simplified build to the repair env.

Scope guard: only the repair units (nodesteer-hub-repair-20260930-101945.service,
nodesteer-agent-repair-20260930-101945-native-N.service) are replaced/restarted.
The primary nodesteer-hub.service (:8080) and nodesteer-agent.service are never
touched, and no VM is rebooted. Backups (*.bak-20261017-p01) are taken first and
the exact rollback commands are recorded in deploy-evidence.json.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import p01lib as L  # noqa: E402

BUILD = Path('/tmp/opencode/nodesteer-build-20261017-p01')
LABEL = 'dddee4f0-e2e-repair-20261017-p01'
BACKUP_DIR = '/opt/nodesteer-repair-20260930-101945/backup-20261017-p01'
HUB_BAK = BACKUP_DIR + '/nodesteer-hub-static-login-guard.bak-20261017-p01'
AGENT_BAK = L.AGENT_BIN + '.bak-20261017-p01'
EV = HERE
os.umask(0o077)

ARTIFACTS = {
    'hub_bundled': ('nodesteer-hub-static-login-guard',
                    '029f3f4e5cdfe863a917557171c2672548d1b5773e516bf50eab56e1629e20ee', 55057054,
                    'fdfa667bf51ce1a7e9a0b1aeeeef7660764b605360613d14fb518b67015e36d1', 55058316),
    'agent_amd64': ('nodesteer-agent-static',
                    '449af1d571daccce4264bda11e10d644f30c0db300f084c1f40a320b6bb2d256', 16841929,
                    'b15f6df540a63a8eb530ec837c57d4628ff7925822153a02d624f021a525ac4f', 16842808),
    'agent_arm64': ('nodesteer-agent-arm64',
                    'fcbecc81824e49dcb2865ed5f8f6c5ae99f5b177a7fb6189130e7a79537508d5', 16050846,
                    '7f4edd0eac75a08daa171e78d1897237c5d29a9963277794ecbdb2fa33125471', 16051229),
    'hub_unbundled': ('nodesteer-hub.unbundled',
                      'c45cf5157433eb37a69245487c2454e92d922064143ab119d24eca51c62b9bae', 22164222,
                      'b6c33b75ec76b6a63ebbcab8c39e1c8f10498caa38dbe2cfd15e9b2570b6e593', 22164222),
}


def sha_local(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def node_map():
    import hubclient
    hub = hubclient.Hub()
    hub.login()
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    return {n.get('hostname'): n for n in rows}


def label_of_node():
    try:
        for k, v in node_map().items():
            if k.startswith('qa-repair'):
                return v.get('agent_version')
    except Exception as exc:  # noqa
        return 'err:%s' % exc
    return None


def main():
    ev = {'batch': 'qa-resume-20261017-p01-verify', 'build_label': LABEL,
          'purpose': 'P0-1 去兼容化简化版构建 + repair 环境部署（可回滚）',
          'authority': 'http://192.168.100.209:18080', 'started_at_utc': L.now_utc(),
          'artifacts_local': {}, 'pre_deploy': {}, 'backups': {}, 'deployment': {}, 'post_deploy': {}}
    for key, (name, want_sha, want_size, prev_sha, prev_size) in ARTIFACTS.items():
        p = BUILD / name
        got = sha_local(p)
        if got != want_sha or p.stat().st_size != want_size:
            raise RuntimeError('local artifact drift %s: %s %s' % (name, got, p.stat().st_size))
        ev['artifacts_local'][key] = {
            'path': str(p), 'sha256': got, 'size': p.stat().st_size,
            'previous_build_sha256': prev_sha, 'previous_build_size': prev_size,
            'changed_vs_previous_build': got != prev_sha}
    ev['build_commands'] = [
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -o /tmp/opencode/nodesteer-build-20261017-p01/nodesteer-hub.unbundled ./cmd/hub",
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -ldflags '-X main.version=%s' -o /tmp/opencode/nodesteer-build-20261017-p01/nodesteer-agent-static ./cmd/agent" % LABEL,
        "CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -buildvcs=false -ldflags '-X main.version=%s' -o /tmp/opencode/nodesteer-build-20261017-p01/nodesteer-agent-arm64 ./cmd/agent" % LABEL,
        "sh packaging/bundle-agent.sh /tmp/opencode/nodesteer-build-20261017-p01/nodesteer-hub-static-login-guard /tmp/opencode/nodesteer-build-20261017-p01/nodesteer-hub.unbundled amd64 /tmp/opencode/nodesteer-build-20261017-p01/nodesteer-agent-static arm64 /tmp/opencode/nodesteer-build-20261017-p01/nodesteer-agent-arm64",
    ]
    rc, _, _ = L.ssh(L.HUB_HOST, 'true')  # connectivity precheck
    ev['preflight'] = {'ssh_hub_ok': rc == 0}

    # ---------- pre-deploy state ----------
    pre = ev['pre_deploy']
    pre['hub_209'] = {
        'unit': L.HUB_UNIT, 'state': L.unit_state(L.HUB_HOST, L.HUB_UNIT),
        'disk_sha256': L.sha256(L.HUB_HOST, L.HUB_BIN),
        'healthz': L.curl_code(L.HUB_HOST, '/healthz'), 'readyz': L.curl_code(L.HUB_HOST, '/readyz'),
        'node_version': label_of_node(),
    }
    pid = L.unit_pid(L.HUB_HOST, L.HUB_UNIT)
    pre['hub_209']['main_pid'] = pid
    pre['hub_209']['proc_exe'] = L.exe_sha(L.HUB_HOST, pid)
    pre['primary_hub_untouched'] = {'unit': L.PRIMARY_UNIT,
                                    'state': L.unit_state(L.HUB_HOST, L.PRIMARY_UNIT)}
    pre['agents'] = {n: {'unit': L.AGENT_UNIT_FMT % n, 'ip': L.NODES[n],
                         'state': L.unit_state(L.NODES[n], L.AGENT_UNIT_FMT % n),
                         'disk_sha256': L.sha256(L.NODES[n], L.AGENT_BIN),
                         'proc_exe': L.exe_sha(L.NODES[n], L.unit_pid(L.NODES[n], L.AGENT_UNIT_FMT % n))}
                     for n in L.NODES}
    pre['primary_agent_unit_snapshot'] = {"note": "nodesteer-agent.service 未触碰",
                                          "states": {n: L.unit_state(L.NODES[n], L.PRIMARY_AGENT_UNIT) for n in L.NODES}}
    (EV / 'evidence' / 'predeploy-state.json').write_text(json.dumps(pre, ensure_ascii=False, indent=1) + '\n')

    # ---------- backups ----------
    rc, out, err = L.ssh(L.HUB_HOST, 'mkdir -p %s && cp -p %s %s && sha256sum %s' % (BACKUP_DIR, L.HUB_BIN, HUB_BAK, HUB_BAK), check=False)
    ev['backups']['hub'] = {'path': HUB_BAK, 'rc': rc, 'sha256': (out.strip().split()[0] if out.strip() else None), 'err': err[-200:]}
    ev['backups']['agents'] = {}
    for n in L.NODES:
        rc, out, err = L.ssh(L.NODES[n], 'cp -p %s %s && sha256sum %s' % (L.AGENT_BIN, AGENT_BAK, AGENT_BAK), check=False)
        ev['backups']['agents'][n] = {'path': AGENT_BAK, 'rc': rc,
                                      'sha256': (out.strip().split()[0] if out.strip() else None), 'err': err[-200:]}

    # ---------- upload ----------
    up = {}
    rc, out, err = L.scp_up(str(BUILD / 'nodesteer-hub-static-login-guard'), L.HUB_HOST, '/tmp/p01-hub-new')
    up['hub'] = {'scp_rc': rc, 'err': err[-200:]}
    for n in L.NODES:
        rc, out, err = L.scp_up(str(BUILD / 'nodesteer-agent-static'), L.NODES[n], '/tmp/p01-agent-new')
        up[n] = {'scp_rc': rc, 'err': err[-200:]}
    ev['upload'] = up

    # ---------- install + restart hub ----------
    hub_new = ev['artifacts_local']['hub_bundled']['sha256']
    rc, out, err = L.ssh(L.HUB_HOST, 'install -o root -g root -m 0755 /tmp/p01-hub-new %s && rm -f /tmp/p01-hub-new && sha256sum %s && systemctl restart %s && sleep 3 && systemctl is-active %s' % (L.HUB_BIN, L.HUB_BIN, L.HUB_UNIT, L.HUB_UNIT), check=False)
    ev['deployment']['hub'] = {'install_rc': rc, 'out': out.strip(), 'err': err[-300:], 'new_sha256': hub_new}
    deadline = time.time() + 90
    while time.time() < deadline:
        if L.curl_code(L.HUB_HOST, '/healthz') == '200' and L.curl_code(L.HUB_HOST, '/readyz') == '200':
            break
        time.sleep(2)
    pid = L.unit_pid(L.HUB_HOST, L.HUB_UNIT)
    ev['deployment']['hub'].update({
        'state': L.unit_state(L.HUB_HOST, L.HUB_UNIT), 'main_pid': pid,
        'exe_sha256_after': L.exe_sha(L.HUB_HOST, pid), 'disk_sha256_after': L.sha256(L.HUB_HOST, L.HUB_BIN),
        'healthz': L.curl_code(L.HUB_HOST, '/healthz'), 'readyz': L.curl_code(L.HUB_HOST, '/readyz'),
        'primary_hub_pid_after': L.unit_pid(L.HUB_HOST, L.PRIMARY_UNIT)})
    if ev['deployment']['hub']['disk_sha256_after'] != hub_new:
        raise RuntimeError('hub disk sha mismatch after install')

    # ---------- install + restart agents ----------
    agent_new = ev['artifacts_local']['agent_amd64']['sha256']
    ev['deployment']['agents'] = {}
    for n in L.NODES:
        unit = L.AGENT_UNIT_FMT % n
        rc, out, err = L.ssh(L.NODES[n], 'install -o root -g root -m 0755 /tmp/p01-agent-new %s && rm -f /tmp/p01-agent-new && sha256sum %s && systemctl restart %s && sleep 2 && systemctl is-active %s' % (L.AGENT_BIN, L.AGENT_BIN, unit, unit), check=False)
        rec = {'install_rc': rc, 'out': out.strip(), 'err': err[-300:], 'new_sha256': agent_new}
        pid = L.unit_pid(L.NODES[n], unit)
        rec.update({'state': L.unit_state(L.NODES[n], unit), 'main_pid': pid,
                    'exe_sha256_after': L.exe_sha(L.NODES[n], pid),
                    'disk_sha256_after': L.sha256(L.NODES[n], L.AGENT_BIN)})
        ev['deployment']['agents'][n] = rec
        if rec['disk_sha256_after'] != agent_new:
            raise RuntimeError('agent disk sha mismatch after install on %s' % n)

    # ---------- post health ----------
    nodes = node_map()
    ev['post_deploy'] = {
        'at_utc': L.now_utc(),
        'healthz': L.curl_code(L.HUB_HOST, '/healthz'), 'readyz': L.curl_code(L.HUB_HOST, '/readyz'),
        'nodes': {k: {'status': v.get('status'), 'sync_status': v.get('sync_status'),
                      'agent_version': v.get('agent_version')} for k, v in sorted(nodes.items())},
        'primary_hub_pid': L.unit_pid(L.HUB_HOST, L.PRIMARY_UNIT),
    }
    ev['rollback'] = {
        'method': 'install 备份回原路径并重启对应 repair 单元（不触碰 :8080 主 Hub 与 nodesteer-agent.service）',
        'commands': [
            'install -o root -g root -m 0755 %s %s && systemctl restart %s' % (HUB_BAK, L.HUB_BIN, L.HUB_UNIT),
            'install -o root -g root -m 0755 %s %s && systemctl restart <agent unit>' % (AGENT_BAK, L.AGENT_BIN),
        ],
        'not_executed': True,
    }
    ev['finished_at_utc'] = L.now_utc()
    (EV / 'deploy-evidence.json').write_text(json.dumps(ev, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({'hub': ev['deployment']['hub']['disk_sha256_after'],
                      'agent': ev['deployment']['agents']['native-1']['disk_sha256_after'],
                      'healthz': ev['post_deploy']['healthz'], 'readyz': ev['post_deploy']['readyz'],
                      'nodes': ev['post_deploy']['nodes']}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
