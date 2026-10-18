#!/usr/bin/env python3
"""qa-resume-20261017-fixall-verify: deploy the fixall build (Go 6 + Web 6 + CR-3)
to the repair environment (rollback-capable).

Scope guard: only the repair units are replaced/restarted. The primary
nodesteer-hub.service (:8080) and nodesteer-agent.service units are never
touched, and no VM is rebooted. Backups (*.bak-20261017-fixall) are taken first
and the exact rollback commands are recorded in deploy-evidence.json.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import fixalllib as L  # noqa: E402
import hubclient  # noqa: E402

BUILD = Path('/tmp/opencode/nodesteer-build-20261017-fixall')
LABEL = 'dddee4f0-e2e-repair-20261017-fixall'
BACKUP_DIR = '/opt/nodesteer-repair-20260930-101945/backup-20261017-fixall'
HUB_BAK = BACKUP_DIR + '/nodesteer-hub-static-login-guard.bak-20261017-fixall'
AGENT_BAK = L.AGENT_BIN + '.bak-20261017-fixall'
EV = HERE
os.umask(0o077)

PREV_HUB_SHA = '029f3f4e5cdfe863a917557171c2672548d1b5773e516bf50eab56e1629e20ee'
PREV_AGENT_SHA = '449af1d571daccce4264bda11e10d644f30c0db300f084c1f40a320b6bb2d256'
PREV_HUB_SIZE = 55057054
PREV_AGENT_SIZE = 16841929

ARTIFACTS = {
    'hub_bundled': ('nodesteer-hub-static-fixall', None, PREV_HUB_SHA, PREV_HUB_SIZE),
    'agent_amd64': ('nodesteer-agent-static', None, PREV_AGENT_SHA, PREV_AGENT_SIZE),
    'agent_arm64': ('nodesteer-agent-arm64', None, None, None),
    'hub_unbundled': ('nodesteer-hub.unbundled', None, None, None),
}

WEB_BUILD_CMD = 'cd web && npm run build   # tsc -b && vite build'


def sha_local(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def node_map():
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


def remote_sha_of_url(host, url):
    rc, out, _ = L.ssh(host, 'curl -s http://127.0.0.1:18080%s | sha256sum' % url)
    return out.split()[0] if rc == 0 and out.strip() else None


def local_index_assets():
    html = (Path('/home/superc/code/nodesteer/web/dist/index.html')).read_text()
    urls = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)))
    return urls


def main():
    ev = {'batch': 'qa-resume-20261017-fixall-verify', 'build_label': LABEL,
          'purpose': 'Go 6 项 + Web 6 项 + CR-3 全部修复构建 + repair 环境部署（可回滚）',
          'authority': 'http://192.168.100.209:18080', 'started_at_utc': L.now_utc(),
          'artifacts_local': {}, 'web_build': {}, 'pre_deploy': {}, 'backups': {},
          'deployment': {}, 'post_deploy': {}, 'ui_assets': {}}
    for key, (name, _want, prev_sha, prev_size) in ARTIFACTS.items():
        p = BUILD / name
        got = sha_local(p)
        ev['artifacts_local'][key] = {
            'path': str(p), 'sha256': got, 'size': p.stat().st_size,
            'previous_build_sha256': prev_sha, 'previous_build_size': prev_size,
            'changed_vs_previous_build': (got != prev_sha) if prev_sha else None}
    ev['build_commands'] = [
        WEB_BUILD_CMD,
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -o %s/nodesteer-hub.unbundled ./cmd/hub" % BUILD,
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -ldflags '-X main.version=%s' -o %s/nodesteer-agent-static ./cmd/agent" % (LABEL, BUILD),
        "CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -buildvcs=false -ldflags '-X main.version=%s' -o %s/nodesteer-agent-arm64 ./cmd/agent" % (LABEL, BUILD),
        "sh packaging/bundle-agent.sh %s/nodesteer-hub-static-fixall %s/nodesteer-hub.unbundled amd64 %s/nodesteer-agent-static arm64 %s/nodesteer-agent-arm64" % (BUILD, BUILD, BUILD, BUILD),
    ]
    idx = Path('/home/superc/code/nodesteer/web/dist/index.html')
    ev['web_build'] = {
        'command': WEB_BUILD_CMD,
        'dist_index_sha256': sha_local(idx),
        'dist_files': sum(1 for _ in (Path('/home/superc/code/nodesteer/web/dist').rglob('*')) if _.is_file()),
        'manifest_note': 'web-dist-manifest.txt（70 文件全量 sha256，构建目录内）',
        'log': 'web-build.log 尾行 WEB_BUILD_EXIT=0',
    }
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
    rc, out, err = L.scp_up(str(BUILD / 'nodesteer-hub-static-fixall'), L.HUB_HOST, '/tmp/fixall-hub-new')
    up['hub'] = {'scp_rc': rc, 'err': err[-200:]}
    for n in L.NODES:
        rc, out, err = L.scp_up(str(BUILD / 'nodesteer-agent-static'), L.NODES[n], '/tmp/fixall-agent-new')
        up[n] = {'scp_rc': rc, 'err': err[-200:]}
    ev['upload'] = up

    # ---------- install + restart hub ----------
    hub_new = ev['artifacts_local']['hub_bundled']['sha256']
    rc, out, err = L.ssh(L.HUB_HOST, 'install -o root -g root -m 0755 /tmp/fixall-hub-new %s && rm -f /tmp/fixall-hub-new && sha256sum %s && systemctl restart %s && sleep 3 && systemctl is-active %s' % (L.HUB_BIN, L.HUB_BIN, L.HUB_UNIT, L.HUB_UNIT), check=False)
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
        rc, out, err = L.ssh(L.NODES[n], 'install -o root -g root -m 0755 /tmp/fixall-agent-new %s && rm -f /tmp/fixall-agent-new && sha256sum %s && systemctl restart %s && sleep 2 && systemctl is-active %s' % (L.AGENT_BIN, L.AGENT_BIN, unit, unit), check=False)
        rec = {'install_rc': rc, 'out': out.strip(), 'err': err[-300:], 'new_sha256': agent_new}
        pid = L.unit_pid(L.NODES[n], unit)
        rec.update({'state': L.unit_state(L.NODES[n], unit), 'main_pid': pid,
                    'exe_sha256_after': L.exe_sha(L.NODES[n], pid),
                    'disk_sha256_after': L.sha256(L.NODES[n], L.AGENT_BIN)})
        ev['deployment']['agents'][n] = rec
        if rec['disk_sha256_after'] != agent_new:
            raise RuntimeError('agent disk sha mismatch after install on %s' % n)

    # ---------- post health + UI asset equality ----------
    deadline = time.time() + 120
    nodes = {}
    while time.time() < deadline:
        nodes = node_map()
        repair = {k: v for k, v in nodes.items() if k.startswith('qa-repair')}
        if len(repair) == 4 and all(v.get('status') == 'online' and v.get('sync_status') == 'synced'
                                    and v.get('agent_version') == LABEL for v in repair.values()):
            break
        time.sleep(3)
    ev['post_deploy'] = {
        'at_utc': L.now_utc(),
        'healthz': L.curl_code(L.HUB_HOST, '/healthz'), 'readyz': L.curl_code(L.HUB_HOST, '/readyz'),
        'nodes': {k: {'status': v.get('status'), 'sync_status': v.get('sync_status'),
                      'agent_version': v.get('agent_version')} for k, v in sorted(nodes.items())},
        'primary_hub_pid': L.unit_pid(L.HUB_HOST, L.PRIMARY_UNIT),
    }
    # UI: served index.html + every /assets/* it references must be byte-identical
    local_idx_sha = sha_local(Path('/home/superc/code/nodesteer/web/dist/index.html'))
    served_idx_sha = remote_sha_of_url(L.HUB_HOST, '/')
    ui = {'local_index_sha256': local_idx_sha, 'served_index_sha256': served_idx_sha,
          'index_match': local_idx_sha == served_idx_sha, 'assets': {}}
    for url in local_index_assets():
        local_p = Path('/home/superc/code/nodesteer/web/dist') / url.lstrip('/')
        want = sha_local(local_p) if local_p.is_file() else None
        got = remote_sha_of_url(L.HUB_HOST, url)
        ui['assets'][url] = {'local': want, 'served': got, 'match': want is not None and want == got}
    ui['all_assets_match'] = bool(ui['assets']) and all(a['match'] for a in ui['assets'].values())
    ev['ui_assets'] = ui
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
                      'ui_index_match': ui['index_match'], 'ui_assets_match': ui['all_assets_match'],
                      'nodes': ev['post_deploy']['nodes']}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
