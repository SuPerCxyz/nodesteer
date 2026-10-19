#!/usr/bin/env python3
"""Emit failures-classified.json + run-summary.json for the repair Playwright run."""
import json, pathlib, time

OUT = pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-playwright')
res = json.loads((OUT / 'per-test-results.json').read_text())

# ---- classification table (keyed by "file::title") -------------------------
C = {
 'full-feature-ui::FULL-06 UI文件中继双目标与客户机文件一致、失败重试取消': dict(
   category='HARNESS_ENV', subcategory='env-coupled hardcode',
   why='测试用 nodes.find(n => /agent-2/i.test(n.hostname)) 找第二目标节点；repair 环境节点主机名为 qa-repair-20260930-101945-native-*，不含 "agent-2"，只匹配到 1 个目标，toHaveLength(2) 失败。',
   evidence='error: Expected length: 2 Received length: 1; received array hostname=qa-repair-20260930-101945-native-2',
   repro='grep -n "agent-2" web/e2e/tests/full-feature-ui.spec.ts:295', severity='low', product_defect=False),
 'full-feature-ui::FULL-08 真实应用UI Deploy/Stop/Start/Restart/Upgrade与Unit/config/SHA': dict(
   category='REDLINE_LIMITED', subcategory='needs guest SSH + ELF fixture',
   why='测试要求 NODESTEER_E2E_APP_ELF / NODESTEER_E2E_GUEST_INSPECT / NODESTEER_E2E_APP_DIR；这些需要 SSH 登录 Agent 客户机并落 systemd unit。本轮红线禁止触碰 Agent 节点，且历史 ELF/inspector 依赖的私有目录已随 /tmp 清理消失。',
   evidence='error: Authorized ELF/guest inspector/app directory are required (full-feature-ui.spec.ts:435)',
   repro='ls /tmp/nodesteer-e2e-20260930-101945-7wksl0_x -> No such file or directory', severity='n/a (out of authorized scope)', product_defect=False),
 'full-feature-ui::FULL-09 App Task UI创建/运行Deploy与四操作真实Unit/config/SHA/健康': dict(
   category='REDLINE_LIMITED', subcategory='needs guest SSH + ELF fixture',
   why='同 FULL-08，需要客户机 systemd/文件 SHA 核验。',
   evidence='error: Authorized ELF/guest inspector/app directory are required (full-feature-ui.spec.ts:435)',
   repro='same as FULL-08', severity='n/a (out of authorized scope)', product_defect=False),
 'full-feature-ui::ROLE-01 Operator与Viewer只能读取自己的Audit记录': dict(
   category='HARNESS_ENV', subcategory='env-coupled hardcode',
   why='QA_ROLE_USER_ID 硬编码为原 :8080 环境的用户 8ac0ef0f-...，repair 环境不存在该用户，私有凭据库也无其密码。',
   evidence='error: Exact retained QA user password is missing from private storage (full-feature-ui.spec.ts:12)',
   repro='curl /api/users | grep 8ac0ef0f -> not found', severity='low', product_defect=False),
 'full-feature-ui::ROLE-02 Operator真实部署自有应用但定义只读；Viewer部署与定义写入被拒': dict(
   category='HARNESS_ENV', subcategory='env-coupled hardcode',
   why='同 ROLE-01。',
   evidence='error: Exact retained QA user password is missing from private storage',
   repro='same as ROLE-01', severity='low', product_defect=False),
 'gap-ui::GAP-07 Audit真实服务端分页与跨页搜索': dict(
   category='HARNESS_ENV', subcategory='data/timing precondition',
   why='测试要求第 2 页存在一个 resource_id 不出现在第 1 页的审计行。运行时第 2 页恰好以 auth login 行（resource_id 为空）为主，找不到 marker 即抛错。实测当前 /api/audit 第 2 页存在 6 个唯一 resource_id（2dd5b1f9/eb85376c/70acc2f7/90898a7d/63df6d7d/1523924f），说明这是运行时刻的数据/时序条件，不是产品行为。',
   evidence='error: No unique cross-page audit resource ID available; fixture precondition not met (gap-ui.spec.ts:296); live probe X-Total-Count=7653, page2 有 6 个唯一 resource_id',
   repro='curl "/api/audit?limit=10&offset=10"', severity='low', product_defect=False),
 'gap-ui::GAP-14 Login与五类对象真实CRUD Audit逐事件': dict(
   category='SUITE_BUG', subcategory='unsynchronized assertion / race',
   why='expect(await page.locator("tbody tr").count()).toBe(object.actions.length) 在搜索结果重渲染前取值：未过滤列表（每页 10 行）里已包含刚创建对象的 ID，前一行 toContainText 立即通过，于是 count 取到 10 而不是过滤后的 3。独立复现证明产品行为正确：设置每页 50 行时未过滤 50 行，填入精确资源 ID 后 tbody 变为 1 行且包含该 ID。',
   evidence='error: Expected: 3 Received: 10 (gap-ui.spec.ts:535); repro: unfiltered tbody rows=50, filtered tbody rows=1, contains id=true',
   repro='node repro-gap14.cjs', severity='medium (false-negative, hides real regressions)', product_defect=False),
 'gap-ui::GAP-15 Root控制Node2物理offline窗口的真实UI Run': dict(
   category='HARNESS_ENV', subcategory='env-coupled hardcode + needs physical node control',
   why='nodeId 硬编码为原 :8080 环境节点 d77e2440-...，repair 环境不存在；且该用例需要 Root 侧把 Node2 物理离线，超出本轮红线（禁碰 Agent 节点）。',
   evidence='error: expect(PUT /api/tasks/<id> target d77e2440-...).toBeTruthy() -> false (gap-ui.spec.ts:548)',
   repro='curl /api/nodes | grep d77e2440 -> not found', severity='n/a (out of authorized scope)', product_defect=False),
 'gap-ui::GAP-16 [UI_MOCK] 11表ErrorRetry/Empty与Dashboard空态Hub指示': dict(
   category='SUITE_BUG', subcategory='expectation conflicts with repo empty-state design',
   why='断言「空数据集必须出现字面量 暂无数据 且不得出现 没有结果。」。实测 mock 空数据集：agents/groups/scripts/tasks/schedules/applications/artifacts/users 8 表为 暂无数据（通过）；executions 显示「没有匹配的执行记录」、transfers 显示「暂无文件传输」（领域化空态文案）、audit 显示「没有结果。」+共 0 条（共享 DataTable 的空数据文案，其自带单测 data-table.test.tsx:131 明确要求空数据显示「没有结果。」）。docs/PRODUCT.md 未规定该字面量。',
   evidence='evidence/all-tables-error-retry-mock.json: executions/transfers/audit distinctEmpty=false，其余 8 表 true；web/src/features/shared/data-table.test.tsx:131',
   repro='evidence/all-tables-error-retry-mock.json', severity='low (UX 一致性观察)', product_defect=False),
 'gap-ui::GAP-17 Application列表入口编辑取消与Viewer审计不可写': dict(
   category='HARNESS_ENV', subcategory='env-coupled hardcode',
   why='viewerId 硬编码为原环境用户 8ac0ef0f-...，repair 环境不存在该用户。',
   evidence='error: TypeError: Cannot read properties of undefined (reading role) (gap-ui.spec.ts:633)',
   repro='curl /api/users | grep 8ac0ef0f -> not found', severity='low', product_defect=False),
 'gap-ui::GAP-18 UI LoginDashboardAgents连续链与真实统计详情链接': dict(
   category='HARNESS_ENV', subcategory='pre-existing env data residue',
   why='断言 Dashboard「托管应用状态」为 0/1，但 repair 环境存在上一轮修复遗留的应用 qa-repair-20260930-101945-app-async-live-6a32f（创建于 2026-10-04T05:44Z，非本轮），统计变成 1/2。测试假设环境只有它自己创建的应用，应改为断言相对增量。',
   evidence='error: Expected substring "0/1" Received "托管应用状态1/2 健康..." ; GET /api/applications -> 1 个 2026-10-04 遗留应用；补充证实：15:03 另一并发操作者（qa-resume-20261018-housekeeping）删除该遗留应用后，本轮 trace 批次复跑 GAP-18 → PASSED（22s）',
   repro='curl /api/applications', severity='low', product_defect=False),
 'gap-ui::GAP-20 禁用Script实际引用拒绝恢复和定义边界': dict(
   category='SUITE_BUG', subcategory='stale fixture size vs documented limit',
   why='用例构造 16384 行内容（实测 294954 字节）期望 POST 成功，但产品对脚本内容有 256 KiB 上限（internal/hub/validation.go:221 maxScriptContentBytes=256*1024），返回 400 script content too large。现场边界复验：恰好 262144 字节 → 200；294954 字节 → 400。产品行为与文档/单测（definitions_test.go:101-122）一致，是测试夹具过大。',
   evidence='probe: 294954 bytes -> HTTP 400 {"error":"script content too large: 294954 bytes (max 262144)"}; exact 262144 -> HTTP 200; gap-ui.spec.ts:719',
   repro='node probe-script-limit.cjs', severity='low', product_defect=False),
 'p23-detail-layout::P23-02 任务详情单页分区且字段多列': dict(
   category='HARNESS_ENV', subcategory='data precondition (empty task list)',
   why='firstIds() 取 /api/tasks 第一行；运行时任务列表为空 → taskId=undefined → 打开 /tasks/undefined（不存在资源页），因此找不到 概览/定义/目标/执行/调度 分区。任务详情页本身正确：存在任务时手工复现 main 文本含「概览 类型 命令 目标 超时（秒）…定义…目标…执行…调度」。',
   evidence='error: locator("main").getByText(/概览|overview/i) 不可见；手工复现 hasOverview=true；两次运行均失败（14:28 与 14:47），期间任务列表均为空；决定性实验：先建 qa-pw-20261018-precond-task-8c374d24 再单跑 P23-02 → PASSED(2.7s)，随后删除 fixture → GET 404',
   repro='node repro-p23-02.cjs（有任务时 hasOverview=true）；bash precond.sh', severity='medium (套件无法在干净环境运行)', product_defect=False),
 'rbac::RBAC-02/03 Operator/Viewer 读取正常': dict(category='HARNESS_ENV', subcategory='run-config gap (operator/viewer credentials)',
   why='本轮运行未配置 NODESTEER_E2E_OPERATOR_USERNAME/VIEWER_USERNAME/RBAC_PASSWORD，回落到默认 test-operator/test-viewer + 空密码 → HTTP 401。补齐自有前缀 operator/viewer 账号后复跑通过。',
   evidence='error: Login failed: HTTP 401; rerun with qa-pw-20261018-operator-b92018b0 / -viewer-6c322191 -> passed',
   repro='rerun-rbac', severity='n/a (harness config)', product_defect=False),
 'rbac::RBAC-02 Viewer 写操作被拒': dict(category='HARNESS_ENV', subcategory='run-config gap (operator/viewer credentials)',
   why='同上；复跑通过（403 断言成立）。', evidence='rerun-rbac passed', repro='rerun-rbac', severity='n/a', product_defect=False),
 'rbac::RBAC-03 Operator 写操作被拒': dict(category='HARNESS_ENV', subcategory='run-config gap (operator/viewer credentials)',
   why='同上；复跑通过。', evidence='rerun-rbac passed', repro='rerun-rbac', severity='n/a', product_defect=False),
 'rbac::RBAC-04 Operator 本轮节点状态修改被拒': dict(category='HARNESS_ENV', subcategory='run-config gap (operator/viewer credentials)',
   why='同上；复跑通过（operator 改节点状态 403，测试自建 pending 节点并清理）。', evidence='rerun-rbac passed', repro='rerun-rbac', severity='n/a', product_defect=False),
 'rbac::RBAC-08 页面只展示角色允许的操作': dict(category='HARNESS_ENV', subcategory='data precondition (empty task list)',
   why='Operator 分支要求 /tasks 列表里存在「立即运行」链接；复跑时任务列表为空（唯一任务在上一用例结束时已被清理），无行即无链接。属数据前提缺失，非权限缺陷（同文件 RBAC-02/03 读权限与 403 断言均已通过）。',
   evidence='error: getByRole("link",{name:/run now|立即运行/}).first() 不可见（rbac.spec.ts:107）；补齐凭据后复跑 6 通过 1 失败，仅此用例；决定性实验：先建 qa-pw-20261018-precond-task-8c374d24 再单跑 RBAC-08 → PASSED(4.6s)', severity='low', product_defect=False),
 'safe-ui::SAFE-01 所有11表格搜索/无结果/Reset/排序/列持久化/分页': dict(
   category='HARNESS_ENV', subcategory='Playwright actionability vs Radix Select (deterministic)',
   why='点击「每页行数」下拉项时 Radix Select 选项反复被判为「element is not stable」直到 15s actionTimeout。首轮失败在选项 30，复跑失败在选项 40，两次均同一原因 → 确定性而非偶发。应用内不存在 infinite 动画（grep infinite 无命中），同一组件在其它用例（GAP-07/GAP-14/SAFE-05）可正常点击，故属该用例快速遍历 11 张表时下拉展开与表格重渲染叠加的自动化可操作性冲突，非产品缺陷。',
   evidence='error(首轮): getByRole("option",{name:"30"}) - element is not stable; error(复跑): getByRole("option",{name:"40"}) - element is not stable',
   repro='rerun-safe-subset（-g SAFE-01）仍失败', severity='low', product_defect=False),
 'safe-ui::SAFE-05 Schedule类型切换/必填/非法输入/Owner/Misfire': dict(
   category='SUITE_BUG', subcategory='stale expectation after intentional behavior change',
   why='用例在 interval 类型填入间隔 0 后点击「保存」并期待点击后出现校验文案；产品已按缺陷修复 OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED 把保存按钮在 interval<1 时禁用（features/editors/index.tsx:433 disabled=saving||intervalInvalid||runAtPast），校验文案改为内联实时展示（同文件 297-299）。禁用按钮无法点击 → 15s 超时。属用例未随已确认缺陷修复同步。',
   evidence='error: locator resolved to <button disabled aria-busy="false" ...>保存</button> - element is not enabled', severity='low', product_defect=False),
 'safe-ui::SAFE-21 新用户UI创建/重复/三角色保存登录/密码新旧边界': dict(
   category='SUITE_BUG', subcategory='stale contract expectation',
   why='断言 /api/me 返回 {Username, Role}（PascalCase）。产品契约已固定 snake_case：docs/PRODUCT.md:1392「会话对象键名固定 snake_case：token、user_id、username、role、expires」，并由 c4e05bc 给 internal/hub/auth/auth.go Session 加上小写 json tag；现场 /api/me 实测 keys=[expires, role, token, user_id, username]。用例未随契约同步（该 spec 属提交 1dd6778「会话前既有 Playwright 用例，非本次修复」）。',
   evidence='error: toMatchObject - Expected "Role"/"Username"; Received keys expires/role/token/user_id/username; PRODUCT.md:1392', severity='low', product_defect=False),
 'safe-ui::SAFE-23 Script全字段/启停/Clone参数环境/删除Tombstone与Agent cache': dict(
   category='REDLINE_LIMITED', subcategory='needs agent local state access',
   why='要求 NODESTEER_E2E_SCRIPT_CACHE_INSPECT（读取 Agent data_dir/state.db 的脚本缓存），需要 SSH 到 Agent 节点，超出本轮红线。',
   evidence='error: Scoped cache inspector required (safe-ui.spec.ts:695)', severity='n/a (out of authorized scope)', product_defect=False),
 'safe-ui::SAFE-24 仅新QA Admin自身UI降为Viewer与旧会话权限收敛': dict(
   category='SUITE_BUG', subcategory='cascade from SAFE-21 + stale prefix',
   why='读取 ui-users.jsonl 中 final:true 记录，要求用户名前缀 qa-20260930-101945-web-user-（上一轮运行前缀）。该记录由 SAFE-21 在第 648 行写入，而 SAFE-21 在第 641 行已失败，故记录不存在 → 级联失败；同时硬编码旧运行前缀，本轮 qa-pw-20261018- 前缀下永远不匹配。',
   evidence='error: TypeError: Cannot read properties of undefined (reading username) (safe-ui.spec.ts:731); ui-users.jsonl 只有 1 条 token 记录，无 final:true',
   severity='low', product_defect=False),
}

