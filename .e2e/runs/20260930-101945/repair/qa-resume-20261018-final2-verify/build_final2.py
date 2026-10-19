#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: record build evidence for the final2 rebuild.

Build order (T1 lesson): web first, then hub (embeds web/dist), then agents, then bundle.
Previous deployed build: dddee4f0-e2e-repair-20261018-final
(hub 00e1f375…, agent c6ccfe10…, arm64 7d987125…, unbundled 6b2daed2…).
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = Path('/tmp/opencode/nodesteer-build-20261018-final2')
REPO = Path('/home/superc/code/nodesteer')
LABEL = 'dddee4f0-e2e-repair-20261018-final2'
os.umask(0o077)

PREV = {
    'hub_bundled': ('00e1f375bf67a59ad04dfc6283741cb276ac6be70ce2f21e02d25094845e7a71', 55161802),
    'agent_amd64': ('c6ccfe104fec2dfdd25d2e244d60809e6db84066ee296f4a9b3167944d238d01', 16886945),
    'agent_arm64': ('7d987125d6df05282058b21d16cd89f0d4b8c61759f45db9a19e17c89e6c8e90', 16066534),
    'hub_unbundled': ('6b2daed23bba1ca629f94cf7a2e3d0c4598ccb5eb4914d0c0fde8bebdc3f3391', 22208226),
}
ARTIFACTS = {
    'hub_bundled': 'nodesteer-hub-static-final',
    'agent_amd64': 'nodesteer-agent-static',
    'agent_arm64': 'nodesteer-agent-arm64',
    'hub_unbundled': 'nodesteer-hub.unbundled',
}


def sha_local(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ev = {
        'batch': 'qa-resume-20261018-final2-verify',
        'build_label': LABEL,
        'purpose': 'P6/P14/P16 Go 修复 + P5 跨标签/CR-3 + P11/P17 a11y + P12 套件修复全部重建 → repair 环境部署（可回滚）',
        'authority': 'http://192.168.100.209:18080',
        'git_head': subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'],
                                   capture_output=True, text=True).stdout.strip(),
        'worktree_note': '构建基于工作树磁盘状态（含未提交修复）；HEAD 仍为 075a26f，未做任何 Git 写操作',
        'build_order_note': 'T1 教训：先 cd web && npm run build，再 go build hub（go:embed all:dist），再 agent，再 bundle',
        'artifacts_local': {},
        'web_build': {},
        'build_commands': [
            'cd web && npm run build   # tsc -b && vite build',
            'CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -o %s/nodesteer-hub.unbundled ./cmd/hub' % BUILD,
            "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -ldflags '-X main.version=%s' -o %s/nodesteer-agent-static ./cmd/agent" % (LABEL, BUILD),
            "CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -buildvcs=false -ldflags '-X main.version=%s' -o %s/nodesteer-agent-arm64 ./cmd/agent" % (LABEL, BUILD),
            'sh packaging/bundle-agent.sh %s/nodesteer-hub-static-final %s/nodesteer-hub.unbundled amd64 %s/nodesteer-agent-static arm64 %s/nodesteer-agent-arm64' % (BUILD, BUILD, BUILD, BUILD),
        ],
        'build_logs': {
            'web': 'web-build.log（尾行 WEB_BUILD_EXIT=0）',
            'hub': 'hub-build.log（HUB_BUILD_EXIT=0）',
            'agent_amd64': 'agent-amd64-build.log（AGENT_AMD64_EXIT=0）',
            'agent_arm64': 'agent-arm64-build.log（AGENT_ARM64_EXIT=0）',
            'bundle': 'bundle.log（BUNDLE_EXIT=0）',
            'note': '构建日志位于 /tmp/opencode/nodesteer-build-20261018-final2/（临时构建目录）',
        },
    }
    for key, name in ARTIFACTS.items():
        p = BUILD / name
        got = sha_local(p)
        prev_sha, prev_size = PREV[key]
        ev['artifacts_local'][key] = {
            'path': str(p), 'sha256': got, 'size': p.stat().st_size,
            'previous_build_sha256': prev_sha, 'previous_build_size': prev_size,
            'changed_vs_previous_build': got != prev_sha,
        }
    dist = REPO / 'web' / 'dist'
    files = sorted(f for f in dist.rglob('*') if f.is_file())
    ev['web_build'] = {
        'command': 'cd web && npm run build',
        'dist_index_sha256': sha_local(dist / 'index.html'),
        'dist_files': len(files),
        'previous_dist_index_sha256': '1fdfc0b9cd9e01fccc2c22a59f9068790ae1ca752990c729ba9159f586231c01',
        'previous_dist_files': 74,
        'new_strings_in_bundle': {},
    }
    # new fix strings must exist in the built assets
    for needle in ('标签键', '日志搜索', '没有结果。', '间隔必须大于 0 秒'):
        hits = []
        for f in files:
            if f.suffix == '.js':
                try:
                    if needle in f.read_text(errors='replace'):
                        hits.append(f.relative_to(dist).as_posix())
                except Exception:
                    pass
        ev['web_build']['new_strings_in_bundle'][needle] = hits
    hub_bytes = (BUILD / 'nodesteer-hub-static-final').read_bytes()
    ev['web_build']['strings_in_hub_binary'] = {
        s: (s.encode() in hub_bytes) for s in (
            'transfer already finished', 'storage busy, please retry',
            'nodesteer_session_invalidated', 'deployments-summary.json', 'superseded attempt')}
    for name in ('nodesteer-agent-static', 'nodesteer-agent-arm64'):
        data = (BUILD / name).read_bytes()
        ev.setdefault('agent_version_injection', {})[name] = LABEL.encode() in data
    (HERE / 'build-evidence.json').write_text(json.dumps(ev, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({
        'hub': ev['artifacts_local']['hub_bundled']['sha256'],
        'agent': ev['artifacts_local']['agent_amd64']['sha256'],
        'arm64': ev['artifacts_local']['agent_arm64']['sha256'],
        'web_index': ev['web_build']['dist_index_sha256'],
        'web_files': ev['web_build']['dist_files'],
        'all_changed': all(v['changed_vs_previous_build'] for v in ev['artifacts_local'].values()),
    }, indent=1))


if __name__ == '__main__':
    main()
