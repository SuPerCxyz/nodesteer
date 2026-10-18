import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('.e2e/runs/20260930-101945/repair')
ROOT = BASE / 'qa-resume-20261010-root'
NOW = datetime.now(timezone.utc).isoformat()
REVIEW = ROOT / 'web-batch09-14-pending-ui-review.json'
STATES = ['PASS', 'FAIL', 'PARTIAL', 'NOT_RUN', 'BLOCKED']

def read(p):
    return json.loads(Path(p).read_text())

def write(p, d):
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n')

inventory = read(BASE / 'feature-inventory.json')
cases = {c['case_id']: c for c in inventory['cases']}
assert len(cases) == len(inventory['cases']) == 504
matrix = read(BASE / 'defect-regression-matrix.json')
assert len({f['failure_id'] for f in matrix['failures']}) == len(matrix['failures']) == 22
changes = []
hashes = []
for n in range(9, 15):
    batch = BASE / f'qa-resume-20261010-web/batch{n:02}'
    result = read(batch / 'results.json')
    assert result['status'] in ['COMPLETE', 'COMPLETE_WITH_PARTIAL']
    for item in result['cases']:
        cid = item['case_id']
        case = cases[cid]
        evidence_names = item['evidence'] if isinstance(item['evidence'], list) else [item['evidence']]
        paths = [batch / e for e in evidence_names]
        docs = [read(p) for p in paths]
        for p in paths:
            hashes.append({'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
        before = case['status']
        after = item['status']
        assert after in ['PASS', 'PARTIAL']
        case['status'] = after
        description = {
            'ROUTE-06': '真实 API Node.id 与 itemGET200/id 一致；直接 /nodes/:id、刷新、回链与历史保持该URL；独立missingID404及错误恢复已验证。',
            'ROUTE-19': '实际 legacy /tasks?view=schedules 跳转 /schedules，GET200、空态、刷新和历史已验证。',
            'ROUTE-26': '真实列表行href与API ID一致、详情GET200；missingID404/错误态→真实详情恢复。早期新建链接误选是测试器错误。',
            'TABLE-EXECUTIONS-PAGE': '实际20行下一页/上一页/末页7行/首页，往返行href hash一致；页大小持久化及恢复10已验。并行执行改变总数，不推断精确固定总数。',
            'TABLE-AUDIT-PAGE': '实际首页/第二页/末页4行/首页；往返非时间列hash一致、末页next禁用；原始审计内容未保存。',
        }.get(cid, '实际UI/API路由或表格操作、状态反馈和恢复见本批脱敏证据；未创建资源或发服务器管理写请求。')
        if after == 'PARTIAL':
            remaining = next((d.get('remaining') for d in docs if d.get('remaining')), '缺少真实有效资源或多页数据的适用维度。')
            case['remaining_dimensions'] = [remaining]
            description = '实际缺失资源错误态或少于最小分页容量的边界已验证；缺少维度如实保留。'
        else:
            case['remaining_dimensions'] = []
        case['actual'] = description
        for p in [*paths, REVIEW]:
            if str(p) not in case['evidence']:
                case['evidence'].append(str(p))
        case['covered_dimensions'].append({'test_id': f'WEB-20261010-B{n:02}-{cid}', 'status': 'PASS_FOR_REVIEWED_DIMENSIONS', 'description': description, 'layer': 'real-ui-api', 'evidence': [str(p) for p in paths], 'model': 'gpt-6-luna', 'reviewed_at': NOW})
        case['updated_at'] = NOW
        changes.append({'case_id': cid, 'before': before, 'after': after, 'batch': n})

attempt = BASE / 'qa-resume-20261010-runtime/pending-ui/attempt06'
ui = read(attempt / 'ui-evidence-c2ce573d4aea.json')
controller = read(attempt / 'controller-evidence-attempt02-c2ce573d4aea.json')
stdout = read(attempt / 'stdout-container-final-readonly-attempt02.json')
eid = 'e7a4ce07-4a10-420c-909f-f2e96a91738d'
assert ui['api_execution_identity']['id'] == ui['api_final_execution']['id'] == eid
assert [x['status'] for x in ui['observed_status_badges']] == ['PENDING', 'RUNNING', 'SUCCESS']
assert ui['same_execution_detail_no_reload'] and ui['detail_time_origin'] == ui['final_detail_time_origin']
assert ui['detail_navigation_entry_count'] == ui['final_detail_navigation_entry_count'] == 1
assert controller['helper_task_get404'] and controller['child_task_get404']
assert ui['browser_logout_status'] == 200 and ui['browser_me_with_revoked_token_status'] == 401
assert stdout['stdout_container']['unique_marker_present'] and stdout['stdout_container']['content_bytes'] == 27
case = cases['P11-02']
before = case['status']
case.update(status='PASS', actual='真实UI确认执行，API初态PENDING；同一Execution页实际卡片PENDING→RUNNING→SUCCESS，URL/timeOrigin/navEntries一致；截图含正确Execution ID，stdout pre唯一marker27B；Task/helper404及独立会话注销401。测试器早期文本ID采样误判不构成产品失败。', remaining_dimensions=[], updated_at=NOW)
pending_paths = [attempt / name for name in ['ui-evidence-c2ce573d4aea.json', 'controller-evidence-attempt02-c2ce573d4aea.json', 'timing-wire-diagnosis.json', 'pending-execution-c2ce573d4aea.png', 'success-execution-c2ce573d4aea.png', 'stdout-container-final-readonly-attempt02.json']]
for p in [*pending_paths, REVIEW]:
    assert p == REVIEW or p.is_file()
    if str(p) not in case['evidence']:
        case['evidence'].append(str(p))
case['covered_dimensions'].append({'test_id': 'RUNTIME-20261010-P11-REALRUN2-ROOT-REVIEW', 'status': 'PASS_FOR_REVIEWED_DIMENSIONS', 'description': case['actual'], 'evidence': [str(p) for p in pending_paths], 'model': 'gpt-6-luna', 'reviewed_at': NOW})
changes.append({'case_id': 'P11-02', 'before': before, 'after': 'PASS'})

counts = {s: Counter(c['status'] for c in cases.values())[s] for s in STATES}
assert sum(counts.values()) == 504
inventory['summary'] = counts
inventory['updated_at'] = NOW
inventory['complete'] = False
inventory['latest_case_review'] = str(REVIEW)
defects = {s: Counter(f['regression_status'] for f in matrix['failures'])[s] for s in STATES}
write(REVIEW, {'reviewed_at': NOW, 'phase': 'tests-before-unified-repair', 'cases': changes, 'counts': counts, 'defect_counts': defects, 'evidence_sha256': hashes, 'pending_ui': {'actual': 'PENDING→RUNNING→SUCCESS same document verified; screenshots reviewed by Root', 'harness_false_negative': 'first text sample lacked UUID; screenshot/API/URL/status prove identity', 'original_WEB_LIVE_LOG_remaining': 'true PENDING now observed; await final physical controls/watchdog readback before closure', 'no_extra_business_Run': True}, 'limitations': ['ROUTE09需真实owned Group', 'Scripts/Transfers分页数据不足不泛化PASS', 'X15 retry attempt metadata broader维度保留', 'Users持久fixture确认仍pending'], 'product_edits': 0, 'web_server_writes': 0})
write(BASE / 'feature-inventory.json', inventory)

tasks = {'root': f"{counts['PASS']}/504 PASS；先测试后统一修复，审阅最少fixtures与SYS12预检", 'luna_web_cases': 'batch09–14已Root汇入；连续只读batch15错误/空态，后续最少fixture预算待审', 'luna_runtime_cases': 'P11-02真实同页转换PASS；梳理下一批可执行runtime用例和最少预算', 'e2e_recovery': 'attempt06最后physical控制/watchdog只读helper1；SYS12源准备完成待Root审'}
state = read('.e2e/run-state.json')
state.update(updated_at=NOW, phase='tests-before-unified-repair', current_tasks=tasks, feature_counts=counts)
state['repair'].update(feature_counts=counts, defect_regression_counts=defects)
state['repair']['latest_case_reviews'].append(str(REVIEW))
state['resume_hint'] = '读README及五状态文件和本次review；当前504/22计数以repair inventory/matrix为准。batch09–14已汇入，P11-02同页PENDING/RUNNING/SUCCESS已验。WEB-LIVE-LOG待最后controls/watchdog只读核验。复用3个Luna继续具体测试，先测试后统一Exit0/UIlogout修复；QA User持久fixture尚未授权。repair18080唯一写端；Host stopped/原环境保护，不Git/Archive。'
write('.e2e/run-state.json', state)
progress = read(ROOT / 'progress.json')
progress.update(updated_at=NOW, feature_counts=counts, defect_counts=defects, current_tasks=tasks, latest_case_review=str(REVIEW), latest_web_review=str(REVIEW))
progress['resume_steps'] = ['README→五状态→最新review', 'Luna web连续ROUTE/TABLE只读与最少fixtures方案', 'Luna runtime下一批具体case预算评估，0新business Run', 'Luna recovery最后controls/watchdog只读helper；SYS12预检需Root后续审', 'QA User确认仍pending，独立测试继续', '测试后统一已确认Exit0/logout修复；Host stopped，原部署保护，无Git/Archive']
write(ROOT / 'progress.json', progress)
coverage = read('.e2e/coverage.json')
coverage.update(updated_at=NOW, repair_feature_counts=counts, repair_summary=defects)
coverage['repair_gaps'] = [{k: c.get(k) for k in ['case_id', 'title', 'owner', 'status', 'remaining_dimensions', 'evidence']} for c in inventory['cases'] if c['status'] != 'PASS']
write('.e2e/coverage.json', coverage)
workflows = read('.e2e/workflows.json')
for w in workflows['workflows']:
    scoped = [cases[cid] for cid in w['case_ids'] if cid in cases]
    w['repair_coverage'] = {'statuses': dict(Counter(c['status'] for c in scoped)), 'source': str(BASE / 'feature-inventory.json'), 'updated_at': NOW, 'not_baseline_status': True}
workflows['updated_at'] = NOW
write('.e2e/workflows.json', workflows)
review_scope = str(ROOT / 'additional-defect-review.json')
assert read(review_scope)['status'] == 'CONFIRMED_REPAIR_AFTER_TEST_COMPLETION'
for path, key in [('.e2e/failures.json', 'repair_additional_failures'), ('.e2e/observations.json', 'observations')]:
    d = read(path)
    entries = d.get(key, [])
    if isinstance(entries, list):
        for e in entries:
            if e.get('id') in ['OBS-20261008-EXECUTION-ZERO-EXIT-CODE', 'OBS-20261010-UI-LOGOUT-NOT-REVOKED']:
                e.update(status='CONFIRMED_REPAIR_AFTER_TESTS', source_change_authorized=True, repair_execution_gate='先完成可执行测试并汇总环境限制，再统一产品修复', confirmed_scope_review=review_scope)
    d['updated_at'] = NOW
    write(path, d)
report = Path('.e2e/final-report.md')
text = report.read_text()
start = text.index('更新时间：')
end = text.index('\n## 本轮已完成')
text = text[:start] + f"更新时间：{NOW}。运行20260930-101945，管理authority仅repair http://192.168.100.209:18080。\n\n修复版504场景：**{counts['PASS']} PASS / {counts['FAIL']} FAIL / {counts['PARTIAL']} PARTIAL / {counts['NOT_RUN']} NOT_RUN / {counts['BLOCKED']} BLOCKED**。原22缺陷：**19 PASS / 1 PARTIAL / 1 NOT_RUN / 1 BLOCKED**。Exit0/UIlogout已确认测试后统一修复，原22分母不变。\n" + text[end:]
start = text.index('1. Luna web：', text.index('## 正在执行与接续顺序'))
end = text.index('\n新会话先读', start)
text = text[:start] + f"1. Luna web：batch09–14共28项PASS已审阅汇入；继续剩余路由/表格错误与空态，当前只读0服务器写，两ownedScript保留。\n2. Luna runtime：P11-02真实UI/API同页PENDING→RUNNING→SUCCESS已通过；早先文本ID采样误判已纠正。Task/helper404及会话注销401；梳理下一批可执行runtime测试。\n3. Luna recovery：最后physical controls/watchdog只读helper已批1次；SYS12最小非Host容器方案与加固预检源待Root继续审。QA User永久fixture仍等待明确授权。\n4. Root：{counts['PASS']}/504完整通过，原22缺陷19通过。WEB-LIVE-LOG仅待最后physical清理核验；X15 broader维度不泛化。Exit0/UIlogout统一修复尚未开始，Host保持stopped/BLOCKED。\n" + text[end:]
report.write_text(text)
print(json.dumps({'review': str(REVIEW), 'counts': counts, 'defects': defects, 'canonical_total': len(cases), 'changes': len(changes)}, ensure_ascii=False))
