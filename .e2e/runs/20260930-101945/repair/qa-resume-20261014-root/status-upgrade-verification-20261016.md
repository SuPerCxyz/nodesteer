# 批次状态升档/降档独立核验（2026-10-16）

- 核验范围：`X06`、`X08`、`X14`、`E2E-15`、`FL-12`、`FL-15`、`P04-07`、`P17P18-04`、`P14P15-07`、`P14P15-16`、`G04-01`、`SYS-40`（共 12 条）
- 核验对象：`.e2e/runs/20260930-101945/repair/feature-inventory.json` 台账 + 其 `evidence` 指向的真实批次证据文件
- 核验方式：只读；回到证据文件本身，不看批次自述结论；不采信 `partial-closeout-plan-20261016.md` 的口径
- 判定纪律：只认真实运行证据（UI_MOCK / 静态阅读 / 源码推断不计覆盖）；"没测"≠FAIL；"环境不可做"≠FAIL；转 FAIL 必须有直接反证
- 本轮无任何文件被修改（除本文件），未访问远程主机/浏览器，未运行测试

## 结论汇总

| 用例 | 台账现状态 | 本次裁定 | 置信度 | 一句话理由 |
|---|---|---|---|---|
| `X06` | PARTIAL | 维持 PARTIAL | 中高 | 预选/成功自动选中/取消不覆盖均有真实证据，仅"服务器端上传失败"分支是 UI_MOCK |
| `X08` | PARTIAL | 维持 PARTIAL | 中 | remaining 列的四项其实都已有证据（含吊销取消 2026-10-09）；无直接反证，仅"创建表单慢响应重复点击"由 G04-01 证据反证 |
| `X14` | PARTIAL | 维持 PARTIAL | 中高 | 跨页搜索已实测通过（方案所引缺陷已过期）；缺"批量选择漂移/跨页选择保持" |
| `E2E-15` | PARTIAL | **建议升 PASS** | 高 | 创建/重复/三角色保存+新登录/密码新旧全链真实 UI 证据；无删除用户 API 属产品边界 |
| `FL-12` | PARTIAL | **建议升 PASS** | 中高 | 维护→Dashboard 计数→Run 400 阻断→恢复全链真实 UI+API 实测，限自有节点记录 |
| `FL-15` | PARTIAL | **建议升 PASS** | 高 | 改角色→新登录权限正确 + 旧会话旧 token 即时降权（400→403）均实测 |
| `P04-07` | PARTIAL | 维持 PARTIAL | 中高 | 按钮隐藏已实测（operator+viewer），但非 admin `POST /api/nodes/enrollment` 403 无直接断言 |
| `P17P18-04` | PARTIAL | 维持 PARTIAL + 建议新立低危缺陷 | 中 | manual/schedule 真实样本一致；`system` 无样本且 UI 暴露了产品无法产出的枚举值 |
| `P14P15-07` | PARTIAL | 维持 PARTIAL | 中高 | Revision+1 与版本历史均已实测；仅差排除 `OBS-B07-02`（API 创建应用的保存无请求） |
| `P14P15-16` | PARTIAL | **建议升 PASS** | 中高 | 删除取消/确认 + 关联清理均有真实证据（含 2026-10-14 当前构建） |
| `G04-01` | PARTIAL | **建议转 FAIL** + 新立缺陷 | 高 | 真实证据显示 Group/Script 编辑器保存按钮 pending 未禁用，重复点击产生 2 次 POST |
| `SYS-40` | PARTIAL | 维持 PARTIAL | 中 | retry 次数/间隔/终态/每次日志/一条 execute 审计均有证据；per-attempt 审计属产品口径裁决，无反证 |

**计数：建议升 PASS = 4 条；建议转 FAIL = 1 条；建议新立缺陷 = 2 条（G04-01 编辑器 pending、P17P18-04 系统筛选项）；维持 PARTIAL = 7 条。**

### 与收口方案的分歧（不附和点）

1. 方案 C3 称 `X06`/`P04-07`/`P17P18-04` 可升 PASS：核验后三条各差一块（UI_MOCK 分支 / 单端点 403 / `system` 维度），**不升**。
2. 方案称 `X06` 三项剩余与证据矛盾：**部分成立**——"三项均未执行"确实过期（前两项已实测），但第三项中的服务器端失败分支证据文件自述为 UI_MOCK，仍属未覆盖。
3. 方案把 `G04-01`/`X08` 归因于已开缺陷 `WEB-TASK-PENDING-SAVE`（`web/src/features/tasks/index.tsx:631`）：**归因过期**。该缺陷在 `defect-regression-matrix.json` 中 `regression_status=PASS`，2026-10-04 `GAP-02` 实测"pending 禁用 + 仅 1 次 PUT + revision2"（`qa-resume-20261004/evidence/resume-20261004-gap/task-pending-secret.json`）。真正对应的是 2026-10-15 新观测 `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED`（Group/Script 创建表单，POST）。
4. 方案 `X14`/`X08` "强建议转 FAIL" 依据的 `WEB-EXEC-SEARCH-PAGE` 同样是**基线证据**（`…/20260930-101945/web/evidence/execution-cross-page-search.json`，原始运行返回"没有结果。"）；修复批实测已通过（`SAFE-02`、`GAP-07b`、`regression_status=PASS`），**不构成转 FAIL 依据**。
5. 方案 `P14P15-16` 标"需先补测"：实际 2026-10-04 的 `FULL-02` 已含应用删除"先取消后确认"，2026-10-14 `batch04/FL-09` 又在当前构建上复核了删除确认与 404，**可升**。

