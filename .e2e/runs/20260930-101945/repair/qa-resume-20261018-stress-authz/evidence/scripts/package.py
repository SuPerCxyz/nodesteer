#!/usr/bin/env python3
"""Package final deliverables into .e2e/runs/.../qa-resume-20261018-stress-authz/."""
import json
import os
import sys
from collections import Counter

WORK = '/tmp/opencode/nodesteer-qa-20261018-stress-authz'
RUN = '/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-stress-authz'

stress = json.load(open(os.path.join(WORK, 'stress-results.json')))
authz = json.load(open(os.path.join(WORK, 'authz-matrix.json')))
repro_s = json.load(open(os.path.join(WORK, 'repro-schedules.json')))
deadlock = json.load(open(os.path.join(WORK, 'deadlock-probe.json')))
cleanup = json.load(open(os.path.join(WORK, 'fixture-cleanup.json')))

# ---------- authz-matrix.json: add corrected verdict notes ----------
rows = authz['rows']
by_spec = {}
for r in rows:
    by_spec.setdefault(r['spec'], []).append(r)


def authorized(perm, role, has_agent_token=False):
    if perm in ('public', 'probe'):
        return True
    if perm == 'agent':
        return has_agent_token
    if role == 'anon':
        return False
    if perm == 'read':
        return True
    if perm in ('write', 'admin'):
        return role == 'administrator'
    if perm == 'run':
        return role in ('administrator', 'operator')
    return False


violations = []
for r in rows:
    if r['spec'].startswith('artifact_download_agent'):
        continue  # agent-token paths verified separately
    if not authorized(r['perm'], r['role']):
        want = 401 if r['role'] == 'anon' else 403
        if r['status'] != want:
            violations.append(r)

authz['verdict_notes'] = {
    'matrix_rows': len(rows),
    'unauthorized_rows_excluding_agent_token_paths': sum(
        1 for r in rows if not authorized(r['perm'], r['role'])
        and not r['spec'].startswith('artifact_download_agent')),
    'unauthorized_row_violations': len(violations),
    'privilege_escalations': 0,
    'existence_oracle_mismatches_unauthorized_roles': 0,
    'agent_token_paths': {
        'note': 'artifact_download_agent_header / artifact_download_agent_query with the valid '
                'registration token return 200 (existing) / 404 (nonexistent) for any caller; this is '
                'the designed Agent identity path of withAgentOrUserAuth, not an escalation. Without a '
                'token the same endpoint returns 401 (artifact_download_user, role anon).',
        'observation': 'the registration token is accepted via ?agent_token= query string, which can '
                       'appear in access logs / referrers (existing design; informational only)',
    },
    'status_distribution': {str(k): v for k, v in sorted(Counter(r['status'] for r in rows).items())},
    'fix_verification': {
        'authz_order_403_before_404': 'verified: every unauthorized role received 403 for both existing '
                                      'and nonexistent ids on revoke-credential / assign / deploy / cancel / '
                                      'retry / PUT / DELETE sub-resources (116 x 403, 115 x 401, 0 oracle mismatches)',
        'anon_401': 'verified on all authenticated endpoints (login/oidc/agent-binary/healthz/readyz public by design)',
        'not_covered': 'the 5 defects below are real exceptions found by this sweep',
    },
}
with open(os.path.join(RUN, 'authz-matrix.json'), 'w') as f:
    json.dump(authz, f, indent=1)

# ---------- findings.json ----------
def rows_of(spec, filt=None):
    out = [r for r in rows if r['spec'] == spec]
    if filt:
        out = [r for r in out if filt(r)]
    return [{'role': r['role'], 'variant': r['variant'], 'status': r['status'],
             'error': r['error'], 'path': r['path']} for r in out]


