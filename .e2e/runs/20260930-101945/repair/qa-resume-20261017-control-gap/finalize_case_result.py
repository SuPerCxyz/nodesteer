#!/usr/bin/env python3
"""Finalize: case-result.json + progress.json (batch root), close browser session."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import cgap_lib as L  # noqa: E402

B = (L.load('phase-b-results.json') or {}).get('tests', {})
SETUP = L.load('phase-a-setup.json')
LEDGER = json.loads((HERE / 'fixture-ledger.json').read_text())
ORACLE = json.loads((HERE / 'final-cleanup-oracle.json').read_text())
PRESELECT = L.load('b5b-artifact-preselect-deeplink.json')

now = datetime.now(timezone.utc).isoformat()

controls = [
    {
        'control': '触发方式 过滤器', 'page': '/executions', 'case_ids': ['P17P18-04'],
        'result': 'COVERED', 'test_ref': 'B1-executions-trigger-filter',
        'evidence': ['evidence/phase-b-results.json#B1', 'evidence/b1-executions-trigger-manual.png',
                     'evidence/b1-executions-trigger-schedule.png', 'evidence/b1-executions-trigger-system.png',
                     'evidence/b1-system-empty-state.json', 'evidence/b1-executions-trigger-all.png'],
        'findings': [
            '选项: 全部触发方式/手动/调度/系统；选择后 URL ?trigger=、API ?trigger_type= 均正确。',
            'manual: GET trigger_type=manual 200，页面 10/10 行触发方式=手动；schedule: 10/10 行=调度。',
            'system: GET trigger_type=system 200，0 行，空态“没有匹配的执行记录”；API 侧 system 计数=0（环境无样本，数据源限制）。',
            '全部触发方式: trigger 参数移除，恢复未过滤列表。',
        ],
    },
    {
        'control': '时区（含 One-Time 换算/空 run_at/非法时区）', 'page': '/schedules/new + /schedules/{id}',
        'case_ids': ['P12P13-05', 'X10'], 'result': 'COVERED', 'test_ref': 'B2-schedules-timezone',
        'evidence': ['evidence/phase-b-results.json#B2', 'evidence/b2-schedules-new-timezone.png',
                     'evidence/b2-schedules-edit-onstart-reset.png', 'evidence/b2-schedules-list-rows.json'],
        'findings': [
            '默认时区=浏览器时区（Asia/Shanghai）；任务预选锁定横幅正确。',
            'One-Time 墙钟 2030-06-16T10:00 + Asia/Shanghai 经真实 PUT 保存后 API run_at=2030-06-16T02:00:00Z（UTC 换算正确，revision 2→3）。',
            '编辑页回显 2030-06-16T10:00（墙钟）；保存后重开仍一致（instant 保留）。',
            '空 run_at 点保存：内联错误“请选择运行时间”，0 写请求。',
            '非法时区 Not/AZone：预览显示“时区无效：Not/AZone”；切 on_start 隐藏时区输入并重置为合法浏览器时区；切回 cron 时区=Asia/Shanghai，全程 0 写请求。',
        ],
    },
    {
        'control': 'Skip to Main', 'page': '(全局壳)', 'case_ids': ['G04-04', 'X19'],
        'result': 'DEFECT', 'test_ref': 'B3-skip-to-main',
        'evidence': ['evidence/phase-b-results.json#B3', 'evidence/b3-skip-to-main-focused.png',
                     'evidence/b3-skip-to-main-after-enter.png'],
        'findings': [
            '首个 Tab 即聚焦 Skip to Main（键盘可达 ✓）。',
            'Enter 激活后 hash=#content，但页面不存在 id="content" 元素（DOM 断言 false），焦点仍停在链接、scrollY=0，未跳到主内容。',
            '激活后下一次 Tab 落到品牌链接（未进入主内容），证明“跳过导航”未生效。',
            '代码位：web/src/components/skip-to-main.tsx:5 href="#content"；web/src 全库无 id="content" 目标。',
            '新增缺陷候选 WEB-SKIP-TO-MAIN-DEAD-ANCHOR-20261017（P3，a11y）。',
        ],
    },
    {
        'control': '打开日志', 'page': '/executions/{id}', 'case_ids': ['P17P18-08'],
        'result': 'COVERED', 'test_ref': 'B4-execution-open-logs',
        'evidence': ['evidence/phase-b-results.json#B4', 'evidence/b4-execution-detail-before-openlogs.png',
                     'evidence/b4-execution-detail-after-openlogs.png'],
        'findings': [
            '实测行为=页内 scrollIntoView 到 #execution-logs（审计结论确认）。',
            '点击前 logs 区 top=828（视口外），点击后 scrollY 0→812、logs top=16（视口内，scroll-mt-4）；无对话框打开、URL 不变。',
        ],
    },
    {
        'control': '取消（编辑脚本）', 'page': '/scripts/{id}', 'case_ids': ['X08'],
        'result': 'COVERED', 'test_ref': 'B5a-script-editor-cancel',
        'evidence': ['evidence/phase-b-results.json#B5a'],
        'findings': ['修改描述后点取消：0 非 GET 请求；API revision/描述不变；该脚本 audit 仅 create/delete 各 1 条（取消未产生审计）。'],
    },
    {
        'control': '取消 + 上传新发布包（内嵌）', 'page': '/applications/{id}', 'case_ids': ['X06', 'X08'],
        'result': 'COVERED', 'test_ref': 'B5b-app-editor-inline-upload-cancel',
        'evidence': ['evidence/phase-b-results.json#B5b', 'evidence/b5b-app-inline-upload-form.png',
                     'evidence/b5b-artifact-preselect-deeplink.json'],
        'findings': [
            '内嵌表单开合正常（name/version/arch/file + 上传/取消）。',
            '内嵌取消：0 POST，预选 artifact 组合框保持原值。',
            '不选文件直接上传：file.validity.valueMissing=true，0 POST（原生 required 拦截）。',
            '真实上传 owned art2：POST /api/artifacts 200 → 组合框自动切到“qa-cgap-1cdad4fe-art2 2.0.0”、版本输入 2.0.0；应用对象未保存（artifact_id/version/revision 不变）。',
            '页面级取消：0 PUT，应用定义与名称均未变。',
            '深链 /applications/new?artifact_id=…&version=v1 预选（只读）：组合框=对应发布包、版本输入=v1。',
        ],
    },
    {
        'control': '取消（上传发布包）', 'page': '/artifacts/new', 'case_ids': ['X06'],
        'result': 'COVERED', 'test_ref': 'B5c-artifact-editor-cancel',
        'evidence': ['evidence/phase-b-results.json#B5c', 'evidence/b5c-artifact-upload-editor.png'],
        'findings': ['填写名称/版本并选择文件后点取消：0 POST，未创建发布包（qa-cgap-1cdad4fe-art3 不存在）。'],
    },
    {
        'control': '编辑任务/立即运行页面（owned Task 非空态）', 'page': '/tasks/{id}/edit + /tasks/{id}/run',
        'case_ids': ['ROUTE-16', 'ROUTE-17', 'P11-01', 'X09'], 'result': 'COVERED', 'test_ref': 'B6-task-edit-run-pages',
        'evidence': ['evidence/phase-b-results.json#B6', 'evidence/b6-task-edit-page.png',
                     'evidence/b6-task-edit-main-text.json', 'evidence/b6-task-run-page.png',
                     'evidence/b6-task-run-confirm-dialog.png'],
        'findings': [
            '编辑页真实渲染 owned Task：名称/类型=命令/目标=节点(native-1 已选)/重试/运行用户/离线策略/参数 msg(string,必填)/保存/取消，无错误态。',
            '运行页渲染参数 msg 输入 + 立即运行 + 取消(/tasks/{id})。',
            '确认框打开（含任务名），点取消：对话框关闭、0 POST /tasks/{id}/run、该任务执行数 0→0。',
        ],
    },
    {
        'control': '分组详情页 + 调度详情页（owned 非空态）', 'page': '/groups/{id} + /schedules/{id}',
        'case_ids': ['ROUTE-09', 'ROUTE-22', 'P06-04', 'P12P13-10'], 'result': 'COVERED',
        'test_ref': 'B7-group-detail-page + B2 编辑页',
        'evidence': ['evidence/phase-b-results.json#B7', 'evidence/b7-group-edit-page.png',
                     'evidence/phase-b-results.json#B2'],
        'findings': [
            '/groups/{id} 渲染 owned Group：名称回显、类型=静态、成员 native-1 可见、保存/取消，无错误态。',
            '/schedules/{id} 渲染 owned Schedule：类型=一次性、运行时间墙钟、时区、执行方/离线/漏触发策略，无错误态。',
        ],
    },
]

proposals = {
    'P17P18-04': {'current': 'PARTIAL', 'proposed': 'PASS',
                  'reason': 'manual/schedule 过滤的 URL、API trigger_type 参数、行一致性全部实测；system 请求正确且显示空态，无样本为环境数据源限制（候选缺陷待产品裁决，与既有 remaining 一致）。'},
    'P12P13-05': {'current': 'PARTIAL', 'proposed': 'PASS',
                  'reason': '时区换算端到端已验证（墙钟→UTC instant→重载墙钟），空 run_at UI 拦截 0 写。remaining 中“服务端空 run_at 拒绝”属 API 层负向，建议转 API 批次或保留为单独未验证维度。'},
    'X10': {'current': 'PARTIAL', 'proposed': 'PASS',
            'reason': '非法时区回退 on_start（隐藏+重置合法值）与 One-Time 时区转换本轮实测；字段裁剪/Owner-Offline 有既有 PASS 证据。DST 边界为“观察项”未执行，建议保留为 observation。'},
    'G04-04': {'current': 'PARTIAL', 'proposed': 'PARTIAL（维持）',
               'reason': 'Skip to Main 键盘可达但激活为死锚点（新缺陷候选），该键盘维度仍不满足；缺陷修复后复测可转 PASS。'},
    'X19': {'current': 'PARTIAL', 'proposed': 'PARTIAL（维持）',
            'reason': '同上；批量工具栏既有证据仍有效，Skip to Main 维度缺陷未修复。'},
    'X06': {'current': 'PARTIAL', 'proposed': 'PASS',
            'reason': 'remaining 三项本轮全部验证：深链预选（只读实测）、内嵌上传成功自动选中、失败/取消不覆盖既有定义（含原生 required 失败与内嵌/页面取消）。'},
    'X08': {'current': 'PARTIAL', 'proposed': 'PARTIAL（维持，remaining 收窄）',
            'reason': '“取消编辑不改 Revision/Audit”本轮对脚本/应用编辑器实测通过（0 写、revision/audit 不变）；停止/批量升级/吊销取消、慢响应重复点击、失败重试仍未执行。'},
}

defects = [{
    'id_candidate': 'WEB-SKIP-TO-MAIN-DEAD-ANCHOR-20261017',
    'severity': 'P3', 'status': 'candidate (not yet registered in .e2e/failures.json)',
    'title': 'Skip to Main 链接指向不存在的锚点 #content，激活后不移动焦点/滚动到主内容',
    'case_ids': ['G04-04', 'X19'],
    'repro': ['登录后任意页，按首个 Tab 聚焦 Skip to Main', '按 Enter', '观察 hash=#content 但无 id="content" 元素、焦点仍在链接、scrollY 不变；下一次 Tab 落到品牌链接'],
    'code_refs': ['web/src/components/skip-to-main.tsx:5 (href="#content")', 'web/src/components/layout/main.tsx（Main 无 id）', 'web/src 全库无 id="content"'],
    'evidence': ['evidence/phase-b-results.json#B3', 'evidence/b3-skip-to-main-after-enter.png'],
}]

remaining_unverified = [
    'P17P18-04 system 触发筛选：环境无 system 样本（API 计数 0），无法验证有数据时的行过滤（数据源限制）。',
    'P12P13-05 服务端对空 run_at 的拒绝（API 层负向）未直接验证；UI 侧已拦截。',
    'X10 DST 边界未执行（用例本身为“观察项”）；四类型字段裁剪未在本轮复验（依赖 20261004 既有证据）。',
    'X08 停止/批量升级/吊销取消、慢响应重复点击、失败后重试未执行。',
    'ROUTE-16/17/09/22 的“真实网络故障”维度未验证。',
    'Skip to Main 缺陷修复后需复测（缺陷候选）。',
    '时区换算仅验证 Asia/Shanghai 一个时区（浏览器默认同区），未覆盖跨区/半时区。',
    '“打开日志”仅验证有日志的 SUCCESS 执行；PENDING/RUNNING 的实时日志行为属已知缺陷 WEB-LIVE-LOG 范畴，本轮未触碰。',
]

case_result = {
    'batch_id': 'qa-resume-20261017-control-gap',
    'title': '控件级覆盖审计“必须补测”档补测（trigger 过滤 / 时区 / Skip to Main / 打开日志 / 取消三处 / 内嵌上传 / 需夹具页面）',
    'authorization_basis': '父会话任务：补测 control-coverage-audit.md 的“必须补测”档；只创建 owned fixture，测完精确删除并 GET404。',
    'nonce': L.NONCE, 'owned_prefix': L.PREFIX,
    'authority': L.UI + ' (repair)', 'primary_8080_contacted': False,
    'build_label_under_test': 'dddee4f0-e2e-repair-20261017-p01',
    'executed_at_utc': SETUP.get('at_utc'), 'finished_at_utc': now,
    'browser_session': L.BROWSER_SESSION + ' (closed after run)',
    'controls': controls,
    'ledger_status_proposals': proposals,
    'new_defect_candidates': defects,
    'remaining_unverified': remaining_unverified,
    'cleanup': {
        'all_owned_deleted_get404': ORACLE.get('all_owned_deleted_get404'),
        'leftover_owned_objects': ORACLE.get('leftover_owned_objects'),
        'no_owned_executions': ORACLE.get('no_owned_executions'),
        'healthz_readyz': ORACLE.get('healthz_readyz'),
        'protected_nodes': ORACLE.get('protected_nodes'),
        'counts_pre_post': ORACLE.get('counts_pre_post'),
        'browser_closed': ORACLE.get('browser_session_closed', {}).get('rc') == 0,
        'tmp_files_removed': ORACLE.get('tmp_files_removed'),
        'ledger_file': 'fixture-ledger.json', 'oracle_file': 'final-cleanup-oracle.json',
    },
    'evidence_index': {
        'raw_per_test': 'evidence/phase-b-results.json',
        'setup_and_fixtures': 'evidence/phase-a-setup.json',
        'screenshots': 'evidence/*.png',
        'supplementary': ['evidence/b1-system-empty-state.json', 'evidence/b2-schedules-list-rows.json',
                          'evidence/b6-task-edit-main-text.json', 'evidence/b5b-artifact-preselect-deeplink.json'],
        'tools': 'evidence/tools/cgap_lib.py + phase_a/b/c scripts at batch root',
    },
}
(HERE / 'case-result.json').write_text(json.dumps(case_result, ensure_ascii=False, indent=1))

status_map = {v['test_ref'].split(' +')[0]: v['result'] for v in controls}
progress = {
    'batch': 'qa-resume-20261017-control-gap',
    'phase': 'done',
    'authority': L.UI,
    'nonce': L.NONCE,
    'tests': status_map,
    'summary': {'COVERED': sum(1 for v in controls if v['result'] == 'COVERED'),
                'DEFECT': sum(1 for v in controls if v['result'] == 'DEFECT'),
                'PARTIAL': sum(1 for v in controls if v['result'] == 'PARTIAL')},
    'finished_at_utc': now,
}
(HERE / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=1))

close = L.ab_close()
print(json.dumps({'case_result': 'written', 'progress': progress, 'browser_close_rc': close['rc']}, ensure_ascii=False, indent=1))