### 与本次核验相关的环境事实（供口径判断）

- 2026-10-16 修复批（`implementation-report-20261016.md`）只改 Go 侧 10 个文件，`qa-resume-20261016-verify/deploy-evidence.json` 明示 `web/dist` 与上一版一致 → **前端行为与 2026-10-14/15 批次同一版本**，故 p-table 的编辑器 pending 证据对当前构建仍然有效。
- `qa-resume-20261016-appui`、`qa-resume-20261016-webgap` 两个批次 2026-10-16 15:21–15:26 启动，`evidence/` 仅有 `recon.json`/空目录，**无结论可计入覆盖**；若其后续覆盖 `P14P15-07` 或 `P04-07`，可按本报告"缺口"一节复核后翻档。

---

## X06

1. **台账现状**：`status=PARTIAL`（`baseline_status=PARTIAL`，P1）。`remaining_dimensions` 原文：
   > `["预选 artifact_id/version、内嵌上传成功自动选中、失败/取消不覆盖既有定义三项均未执行。"]`
2. **expected 核心语义**：应用表单能用 `artifact_id` 深链预选制品/版本，应用内嵌上传成功后自动选中新制品，且上传失败/取消不得覆盖已有定义。
3. **证据覆盖核查**（`feature-inventory.json:17464` 起）：
   - **已覆盖（真实运行）**
     - 深链预选：`qa/full-feature-01-05-07-results.json` 中 `FULL-02` `ok:true`（`startTime 2026-10-04T05:38:58Z`），断言体见 `web/e2e/tests/full-feature-ui.spec.ts:190-231`：`/applications/new?artifact_id=<id>` → 版本输入框值 `v1` → 保存 200 → 应用创建成功。
     - 成功自动选中：`qa-resume-20261004/evidence/resume-20261004-gap_more/inline-artifact-upload.json` → `checks[1].auto_selected_artifact_id=cf216f99…`、`auto_selected_version=v2`、`saved_app_id=d1245963…`（`classification=REAL_UI`）。
     - 取消不覆盖：同文件 `checks[0].cancel_upload_zero_post=true`、`existing_preset_unchanged=true`；表单必填失败分支：`checks[1].native_required_zero_post=true`；另有 `evidence/resume-20261004-safe_more/artifact-app-form.json`（`required_zero_post:true`、保存应用 `revision:1`）。
     - 入口存在性（本批）：`qa-resume-20261014-root/p-web-review-20261014.json` 的 `X06-20261014-PWEB` 记录 `embedded_artifact_entry=[发布包管理/选择发布包…/上传新发布包]`。
   - **仍缺**
     - **服务器端上传失败**分支：`inline-artifact-upload.json` 自身 `remaining` 明写 "server-side upload failure rendering tested separately using **UI_MOCK**; not represented by native required validation"。按纪律 UI_MOCK 不计覆盖。
   - **台账口径问题**：`X06-20261014-PWEB` 的 `preselect_verified:false` 只是"本批未验"，与同条目 2026-10-04 的 `FULL-02` 证据冲突；`remaining_dimensions` 的"三项均未执行"整体过期（应改为一项）。
4. **裁定**：**维持 PARTIAL**。置信度中高。理由：两项半语义已有真实证据，但"失败不覆盖"里唯一与"服务器端失败"相关的分支只有 UI_MOCK；不能按"证据已够"升档。
5. 关闭方式：补一条真实的服务端上传失败（如上传接口 5xx/中断）观察预设是否被覆盖，或产品明确"失败=表单校验失败"并豁免。

## X08

1. **台账现状**：`status=PARTIAL`（`baseline_status=FAIL`，P0）。`remaining_dimensions` 原文：
   > `["停止/批量升级/吊销取消、慢响应重复点击、失败后重试、取消编辑不改 Revision/Audit 均未执行。"]`
2. **expected 核心语义**：删除/Run/Stop/批量升级/吊销的"取消=零写、确认=单请求"，加上慢响应重复点击、失败后重试、取消编辑不改 Revision/Audit。
3. **证据覆盖核查**（`feature-inventory.json:17617` 起）：
   - **已覆盖（真实运行）**
     - 删除取消/确认：`GAP-19`（制品，`qa-resume-20261004/evidence/resume-20261004-gap_more/artifact-unreferenced-ui-delete.json`）、`SAFE-12`（任务）、`qa-resume-20261014-web/p-web/results.json → X08`（`script_delete 0/1`、`schedule_delete 0/1`）。
     - Run/Stop 取消与确认：同 p-web（`task_run` 取消 0、确认 1）；`SAFE-15`（`web/e2e/tests/safe-ui.spec.ts:447-498`）实测 `stop_cancel_zero_write:true`、`stop_confirm_requests:1`、终态 CANCELED、且 `/api/audit` 中真实存在该 `cancel/execution` 行。
     - 批量升级/维护取消零写：`SAFE-20` + `qa-resume-20261004/evidence/resume-20261004-safe_more/bulk-selection-keyboard.json`（`upgrade_cancel_zero_write:true`、`maintenance_cancel_zero_write:true`、`selection_preserved_on_cancel:true`）。
     - **吊销取消零写（remaining 点名"未执行"，实际已测）**：`.e2e/runs/20260930-101945/repair/qa-resume-20261009-backend/reenroll-revoke-cancel.json` → 取消后 `post_cancel_revoke_post_requests:0`、`credential_unchanged:true`、节点仍 online（`status:PASS`）。
     - 失败后重试：`GAP-06` → `evidence/resume-20261004-gap_more/enrollment-retry-pending.json`（真实 400 后重试提交、`pending_disabled:true`、`successful_post_count:1`）。
     - 取消编辑不改 Revision：`LEGACY-AB-20261005:X08`（`P08-08`，`qa-resume-20261004/resume-20261005-b-01-results.json`，脚本编辑取消 0 次 PUT、回读 `revision 1`）。
     - pending 单请求：Task `evidence/resume-20261004-gap/task-pending-secret.json`（`pending_save_disabled:true`、`actual_put_requests:1`）、制品 8MiB（`artifact-large-pending.json`）、纳管（`enrollment-retry-pending.json`）。
   - **仍缺 / 需澄清**
     - 创建表单类"慢响应重复点击"：`qa-resume-20261014-web/p-table/evidence/G04-01-pending-submit.json` 显示 Group/Script 编辑器 pending 未禁用并产生第 2 次 POST（真实反证，但表面是"创建保存"，不是本用例列举的破坏性动作）。
     - "取消编辑不改 Audit"：只断言了零写与 Revision，未直接断言 Audit 行（零写可间接支撑）。
   - **口径问题**：`remaining_dimensions` 列的四项在证据集合里基本都能找到对应实测（尤其"吊销取消""失败后重试""取消编辑"），描述整体过期。