findings = [
    {
        'id': 'QA-20261018-F1', 'severity': 'P1', 'kind': 'connection_pool_deadlock_hang',
        'title': '连接池嵌套查询死锁：≥8 并发 GET /api/transfers（及 DELETE /api/nodes）永久挂起，挂起期间全 Hub 数据库端点阻塞',
        'endpoints': ['GET /api/transfers', 'DELETE /api/nodes/{id}'],
        'actual': '7 并发 GET /api/transfers 全部 200（max 8.5ms）；8 并发（31 条 transfer 数据）全部挂起至客户端超时（10s），'
                  '挂起期间 GET /api/tasks 需 3024ms（正常 ~1ms）；16 并发 DELETE /api/nodes 在压力测试中客户端 30s 超时，'
                  '48 个节点仅删除 1 个；客户端断开（ctx cancel）后恢复。',
        'expected': '并发读/删除请求应在 busy_timeout/超时机制内完成或明确失败，不得无限挂起并拖垮全库请求',
        'root_cause': 'ListFileTransfers（internal/store/sqlite_file_transfers.go:55-75）在遍历 rows（占用 1 条池连接）时，'
                      '对每行调用 listFileTransferTargets 再申请第 2 条连接；OpenSQLite SetMaxOpenConns(8)。'
                      '≥8 个并发调用者各持 1 条连接等待第 2 条 → 连接池死锁，只能靠客户端断开释放。'
                      'NodeManager.Delete（internal/hub/node.go:445）内部调用 ListFileTransfers，因此并发删除节点同样触发。',
        'evidence': ['evidence/deadlock-probe.json', 'evidence/hub-evidence-probe.log（7 条 context canceled）',
                     'evidence/stress-results.json（anomalies: batch:nodes_delete TimeoutError）'],
        'repro': '对隔离实例：先建 1 个 transfer；用 barrier 同时发起 7 个 GET /api/transfers（全部 200）；'
                 '扩到 31 个 transfer 后同时发起 8 个 GET /api/transfers（全部挂起超时）；'
                 '期间另发 GET /api/tasks 观察阻塞；脚本见 evidence/scripts/deadlock_probe.py',
        'raw': deadlock['probes'],
    },
    {
        'id': 'QA-20261018-F2', 'severity': 'P1', 'kind': 'sqlite_busy_leak_on_concurrent_schedule_delete',
        'title': 'DELETE /api/schedules 并发 16 时约 33% 失败：409 + 原文 "database is locked (5) (SQLITE_BUSY)"，且无 ERROR 日志',
        'endpoints': ['DELETE /api/schedules/{id}'],
        'actual': '16 并发 × 3 轮：16/48 返回 409，响应体 {"error":"database is locked (5) (SQLITE_BUSY)"}，'
                  '失败请求等待约 5000ms（busy_timeout）；独立复现 17/48 失败；Hub 日志无任何 ERROR 行。',
        'expected': '并发删除应成功或按归一化语义失败（503 storage busy / 404），不得把驱动原文回给客户端，且应记录 error 日志',
        'root_cause': 'ScheduleManager.Delete（internal/hub/schedule.go:150-172）在 runMutationTx（_txlock=immediate 写事务）内，'
                      '第 166 行用外层 ctx 执行 sm.store.GetSchedule（非 txctx）→ 持写锁同时向连接池申请第 2 条连接；'
                      '16 并发时池（8）被等待写锁的事务占满，等待者 busy_timeout 5s 后 SQLITE_BUSY；'
                      'API 层 handleScheduleByID 用 writeErr(409, err.Error()) 原样返回，绕过 writeInternalError 的日志+归一化。',
        'evidence': ['evidence/stress-results.json（batches.schedules_delete + sql_leaks 16 条）',
                     'evidence/repro-schedules.json（17/48 失败、max 5043ms）', 'evidence/hub.log（无 ERROR 行）'],
        'repro': '创建 48 个 interval schedule（enabled=false），16 并发 × 3 轮 DELETE /api/schedules/{id}；'
                 '脚本见 evidence/scripts/repro.py / stress.py',
        'raw': {'stress_batches': [b for b in stress['batches'] if b['batch'] == 'schedules_delete'],
                'repro': repro_s['rounds']},
    },
    {
        'id': 'QA-20261018-F3', 'severity': 'P2', 'kind': 'http_500_on_missing_node',
        'title': 'POST /api/nodes 携带不存在的节点 id 返回 500 internal error（应 404）',
        'endpoints': ['POST /api/nodes'],
        'actual': 'admin POST {"id":"qa-nonexistent-…","status":"offline"} → 500 {"error":"internal error"}；'
                  'Hub 日志记录 error="sql: no rows in result set"（已归一化，无原文外泄）',
        'expected': '404 + 归一化文案（与 GET/PUT/DELETE /api/nodes/{id} 一致）',
        'root_cause': 'handleNodes 的 SetNodeStatus 错误分支走 writeInternalError → 500；'
                      'SetNodeStatus（node.go:206-220）对不存在节点返回 sql.ErrNoRows，未映射为 404。',
        'evidence': ['authz-matrix.json rows nodes_post administrator/nonexistent',
                     'evidence/hub.log 行：level=ERROR … method=POST path=/api/nodes error="sql: no rows in result set"'],
        'repro': 'curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" '
                 '-d \'{"id":"no-such-node","status":"offline"}\' http://<hub>:18089/api/nodes  → 500',
        'raw': rows_of('nodes_post', lambda r: r['role'] == 'administrator'),
    },
    {
        'id': 'QA-20261018-F4', 'severity': 'P2', 'kind': 'sql_text_leak',
        'title': '4 个子资源写端点对不存在 id 返回 400 + SQL 驱动原文 "sql: no rows in result set"，且无日志',
        'endpoints': ['POST /api/nodes/{id}/revoke-credential', 'POST /api/transfers/{id}/retry',
                      'POST /api/transfers/{id}/cancel', 'POST /api/executions/{id}/cancel'],
        'actual': '管理员（execution cancel 还包括 operator）对不存在 id：'
                  'revoke-credential → 400 {"error":"sql: no rows in result set"}；'
                  'transfer retry/cancel → 400 同上；execution cancel → 400 同上；均无 ERROR 日志',
        'expected': '404 + 归一化文案（如 "node not found" / "transfer not found" / "execution not found"），错误路径应记录日志',
        'root_cause': 'handleNodeByID（revoke 分支）、handleTransferByID（retry/cancel）、handleExecutionByID（cancel）'
                      '将底层 sql.ErrNoRows 经 writeErr(400, err.Error()) 原样返回，绕过 writeInternalError 的归一化与日志。',
        'evidence': ['authz-matrix.json rows：node_revoke_post / transfer_retry_post / transfer_cancel_post / '
                     'execution_cancel_post（administrator+operator, nonexistent）',
                     'evidence/hub.log：以上路径无对应 ERROR 行'],
        'repro': 'curl -s -X POST -H "Authorization: Bearer $TOKEN" http://<hub>:18089/api/transfers/no-such-id/cancel'
                 '  → 400 {"error":"sql: no rows in result set"}',
        'raw': {s: rows_of(s, lambda r: r['variant'] == 'nonexistent' and r['status'] >= 400)
                for s in ('node_revoke_post', 'transfer_retry_post', 'transfer_cancel_post', 'execution_cancel_post')},
    },
    {
        'id': 'QA-20261018-F5', 'severity': 'P3', 'kind': 'silent_success_on_missing_id',
        'title': 'PUT /api/users/{id} 对不存在的用户返回 200 {"ok":"true"}（静默成功，应 404）',
        'endpoints': ['PUT /api/users/{id}'],
        'actual': 'admin PUT /api/users/qa-nonexistent-… {"role":"viewer"} → 200 {"ok":"true"}',
        'expected': '404（用户不存在）',
        'root_cause': 'handleUserByID → store.UpdateUserRole/UpdateUserPassword（sqlite_users_nodes.go:69-82）'
                      '为纯 UPDATE，无 RowsAffected 校验，缺行时仍返回 nil。',
        'evidence': ['authz-matrix.json rows user_put administrator/nonexistent'],
        'repro': 'curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" '
                 '-d \'{"role":"viewer"}\' http://<hub>:18089/api/users/no-such-user  → 200',
        'raw': rows_of('user_put', lambda r: r['variant'] == 'nonexistent'),
    },
    {
        'id': 'QA-20261018-F6', 'severity': 'P3', 'kind': 'method_405_missing_allow_header',
        'title': '多个端点的 405 响应缺少 Allow 头',
        'endpoints': ['/api/tasks', '/api/tasks/{id}', '/api/settings', '/api/groups/{id}', '/api/scripts/{id}',
                      '/api/schedules/{id}', '/api/artifacts/{id}', '/api/nodes', '/api/nodes/upgrade',
                      '/api/transfers', '/api/executions/{id}/cancel', '/api/applications/{id}/state',
                      '/api/oidc/state', '/api/login'],
        'actual': 'PATCH（或不支持的方法）返回 405 但 Allow 头为 None（对比：users/node_id/audit/transfer_id 等正确带 Allow）',
        'expected': '405 应携带 Allow 头',
        'root_cause': '各 handler default 分支 writeErr(405) 未设置 Allow；仅部分 handler（logout/nodes id/users/executions id/transfers 守卫）设置。',
        'evidence': ['authz-matrix.json method_probes（allow=None 的条目）'],
        'repro': 'curl -s -i -X PATCH -H "Authorization: Bearer $TOKEN" http://<hub>:18089/api/tasks  → 405 无 Allow',
        'raw': [p for p in authz['method_probes'] if p['status'] == 405 and not p['allow']],
    },
    {
        'id': 'QA-20261018-F7', 'severity': 'P3', 'kind': 'method_not_rejected',
        'title': '方法守卫缺失：login 接受任意方法；GET /api/tasks/{id}/run 执行 RunNow；应用子资源 revisions/nodes 接受 DELETE',
        'endpoints': ['/api/login', 'GET /api/tasks/{id}/run', 'DELETE /api/applications/{id}/revisions',
                      'DELETE /api/applications/{id}/nodes', 'GET /api/applications/{id}/assign|deploy'],
        'actual': 'GET/PATCH /api/login → 400 invalid request（未 405，且任意方法可携带正确凭据登录）；'
                  'GET /api/tasks/{id}/run → 进入 RunNow 逻辑（返回 400 no target nodes，而非 405）；'
                  'DELETE /api/applications/{id}/revisions → 200（返回版本列表）；DELETE /api/applications/{id}/nodes → 200（返回节点列表）',
        'expected': '不支持的方法统一 405 + Allow；有副作用的方法不应被 GET 触发',
        'root_cause': 'handleLogin 无方法守卫；handleTaskByID 的 run 子路径在方法分支之前直接进入 handleRunNow；'
                      'handleApplicationByID 的 revisions/nodes 子路径无方法检查。',
        'evidence': ['authz-matrix.json method_probes：probe_get_login / probe_patch_login / probe_get_task_run / '
                     'probe_delete_app_revisions / probe_delete_app_nodes / probe_get_app_assign / probe_get_app_deploy'],
        'repro': 'curl -s -i -X DELETE -H "Authorization: Bearer $TOKEN" '
                 'http://<hub>:18089/api/applications/<id>/revisions  → 200',
        'raw': [p for p in authz['method_probes'] if p['name'] in (
            'probe_get_login', 'probe_patch_login', 'probe_get_task_run',
            'probe_delete_app_revisions', 'probe_delete_app_nodes',
            'probe_get_app_assign', 'probe_get_app_deploy')],
    },
]

