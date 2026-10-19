#!/usr/bin/env python3
"""Post-process the scale run: findings.json, enriched scale-results.json /
resource-usage.json, and the final (phase-2) cleanup oracle."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
RAW = RUN / 'evidence' / 'raw'
BATCH = 'qa-resume-20261018-scale'
HUB_PORT = 18921
BASE = Path('/var/tmp/nodesteer-qa-20261018-scale')


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def write_json(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + '\n')


# ----------------------------------------------------------------- findings
findings = {
    'batch': BATCH,
    'generated_at_utc': now_utc(),
    'scope': '8-node local-loopback scale test (hub 127.0.0.1:18921 + 8 owned agents). '
             'Report-only: no product code modified.',
    'summary': {'new_defects': 0, 'observations': 4,
                'note': '7/7 scenarios PASS；未发现新的功能性缺陷；以下为运行时观察项。'},
    'findings': [
        {
            'id': 'OBS-SCALE-20261018-01',
            'severity': 'info',
            'type': 'observation/transient',
            'title': '24 并发执行刚结束时，同一瞬间抽样的 API 延迟短暂升高（未复现）',
            'scenario': 'S7_api_latency_at_scale',
            'detail': '主轮 S7 紧接 S6（24 并发执行）之后抽样：/api/nodes p50 7.94ms/p95 14.57ms、'
                      '/api/nodes/{id} p95 9.24ms、/api/executions p95 14.45ms、/healthz p95 5.18ms。'
                      '同期 hub CPU 出现 37.9% 尖峰（S6 完成尾段）。受控复测（相同二进制、相同 48 条执行历史、'
                      '机器 load1 4.2-4.6 更高）未复现：1 节点 vs 8 节点 p95 分别为 1.37/2.70ms，'
                      '压测后尾段 p95 2.02-2.69ms。判定为瞬时尾部效应（完成风暴+GC），非稳态退化。',
            'repro': '1) 8 节点在线；2) 3 个并发 RunManual 触发 24 执行；3) 全部终态后立即连续抽样列表/详情端点。',
            'evidence': ['evidence/raw/s7-api-latency.json', 'evidence/raw/s7-verification.json',
                         'evidence/raw/resource-samples.json'],
            'impact': '低：稳态延迟不受影响（绝对值 <3ms）；仅提示“压测刚结束即抽样”会高估延迟。',
            'suggestion': '如需对外发布延迟基线，应在压测结束后静默 ≥10s 再抽样，或注明测量条件。',
        },
        {
            'id': 'OBS-SCALE-20261018-02',
            'severity': 'low',
            'type': 'observation/log-noise',
            'title': '心跳超时关闭会话时 hub 打印 ERROR 级 "agent websocket write failed; closing connection"',
            'scenario': 'S5_node_jitter (SIGSTOP 心跳超时探针)',
            'detail': '对 agent-4 发送 SIGSTOP（进程存活、TCP 未断）26.0s 后 hub 正确判定 offline 并关闭会话；'
                      '关闭瞬间恰有一次 NODE_STATUS 写失败，hub 记录：'
                      'level=ERROR msg="agent websocket write failed; closing connection" '
                      'error="failed to write msg: failed to write frame: use of closed network connection"。'
                      '状态机结果正确（offline → SIGCONT 后 3.8s 内重连、30.0s 恢复 online+synced），'
                      '但该行以 ERROR 记录会让“无错误日志”类巡检产生假阳性。',
            'repro': 'kill -STOP <agent-pid>；等待 heartbeat_timeout(20s)+checker tick(≤30s) → offline；'
                     'kill -CONT <agent-pid>。',
            'evidence': ['evidence/raw/s5-jitter.json', 'evidence/raw/hub.log',
                         'evidence/raw/agent-4.log'],
            'impact': '极低：仅日志级别/噪声，不影响节点状态与恢复。',
            'suggestion': '可考虑将“会话已被超时清理后的写失败”降级为 WARN/DEBUG 并附 close 原因。',
        },
        {
            'id': 'OBS-SCALE-20261018-03',
            'severity': 'info',
            'type': 'environment',
            'title': '本轮 hub 二进制未内置 agent 载荷（启动 WARN），/api/agent/binary 与自升级路径未覆盖',
            'scenario': 'environment',
            'detail': 'hub 启动时记录 level=WARN msg="load bundled agent payloads failed" '
                      'error="invalid agent bundle architecture"（未执行 packaging 的裸构建）。'
                      '本轮仅测试纳管/心跳/执行/调度/规模 API，不涉及二进制分发与自升级；该能力在裸构建下不可用，'
                      '不构成产品缺陷。',
            'repro': '直接 go build ./cmd/hub（不运行 packaging）后启动。',
            'evidence': ['evidence/raw/hub.log', 'evidence/source-snapshot.txt'],
            'impact': '无（超出本轮范围）。',
            'suggestion': '若需验证升级/分发路径，应使用 packaging 产出的 hub。',
        },
        {
            'id': 'OBS-SCALE-20261018-04',
            'severity': 'info',
            'type': 'observation/positive',
            'title': '本地回环可容纳 8 个同源 Agent，无同 IP 注册限制；纳管与 revision 收敛耗时很低',
            'scenario': 'S1_bulk_enrollment',
            'detail': '8 个 agent 全部从 127.0.0.1 连接（node_ip 10.99.0.1-8 由纳管参数指定），'
                      'enroll API 1.8-4.1ms/节点，launch→online+synced 2.0-3.6s/节点，'
                      '8 节点全部纳管 wall 4.79s；revision 0→1→2 全量收敛（realtime 通知 + 周期校验）。',
            'repro': 'POST /api/nodes/enrollment ×8 → 启动 agent → GET /api/nodes 校验 online/synced。',
            'evidence': ['evidence/raw/s1-enrollment.json'],
            'impact': '正向结论：本机回环规模测试无需额外 IP 别名。',
            'suggestion': '无需处理。',
        },
    ],
}
write_json(RUN / 'findings.json', findings)

# ------------------------------------------------------- enrich scale results
results = read_json(RUN / 'scale-results.json', {}) or {}
snap_lines = {}
snap = (RUN / 'evidence' / 'source-snapshot.txt')
if snap.is_file():
    for line in snap.read_text().splitlines():
        k, _, v = line.partition(':')
        snap_lines.setdefault(k.strip(), []).append(v.strip())
results['source_snapshot'] = {
    'git_head': (snap_lines.get('git_head') or [None])[0],
    'git_diff_sha256_final': (snap_lines.get('git_diff_sha256') or [None])[-1],
    'hub_binary_sha256': (snap_lines.get('hub_sha256') or [None])[-1],
    'agent_binary_sha256': (snap_lines.get('agent_sha256') or [None])[-1],
    'built_at_utc': (snap_lines.get('built_at_utc') or [None])[-1],
    'note': '仓库由其他会话并发修改中；本轮以构建时刻的二进制快照为测试对象。'
            'hub 二进制与主轮/S7 复测一致（sha 相同）。',
}
s7v = read_json(RAW / 's7-verification.json', {}) or {}
if s7v:
    panels = s7v.get('panels', {})

    def pick(panel, ep):
        s = (panels.get(panel) or {}).get(ep) or {}
        return {'p50_ms': s.get('p50'), 'p95_ms': s.get('p95'), 'max_ms': s.get('max')}
    results['s7_controlled_verification'] = {
        'design': s7v.get('design'),
        'history_executions': s7v.get('history_executions'),
        'one_node': {ep: pick('one_node', ep) for ep in
                     ('nodes_list', 'node_detail', 'executions_list', 'execution_detail', 'healthz', 'readyz')},
        'eight_nodes_quiet': {ep: pick('eight_nodes', ep) for ep in
                              ('nodes_list', 'node_detail', 'executions_list', 'execution_detail', 'healthz', 'readyz')},
        'eight_nodes_post_burst': {ep: pick('eight_nodes_post_burst', ep) for ep in
                                   ('nodes_list', 'node_detail', 'executions_list', 'execution_detail', 'healthz', 'readyz')},
        'verdict': '8 节点 vs 1 节点：列表端点 p50 0.82→1.64ms（+0.8ms，随返回体量增长），'
                   '详情/健康检查 ≤0.8ms 基本持平；无规模性退化。',
    }
results['findings'] = {'new_defects': 0, 'observations': 4,
                       'file': 'findings.json'}
results['limitations'] = [
    '纯本地回环（127.0.0.1）单机 8 agent，不覆盖跨主机网络、NAT、TLS、真实时钟漂移。',
    'hub 二进制未内置 agent 载荷（未跑 packaging），未覆盖 /api/agent/binary 分发与自升级。',
    '测试对象为并发会话在途修改的源码快照（见 source_snapshot）；仓库仍在变动，结论仅对该二进制快照成立。',
    '/tmp 为配额 tmpfs 且配额已满，构建改在 /var/tmp（仓库 web/dist 与产物未改动）。',
    'S2 观察窗口 185s；S4 使用 interval=20s 观察 2 个触发周期。',
]
write_json(RUN / 'scale-results.json', results)

# ------------------------------------------------------- enrich resource usage
usage = read_json(RUN / 'resource-usage.json', {}) or {}
proc = usage.get('processes', {})
hub = proc.get('hub', {})
agents = {k: v for k, v in proc.items() if k.startswith('agent-')}
usage['summary'] = {
    'hub_peak_rss_mb': hub.get('peak_rss_mb'),
    'hub_peak_rss_note': '峰值出现在 16:07:50（启动/基线阶段堆增长），稳态 24-25MB',
    'hub_avg_cpu_pct': hub.get('avg_cpu_pct'),
    'hub_peak_interval_cpu_pct': hub.get('peak_interval_cpu_pct'),
    'hub_peak_interval_cpu_note': '0.5s 窗口尖峰 49.95%，出现在 16:11:43（S6 完成尾段）',
    'agent_peak_rss_mb_max': max((v.get('peak_rss_mb', 0) for v in agents.values()), default=None),
    'agent_count': len(agents),
    'host_peak_load1': (usage.get('host_load') or {}).get('peak_load1'),
    'host_min_mem_available_mb': round(((usage.get('host_load') or {}).get('min_mem_available_kb') or 0) / 1024.0, 1),
    'executions_total': (usage.get('hub_db') or {}).get('executions'),
    'execution_status': (usage.get('hub_db') or {}).get('exec_status'),
}
write_json(RUN / 'resource-usage.json', usage)

# --------------------------------------------------- phase-2 cleanup checks
def port_free(port, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            time.sleep(0.3)
        finally:
            s.close()
    return False


def port_connectable(port, timeout=1.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def leftover():
    """Processes really belonging to this run: our hub/agent binaries or agent
    configs under the owned base dir. (Shell wrappers are not run artifacts.)"""
    hits = []
    me = os.getpid()
    needles = ('/var/tmp/nodesteer-qa-20261018-scale/build/hub',
               '/var/tmp/nodesteer-qa-20261018-scale/build/agent',
               '/var/tmp/nodesteer-qa-20261018-scale/work/agent-',
               '/var/tmp/nodesteer-qa-20261018-scale/verify-s7/')
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == me:
            continue
        try:
            cmdline = Path('/proc/%s/cmdline' % entry).read_bytes().decode('utf-8', 'replace')
        except OSError:
            continue
        if any(n in cmdline for n in needles):
            hits.append({'pid': pid, 'cmdline': cmdline.replace('\x00', ' ').strip()[:200]})
    return hits


checks = []
left = leftover()
checks.append({'name': 'no_leftover_processes', 'ok': not left, 'detail': left})
checks.append({'name': 'hub_port_free', 'ok': port_free(HUB_PORT)})
checks.append({'name': 'hub_port_not_connectable', 'ok': not port_connectable(HUB_PORT)})
for d in (BASE / 'work', BASE / 'verify-s7', BASE / 'build'):
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    checks.append({'name': 'dir_removed_%s' % d.name, 'ok': not d.exists(), 'detail': str(d)})
if BASE.exists():
    shutil.rmtree(BASE, ignore_errors=True)
checks.append({'name': 'base_dir_removed', 'ok': not BASE.exists(), 'detail': str(BASE)})
foreign = [l for l in subprocess.run(['ls', '/var/tmp'], capture_output=True, text=True).stdout.splitlines()
           if 'nodesteer' in l]
checks.append({'name': 'foreign_nodesteer_dirs_info', 'ok': True,
               'detail': {'remaining': foreign,
                          'note': 'nodesteer-qa-20261018-soak 属于其他会话正在运行的 soak 任务，未触碰'}})
go_tmp = Path('/var/tmp/nodesteer-go-tmp')
if go_tmp.exists():
    shutil.rmtree(go_tmp, ignore_errors=True)
checks.append({'name': 'go_tmp_removed', 'ok': not go_tmp.exists()})
p13 = Path('/tmp/opencode/p13')
if p13.exists():
    subprocess.run(['rmdir', str(p13)], check=False)
checks.append({'name': 'tmp_p13_dir_gone', 'ok': not p13.exists(),
               'detail': 'tmpfs 用户配额已满，未写入任何文件'})
try:
    ss = subprocess.run(['ss', '-ltn'], capture_output=True, text=True, timeout=10).stdout
    listeners = [l for l in ss.splitlines() if ':%d' % HUB_PORT in l]
except Exception:
    listeners = []
checks.append({'name': 'no_listener_on_hub_port', 'ok': not listeners, 'detail': listeners})
try:
    find = subprocess.run(
        ['find', str(RUN.parents[4]), '-newermt', '@%d' % int(time.time() - 3600),
         '-type', 'f', '-not', '-path', '*/.git/*', '-not', '-path', '*qa-resume-20261018-scale*',
         '-not', '-path', '*/node_modules/*', '-not', '-path', '*/.ruff_cache/*',
         '-not', '-path', '*/playwright-report/*', '-not', '-path', '*/.tanstack/*'],
        capture_output=True, text=True, timeout=30).stdout
    outside = [l for l in find.splitlines() if l.strip()]
except Exception as exc:
    outside = ['scan_error:%s' % type(exc).__name__]
checks.append({'name': 'repo_write_scope_info', 'ok': True,
               'detail': {'modified_outside_run_dir_last_hour': outside[:50], 'count': len(outside),
                          'note': 'info-level；其他会话可能并发写入仓库'}})
artifacts = ['scale-results.json', 'findings.json', 'resource-usage.json']
checks.append({'name': 'deliverable_artifacts_present',
               'ok': all((RUN / a).is_file() for a in artifacts), 'detail': artifacts})

oracle = read_json(RUN / 'final-cleanup-oracle.json', {}) or {}
phase1_checks = oracle.get('checks', [])
phase1_ok = all(c['ok'] for c in phase1_checks)
oracle['phase1_main_run'] = {
    'verdict': 'PASS' if phase1_ok else 'FAIL',
    'checks': phase1_checks,
    'failed_checks': [c['name'] for c in phase1_checks if not c['ok']],
}
oracle['phase2_final'] = {
    'checked_at_utc': now_utc(),
    'context': '主轮 harness 清理 + S7 专项复测清理之后，最终复验（含目录销毁）',
    'checks': checks,
    'verdict': 'PASS' if all(c['ok'] for c in checks) else 'FAIL',
    'failed_checks': [c['name'] for c in checks if not c['ok']],
}
oracle['verdict'] = 'PASS' if (phase1_ok and oracle['phase2_final']['verdict'] == 'PASS') else 'FAIL'
oracle['note'] = 'phase1 = 主轮（8 节点规模测试）清理核验；phase2 = 全部活动结束后的最终核验。'
write_json(RUN / 'final-cleanup-oracle.json', oracle)

print('findings: new_defects=%d observations=%d' % (
    findings['summary']['new_defects'], findings['summary']['observations']))
print('oracle phase2:', oracle['phase2_final']['verdict'],
      oracle['phase2_final']['failed_checks'])
print('oracle overall:', oracle['verdict'])