4. **裁定**：**维持 PARTIAL**（不改状态）。置信度中。理由：方案"强建议转 FAIL"没有指向本用例语义的直接反证（跨页/选择类问题见 `X14`，编辑器 pending 见 `G04-01`）；`WEB-TASK-PENDING-SAVE` 已回归通过（GAP-02），不能再作为本用例的失败依据。
5. 待澄清项：若产品口径把"慢响应重复点击"理解为全站提交按钮（含创建表单），则本用例应随 `G04-01` 一起转 FAIL——请裁决口径后一并处理；否则 `remaining` 应改写为"仅剩创建表单类按钮的重复点击语义（见 G04-01）"。若"取消编辑不改 Audit"需直接断言，补一条 `/api/audit` 查询即可关闭。

## X14

1. **台账现状**：`status=PARTIAL`（`baseline_status=FAIL`，P0）。`remaining_dimensions` 原文：
   > `["批量目标选择后的行漂移、跨页选择保持、Execution 跨页搜索与 Trigger 组合均未执行。"]`
2. **expected 核心语义**：选中 Node 后经过滤/排序/翻页/列表增删/刷新，批量请求的目标 ID 不漂移；末页删除、pageSize/筛选/总数语义正确；Execution 跨页搜索与 Trigger 筛选正确。
3. **证据覆盖核查**（`feature-inventory.json:18052` 起）：
   - **已覆盖（真实运行）**
     - **Execution 跨页搜索（remaining 点名"未执行"，实际已测）**：`SAFE-02`（`qa-resume-20261004/resume-20261004-safe-results.json` passed；断言体 `web/e2e/tests/safe-ui.spec.ts:85-95`：取 `limit=1&offset=20` 的第 21 条 ID，UI 搜索后断言 `a[href="/executions/<id>"]` 可见）；证据 `evidence/resume-20261004-safe/execution-cross-page-search.json`。Audit 同类：`GAP-07b` + `evidence/resume-20261004-gap07b/audit-cross-page-search.json`（`second_page_hit:true`、`global_response_total:"2"`）。
     - Trigger/状态筛选与 URL：`SAFE-18` + `evidence/resume-20261004-safe/execution-filters.json`、`evidence/resume-20261004-safe/execution-filter-transfer-draft.json`（清空/反向操作）；2026-10-10 只读批 `qa-resume-20261010-runtime/readonly-execution-auth/filter-case-results.json`（manual 561/schedule 7 样本 ID 与 UI 一致）；`p-web-review-20261014.json`（manual 735/schedule 36）。
     - 分页/末页删除/pageSize/搜索：`qa-resume-20261014-web/p-table`（`TABLE-NODES-PAGE` PASS：13 节点数据集，首/前/页/后/末、末页删除 Escape 取消 0 DELETE / 确认 1 DELETE、3→2 行；`p-table-review-20261014.json` verdicts）；搜索/无命中/reset：G03-01/02/03 同批 PASS。
     - 多选取消保持选择：`SAFE-20` + `bulk-selection-keyboard.json`。
   - **仍缺**
     - "批量目标选择后的行漂移"与"跨页选择保持"：`p-web/results.json → X14-20261014-PWEB` 明确 `selection_drift: "not executed"`，全仓证据中未找到任何"选中→过滤/排序/翻页后批量请求 ID 集合"的断言（`grep -rn "drift"` 仅命中台账与计划文本）。
     - "Execution 跨页搜索与 Trigger 组合"：跨页搜索与 Trigger 分别验证过，未验证二者组合（弱相关，属可选补强）。
   - **方案依据过期**：方案以 `WEB-EXEC-SEARCH-PAGE`（OPEN）解释 X14 转 FAIL；该缺陷的 `baseline_evidence` 指原始运行 `.e2e/runs/20260930-101945/web/evidence/execution-cross-page-search.json`（返回"没有结果。"）；`defect-regression-matrix.json → failures[11]` 的 `regression_status=PASS` 且上述 2026-10-04 真实证据已复现通过，当前源码也确实把 `q` 传给服务端（`web/src/features/executions/index.tsx:88`）。
4. **裁定**：**维持 PARTIAL**。置信度中高（"不应转 FAIL"部分置信度高）。理由：跨页搜索维度已被后续真实性证据覆盖且通过，转 FAIL 无依据；但"选择漂移/跨页选择保持"确实一条未测，属 expected 明确列举的核心语义（批量请求 ID 不漂移）。
5. 关闭方式：独占批构造大数据集，选中若干行后执行 过滤/排序/翻页/删除/刷新，断言批量请求 payload 的 ID 集合与 UI 勾选一致（含跨页保持），即可升 PASS。