rows = res['tests']
for t in rows:
    t['canonical_file'] = (t.get('spec_file') or t['file']).replace('.spec.ts', '')

# Merge first-run and rerun records per (canonical_file, title); rerun wins as final status.
merged = {}
for t in rows:
    key = (t['canonical_file'], t['title'])
    rec = merged.setdefault(key, {'canonical_file': t['canonical_file'], 'title': t['title'],
                                  'suite': t['suite'], 'line': t['line'], 'attempts': []})
    rec['attempts'].append({'run': t['file'], 'status': t['status'], 'duration_ms': t['duration_ms'],
                            'start_time': t['start_time'],
                            'error': (t['error'] or '')[:600], 'trace': [a for a in t['attachments'] if a and a.endswith('.zip')]})
for rec in merged.values():
    rec['attempts'].sort(key=lambda a: a['start_time'] or '')
final = []
for rec in merged.values():
    last = rec['attempts'][-1]
    rec['final_status'] = last['status']
    rec['final_run'] = last['run']
    rec['rerun_changed'] = len(rec['attempts']) > 1 and rec['attempts'][0]['status'] != last['status']
    final.append(rec)

classified = []
skipped_list = []
for rec in final:
    ever_failed = any(a['status'] == 'failed' for a in rec['attempts'])
    if rec['final_status'] == 'skipped':
        skipped_list.append({'file': rec['canonical_file'], 'title': rec['title'],
                             'reason': rec['attempts'][-1]['error'] or 'test.skip condition (see notes)'})
        continue
    if not ever_failed:
        continue
    key = f"{rec['canonical_file']}::{rec['title']}"
    meta = C.get(key)
    if meta is None:
        meta = dict(category='UNCLASSIFIED', subcategory='', why='', evidence=rec['attempts'][-1]['error'][:300], severity='unknown', product_defect=None)
    classified.append({
        'file': rec['canonical_file'], 'suite': rec['suite'], 'title': rec['title'], 'line': rec['line'],
        'final_status': rec['final_status'], 'final_run': rec['final_run'],
        'attempts': rec['attempts'], 'rerun_changed': rec['rerun_changed'],
        'category': meta['category'], 'subcategory': meta.get('subcategory', ''),
        'why': meta['why'], 'evidence': meta['evidence'], 'severity': meta['severity'],
        'product_defect': meta['product_defect'], 'repro': meta.get('repro'),
        'error_excerpt': rec['attempts'][-1]['error'],
    })

