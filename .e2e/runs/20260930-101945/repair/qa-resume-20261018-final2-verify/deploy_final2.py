#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: deploy the final2 build to the repair env.

Scope guard: only repair units are replaced/restarted. Primary
nodesteer-hub.service (:8080) and nodesteer-agent.service are never touched,
no VM is rebooted. Backups (*.bak-20261018-final2) taken first; rollback
commands recorded in deploy-evidence.json.

Deploy order: hub first (embedded agent payload), then 4 native agents.
UI byte-compare: index.html + every local dist file served by the hub.
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
import finallib as L  # noqa: E402
import hubclient  # noqa: E402

BUILD = Path('/tmp/opencode/nodesteer-build-20261018-final2')
LABEL = 'dddee4f0-e2e-repair-20261018-final2'
BACKUP_DIR = '/opt/nodesteer-repair-20260930-101945/backup-20261018-final2'
HUB_BAK = BACKUP_DIR + '/nodesteer-hub-static-login-guard.bak-20261018-final2'
AGENT_BAK = L.AGENT_BIN + '.bak-20261018-final2'
EV = HERE
os.umask(0o077)

PREV_HUB_SHA = '00e1f375bf67a59ad04dfc6283741cb276ac6be70ce2f21e02d25094845e7a71'
PREV_AGENT_SHA = 'c6ccfe104fec2dfdd25d2e244d60809e6db84066ee296f4a9b3167944d238d01'
NEW_HUB_SHA = '3b5eec15ea2ce7ad6cd0e0f90b7f0a2382e02db3c577c905ba7f4b6fd9c3902b'
NEW_AGENT_SHA = '89f2a3bb4b6bcb8b208230b0c9bffa48d7fb9ddda577b491dd6f4cf317f90008'
NEW_ARM64_SHA = '8e4e78af6bd53fbb9f816e883c4ed26972d480e92fc43e5b91916091b8b399f2'
NEW_UNBUNDLED_SHA = '06c2f343a54b27b381c9bdfc81004c50477e943f5464408154d3b083616e49a7'