## E2E-15

1. **台账现状**：`status=PARTIAL`（`baseline_status=PASS`，P0）。`remaining_dimensions` 原文：
   > `["未创建用户（无删除用户 API，共享权威上会留下不可清理对象）；本批未执行。"]`
2. **expected 核心语义**：用户生命周期——创建用户、登录、修改角色、新会话权限正确。
3. **证据覆盖核查**（`feature-inventory.json:14221` 起）：
   - **已覆盖（真实运行）**
     - 创建/重复：`qa-resume-20261004/resume-20261004-safe01_21-results.json`（`SAFE-21` passed，`startTime 2026-10-04T16:06:07Z`；`SAFE-01` 本批失败不涉及本用例）+ `evidence/resume-20261004-safe01_21/user-lifecycle-ui.json`：`username=qa-resume-…-user-210f47eb`、`duplicate_http=400`。
     - 登录 + 改角色 + 新会话权限：同文件的 `roles[]` 三条（operator/administrator/viewer），每条 `role_saved_and_reload:true`、`login_http:200`、`me_role` 与角色一致 → "新会话权限正确"。
     - 密码重置：同文件 `old_password_http`/`new_password_http`（值已脱敏，状态码存在）。
     - 越权/读写边界：`HISTORICAL-C-CR-APP-20261008`（`resume-20261005-cr-01-results.json`，RBAC-02/03/04/06/07/08 全 passed）。
     - 旧会话行为：`user-role-downgrade-live.json`（管理员降权后旧 operator token `me_role=viewer`、取消权限 400→403）+ `user-role-self-demote-ui.json`（同页降权 UI 收敛，`new_user_button_enabled_after_self_demote:false`）。
   - **仍缺**
     - 语义维度无缺。`remaining` 所述"未创建用户"与本用例证据直接矛盾（创建动作确有真实 UI 执行）；"无删除用户 API"已由 `P20-05 无删除用户功能`（`resume-20261005-b-01-results.json` passed）确认为产品边界，且账号按 Viewer 保留（`retention` 字段）。
   - **残留风险（非覆盖缺口）**：共享权威上留下一个不可清理的 Viewer 账号；属流程/环境事项，应在台账里写成边界说明而非"未执行"。
4. **裁定**：**建议升 PASS**。置信度**高**。理由：创建→重复→三角色保存/新登录→密码新旧→越权拒绝，全部有真实 UI/API 运行证据，且无任何反证（`OBS-20261014-NAV-ROLE-UNFILTERED` 是同批 info 级观察，页面本体仍显示管理员限制，不构成权限未生效）。
5. 建议同步：把 `remaining_dimensions` 改为"无删除用户 API，测试账号按 Viewer 保留（产品边界，P20-05 已测）"。

## FL-12

1. **台账现状**：`status=PARTIAL`（`baseline_status=PASS`，P0）。`remaining_dimensions` 原文：
   > `["需要节点维护状态变更（本批禁止改 protected 节点状态）；本批未执行。"]`
2. **expected 核心语义**：节点进入维护态后 Dashboard 统计随之变化，任务运行被状态阻断，恢复后统计回位。
3. **证据覆盖核查**（`feature-inventory.json:12989` 起）：
   - **已覆盖（真实运行）**
     - 状态变更确实执行（remaining 说"本批未执行"是 2026-10-14 批的限制，2026-10-04 批已执行）：`GAP-23` passed（`qa-resume-20261004/resume-20261004-gap_more-results.json`），断言体 `web/e2e/tests/gap-ui.spec.ts:815-880` —— 用自有 pending 纳管记录（`POST /api/nodes/enrollment` 200）置 online，再用节点页"操作"菜单真实点"维护"，并 `expect.poll` API `status=maintenance`。
     - Dashboard 统计：`evidence/resume-20261004-gap_more/maintenance-dashboard-run-guard.json`：`online_counts.total/online=5/5` → `maintenance_counts.online=4, degraded=1`（断言 `online-1`、`degraded+1`、`total` 不变），且与前端卡片文本逐项比对（用例内 `dashboardCounts()`）。
     - Run 阻断：`run_http=400`、`run_body.error="node … is not online (status=maintenance)"`、`specific_maintenance_error_visible=true`（UI 文本包含 `status=maintenance`）、`no_executions=true`、`task_command_executed=false`。
     - 恢复：`restored_counts.online=5`，与初始一致。
   - **仍缺 / 限制**
     - 无连接 Agent 参与（`scope` 字段自述"own pending enrollment record only; no Agent daemon/service"）；维护阻断发生在 Hub 侧，符合 expected（expected 未要求 Agent 在线）。
4. **裁定**：**建议升 PASS**。置信度中高。理由：expected 三项（维护→统计变化→运行阻断）全部有真实 UI+API 实测与反向恢复断言；无可指认的反证。若产品要求"真实 Agent 在线节点"才算覆盖，则需另立一条"在线节点维护"用例，而不是把本用例压成 PARTIAL。
5. 建议同步：`remaining_dimensions` 改为"未涉及已连接 Agent（阻断为 Hub 侧判定，证据已声明 scope）"。

## FL-15

1. **台账现状**：`status=PARTIAL`（`baseline_status=FAIL`，P0）。`remaining_dimensions` 原文：
   > `["需要可创建/删除的用户与会话；本批未执行。"]`
