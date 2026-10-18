#!/usr/bin/env python3
"""合并本轮真实attempt；不改Web库存、不以源码推定运行PASS。"""
import collections
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def read(path):
    return json.loads((ROOT / path).read_text())


def save(path, data):
    (ROOT / path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


attempts = [json.loads(line) for line in (HERE / 'results.jsonl').read_text().splitlines()]
latest = {}
for result in attempts:
    result.setdefault('hub_url', 'http://192.168.100.209:18080' if '/isolated/' in result.get('evidence', '') else 'http://192.168.100.209:8080')
    latest[result['id']] = result


def ev(cid):
    return latest[cid]['evidence']


mapping = read('backend/web-system-mapping.json')
cases = {case['case_id']: case for case in mapping['cases']}


def dimension(name, status, evidence=(), actual='', remaining=''):
    return {'dimension': name, 'status': status, 'evidence': list(evidence), 'actual': actual, 'remaining': remaining}


def set_case(cid, status, dimensions, remaining=''):
    case = cases[cid]
    case.update(status=status, dimensions=dimensions, evidence=list(dict.fromkeys(path for d in dimensions for path in d['evidence'])), actual='；'.join(d['actual'] for d in dimensions if d['actual']), remaining=remaining)


# 已存在PASS的证据逐份拆开，复合剩余维度始终保留。
for case in cases.values():
    previous = case['status']
    dims = []
    for path in case['evidence']:
        if not (ROOT / path).exists():
            dims.append(dimension('原关联证据缺失', 'NOT_RUN', actual='', remaining=path))
            continue
        source = read(path)
        if isinstance(source, dict) and 'result' in source:
            result = source['result']
            status = {'passed': 'PASS', 'failed': 'FAIL', 'blocked': 'BLOCKED'}.get(result['status'], 'NOT_RUN')
            dims.append(dimension(result.get('name', result['id']), status, [path], result.get('reason') or ('真实API、Agent状态与客户机断言通过: ' + result['id'])))
        else:
            dims.append(dimension('主控/环境运行证据: ' + path, 'PASS' if previous == 'PASS' else 'PARTIAL', [path], '参照该运行证据中的逐项断言；复合结果另列，不以摘要代替运行。'))
    if case['remaining']:
        dims.append(dimension('尚缺维度', 'BLOCKED' if case['case_id'] == 'SYS-24' else 'NOT_RUN', remaining=case['remaining']))
    case['dimensions'] = dims
    if previous == 'BLOCKED' and case['case_id'] != 'SYS-24':
        case['status'] = 'PARTIAL' if case['evidence'] else 'NOT_RUN'

set_case('SYS-18', 'PARTIAL', [
    dimension('Heartbeat运行时配置和周期', 'PASS', ['main/settings-runtime-evidence.json'], '真实UI heartbeat30→3→30；4Agent实测3～4秒、共享设置恢复。'),
    dimension('Revision定时自愈', 'PASS', [ev('arch.lost_notification')], '通知真实丢弃后45秒Revision定时检查收敛。'),
    dimension('重连backoff/jitter分布', 'NOT_RUN', remaining='尚未连续采样多次网络失败的退避和抖动分布。')], '重连backoff/jitter完整统计未测。')

dup = dimension('相同ExecutionID运行中/终态重复下发', 'PASS', [ev('feature.execution.duplicate-id')], '透明代理即时重复原RUN_EXECUTION、4秒后原样重放同ID；客户机一行，Hub/Journal各一条记录。')
set_case('SYS-31', 'PASS', [dup, dimension('结果重传与ACK幂等', 'PASS', [ev('arch.duplicate_execution_upload')], '终态重新上传后无重复Execution/效果。')])
set_case('INV-07', 'PASS', [dup, dimension('相同Schedule Slot Key', 'PASS', [ev('arch.duplicate_schedule_trigger')], '保卷重建重复Slot不重复实际执行。'), dimension('结果上传', 'PASS', [ev('arch.duplicate_execution_upload')], '恢复重传只有一条结果。')])

onstart = dimension('on_start进程启动、网络重连、禁用', 'PASS', [ev('feature.schedule.onstart-network-disabled'), ev('feature.schedule.on_start')], '仅新进程启动产生Slot；只断开WS后PID不变、新WS/任务成功且无新Slot；disabled保存后重启没有新Slot。')
set_case('X03', 'PASS', [onstart])
set_case('E2E-07', 'PARTIAL', [onstart, dimension('删除Tombstone收敛', 'PASS', [ev('feature.sync.tombstone')], '离线删除script/task/schedule，重连后本地行移除。'), dimension('Interval/Cron禁用持续观察', 'NOT_RUN', remaining='尚未独立跨多个未来Slot观察全部周期类型禁用。')], 'Interval/Cron禁用多Slot持续观察尚缺。')
if latest.get('feature.schedule.disable-delete', {}).get('status') == 'passed':
    disabled = dimension('三类Schedule双owner禁用和未来slot停止', 'PASS', [ev('feature.schedule.disable-delete')], 'Agent/Hub两个owner的Interval/Cron/OneTime disabled跨70秒/分钟边界/45秒Hub tick无Execution或marker；Interval重启用真实恢复，DELETE后跨两个未来Slot无新增。')
    set_case('E2E-07', 'PASS', [disabled, onstart])
    cases['SYS-09']['dimensions'].append(disabled)
    cases['SYS-09']['evidence'] = list(dict.fromkeys(cases['SYS-09']['evidence'] + disabled['evidence']))

conditions = ev('feature.condition.unknown-nested-offline')
set_case('E2E-09', 'PASS', [dimension('本地满足/False/UNKNOWN', 'PASS', [ev('condition.false'), conditions], '真实命令条件成功执行/False SKIPPED；命令exit3为BLOCKED且无marker。')])
set_case('SYS-26', 'PASS', [dimension('AND合法组合及多层递归', 'PASS', [ev('feature.condition.operators-and-remote'), conditions], '真/假/未知组合、3层AND真实状态与效果一致；空AND/非法子类型API400。')])
set_case('SYS-28', 'PASS', [dimension('last_execution SUCCESS/FAILED/无历史', 'PASS', [ev('feature.condition.operators-and-remote'), conditions, ev('condition.remote-unknown')], 'SUCCESS与FAILED条件都真实成功；FAILED不相等SKIPPED，无历史BLOCKED且无效果。')])
for cid in ['E2E-10', 'SYS-29', 'INV-09']:
    set_case(cid, 'FAIL', [dimension('无历史/离线FailClosed', 'PASS', [conditions, ev('condition.remote-unknown')], '无历史、Hub断线Agent-owned触发均BLOCKED/no effect；恢复后上传ACK。'), dimension('过期RemoteState', 'FAIL', [ev('feature.condition.remote-ttl')], 'ObservedAt2000/TTL1仍被Agent接受并SUCCESS，客户机marker真实出现；BE-REMOTE-TTL-004。')])

pg_native = dimension('Native忽略TERM后代cancel与timeout', 'PASS', [ev('feature.process.native')], '两种路径的父/孙PID在host init收割后/proc完全不存在。')
pg_docker = dimension('Docker忽略TERM后代', 'PARTIAL', [ev('arch.process_group_cleanup')], '两种路径没有活进程；每种路径2个Z态由容器PID1未收割，原始stat保留。', 'Z态不等同运行泄漏；“无任何子孙残留”复合要求未全满足。')
for cid in ['SYS-38', 'SYS-39', 'AR-23']:
    set_case(cid, 'PARTIAL', [pg_native, pg_docker], 'Docker两条路径仍观察到Z态；Native全部PID收割已通过。')
set_case('E2E-08', 'PARTIAL', [dimension('retry次数与终态', 'PASS', [ev('task.retry'), ev('task.timeout'), ev('task.cancel')], 'retry2真实三次、单Execution，timeout/cancel终态及客户机效果核实。'), pg_native, pg_docker], 'Docker Z态观察不代表活进程泄漏，但复合无残留尚未全通过。')

relay = 'main/relay-edge-results.json'
set_case('SYS-59', 'PASS', [dimension('Source身份精确授权', 'PASS', [relay, 'environment/tls-full-results.json'], 'wrong-source403；真实Source身份上传，通过HTTPS证书校验并实际SHA一致。')])
set_case('SYS-60', 'PASS', [dimension('Target身份及Stage门禁', 'PASS', [relay, 'environment/tls-full-results.json'], 'stage前所有Agent下载404；stage后Source/Other403、Target200 bytes一致。')])
set_case('SYS-61', 'PARTIAL', [dimension('Stage元数据/Range/部分上传与续传', 'PASS', [relay], '非法size/range/contentlength拒绝；chunked part13/blob不存在/SHA空，正确offset续传真SHA；重复上传保原SHA。'), dimension('Hub重启时Stage原子提升', 'NOT_RUN', remaining='未在Stage原子提升边界重启Hub。')], 'Hub Stage原子提升精确故障窗口尚缺。')
set_case('SYS-62', 'PARTIAL', [dimension('真实多目标与Native symlink旧文件保护', 'PASS', [relay, ev('feature.transfer.multitarget-retry')], '部分目标失败不影响其他成功；Native目标symlink拒绝、old sentinel未变、tmp无残留。'), dimension('目标接收中断/替换边界故障', 'NOT_RUN', remaining='未在目标下载/AtomicRename窗口终止Agent。')], '目标中途故障与原子替换窗口尚未精确注入。')
set_case('SYS-54', 'PARTIAL', [dimension('真实Host Integration inventory与挂载映射', 'PASS', ['environment/hostintegration-preflight.json', 'environment/hostintegration-application.json'], 'host PID/network、最小caps与只读proc/sys/os-release及精确可写allowlist真实配置；正确nsenter命令只读探针exit0。'), dimension('HostOS/能力所有字段独立断言', 'NOT_RUN', remaining='摘要不等于逐字段Node inventory和host本体OS/capability比对；待Root/Env实际证据合入。')], '完整HostOS/capability字段比对证据待主控整合；HostDeploy失败另列。')

if latest.get('feature.capability.host-operation', {}).get('status') == 'passed':
    caps = dimension('BasicDocker应用HostOperation及Task缺cap过滤', 'PASS', [ev('feature.capability.host-operation'), ev('feature.node.docker-upgrade')], '真实Docker deploy/upgrade/start/stop/restart及app_deploy/app_operation API缺cap400；没有RUN_EXECUTION/本地Journal、binary/unit；Agent升级SKIPPED、原二进制SHA不变。')
    for cid in ['SYS-58', 'AR-33', 'INV-16']: set_case(cid, 'PASS', [caps])
storage = 'main/native-storage-edge-results.json'
if (ROOT / storage).exists():
    set_case('SYS-52', 'PARTIAL', [dimension('磁盘不足/只读/Native权限失败', 'PASS', [storage], '独占4KiB tmpfs ENOSPC、只读tmpfs EROFS、nobody到0700目录Permission denied；实际FAILED、文件/Unit/tmp无残留且Agent active。'), dimension('Backup丢失独立恢复故障', 'NOT_RUN', remaining='缺失Backup独立注入未做；PREPARING丢文件反例另列INV11。')], '独立Backup缺失明确失败/恢复路径故障仍未注入。')
    set_case('SYS-53', 'PARTIAL', [dimension('Native路径/User/权限故障', 'PASS', [storage], 'nobody写0700拒绝、不存在user明确FAILED；../binary_path Hub400且无definition/客户机效果。'), dimension('Native文件模式/chown/group全部组合', 'NOT_RUN', remaining='完整文件mode、group/chown负向与回滚组合仍未实测。')], 'mode/group/chown完整组合未覆盖。')
backup = 'main/backup-missing-result.json'
if (ROOT / backup).exists():
    set_case('SYS-52', 'PASS', [dimension('磁盘不足/只读/权限失败', 'PASS', [storage], '真实ENOSPC/EROFS/Permission denied明确FAILED、无文件/Unit/tmp残留、Agent active。'), dimension('应用Backup缺失明确失败', 'PASS', [backup], 'VERIFYING删除唯一该App的binary backup，健康失败后Execution FAILED/unhealthy/rollback health failed；未误SUCCESS、Unit不active。旧binary物理不可恢复事实保留。')])
hostneg = 'environment/host-negative-results.json'
if (ROOT / hostneg).exists():
    rejected_paths = dimension('HostTraversal/Allowlist/Symlink拒绝', 'PASS', [hostneg], '真实Host ../、allowlist外路径和symlink拒绝，宿主旧sentinel不变；精准本轮fixture清理。')
    set_case('SYS-56', 'PASS', [rejected_paths, dimension('Native traversal提前API拒绝', 'PASS', [storage], '../binary_path POST400，无definition/客户机效果。')])
    set_case('AR-34', 'PASS', [rejected_paths])
    mount_bad = dimension('Host缺失/错误映射', 'FAIL', [hostneg], 'ENV-HOSTNEG-MISSING/WRONG两条真实反例：缺失或错误宿主映射仍报告传输SUCCESS并写容器路径，宿主目标未更新；FAIL-ENV-HOST-002。')
    mount_readonly = dimension('Host只读映射', 'PASS', [hostneg], '只读挂载真实拒绝，宿主旧文件保留。')
    set_case('SYS-55', 'FAIL', [mount_bad, mount_readonly])
    set_case('AR-31', 'FAIL', [mount_bad, mount_readonly])
    set_case('AR-32', 'PARTIAL', [dimension('Host缺cap与只读拒绝', 'PASS', [hostneg], '真实缺cap/只读拒绝，无HostOperation效果。'), dimension('HostAdapter全部permission组合', 'NOT_RUN', remaining='独立宿主文件owner/group拒绝组合未全部验证；nsenter126问题另列。')], '独立宿主owner/group权限组合未完整实测。')

native_app = dimension('Native应用正常闭环和Operator效果', 'PASS', [ev('feature.application.lifecycle'), ev('feature.application.operator-deploy')], '首次Operator部署、4种health、升级失败回滚、task/计划操作均验证真实Unit/bin/config与API/Journal；精确删除。')
host_app = dimension('Host Integration应用部署', 'FAIL', ['environment/hostintegration-preflight.json', 'environment/hostintegration-application.json'], '实际install unit exit126；nsenter -r分离参数被作为可执行路径，FAIL-ENV-HOST-001。')
for cid in ['SYS-45', 'AR-26']: set_case(cid, 'FAIL', [native_app, host_app])
crash_dims = [dimension('Native升级' + phase + '崩溃恢复', 'PASS', ['main/native-results.json'], phase + '真实定点kill，原v1 binary/config/unit恢复并active。') for phase in ['REPLACED', 'STARTED', 'VERIFYING', 'ROLLING_BACK']]
crash_dims += [dimension('Native升级PREPARING崩溃', 'FAIL', ['main/native-results.json'], '实际Journal三个BackupPath为空，kill后DONE但原binary/config/unit全部不存在，ExecutionFAILED agent_restarted；P0恢复路径删除旧文件。'), dimension('STOPPED阶段', 'BLOCKED', ['main/native-results.json'], remaining='当前实现从未写STOPPED，不能伪造阶段；其他实际阶段已执行。')]
for cid in ['E2E-13', 'SYS-51', 'AR-30', 'INV-11']: set_case(cid, 'FAIL', crash_dims)

if latest.get('feature.transfer.offline-restart', {}).get('status') == 'passed':
    set_case('SYS-66', 'PASS', [dimension('离线精确Target旧文件和Hub重启Stage恢复', 'PASS', [ev('feature.transfer.offline-restart')], '授权generatedDocker目标停机：源实际512KiB+标记上传/Hub Stage SHA与size一致、目标旧文件保留；coreHub真实stop/start后Stage持久；原目标原卷启动收到完整SHA且无.tmp，identity不变online/synced；精确取消传输与fixture清理。')])

for cid, feature in [('SYS-33', 'feature.execution.journal-write-failure'), ('INV-08', 'feature.execution.journal-write-failure'), ('SYS-10', 'feature.sync.commit-failure-notify'), ('INV-17', 'feature.sync.commit-failure-notify')]:
    if feature in latest and latest[feature]['status'] == 'passed':
        actual = '真实15秒BEGIN IMMEDIATE纯锁、busy_timeout后写失败；无schema/data写入，finally ROLLBACK；释放后同对象完成。'
        if feature.endswith('journal-write-failure'): actual += '同ID锁内无Journal/marker、明确sqlite busy日志，释放后受控同ID重放单记录/效果/ACK。'
        else: actual += '失败API明确SQLiteBusy、GlobalRevision/脚本不变且无Notify；释放后同脚本成功收敛/运行。'
        set_case(cid, 'PASS', [dimension(feature, 'PASS', [ev(feature)], actual)])

# 失败反例必须保留，其他输入PASS不能覆盖。
assert cases['INV-05']['status'] == 'FAIL' and cases['INV-09']['status'] == 'FAIL' and cases['INV-11']['status'] == 'FAIL'
mapping['updated_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
mapping['counts'] = dict(collections.Counter(c['status'] for c in cases.values()))
mapping['cases'] = list(cases.values())
save('backend/web-system-mapping.json', mapping)

inventory = read('backend/case-inventory.json')
arch_ids = [c['id'] for c in inventory['cases'] if c['id'].startswith('arch.')][:35]
for i, cid in enumerate(arch_ids, 1):
    if cid not in latest:
        c = next(x for x in inventory['cases'] if x['id'] == cid); m = cases[f'AR-{i:02}']
        c.update(status=m['status'].lower(), evidence=m['evidence'], actual=m['actual'], remaining=m['remaining'])
for c in inventory['cases']:
    if c['id'].startswith('invariant.'):
        m = cases['INV-' + c['id'].split('.')[1]]
        c.update(status=m['status'].lower(), evidence=m['evidence'], actual=m['actual'], remaining=m['remaining'])
    elif c['id'] in latest:
        result = latest[c['id']]; c.update(status=result['status'], last_result=result, evidence=[result['evidence']])
    elif c.get('status') == 'pending':
        c.update(status='not_run', remaining='尚无本case完整真实运行证据；相关维度仅按外部实际证据部分合并。')
aliases = {
    'feature.node.enrollment': ('partial', ['environment/agent-install-results.json', 'environment/extra-agent-results.json', 'main/api-contract-results.json'], 'Native/Docker/Compose/架构/待纳管边界实测；重新纳管状态缺陷另列。'),
    'feature.node.upgrade': ('partial', ['main/upgrade-ui-result.json', ev('feature.node.docker-upgrade')], 'Native真实UI升级与Docker过滤；混合批/部分失败/暂停离线全组合未全部实测。'),
    'feature.application.task': ('passed', [ev('feature.application.operator-deploy')], 'Operator首次部署、app_deploy Task、app_operation及Agent-owned schedule真实效果。'),
    'feature.settings.runtime': ('partial', ['main/settings-runtime-evidence.json', 'main/api-results.json'], '心跳真实UI下发/恢复/校验；所有设置的重连继承与运行效果未逐项闭环。'),
    'feature.auth.oidc': ('passed', ['main/sso-results.json', 'main/idp-recovery-result.json'], '真实OIDC三角色、模式/state与本地回退，IdP失败恢复由主控核验。'),
    'feature.condition.and-remote': ('failed', [ev('feature.condition.operators-and-remote'), conditions, ev('feature.condition.remote-ttl')], '合法比较符、AND/递归/未知/SUCCESS/FAILED/离线已补；过期TTL真实FailClosed缺陷。'),
}
for cid, (status, evidence, actual) in aliases.items():
    c = next(x for x in inventory['cases'] if x['id'] == cid); c.update(status=status, evidence=evidence, actual=actual, remaining='' if status in {'passed', 'failed'} else actual)
for c in inventory['cases']:
    c['status'] = {'pass': 'passed', 'fail': 'failed'}.get(c['status'], c['status'])
    if c['id'] == 'arch.process_group_cleanup':
        c.update(status='partial', evidence=[ev('arch.process_group_cleanup'), ev('feature.process.native')], actual='Native cancel/timeout后全部PID消失；Docker无活进程但有Z态，不能以PID存在断言运行泄漏。', remaining='Docker PID1未收割Z态观察。')
inventory['counts'] = dict(collections.Counter(c['status'] for c in inventory['cases']))
inventory['updated_at'] = mapping['updated_at']
save('backend/case-inventory.json', inventory)

superseded = [{**r, 'resolution': 'superseded/retested-pass; 原harness或注入前提错误，不计未修复产品缺陷'} for r in attempts if r['status'] in {'failed', 'blocked'} and latest[r['id']]['status'] == 'passed']
summary = {'run_id': ROOT.name, 'updated_at': mapping['updated_at'], 'unique_executed_cases': len(latest), 'counts': dict(collections.Counter(r['status'] for r in latest.values())), 'results': list(latest.values()), 'superseded_attempts': superseded, 'inventory_counts': inventory['counts'], 'system_mapping_counts': mapping['counts'], 'known_product_failures': read('backend/failures.json')['failures'], 'cleanup': {'primary_ledger': 'backend/ledger.json', 'core_ledger': 'backend/isolated/ledger.json', 'polluted_execution': 'backend/isolated/pollution-cancel-result.json'}}
save('backend/all-suite-summary.json', summary)
normal = [r for r in latest.values() if r['hub_url'].endswith(':8080')]
old_normal = read('backend/normal-summary.json')
old_normal.update(updated_at=mapping['updated_at'], unique_cases=len(normal), counts=dict(collections.Counter(r['status'] for r in normal)), results=normal, superseded_attempts=[r for r in superseded if r['hub_url'].endswith(':8080')])
primary_ledger = read('backend/ledger.json')
old_normal.update(normal_active_schedules=[row['id'] for row in primary_ledger if row['type'] == 'schedule' and row['status'] == 'created'], primary_cleanup_ids=[{'type': row['type'], 'id': row['id']} for row in primary_ledger if row['status'] == 'created'], retained_history_ids=[{'type': row['type'], 'id': row['id']} for row in primary_ledger if row['status'] == 'retained-history'], core_window='backend已完成补测并恢复canonical URL/identity/settings；proxy与纯锁进程清理；core容器/卷交Root/Env最终清理', cleanup_evidence=['backend/primary-cleanup-result.json', 'backend/primary-stage-cleanup-result.json', 'backend/isolated/cleanup-result.json', 'backend/isolated/cleanup-reconciled-definitions.json'], isolated_interrupted_ledger={'evidence': 'backend/isolated/pollution-cancel-result.json', 'execution_id': '8b125d68-63fd-4014-8102-b1a8b21a3bc4', 'status_after_exact_cancel': 'RUNNING', 'agent_journal_rows': 0, 'classification': '环境同IP身份修复期间污染残留；保留历史/来源，不直接改DB，不混入产品通过率'})
save('backend/normal-summary.json', old_normal)
print('real_unique', len(latest), summary['counts'], 'mapping', mapping['counts'], 'inventory', inventory['counts'])
