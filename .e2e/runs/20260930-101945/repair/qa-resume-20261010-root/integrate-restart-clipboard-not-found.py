"""仅汇入已核验的 E2E 证据，不修改产品代码或基线结果。"""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/superc/code/nodesteer')
BASE = Path('.e2e/runs/20260930-101945/repair')
REVIEW = BASE / 'qa-resume-20261010-root/restart-clipboard-not-found-review.json'
STAMP = datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads((ROOT / path).read_text())


def save(path, value):
    (ROOT / path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


clip_path = BASE / 'qa-resume-20261010-web/live-log/clipboard-cold/clipboard-cold-result.json'
restart_path = BASE / 'qa-resume-20261010-runtime/ui-native3/restart/manual-attempt01/case-result.json'
restart_detail = BASE / 'qa-resume-20261010-runtime/ui-native3/restart/manual-attempt01/restart-evidence.json'
nf_path = BASE / 'qa-resume-20261010-api/not-found/results.json'
clip, restart, nf = read(clip_path), read(restart_path), read(nf_path)
detail = read(restart_detail)
assert clip['outcome']['copy']['exact_equal']
assert all(clip['outcome']['stream_filters'][k] for k in ['stdout', 'stderr', 'all'])
assert clip['cleanup']['dedicated_logout_old_me_401'] and clip['cleanup']['listener_gone']
assert restart['durability']['local_revision_before'] == restart['durability']['local_revision_after'] == 1400
assert restart['cleanup']['helper_task_get404'] and detail['overall_restart_local_and_api_success']
assert nf['all_resource_counts_and_ids_unchanged'] and len(nf['items']) == 8
assert all(x['pre_get']['status'] == x['post_get']['status'] == 404 for x in nf['items'])
assert all(x['delete_matches_expected'] for x in nf['items'] if 'delete_matches_expected' in x)

review = {
    'reviewed_at': STAMP, 'reviewer': 'Root', 'scope': '既有22缺陷/504场景继续，证据汇入',
    'clipboard': {'exact_readback_and_filters': 'PASS', 'bytes': 1920,
                  'sha256': clip['outcome']['copy']['clipboard_sha256'],
                  'deny_feedback': 'PARTIAL: 旧toast与新反馈未区分，拒绝后未读clipboard',
                  'evidence': str(clip_path), 'cleanup_verified': True},
    'restart': {'identity_and_revision_persistence': 'PASS', 'revision': 1400,
                'child_pid': 49873, 'child_startticks': 137980573,
                'heartbeat_packet': 'UNVERIFIED', 'commit_call_failure': 'UNVERIFIED',
                'evidence': [str(restart_path), str(restart_detail)]},
    'not_found': {'eight_resource_matrix': 'PASS', 'canonical': 'PARTIAL',
                  'reason': '需核对并补足Execution等剩余适用对象；不外推所有资源已通过',
                  'collections_unchanged': True, 'audit_delta': 6, 'revision_delta': 5,
                  'history_side_effects_expected': True, 'evidence': str(nf_path)},
    'no_product_source_changed': True, 'no_git_or_archive': True,
}
save(REVIEW, review)
inventory = read(BASE / 'feature-inventory.json')
cases = {x['case_id']: x for x in inventory['cases']}
assert len(cases) == len(inventory['cases']) == 504


def dimension(case_id, test_id, description, evidence):
    row = cases[case_id]
    dims = row.setdefault('covered_dimensions', [])
    dims[:] = [x for x in dims if not (isinstance(x, dict) and x.get('test_id') == test_id)]
    dims.append({'test_id': test_id, 'description': description, 'status': 'PASS',
                 'layer': 'real-ui-api-agent' if case_id == 'X15' else 'real-api-agent',
                 'evidence': [str(x) for x in evidence], 'reviewed_at': STAMP,
                 'root_review': str(REVIEW)})
    row['evidence'] = list(dict.fromkeys(row.get('evidence', []) + [str(x) for x in evidence] + [str(REVIEW)]))
    row['updated_at'] = STAMP


dimension('X15', 'WEB-20261010-CLIPBOARD-COLD',
          '真实Copy按钮→系统clipboard readText精确1920bytes/SHA相同；stdout/stderr/all均与API一致；拒绝新反馈未验证。', [clip_path])
cases['X15']['remaining_dimensions'] = ['真实PENDING UI尚未采样。',
    'clipboard拒绝后的新反馈及业务效果未证实，冷页补测中。',
    'API/UI没有retry attempt元数据/分组，不从重复日志推断；broader维度仍未验证。']
dimension('INV-05', 'RUNTIME-20261010-NATIVE4-REVISION-RESTART',
          '成功同步后精确owned child重启，本地Revision1400和Node/Agent身份保持、SQLite quick_check正常，新连接ACK applied1400。', [restart_path, restart_detail])
cases['INV-05']['remaining_dimensions'] = ['CommitSync调用自身失败仍未执行；SQLITE_BUSY不是提交失败。']
cases['INV-05']['actual'] += '；成功后owned child真实重启保留Revision1400/身份，成功ACK1400。'
dimension('SYS-02', 'RUNTIME-20261010-NATIVE4-SAME-IDENTITY-RESTART',
          '同binary/config降权owned child SIGTERM退出并明确重启；同身份online/synced、成功SYNC_ACK，父Unit/proxy/原4Agent保留。', [restart_path, restart_detail])
cases['SYS-02']['remaining_dimensions'] = ['没有明确HELLO和Heartbeat报文/计数证据，last_seen前进不等于报文验证。',
    '该Native child重启不能覆盖Docker/其他宿主变体。']
dimension('API-NOT-FOUND', 'API-20261010-NOT-FOUND-EIGHT-RESOURCES',
          '8类随机UUID先GET404；Node DELETE404，Group/Script/Task/Schedule/App/Artifact DELETE200后GET404，Transfer DELETE403不支持；集合ID/数量未变。', [nf_path])
cases['API-NOT-FOUND'].update(status='PARTIAL',
    actual='8类真实对象缺失读取/删除契约通过；6条audit与5次Revision增量是已记录历史副作用，主资源集合不变。',
    remaining_dimensions=['Execution及其logs等剩余适用对象/方法范围核对与补测；Users item405属于不适用404的当前契约。'])
counts = {k: Counter(x['status'] for x in inventory['cases'])[k] for k in ['PASS','FAIL','PARTIAL','NOT_RUN','BLOCKED']}
inventory.update(summary=counts, updated_at=STAMP)
save(BASE / 'feature-inventory.json', inventory)
matrix = read(BASE / 'defect-regression-matrix.json')
for row in matrix['failures']:
    if row['failure_id'] == 'WEB-LIVE-LOG':
        row['runtime_evidence'] = list(dict.fromkeys(row['runtime_evidence'] + [str(clip_path), str(REVIEW)]))
        row['covered_dimensions'].append('真实secure clipboard精确1920bytes/SHA读回；三个流过滤精确匹配API。')
        row['remaining_dimensions'] = ['clipboard拒绝后的新反馈/业务效果仍待fresh-toast复测。',
            '真实PENDING UI未捕获；retry attempt metadata保留X15 broader维度。']
        row['actual'] = '同页实时新chunks/双流终态、断线恢复、空/截断日志、Follow坐标、搜索/流过滤和真实系统剪贴板精确读回已验；PENDING UI及复制拒绝新反馈仍未验。'
        row['updated_at'] = row['reviewed_at'] = STAMP
matrix['updated_at'] = STAMP
dc = {k: Counter(x['regression_status'] for x in matrix['failures'])[k] for k in ['PASS','FAIL','PARTIAL','NOT_RUN','BLOCKED']}
assert sum(dc.values()) == 22 and sum(counts.values()) == 504
matrix['summary'] = dc
save(BASE / 'defect-regression-matrix.json', matrix)
coverage = read(Path('.e2e/coverage.json'))
coverage.update(updated_at=STAMP, repair_summary=dc, repair_feature_counts=counts)
coverage['repair_gaps'] = [{k: row[k] for k in ['case_id','title','owner','status','remaining_dimensions','evidence'] if k in row}
                         for row in inventory['cases'] if row['status'] != 'PASS']
save(Path('.e2e/coverage.json'), coverage)
workflows = read(Path('.e2e/workflows.json'))
for row in workflows['workflows']:
    row['repair_coverage'] = {'statuses': dict(Counter(cases[c]['status'] for c in row['case_ids'] if c in cases)),
        'source': str(BASE/'feature-inventory.json'), 'updated_at': STAMP, 'not_baseline_status': True}
workflows['updated_at'] = STAMP
save(Path('.e2e/workflows.json'), workflows)
failures = read(Path('.e2e/failures.json'))
for row in failures['failures']:
    if row['id'] == 'WEB-LIVE-LOG':
        m = next(x for x in matrix['failures'] if x['failure_id'] == row['id'])
        row.update(repair_status=m['regression_status'], repair_evidence=m['runtime_evidence'],
                   repair_remaining_dimensions=m['remaining_dimensions'])
failures['updated_at'] = STAMP
save(Path('.e2e/failures.json'), failures)
task_status = {'root': '核验并整合重启/clipboard/NF证据；完整504仍未完成',
    'luna_web_cases': '冷页面fresh toast + 双权限deny复制反馈补测，0Task/0Run',
    'luna_runtime_cases': '只读制定owned proxy稳定PENDING UI方案，未授权信号/控制变更',
    'e2e_recovery': '8资源missing-ID批次已审；只读核对Execution等closure及下一API批次方案'}
state = read(Path('.e2e/run-state.json'))
state.update(updated_at=STAMP, current_tasks=task_status)
state['repair'].update(feature_counts=counts, defect_regression_counts=dc)
state['repair'].setdefault('latest_case_reviews', []).append(str(REVIEW))
state['continuation_20261010'].update(updated_at=STAMP, current_tasks=task_status,
    feature_counts=counts, defect_counts=dc, latest_review=str(REVIEW))
state['resume_hint'] = '3个Luna已真实恢复；最新review见restart-clipboard-not-found。Native4 owned child当前PID49873/tick137980573，proxy44932/tick123677922，与父Unit同cgroup，禁止Unit/cgroup操作；使用前fresh guard。SQLiteBUSY→正常markerRun/UI及重启持久化已补，Commit调用失败未测；PENDING UI方案只读，clipboard deny反馈冷页进行；Host001仍stopped/权限BLOCKED；新增Exit0/UIlogout修复待明确确认。不Git/Archive。'
save(Path('.e2e/run-state.json'), state)
data = read(Path('.e2e/test-data.json'))
data['updated_at'] = STAMP
env = data['repair_environment_state']
child = env['backend_child_20261008']
child['latest_owner_evidence'] = str(restart_detail)
child['latest_owner_guard'].update(child_pid=49873, child_startticks=137980573,
    owner_manifest_sha256=detail['result']['owner_manifest_sha256_after'])
child['latest_restart_evidence'] = str(restart_path)
child['restart_state'] = 'SAME_IDENTITY_RUNNING_RETAINED; all PID guards historical until fresh recheck'
env['web_live_log_20261010'].update(status='COPY_EXACT_READBACK_AND_FILTERS_PASS_DENIAL_FRESH_FEEDBACK_IN_PROGRESS',
    cold_result=str(clip_path), cold_cleanup_verified=True,
    denial_progress=str(BASE/'qa-resume-20261010-web/live-log/clipboard-denial/progress.json'))
env['db_write_fault_20261010'].update(restart_revision_persistence_verified=True, restart_evidence=str(restart_path),
    commit_call_failure_verified=False)
save(Path('.e2e/test-data.json'), data)
progress_path = BASE/'qa-resume-20261010-root/progress.json'
progress = read(progress_path)
progress.update(updated_at=STAMP, current_tasks=task_status, feature_counts=counts,
    defect_counts=dc, latest_review=str(REVIEW))
progress['resume_steps'] = [
    '先.e2e/README及五状态，读本progress和最新review；保持既有授权，新增Exit0/UIlogout修复仍待确认。',
    '三个既有Luna已真实可用：Web denial冷页，Runtime PENDING只读方案，Recovery API closure/下一批只读方案。',
    '已完成P0/SHA/TLS/分页/命令/SQLiteBUSY正反/UI/重启/clipboard精确读回不无故重跑。',
    'Native4 child当前49873/tick137980573/proxy44932，均同父Unit cgroup；每次操作fresh完整guard，禁止父Unit/cgroup清理。',
    '旧日志Task已删，冷页0Task/0Run。PENDING如新增fixture须Root准确方案审核；Commit调用失败仍未执行。',
    'Host001停止保留，权限BLOCKED；须准确policy及新的安全配置Review，禁止擅自放宽。',
    '504/22固定分母，未测不外推PASS；only repair18080管理写，原Hub/4Agent/备份保留，无Git/Archive。']
save(progress_path, progress)
report = ROOT/'.e2e/final-report.md'
text = report.read_text()
lines = text.splitlines()
lines[2] = f'更新时间：{STAMP}。运行20260930-101945，管理authority仅repair `http://192.168.100.209:18080`。'
lines[4] = f'修复版504场景：**{counts["PASS"]} PASS / {counts["FAIL"]} FAIL / {counts["PARTIAL"]} PARTIAL / {counts["NOT_RUN"]} NOT_RUN / {counts["BLOCKED"]} BLOCKED**。原22缺陷：**19 PASS / 1 PARTIAL / 1 NOT_RUN / 1 BLOCKED**。新增退出码0/UI logout独立，不扩大22分母。'
text = '\n'.join(lines) + '\n'
start = text.index('## 正在执行与接续顺序')
end = text.index('新会话先读', start)
text = text[:start] + '''## 正在执行与接续顺序

1. Luna web：真实系统clipboard精确读回及三个流过滤已通过；冷页补复制拒绝后的新反馈，0新Task/0Run。
2. Luna runtime：owned Native4 child同身份重启、Revision1400持久化及新连接ACK已通过；只读制定稳定PENDING UI捕捉方案，尚未执行proxy控制。
3. Luna recovery：8类不存在资源API矩阵已通过，canonical仍PARTIAL；核对Execution等剩余范围及下一批API方案。
4. Root：持续验收与同步504/22；SQLite Commit调用自身失败未测，Host001权限BLOCKED且容器停止保留；ExitCode0/UIlogout新修复待明确确认。

''' + text[end:]
text = text.replace('本轮磁盘 SHA：Hub、libvirt agent-1/2/4 匹配 build manifest；agent-3 路径未核验，所有运行进程 SHA 仍未补齐。',
    '早期磁盘 SHA 仅Hub/agent-1/2/4核验；现已补Native3实际运行PID445056的proc/disk SHA，四Agent分别均有运行进程SHA证据，但不是同一时刻重新采样。')
text = text.replace('记忆 MCP 本次不可用；接续状态以本地文件为准。',
    '接续状态以本地文件为准；此前记忆bootstrap禁用，长期经验已另存并commit，临时测试状态不写长期记忆。')
text += f'\n## 最新证据核验 {STAMP}\n\n真实复制1920bytes与API/DOM SHA相同；owned child重启Revision1400/身份保持，未捕获Heartbeat报文；8类missing-ID契约及集合不变已实测，Execution等待补，因此API-NOT-FOUND为PARTIAL。[Root核验](runs/20260930-101945/repair/qa-resume-20261010-root/restart-clipboard-not-found-review.json)。\n'
report.write_text(text)
print(json.dumps({'feature_counts': counts, 'defect_counts': dc, 'review': str(REVIEW)}, ensure_ascii=False))