2. **expected 核心语义**：管理员改角色后，新登录会话按新角色生效，旧会话行为明确。
3. **证据覆盖核查**（`feature-inventory.json:13091` 起）：
   - **已覆盖（真实运行）**
     - 改角色 → 新登录：`user-lifecycle-ui.json` 三条 `roles[]`（`role_saved_and_reload:true` + `login_http:200` + `me_role` 匹配）。
     - 旧会话行为明确：`user-role-downgrade-live.json` —— 管理员降权后，原 operator 会话 `existing_session_me_role_after_downgrade=viewer`、取消权限探针 `400 → 403`；`user-role-self-demote-ui.json` —— 同一页面无需 reload 即收敛为 Viewer 控件（`new_user_button_enabled_after_self_demote:false`、`admin_form_open_after_self_demote:false`）。
     - 独立账号复核：`HISTORICAL-C-CR-APP-20261008`（CR 批 operator/viewer 新会话的实际读取与写拒绝，`resume-20261005-cr-01-results.json` 全 passed）。
   - **仍缺**：语义无缺。`remaining` 的"需要可创建/删除的用户与会话"在证据里已具备（账号来自 SAFE-21 自有账号），"删除"不可得是产品边界（无删除用户 API）。
4. **裁定**：**建议升 PASS**。置信度**高**。理由：新会话权限与旧会话即时降权两个关键断言均有真实 UI/API 证据，且能相互印证（服务端 me/permission 与前端控件同时翻转）。
5. 建议同步：`remaining_dimensions` 改为"测试账号按 Viewer 保留（无删除用户 API 的产品边界）"。

## P04-07

1. **台账现状**：`status=PARTIAL`（`baseline_status=PASS`，P0）。`remaining_dimensions` 原文：
   > `["非 admin（operator/viewer）会话下按钮隐藏与 403 未在本批实测。"]`
2. **expected 核心语义**：节点页"添加节点"按钮的可见性与后端 admin-only 策略一致。
3. **证据覆盖核查**（`feature-inventory.json:3252` 起）：
   - **已覆盖（真实运行）**
     - 按钮隐藏（remaining 说"未实测"，实际 2026-10-04 已测）：`RBAC-08` passed（`qa-resume-20261004/resume-20261005-cr-01-results.json`），断言体 `web/e2e/tests/rbac.spec.ts:88-104`：operator 与 viewer 两个真实角色会话在 `/nodes` 页 `getByRole('button', {name:/add node|添加节点/i})` 计数为 0。
     - 后端 admin-only 家族（真实 403）：`RBAC-04`（operator 改节点状态 → 403）＋ `qa-resume-20261014-api/p-api/evidence/AUTH-MANAGEMENT-role-write-matrix.json`：operator/viewer 对 `POST /api/nodes`、`DELETE /api/nodes/{id}`、`POST /api/nodes/{id}/revoke-credential` 均 403 `permission denied`。
     - Admin 侧可用：`p-web-review-20261014.json → P04-07-20261014-PWEB`（`POST /api/nodes/enrollment` 200×2，自有子节点）。
   - **仍缺**
     - **非 admin 直接请求 `POST /api/nodes/enrollment` → 403**：全仓证据未找到该断言（`grep "nodes/enrollment"` 命中的都是 admin/未认证矩阵）。现有 admin-only 结论由源码门禁 `internal/hub/api/api.go:606-612`（`if !s.canAdmin(r) → 403`）+ 同族端点 403 推出，属静态阅读，不计覆盖。
4. **裁定**：**维持 PARTIAL**。置信度中高。理由：expected 是两个方向的"一致"断言，UI 侧已实测、后端侧只差这一个端点的直接 403；一行矩阵行即可关闭。方案"升 PASS"结论方向可以接受，但严格口径下仍差一条直接断言。
5. 关闭方式：在 `AUTH-MANAGEMENT-role-write-matrix` 增补 `POST /api/nodes/enrollment`（operator/viewer → 期望 403），复跑该矩阵即升 PASS。

## P17P18-04

1. **台账现状**：`status=PARTIAL`（`baseline_status=PARTIAL`，P1）。`remaining_dimensions` 原文：
   > `["system（系统）未观察到真实样本。"]`
2. **expected 核心语义**：执行列表的 trigger 过滤（manual/schedule/system）控件、URL 与结果正确。
3. **证据覆盖核查**（`feature-inventory.json:10064` 起）：
   - **已覆盖（真实运行）**
     - 控件/URL/结果：`SAFE-18` passed + `evidence/resume-20261004-safe/execution-filters.json`（`triggers_available=[manual,schedule,system]`、清空恢复）与 `execution-filter-transfer-draft.json`。
     - 真实样本一致性：`qa-resume-20261010-runtime/readonly-execution-auth/filter-case-results.json → triggers`：manual `total=561`（10 UI/API 样本 ID 一致）、schedule `total=7`（7 样本一致）、**system `total=0`（api 200、空态匹配）**；`p-web-review-20261014.json`：manual 735 / schedule 36 / `system: no sample`。
   - **仍缺 / 需产品口径**
     - `system` 触发无任何真实样本；且**产品不产出该枚举**：`internal/models/models.go:330-331` 只有 `TriggerManual/TriggerSchedule`（`internal/hub/execution.go`、`internal/agent/agent.go` 仅写入这两值）。
     - **新的不一致观察（非本台账已记）**：前端仍暴露"系统"筛选项与标签映射（`web/src/features/executions/index.tsx:123,133-134,423-426`），而后端无任何生产该值的路径 → UI 提供了一个永远命中不到的枚举值（真实运行证据：上述 `system total=0`、UI 空态匹配）。