# Reruns that turned a first-run failure into a pass (evidence of flake / config fix)
recovered = [{'file': r['canonical_file'], 'title': r['title'],
              'first_run': r['attempts'][0]['status'], 'final': r['final_status']}
             for r in final if len(r['attempts']) > 1 and r['attempts'][0]['status'] == 'failed' and r['final_status'] == 'passed']

buckets = {}
still_failing = []
for c in classified:
    buckets.setdefault(c['category'], []).append(f"{c['file']}::{c['title'][:60]}")
    if c['final_status'] == 'failed':
        still_failing.append(c)
    c['final_status'] = c['final_status']

(OUT / 'failures-classified.json').write_text(json.dumps({
    'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    'base_url': 'http://192.168.100.209:18080',
    'build': 'dddee4f0-e2e-repair-20261018-final',
    'category_legend': {
        'PRODUCT_DEFECT': '① 产品缺陷',
        'HARNESS_ENV': '② harness/环境问题（选择器过时、依赖本地服务、时序、数据前提、运行配置）',
        'SUITE_BUG': '③ 套件本身写错（陈旧期望、竞态、夹具越界、级联依赖）',
        'REDLINE_LIMITED': '② 变体：需要触碰 Agent 节点/客户机 SSH，超出本轮授权红线，未执行',
    },
    'summary': {k: len(v) for k, v in buckets.items()},
    'product_defect_count': sum(1 for c in classified if c['product_defect']),
    'product_defects_confirmed': [c['file'] + '::' + c['title'] for c in classified if c['product_defect']],
    'still_failing_count': len(still_failing),
    'recovered_by_rerun_or_precondition': recovered,
    'skipped': skipped_list,
    'failures': classified,
}, ensure_ascii=False, indent=2))

