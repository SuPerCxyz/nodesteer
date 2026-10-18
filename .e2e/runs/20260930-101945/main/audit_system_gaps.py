"""核对已有真实证据和未覆盖维度；不访问客户机、不把静态核对计作实测。"""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT.parent
PROJECT = RUN.parents[2]

# 补充的是已保存实证。每个复合场景仍保留尚缺的实际运行维度。
SUPPLEMENT = {
    'SYS-43': (['main/cache-recovery-result.json', 'environment/tls-full-results.json'],
               '原生缓存SHA损坏后真实重下并部署正确SHA；TLS私有CA失败时cache/tmp均不存在。',
               '明确下载timeout与HTTP非200的完整tmp清理组合；不能用TLS错误替代HTTP错误。'),
    'SYS-53': (['main/native-storage-edge-results.json'],
               'nobody写0700拒绝、未知用户FAILED、路径穿越Hub400；原生ENOSPC/EROFS安装失败且tmp和Unit无残留。',
               '成功文件的mode、uid/gid、chown/chgrp及权限拒绝组合。'),
    'SYS-54': (['environment/hostintegration-application.json', 'environment/host-negative-results.json'],
               '真实Host Node声明的七项capability已保存；正确bind写到真实宿主SHA，缺失/错误bind假成功，RO映射拒绝且sentinel不变。',
               '宿主OS、CPU、内存、磁盘等完整Inventory与宿主实值逐字段比对。'),
    'SYS-61': (['main/relay-edge-results.json'],
               '13字节part未提升、blob不存在、所有角色下载404；续传真SHA；重复上传保持原SHA。',
               'Hub staged原子提升前后精确故障窗口、持久化与重启恢复。'),
    'SYS-62': (['main/relay-edge-results.json', 'environment/host-negative-results.json'],
               'Native symlink拒绝保old sentinel且tmp空；Host RO拒绝保原SHA，正确bind物理SHA与源一致。',
               '目标下载中途断连以及SHA校验至rename原子替换窗口的精确故障注入。'),
    'X01': (['main/bulk-upgrade-results.json', 'main/upgrade-ui-result.json'],
            '8条真实API组合覆盖维护/禁用/混合Docker过滤、重复在途同UUID、离线部分失败、ID/版本/sync与精确清理；单点真实UI确认和取消已测。',
            '批量UI完整确认到结果反馈闭环；API结果不替代该UI维度。'),
    'AR-27': (['main/cross-fs-result.json', 'main/cache-recovery-result.json', 'main/backup-missing-result.json', 'main/native-results.json'],
              'Native跨文件系统升级、损坏缓存恢复、缺失backup明确失败、多阶段crash恢复已实测；PREPARING缺陷保留。',
              'Host升级被已确认部署exit126缺陷阻断；不能改产品或绕过nsenter宣称通过。'),
    'AR-32': (['main/native-storage-edge-results.json', 'environment/host-negative-results.json'],
              'Native用户/私有目录和RO文件系统拒绝；Host RO、allowlist、symlink、缺cap均保存真实效果证据。',
              '独立宿主owner/group与文件权限的完整组合。'),
    'INV-02': (['main/agent-outbound-sockets.json'],
               '四Native实际Agent进程Socket均主动连Hub8080且无TCP listener。',
               'Docker/Host等其余拓扑的实际进程Socket和反向监听核验；正常连接日志不能证明无监听。'),
    'INV-13': (['environment/hostintegration-application.json', 'environment/host-negative-results.json'],
               '真实HostCore纳管在线synced且capability已记录，HostAdapter错误已复现并单列。',
               '完整HostIntegration与Native/Docker相同任务/离线调度等共享Core行为链路，不能由代码共用直接推定。'),
    'INV-18': (['environment/tls-full-results.json'],
               '已记录HTTPS Artifact API与多目标Relay独立HTTP请求、真实物理SHA、WSS连接；Prefetch私有CA失败单列。',
               '全部payload种类与控制长连接的完整流量分离抓包；TLS已测，不再列为未执行。'),
}