4. **裁定**：**维持 PARTIAL**，并**建议新立低危缺陷/观察项**："trigger 筛选项『系统』无数据源"。置信度中。理由：① expected 明确列了 `system`，该维度在任何批次都无真实样本，不能按"证据已够"升 PASS；② 方案以 `TABLE-AUDIT-ERROR` 的 design-unreachable 先例主张 PASS，但两者性质不同——审计表空态是产品设计的**派生结果**，而此处是**UI 主动暴露了一个模型不存在的枚举值**，因此不能直接沿用先例；③ 无任何证据证明筛选功能本身出错，故不转 FAIL。
5. 关闭方式：产品确认"一期不做 system 触发且该选项为保留项"（写进 `docs/PRODUCT.md` 或删除该选项）→ 可升 PASS；若确认应实现 system 触发，则本用例按未实现转缺陷。

## P14P15-07

1. **台账现状**：`status=PARTIAL`（`baseline_status=PARTIAL`，P0）。`remaining_dimensions` 原文：
   > `["未创建自有应用，应用编辑的 Revision+1 与版本历史未验证。"]`
2. **expected 核心语义**：应用编辑保存后 Revision +1，并能看到版本历史。
3. **证据覆盖核查**（`feature-inventory.json:8646` 起）：
   - **已覆盖（真实运行）**
     - Revision +1：`FULL-02` passed（`qa/full-feature-01-05-07-results.json`），断言体 `web/e2e/tests/full-feature-ui.spec.ts:222-227`：改 version 为 `v2` → 保存 → `expect.poll(GET /api/applications/{id})` 匹配 `{version:'v2', revision:2}`（即 revision 1→2）。
     - 版本历史 UI：`qa-resume-20261004/app-live-state-full08.json` 的应用编辑器 UI 文本包含 `版本历史 | r3 | r2 | r1`（真实渲染的修订条目；前端实现见 `web/src/features/editors/index.tsx:1006-1010,1763-1773` 调用 `/applications/{id}/revisions`）。
     - 当前构建的编辑保存真实请求：`qa-resume-20261014-web/batch04/evidence/FL09-08-network-save-unassign.json`（`PUT /api/applications/862cf8d9…` 200，来自 UI 保存）。
   - **仍缺 / 未决**
     - `qa-resume-20261014-web/batch07/results.json` 的 `OBS-B07-02`（"需二次意见"）：对 **API 创建** 的应用，`/applications/<id>` 的 保存按钮 `enabled,type=submit` 连续 3 次点击**无任何请求**、revision 不变、无提示；同批 `FL-09`（UI 创建的应用）保存正常。该观察在 2026-10-16 修复批之后仍然成立（前端 dist 未变），且**未被任何后续批次复核**。
   - 说明：`remaining` 的"未创建自有应用"过期（`FULL-02`/`app-full08` 均为自有应用），但 OBS 未排除使"编辑保存"语义存在真实疑点。
4. **裁定**：**维持 PARTIAL**。置信度中高。理由：主口径（自有应用、UI 创建）的两项目标都有真实证据；但同一语义在"API 创建的应用"上有一条真实运行反例未复核，此时升 PASS 会把一个未决行为标记为通过。复核成本极低。
5. 关闭方式：对 API 创建的应用重跑一次"打开编辑器→改名→保存"，观察是否发出 PUT 且 revision+1（同时确认是否出现无提示的静默失败）；若正常，则本用例可直接升 PASS，并把 `OBS-B07-02` 关闭或另立缺陷。

## P14P15-16

1. **台账现状**：`status=PARTIAL`（`baseline_status=PASS`，P0）。`remaining_dimensions` 原文：
   > `["应用自身的删除确认与关联 Assignment/State/Revision 行为未执行（无自有应用）。"]`
2. **expected 核心语义**：应用删除确认框的取消/确认行为正确，删除后关联的分配/状态/修订历史处理正确。
3. **证据覆盖核查**（`feature-inventory.json:9270` 起）：
   - **已覆盖（真实运行）**
     - 取消/确认（自有应用、真实 UI）：`FULL-02` passed，`deleteViaUI`（`web/e2e/tests/full-feature-ui.spec.ts:99-108`）：点"删除"→"取消"后断言行仍在；再"删除"→"确认"等待 `DELETE /api/applications/{id}` 成功 → 行消失。
     - 当前构建复核（2026-10-14）：`qa-resume-20261014-web/batch04` 的 `FL-09`（自有应用 UI 删除）→ `evidence/FL09-14-delete-confirm-dialog.png`（确认框截图）、`evidence/FL09-15-cleanup-get404.json`（`GET /api/applications/{id}` 404；`GET …/nodes` 返回 null）。
     - 关联对象处理：`qa-resume-20261004/app-cleanup-final.json` —— `app_delete_ui_http:200` + `application_get_after_delete_http:404`；宿主 `unit_unregistered:true`、`host_files_removed:true`、`mainpid_zero:true`；`task_cleanup`（DELETE 200 / GET 404、`execution_history_retained:true`）；`v2_artifact_cleanup`（DELETE 200 / GET 404，共享 v1 制品 `GET 200` 保留）；`application_execution_history_after_delete.count=14`（历史保留）；`v2_app_reference_count_before_delete:0`（删除前无被引用悬挂）。配合 `HISTORICAL-C-CR-APP-20261008` 的宿主 oracle 复验（`app-host-recheck-20261005-01.json`）。
   - **仍缺 / 弱项**
     - 取消侧未做"DELETE 请求计数=0"的显式断言，而是以"行仍可见 + 随后删除成功"间接证明（`deleteViaUI` 内无请求计数）；属可选补强。
   - 说明：`remaining` 的"无自有应用"过期（`FULL-02`/`app-full08`/`FL-09` 都是自有应用）。