observations = [
    {'kind': 'static_analysis_not_runtime_verified',
     'title': '基础资源子路径宽容：/api/tasks/{id}/<bogus> 等任意子路径按基础资源处理',
     'detail': 'handleTaskByID / handleScriptByID / handleGroupByID / handleScheduleByID / handleNodeByID / '
               'handleApplicationByID 对未知子路径不返回 404，而是落入基础资源方法分支：'
               '例如 DELETE /api/tasks/{id}/bogus 会删除该任务，GET /api/tasks/{id}/bogus 返回任务本体。'
               '本项为代码静态阅读结论，本轮未做运行时验证（隔离实例已销毁）。',
     'severity_if_confirmed': 'P3'},
    {'kind': 'design_note',
     'title': 'registration token 支持 ?agent_token= 查询参数',
     'detail': 'withAgentOrUserAuth 同时接受 X-NodeSteer-Agent-Token 头与 ?agent_token= 查询参数（用于生成纳管命令）；'
               '查询参数可能进入访问日志/Referer，属既有设计，信息性提示。'},
]

fixed_verified = {
    'txlock_immediate': '81 个并发批次（每批 16 并发）共 1280×200 + 16×409（409 均为 F2 的 schedule 删除路径），'
                        '全部写端点（tasks/scripts/schedules/groups/applications/users/nodes/enrollment/transfers/'
                        'settings/artifacts/revoke）无任何 500/503、无 SQLITE_BUSY 泄露；'
                        '混合读写下（4 写者 + 6 读者，8s）写出 1690 次全部 200、读出 4349 次全部 200。',
    'authz_order': '492 行鉴权矩阵：未授权角色 116×403 + 匿名 115×401，'
                   'revoke-credential/assign/deploy/cancel/retry/PUT/DELETE 子资源对存在/不存在 id 状态码一致，'
                   '0 越权、0 存在性 oracle 不一致。',
    'error_normalization': 'F3/F4 所列 5 条路径未覆盖归一化（返回 500 或 SQL 原文），其余端点未见 SQL 原文外泄。',
    'data_integrity': '抽样回读：48 个用户角色更新全部一致、48 个 transfer 全部 CANCELED 且目标结构完整、'
                      '删除后抽样 GET 全部 404、PRAGMA integrity_check=ok、无重名/重复记录。',
}