# run summary
status_counts = {}
first_run_counts = {}
dur = {}
for t in rows:
    status_counts[t['status']] = status_counts.get(t['status'], 0) + 1
    if not ((t['file'].startswith('rerun') or t['file'].startswith('precond') or t['file'].startswith('trace'))):
        first_run_counts[t['status']] = first_run_counts.get(t['status'], 0) + 1
    dur.setdefault(t['canonical_file'], 0)
    dur[t['canonical_file']] += (t['duration_ms'] or 0)
(OUT / 'run-summary.json').write_text(json.dumps({
    'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    'suite': 'web/e2e (Playwright, chromium, workers=1, retries=0)',
    'base_url': 'http://192.168.100.209:18080',
    'build': 'dddee4f0-e2e-repair-20261018-final',
    'total_tests': len(merged),
    'first_run': {'executed_test_results': len(rows) - sum(1 for t in rows if (t['file'].startswith('rerun') or t['file'].startswith('precond') or t['file'].startswith('trace'))),
                  'passed': first_run_counts.get('passed', 0),
                  'failed': first_run_counts.get('failed', 0),
                  'skipped': first_run_counts.get('skipped', 0)},
    'still_failing_by_category': {k: sum(1 for c in still_failing if c['category'] == k)
                                  for k in sorted({c['category'] for c in still_failing})},
    'final_status_after_reruns': {
        'passed': sum(1 for r in final if r['final_status'] == 'passed'),
        'failed': sum(1 for r in final if r['final_status'] == 'failed'),
        'skipped': sum(1 for r in final if r['final_status'] == 'skipped'),
    },
    'skipped_tests': [f"{r['canonical_file']}::{r['title']}" for r in final if r['final_status'] == 'skipped'],
    'still_failing_tests': [f"{c['file']}::{c['title']} [{c['category']}]" for c in still_failing],
    'recovered_by_rerun': [f"{r['file']}::{r['title'][:70]}" for r in recovered],
    'per_file_duration_ms': dur,
    'category_counts': {k: len(v) for k, v in buckets.items()},
    'notes': [
        '首轮全量 124 用例；失败 23 条，逐条复跑/复现后分类见 failures-classified.json。',
        '未确认任何产品缺陷：所有失败落在 harness/环境（②）、套件写错（③）与红线受限（②变体）。',
        '未执行 NODESTEER_E2E_ALLOW_SHARED_SETTINGS（全局设置禁改）与需客户机 SSH 的 APP_ELF/GUEST_INSPECT/SCRIPT_CACHE_INSPECT。',
    ],
}, ensure_ascii=False, indent=2))
print(json.dumps({'buckets': {k: len(v) for k, v in buckets.items()}, 'product_defects': sum(1 for c in classified if c['product_defect']), 'recovered': len(recovered)}, ensure_ascii=False, indent=2))