ARTIFACTS = {
    'hub_bundled': ('nodesteer-hub-static-final', NEW_HUB_SHA, PREV_HUB_SHA, 55161802),
    'agent_amd64': ('nodesteer-agent-static', NEW_AGENT_SHA, PREV_AGENT_SHA, 16886945),
    'agent_arm64': ('nodesteer-agent-arm64', NEW_ARM64_SHA, None, None),
    'hub_unbundled': ('nodesteer-hub.unbundled', NEW_UNBUNDLED_SHA, None, None),
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


def local_dist_files():
    dist = Path('/home/superc/code/nodesteer/web/dist')
    return sorted(f.relative_to(dist).as_posix() for f in dist.rglob('*') if f.is_file())


def main():
    ev = {'batch': 'qa-resume-20261018-final2-verify', 'build_label': LABEL,
          'purpose': 'P6 并发 + P14 路由/静默成功 + P16 传输终态 + P5 跨标签/CR-3 + P11/P17 a11y + P12 套件修复全部重建 + repair 环境部署（可回滚）',
          'authority': 'http://192.168.100.209:18080', 'started_at_utc': L.now_utc(),
          'artifacts_local': {}, 'web_build': {}, 'pre_deploy': {}, 'backups': {},
          'deployment': {}, 'post_deploy': {}, 'ui_assets': {}}
    for key, (name, new_sha, prev_sha, prev_size) in ARTIFACTS.items():
        p = BUILD / name
        got = sha_local(p)
        ev['artifacts_local'][key] = {
            'path': str(p), 'sha256': got, 'size': p.stat().st_size,
            'expected_sha256': new_sha, 'sha_matches_expected': got == new_sha,
            'previous_build_sha256': prev_sha, 'previous_build_size': prev_size,
            'changed_vs_previous_build': (got != prev_sha) if prev_sha else None}
    ev['build_commands'] = [
        WEB_BUILD_CMD,
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -o %s/nodesteer-hub.unbundled ./cmd/hub" % BUILD,
        "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -ldflags '-X main.version=%s' -o %s/nodesteer-agent-static ./cmd/agent" % (LABEL, BUILD),
        "CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -buildvcs=false -ldflags '-X main.version=%s' -o %s/nodesteer-agent-arm64 ./cmd/agent" % (LABEL, BUILD),
        "sh packaging/bundle-agent.sh %s/nodesteer-hub-static-final %s/nodesteer-hub.unbundled amd64 %s/nodesteer-agent-static arm64 %s/nodesteer-agent-arm64" % (BUILD, BUILD, BUILD, BUILD),
    ]
    idx = Path('/home/superc/code/nodesteer/web/dist/index.html')
    ev['web_build'] = {
        'command': WEB_BUILD_CMD,
        'dist_index_sha256': sha_local(idx),
        'dist_files': len(local_dist_files()),
        'previous_dist_index_sha256': '1fdfc0b9cd9e01fccc2c22a59f9068790ae1ca752990c729ba9159f586231c01',
        'log': 'web-build.log 尾行 WEB_BUILD_EXIT=0（构建目录内）',
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
    rc, out, err = L.scp_up(str(BUILD / 'nodesteer-hub-static-final'), L.HUB_HOST, '/tmp/final2-hub-new')
    up['hub'] = {'scp_rc': rc, 'err': err[-200:]}
    for n in L.NODES:
        rc, out, err = L.scp_up(str(BUILD / 'nodesteer-agent-static'), L.NODES[n], '/tmp/final2-agent-new')
        up[n] = {'scp_rc': rc, 'err': err[-200:]}
    ev['upload'] = up

    # ---------- install + restart hub ----------
    rc, out, err = L.ssh(L.HUB_HOST, 'install -o root -g root -m 0755 /tmp/final2-hub-new %s && rm -f /tmp/final2-hub-new && sha256sum %s && systemctl restart %s && sleep 3 && systemctl is-active %s' % (L.HUB_BIN, L.HUB_BIN, L.HUB_UNIT, L.HUB_UNIT), check=False)
    ev['deployment']['hub'] = {'install_rc': rc, 'out': out.strip(), 'err': err[-300:], 'new_sha256': NEW_HUB_SHA}
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
    if ev['deployment']['hub']['disk_sha256_after'] != NEW_HUB_SHA:
        raise RuntimeError('hub disk sha mismatch after install')

    # ---------- install + restart agents ----------
    ev['deployment']['agents'] = {}
    for n in L.NODES:
        unit = L.AGENT_UNIT_FMT % n
        rc, out, err = L.ssh(L.NODES[n], 'install -o root -g root -m 0755 /tmp/final2-agent-new %s && rm -f /tmp/final2-agent-new && sha256sum %s && systemctl restart %s && sleep 2 && systemctl is-active %s' % (L.AGENT_BIN, L.AGENT_BIN, unit, unit), check=False)
        rec = {'install_rc': rc, 'out': out.strip(), 'err': err[-300:], 'new_sha256': NEW_AGENT_SHA}
        pid = L.unit_pid(L.NODES[n], unit)
        rec.update({'state': L.unit_state(L.NODES[n], unit), 'main_pid': pid,
                    'exe_sha256_after': L.exe_sha(L.NODES[n], pid),
                    'disk_sha256_after': L.sha256(L.NODES[n], L.AGENT_BIN)})
        ev['deployment']['agents'][n] = rec
        if rec['disk_sha256_after'] != NEW_AGENT_SHA:
            raise RuntimeError('agent disk sha mismatch after install on %s' % n)

    # ---------- post health + UI asset equality ----------
    deadline = time.time() + 150
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
    # UI: served index.html + EVERY local dist file byte-identical
    local_idx_sha = sha_local(Path('/home/superc/code/nodesteer/web/dist/index.html'))
    served_idx_sha = remote_sha_of_url(L.HUB_HOST, '/')
    ui = {'local_index_sha256': local_idx_sha, 'served_index_sha256': served_idx_sha,
          'index_match': local_idx_sha == served_idx_sha, 'assets': {}, 'all_dist_files': {}}
    local_index_assets = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', (Path('/home/superc/code/nodesteer/web/dist/index.html')).read_text())))
    for url in local_index_assets:
        local_p = Path('/home/superc/code/nodesteer/web/dist') / url.lstrip('/')
        want = sha_local(local_p) if local_p.is_file() else None
        got = remote_sha_of_url(L.HUB_HOST, url)
        ui['assets'][url] = {'local': want, 'served': got, 'match': want is not None and want == got}
    for rel in local_dist_files():
        local_p = Path('/home/superc/code/nodesteer/web/dist') / rel
        want = sha_local(local_p)
        got = remote_sha_of_url(L.HUB_HOST, '/' + rel)
        ui['all_dist_files'][rel] = {'local': want, 'served': got, 'match': want == got}
    ui['all_assets_match'] = bool(ui['assets']) and all(a['match'] for a in ui['assets'].values())
    ui['all_dist_files_match'] = all(a['match'] for a in ui['all_dist_files'].values())
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
                      'ui_all_dist_files_match': ui['all_dist_files_match'],
                      'nodes': {k: (v.get('status'), v.get('agent_version')) for k, v in ev['post_deploy']['nodes'].items()}}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
