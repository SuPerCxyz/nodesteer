#!/usr/bin/env python3
"""qa-resume-20261016-cr1-verify: deploy the CR-1 build to the repair environment.

Scope guard: only the repair units (nodesteer-hub-repair-20260930-101945.service,
nodesteer-agent-repair-20260930-101945-native-N.service) are replaced/restarted.
The primary nodesteer-hub.service (:8080) and nodesteer-agent.service are never
touched, and no VM is rebooted. Backups (*.bak-20261016-cr1) are taken first and
the exact rollback commands are recorded in deploy-evidence.json.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'tools'))
import cr1lib as L  # noqa: E402

BUILD = Path('/tmp/opencode/nodesteer-build-20261016-cr1')
LABEL = 'dddee4f0-e2e-repair-20261016-cr1'
BACKUP_DIR = '/opt/nodesteer-repair-20260930-101945/backup-20261016-cr1'
HUB_BAK = BACKUP_DIR + '/nodesteer-hub-static-login-guard.bak-20261016-cr1'
AGENT_BAK = L.AGENT_BIN + '.bak-20261016-cr1'
EV = HERE.parent
os.umask(0o077)

ARTIFACTS = {
    'hub_bundled': ('nodesteer-hub-static-login-guard', 'cd8b7f9d7e40841bbba982c6838b2f74ccfdfac785674030c5bf7d6f1f69120d', 55057222),
    'agent_amd64': ('nodesteer-agent-static', 'b04383bf83013d61c804f68590d9956395b73ee4e0e4a8494e6a2b756101913c', 16842225),
    'agent_arm64': ('nodesteer-agent-arm64', '5e1dd7b725d6fe6fa5d90cdf50e3732aeb1363582d29d900be5fa6474958fda6', 16050718),
    'hub_unbundled': ('nodesteer-hub.unbundled', 'b6c33b75ec76b6a63ebbcab8c39e1c8f10498caa38dbe2cfd15e9b2570b6e593', 22164222),
}


def sha_local(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ev = {'batch': 'qa-resume-20261016-cr1-verify', 'build_label': LABEL,
          'purpose': 'CR-1 修复版构建 + repair 环境部署（可回滚）',
          'authority': 'http://192.168.100.209:18080', 'started_at_utc': L.now_utc(),
          'artifacts_local': {}, 'pre_deploy': {}, 'backups': {}, 'deployment': {}, 'post_deploy': {}}
    for key, (name, prev_sha, prev_size) in ARTIFACTS.items():
        p = BUILD / name
        ev['artifacts_local'][key] = {
            'path': str(p), 'sha256': sha_local(p), 'size': p.stat().st_size,
            'previous_build_sha256': prev_sha, 'previous_build_size': prev_size,
            'changed_vs_previous_build': sha_local(p) != prev_sha}
    ev['build_commands'] = [
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -o /tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-hub.unbundled ./cmd/hub",
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -ldflags '-X main.version=%s' -o /tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-agent-static ./cmd/agent" % LABEL,
        "CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -buildvcs=false -ldflags '-X main.version=%s' -o /tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-agent-arm64 ./cmd/agent" % LABEL,
        "sh packaging/bundle-agent.sh /tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-hub-static-login-guard /tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-hub.unbundled amd64 /tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-agent-static arm64 /tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-agent-arm64",
    ]

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
    (EV / 'predeploy-state.json').write_text(json.dumps(pre, ensure_ascii=False, indent=1) + '\n')

    # ---------- backups ----------
    rc, out, err = L.ssh(L.HUB_HOST, 'mkdir -p %s && cp -p %s %s && sha256sum %s' % (BACKUP_DIR, L.HUB_BIN, HUB_BAK, HUB_BAK), check=False)
    ev['backups']['hub'] = {'path': HUB_BAK, 'rc': rc, 'sha256': (out.strip().split()[0] if out.strip() else None), 'err': err[-200:]}
    ev['backups']['agents'] = {}
    for n in L.NODES:
        rc, out, err = L.ssh(n and L.NODES[n], 'cp -p %s %s && sha256sum %s' % (L.AGENT_BIN, AGENT_BAK, AGENT_BAK), check=False)
        ev['backups']['agents'][n] = {'path': AGENT_BAK, 'rc': rc,
                                      'sha256': (out.strip().split()[0] if out.strip() else None), 'err': err[-200:]}

    # ---------- upload ----------
    up = {}
    rc, out, err = L.scp_up(str(BUILD / 'nodesteer-hub-static-login-guard'), L.HUB_HOST, '/tmp/cr1-hub-new')
    up['hub'] = {'scp_rc': rc, 'err': err[-200:]}
    for n in L.NODES:
        rc, out, err = L.scp_up(str(BUILD / 'nodesteer-agent-static'), L.NODES[n], '/tmp/cr1-agent-new')
        up[n] = {'scp_rc': rc, 'err': err[-200:]}
    ev['upload'] = up

    # ---------- install + restart hub ----------
    hub_new = ev['artifacts_local']['hub_bundled']['sha256']
    rc, out, err = L.ssh(L.HUB_HOST, 'install -o root -g root -m 0755 /tmp/cr1-hub-new %s && rm -f /tmp/cr1-hub-new && sha256sum %s && systemctl restart %s && sleep 3 && systemctl is-active %s' % (L.HUB_BIN, L.HUB_BIN, L.HUB_UNIT, L.HUB_UNIT), check=False)
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

    # ---------- install + restart agents ----------
    agent_new = ev['artifacts_local']['agent_amd64']['sha256']
    ev['deployment']['agents'] = {}
    for n in L.NODES:
        unit = L.AGENT_UNIT_FMT % n
        rc, out, err = L.ssh(L.NODES[n], 'install -o root -g root -m 0755 /tmp/cr1-agent-new %s && rm -f /tmp/cr1-agent-new && sha256sum %s && systemctl restart %s && sleep 2 && systemctl is-active %s' % (L.AGENT_BIN, L.AGENT_BIN, unit, unit), check=False)
        rec = {'install_rc': rc, 'out': out.strip(), 'err': err[-300:], 'new_sha256': agent_new}
        pid = L.unit_pid(L.NODES[n], unit)
        rec.update({'state': L.unit_state(L.NODES[n], unit), 'main_pid': pid,
                    'exe_sha256_after': L.exe_sha(L.NODES[n], pid),
                    'disk_sha256_after': L.sha256(L.NODES[n], L.AGENT_BIN)})
        ev['deployment']['agents'][n] = rec

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
    (HERE.parent / 'deploy-evidence.json').write_text(json.dumps(ev, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({'hub': ev['deployment']['hub']['disk_sha256_after'],
                      'agent': ev['deployment']['agents']['native-1']['disk_sha256_after'],
                      'healthz': ev['post_deploy']['healthz'], 'readyz': ev['post_deploy']['readyz'],
                      'nodes': ev['post_deploy']['nodes']}, ensure_ascii=False, indent=1))


def node_map():
    import urllib.request
    import sys as _s
    _s.path.insert(0, str(HERE / 'tools'))
    import hubclient
    hub = hubclient.Hub()
    hub.login()
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    return {n.get('hostname'): n for n in rows}


def label_of_node():
    try:
        m = node_map()
        for k, v in m.items():
            if k.startswith('qa-repair'):
                return v.get('agent_version')
    except Exception as exc:  # noqa
        return 'err:%s' % exc
    return None


if __name__ == '__main__':
    main()