NEXT = {
    'E2E-08': '恢复授权隔离Docker测试环境，分别检查timeout/cancel实际PID与Z态、SIGTERM/SIGKILL和日志；不能把Z态说成活进程泄漏。',
    'SYS-18': '专属代理或专属新Agent断连，采样真实重连间隔/backoff和jitter分布，不重启原Hub/Agent1。',
    'SYS-27': '仅专属新peer离线，真实remote-online条件不满足，断言终态和业务marker不产生，再恢复peer。',
    'SYS-34': '专属Task交错写stdout/stderr，逐chunk核对stream、sequence与完整内容，保留terminal对照。',
    'SYS-38': '同E2E-08，补Docker PID1收割语义和SIGTERM到SIGKILL时间证据。',
    'SYS-39': '同E2E-08，补Docker cancel后父/孙进程、Z态和重复cancel最终状态。',
    'SYS-40': '专属确定性Retry任务逐尝试核对日志/间隔/同Execution UUID/审计，避免把最终输出当逐次日志。',
    'SYS-43': '专属下载代理分别超时和非200，核对cache与所有tmp及重新尝试，精确清理。',
    'SYS-53': 'Native3只用本轮目录和真实nobody/现有group验证mode/uid/gid/chown；不改全局用户或既有文件权限。',
    'SYS-54': '专属HostIntegration容器只读Inventory与宿主字段对照，不改变宿主设置。',
    'SYS-61': '专属隔离Hub staged写入/rename窗口注入，保留part/blob/metadata与重启后对照；禁止故障原Hub。',
    'SYS-62': '专属目标路径/文件传输，精确控制断连与rename窗口，断言old/new SHA和tmp残留。',
    'X01': '协同Web执行批量真实确认、terminal反馈与版本重连，禁止与Root升级suite并发。',
    'AR-23': '同E2E-08，只测试本轮Docker进程，不终止其他进程。',
    'AR-27': 'Host部署exit126为既有产品阻断，本轮仅报告，不修复产品；Native已测事实保留。',
    'AR-32': '专属HostIntegration测试目录，仅变更测试文件owner/group/mode，对照宿主原文件不变。',
    'INV-01': '专属新Agent local store只改本轮Task/Script副本，真实Hub reconcile后与权威revision/SHA一致；禁止改Hub DB。',
    'INV-02': '对专属Docker/Host Agent只读其实际进程fd sockets，检查主动Hub连接和无反向listener。',
    'INV-13': '专属Host Agent跑与Native/Docker同一短任务和离线调度对照，不能绕过已确认HostAdapter错误。',
    'INV-18': '在专属代理记录全部payload种类HTTP及控制WS metadata；不记录凭据或业务secret。',
}

mapping = json.loads((RUN / 'backend/web-system-mapping.json').read_text())
jitter_path = RUN / 'environment/jitter/sys18-jitter.json'
if jitter_path.exists() and json.loads(jitter_path.read_text()).get('sampling_status') == 'PASS':
    SUPPLEMENT['SYS-18'] = (
        ['environment/jitter/sys18-jitter.json', 'environment/jitter/reconnect-syn.txt', 'main/jitter-independent-check.json'],
        '独占dummy Agent真实57 SYN/56间隔覆盖2.085～3.992秒重连抖动，Root独立重算；原进程/数据/身份保持，自己的进程/目录已清理。',
        'Revision周期jitter及改值周期消费的独立采样；本dummy采样不证明成功HELLO，既有Hub重连证据需另关联。',
    )
    NEXT['SYS-18'] = '重连delay/jitter已完成，不重复。后续需独立测试authority采样Revision周期jitter/改值，不改变原Hub/Agent配置或共用设置。'
inventory = json.loads((PROJECT / '.e2e/feature-inventory.json').read_text())
cases = {row['case_id']: row for row in inventory['cases']}
rows = []
for row in mapping['cases']:
    if row['status'] != 'PARTIAL':
        continue
    key = row['case_id']
    refs, covered, remaining = SUPPLEMENT.get(key, ([], '', row.get('remaining', '')))
    evidence = list(dict.fromkeys(row.get('evidence', []) + refs))
    rows.append({
        'case_id': key, 'title': cases[key]['title'], 'status': 'PARTIAL',
        'classification': 'EXISTING_REAL_EVIDENCE_AUDIT',
        'prior_actual': row.get('actual', ''), 'additional_covered': covered,
        'remaining_dimensions': remaining, 'evidence': evidence,
        'next_live_test': NEXT[key],
        'execution_blocker': '当前沙箱连接拒绝Errno1且私密连接资料已消失；未执行上述下一步。',
    })

def resolve(ref):
    p = Path(ref)
    return p if p.is_absolute() else (PROJECT / p if ref.startswith('.e2e/') else RUN / p)

missing = [{'case_id': row['case_id'], 'path': ref} for row in rows for ref in row['evidence'] if not resolve(ref).exists()]
assert not missing, missing
output = {
    'run_id': RUN.name, 'time': datetime.now(timezone.utc).isoformat(),
    'scope': '系统当前PARTIAL现证核验与下一次真实运行计划，不是新实时测试',
    'counts': dict(Counter(row['status'] for row in rows)),
    'status_changes': 0, 'missing_evidence': missing, 'cases': rows,
    'delegation': 'backend会话续派被平台用量限制拒绝，主Agent按客观例外完成此审计。',
}
(ROOT / 'remaining-system-dimensions.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'cases': len(rows), 'new_runtime_tests': 0, 'status_changes': 0, 'missing_evidence': len(missing)}, ensure_ascii=False))