4. **裁定**：**建议升 PASS**。置信度中高。理由：expected 的取消/确认与关联清理两侧都有真实证据，其中当前构建（2026-10-14）还有一次独立复核；无任何反证。方案把它列为"需先补测"，与证据不符。
5. 建议补充（非阻塞）：在取消分支加一次 `DELETE` 计数断言（=0），即与 `G04-02` 口径完全一致。

## G04-01

1. **台账现状**：`status=PARTIAL`（`baseline_status=FAIL`，P1）。`remaining_dimensions` 原文：
   > `["Editor 保存按钮 pending 未禁用：重复点击产生第 2 次 POST（唯一名约束避免重复创建）；其它提交控件未穷尽。"]`
2. **expected 核心语义**：所有提交按钮在 pending 期间禁用，重复点击不能重复创建。
3. **证据覆盖核查**（`feature-inventory.json:1365` 起）：
   - **已覆盖（真实运行）**：Task 保存（PUT）、制品 multipart、纳管提交三处 pending 禁用 + 单请求：`GAP-02`/`GAP-06`/`GAP-09`，证据 `qa-resume-20261004/evidence/resume-20261004-gap/task-pending-secret.json`（`pending_save_disabled:true`、`actual_put_requests:1`）、`…/gap_more/enrollment-retry-pending.json`（`pending_disabled:true`、`successful_post_count:1`）、`…/gap_more/artifact-large-pending.json`（`pending_disabled:true`、`upload_requests:1`）。
   - **直接反证（真实运行，非 mock）**：`qa-resume-20261014-web/p-table/evidence/G04-01-pending-submit.json`
     - `checks[0]`（`/groups/new` → `POST /api/groups`，hold 4500ms）：`pending_button.found=true, disabled=false`、`holder[2].phase="APP_POST_EXTRA_WHILE_PENDING", count=2`、`post_count=2`（`post_lines` 两次 POST）。
     - `checks[1]`（`/scripts/new` → `POST /api/scripts`）：`disabled=false`、`APP_POST_EXTRA_WHILE_PENDING count=2`、`post_count=2`。
     - 运行性质：隔离可销毁实例，`qa-resume-20261014-web/p-table/results.json → instance`：`binary_sha256=164ea4aca898…`（与 repair 权威同二进制）、`index_html_identical_to_repair=true`；即**真实产品、真实 UI、真实网络**，不是 UI_MOCK。对象由服务端唯一名约束拒绝（未产生重复对象），但"pending 时禁用"这一子句被直接推翻。
   - **归因纠正**：方案把该剩余归因于 `WEB-TASK-PENDING-SAVE`（Task 编辑 PUT）。该缺陷在 `defect-regression-matrix.json → failures[16]` 中 `regression_status=PASS`，且 2026-10-04 `GAP-02` 已实测修复（见上）。本证据实际对应 `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED`（Group/Script 创建表单，POST；`qa-resume-20261014-root/p-table-review-20261014.json → new_observations`）。
   - **仍缺**：其它编辑器（Application、Schedule、Task 创建）提交按钮的 pending 行为未穷尽（但已发现两处失败，不影响转 FAIL 结论）。
4. **裁定**：**建议转 FAIL**（并**新立缺陷**）。置信度**高**。
   - 直接证明产品行为错误的那一条：`G04-01-pending-submit.json → checks[0].pending_button.disabled=false` 与 `checks[0].holder[APP_POST_EXTRA_WHILE_PENDING].count=2`（脚本编辑器为 `checks[1]` 同型证据）——即在请求 pending 期间按钮既未禁用、重复点击也确实发出了第 2 次 POST。
   - 缺陷建议（供立项）：标题"部分编辑器保存按钮 pending 未禁用，重复点击发重复 POST（至少 Group、Script 创建表单）"；优先级 P2；影响用例 `G04-01`（广义口径下含 `X08` 的"慢响应重复点击"）；建议排查是否与编辑器共用保存组件有关，并覆盖 Application/Task/Schedule 创建表单。
5. 新立缺陷所需的产品修复属单独授权范围，本次只做裁定。

## SYS-40

1. **台账现状**：`status=PARTIAL`（`baseline_status=PASS`，P0）。`remaining_dimensions` 原文：
   > `["per-attempt 审计缺失：每次 attempt 没有独立审计行（仅一条 \"execute\" 审计），这是该用例 expected 中“审计”维度的未满足项，故维持 PARTIAL。", "retry 间隔无产品规范阈值，仅作观测。", "Docker/Compose 执行形态不存在（见 phase8 评估）。"]`
