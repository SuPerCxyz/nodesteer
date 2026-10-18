#!/usr/bin/env python3
"""qa-resume-20261018-final-verify: record build evidence for the 2026-10-18 final rebuild.

Build order (T1 lesson): web first, then hub (embeds web/dist), then agents, then bundle.
Previous build (deployed before this batch): dddee4f0-e2e-repair-20261017-fixall
(hub 1d8cc17e…, agent 9702d2c7…, arm64 297158e4…).
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = Path('/tmp/opencode/nodesteer-build-20261018-final')
REPO = Path('/home/superc/code/nodesteer')
LABEL = 'dddee4f0-e2e-repair-20261018-final'
os.umask(0o077)

PREV = {
    'hub_bundled': ('1d8cc17e3aeeb5df59026bc3881a6ff8b5a3e308617c931ac1f13d70a9cc230b', 55086747),
    'agent_amd64': ('9702d2c7bd2174f1499ef8019b186544afbf4948cdd3bb08945f8b2b63f12adf', 16847621),
    'agent_arm64': ('297158e404ce4059319986c42021869153e9435d0b24c04242700dca4f8b7581', 16052370),
    'hub_unbundled': ('a4437b7d1bdb3d5d5321428cd352717f6252fee534a9388e95d030d06fdd6b41', 22186699),
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
        'batch': 'qa-resume-20261018-final-verify',
        'build_label': LABEL,
        'purpose': 'X16 + T2 Go 修复 + Web3/T3/主 Agent Web 修复全部重建 → repair 环境部署（可回滚）',
        'authority': 'http://192.168.100.209:18080',
        'git_head': subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'],
                                   capture_output=True, text=True).stdout.strip(),
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
        'previous_dist_index_sha256': '0053a663ce0eb16aebf0d54d8001deb747870dfef1871c26e1d8698df3cb8ae8',
        'previous_dist_files': 70,
        'manifest': 'web-dist-manifest.txt（%d 文件全量 sha256，构建目录内）' % len(files),
        'log': 'web-build.log 尾行 WEB_BUILD_EXIT=0',
        'new_strings_in_bundle': {},
    }
    # confirm the fixed UI strings exist in the built assets (source-level proof they are embedded)
    for needle in ('加载中', '健康状态不可用', '条件摘要', '暂无数据', '任务不存在', '投递中'):
        hits = []
        for f in files:
            if f.suffix == '.js':
                try:
                    if needle in f.read_text(errors='replace'):
                        hits.append(f.relative_to(dist).as_posix())
                except Exception:
                    pass
        ev['web_build']['new_strings_in_bundle'][needle] = hits
    # embedded-in-hub check: raw bytes present in the bundled hub binary
    hub_bytes = (BUILD / 'nodesteer-hub-static-final').read_bytes()
    ev['web_build']['strings_in_hub_binary'] = {
        s: (s.encode() in hub_bytes) for s in ('健康状态不可用', '条件摘要', '暂无数据')}
    # agent version injection check
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