findings_doc = {
    'batch': 'qa-resume-20261018-stress-authz',
    'generated_at': '2026-10-18',
    'scope': '隔离实例 192.168.100.209:18089（二进制与最终构建同源 sha256=00e1f375…），'
             '未访问 :18080/:8080 与任何 Agent 节点',
    'defects': findings,
    'observations': observations,
    'fixed_verified': fixed_verified,
    'summary': {
        'p0': 0, 'p1': 2, 'p2': 2, 'p3': 3,
        'note': 'P0=越权/数据破坏；P1=挂起或并发功能性失败；P2=500/原文泄露；P3=契约偏差',
    },
}
with open(os.path.join(RUN, 'findings.json'), 'w') as f:
    json.dump(findings_doc, f, indent=1)

# ---------- instance-ledger.json ----------
ledger = {
    'batch': 'qa-resume-20261018-stress-authz',
    'purpose': '并发压测（全写端点 16 并发 × 3 轮 + 混合读写）+ 全端点鉴权矩阵（3 角色 × 存在/不存在 id × 方法探测）',
    'host': '192.168.100.209',
    'binary': {
        'path': '/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard',
        'sha256': '00e1f375bf67a59ad04dfc6283741cb276ac6be70ce2f21e02d25094845e7a71',
        'same_as_final_build': '/tmp/opencode/nodesteer-build-20261018-final/nodesteer-hub-static-final（sha256 一致）',
    },
    'instance': {
        'web_port': 18089, 'gateway_port': 18090,
        'data_dir': '/tmp/nodesteer-qa-20261018-stress-authz/data（独立 data_dir/config，未复用 repair 生产数据）',
        'config': '独立 yaml（web_addr 0.0.0.0:18089，gateway 127.0.0.1:18090，独立 registration_token/admin）',
        'admin_user': 'qa-stress-admin（独立于 repair 生产账户）',
        'credentials': '本地生成、0600 保存、未打印未入库；跑完已删除',
    },
    'launch_history': [
        {'cycle': 'bootstrap-1', 'launcher': 'setsid nohup（14:30）', 'main_pid': 294685, 'stopped': '14:40（重建，因 fixture 修正后需清库）'},
        {'cycle': 'bootstrap-2', 'launcher': 'setsid nohup（14:40）', 'main_pid': 295384, 'stopped': '14:44（重建，改用 systemd-run）'},
        {'cycle': 'main', 'launcher': 'systemd-run 瞬时单元 nodesteer-qa-stress-20261018.service（14:44:48）',
         'main_pid': 295735, 'stopped': '15:03 systemctl stop'},
        {'cycle': 'evidence-probe', 'launcher': 'systemd-run 瞬时单元 nodesteer-qa-stress-20261018b.service（15:06:40）',
         'main_pid': 299736, 'stopped': '15:09 systemctl stop'},
    ],
    'teardown_verification': {
        'units': 'nodesteer-qa-stress-20261018.service / …b.service 均 inactive 且（--collect）已不存在',
        'ports_18089_18090': 'closed（ss -tlnp 无监听）',
        'processes': '无 qa-20261018-stress-authz 相关进程',
        'data_dir': '/tmp/nodesteer-qa-20261018-stress-authz 已删除（ls: No such file or directory）',
        'verified_at': '2026-10-18T15:09+08:00',
    },
    'scope_guard': {
        'repair_production_18080': '未访问；只读核验其 unit 状态：nodesteer-hub-repair-20260930-101945.service '
                                   'active，MainPID=279199（与开测前一致，未被触碰）',
        'primary_8080': '未访问', 'agent_nodes': '未访问/未触碰',
        'product_source': '未修改任何产品源码',
    },
    'fixture_cleanup': {
        'method': '实例内 API 删除（schedules/tasks/scripts/groups/applications/artifacts/nodes 逐个 DELETE；transfers cancel）',
        'deleted': cleanup['deleted'],
        'failed': cleanup['failed'],
        'remaining_verified': {k: v for k, v in cleanup['remaining'].items() if k != 'users_qa_left'},
        'not_deletable_via_api': {
            'users': len(cleanup['remaining']['users_qa_left']),
            'executions': 1,
            'note': 'users/executions 无 DELETE 端点（405），残留仅存在于随后被销毁的隔离实例数据库中',
        },
    },
    'artifacts': {
        'stress-results.json': '任务 A 全量结果（81 批次 + 混合读写 + 完整性回读 + 5xx/SQL 扫描）',
        'authz-matrix.json': '任务 B 全量矩阵（492 行 + 32 方法探测 + 判定注释）',
        'findings.json': '缺陷清单（P0-P3）+ 观察项 + 今日修复全局核验结论',
        'evidence/': 'hub.log（主实例）、hub-evidence-probe.log（死锁证据）、deadlock-probe.json、'
                     'repro-schedules.json、fixture-cleanup.json、scripts/（全部复现脚本）',
    },
}
with open(os.path.join(RUN, 'instance-ledger.json'), 'w') as f:
    json.dump(ledger, f, indent=1)

print('packaged:')
for fn in ('stress-results.json', 'authz-matrix.json', 'findings.json', 'instance-ledger.json'):
    p = os.path.join(RUN, fn)
    print(' ', fn, os.path.getsize(p), 'bytes')
print('findings:', json.dumps(findings_doc['summary']))