2. **expected 核心语义**：Retry 的次数、间隔、最终状态、每次 attempt 的日志与审计都可观测且正确。
3. **证据覆盖核查**（`feature-inventory.json:16054` 起）：
   - **已覆盖（真实运行）**
     - 次数与终态：`qa/backend-runner/evidence/task.retry-3fa00edc.json`（"Retry真实尝试次数与单Execution"，PASS；`task.retry` 与 `audit.execution-actions` 均为 2026-10-04 real-api-agent）。
     - 间隔/每次日志/UI：`qa-resume-20261010-runtime/next-runtime-batch/sys34-40/business-execution-log-oracle.json` —— `retry=2`、3 次 attempt 各自 BEGIN/END 标记、`attempt_start_monotonic_ns` 实测（差 49,763,362ns 与 61,448,500ns）、stdout/stderr 双流共 18 个分片 `sequence 0..17` 连续、`streams.stdout.bytes=215080`、execution `status=SUCCESS`；同批 `case-result.json` 记录 `SYS-40 canonical_before=PARTIAL / proposed_after=PARTIAL` 与 UI 复核。
     - 审计：`business-linkage-audit-evidence.json` —— `audit.matches=1`、`action_resource="execute/task"`、`resource_id_matches=true`、`checks.exact_task_execute_audit_count_one=true`（一次逻辑 Run 一条 execute 审计，且与 Execution/Task/Script/Node 的 ID 与 revision 对齐）。
   - **仍缺 / 属产品口径**
     - per-attempt 审计行：无（产品按"逻辑 Run"记 1 条）。用例依据 `docs/TEST_PLAN.md:572` 仅一行清单文本，**未规定审计粒度**；`docs/ARCHITECTURE.md` 无相关条款（`grep 审计` 无命中）。
     - retry 间隔无产品阈值（纯观测）；Docker/Compose 执行形态在产品中不存在（Agent 只有 `docker` 部署模式探测 `internal/agent/detect.go`，无 docker/compose 执行器）。
4. **裁定**：**维持 PARTIAL**，**不建议转 FAIL**。置信度中。理由：按判定纪律，"转 FAIL 必须指出直接证明产品行为错误的证据"；现有证据只能证明"审计是每逻辑 Run 一条"，不能证明产品违反任何书面要求（唯一可能的要求文本是 TEST_PLAN 的一行清单，语义开放）。方案"强建议转 FAIL"实际是把**解释口径**当成缺陷，属于应由产品裁决的事项。
5. 待裁决：若产品确认"每次 attempt 必须有独立审计行"，则据同一批证据（`exact_task_execute_audit_count_one=true` 与 3 次 attempt 日志对比）即可转 FAIL 并立缺陷；若确认审计按逻辑 Run 记一条，则本用例的"审计"维度判为满足，`remaining` 只剩"间隔无阈值"与"Docker/Compose 形态不存在"（后者建议单列为产品边界）。

---

## 附：本次核验使用到的关键证据清单（可直接复查）

| 用例 | 关键证据 | 说明 |
|---|---|---|
| X06 | `qa/full-feature-01-05-07-results.json`；`qa-resume-20261004/evidence/resume-20261004-gap_more/inline-artifact-upload.json`；`…/resume-20261004-safe_more/artifact-app-form.json` | 深链预选 / 成功自动选中 / 取消与校验失败零写；服务器端失败仅 UI_MOCK |
| X08 | `…/resume-20261004-safe_more/bulk-selection-keyboard.json`；`…/resume-20261004-gap_more/enrollment-retry-pending.json`；`qa-resume-20261009-backend/reenroll-revoke-cancel.json`；`…/resume-20261004-safe01_21/results.json`（SAFE-15） | 批量升级/维护取消零写；失败重试；吊销取消零写；Stop 取消零写+审计 |
| X14 | `…/resume-20261004-safe-results.json`（SAFE-02）；`…/evidence/resume-20261004-safe/execution-cross-page-search.json`；`…/evidence/resume-20261004-gap07b/audit-cross-page-search.json`；`qa-resume-20261014-web/p-table`（TABLE-NODES-PAGE + G03） | 跨页搜索已通过；分页/末页删除/搜索；选择漂移未测 |
| E2E-15 | `…/resume-20261004-safe01_21-results.json` + `evidence/…/user-lifecycle-ui.json`；`user-role-self-demote-ui.json`；`user-role-downgrade-live.json` | 生命周期全链真实 UI |
| FL-12 | `…/evidence/resume-20261004-gap_more/maintenance-dashboard-run-guard.json` | 维护→统计→Run 400→恢复 |
| FL-15 | `user-lifecycle-ui.json`；`user-role-downgrade-live.json`；`resume-20261005-cr-01-results.json` | 新会话权限 + 旧会话降权 |
| P04-07 | `resume-20261005-cr-01-results.json`（RBAC-08）；`qa-resume-20261014-api/p-api/evidence/AUTH-MANAGEMENT-role-write-matrix.json` | 按钮隐藏+admin-only 家族 403（缺 enrollment 端点行） |
| P17P18-04 | `qa-resume-20261010-runtime/readonly-execution-auth/filter-case-results.json`；`…/evidence/resume-20261004-safe/execution-filters.json` | manual/schedule 样本一致；system total=0 且无产出路径 |
| P14P15-07 | `qa/full-feature-01-05-07-results.json`（FULL-02）；`qa-resume-20261004/app-live-state-full08.json`；`qa-resume-20261014-web/batch04/evidence/FL09-08-network-save-unassign.json`；`batch07/results.json → OBS-B07-02` | Revision+1、版本历史、当前构建保存；API 创建应用保存无请求未决 |
| P14P15-16 | `qa-resume-20261004/app-cleanup-final.json`；`qa-resume-20261014-web/batch04/evidence/FL09-15-cleanup-get404.json`；`web/e2e/tests/full-feature-ui.spec.ts:99-108` | 取消/确认 + 关联清理 |
| G04-01 | `qa-resume-20261014-web/p-table/evidence/G04-01-pending-submit.json`（+ `p-table/results.json → instance`） | pending 未禁用 → 2 次 POST（直接反证） |
| SYS-40 | `qa-resume-20261010-runtime/next-runtime-batch/sys34-40/business-execution-log-oracle.json`、`business-linkage-audit-evidence.json`、`case-result.json`；`qa/backend-runner/evidence/task.retry-3fa00edc.json` | retry/日志/终态/单条 execute 审计 |
