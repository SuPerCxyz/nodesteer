# NodeSteer E2E 75 条 PARTIAL 收口方案（2026-10-16）

- 性质：**只读分析**产物。未修改产品源码、未修改任何既有台账/证据/状态文件、未访问远程环境、未运行测试。
- 唯一写入：本文件。
- 输入：
  - 台账 `.e2e/runs/20260930-101945/repair/feature-inventory.json`（75 条 `status == "PARTIAL"`，`remaining_dimensions`/`covered_dimensions`/`evidence` 为准）；
  - 既往批次与 Root 复核件：`qa-resume-20261014-web/`（batch01–10、p-web、p-table、batch05-users）、`qa-resume-20261014-runtime/`（sys-*、p-cond/p-sched/p-app/p-sysmisc）、`qa-resume-20261014-root/`（各批 Root review、`system-batch-plan-*`、`repair-plan-20261016.md`、`implementation-report-20261016.md`、`pending-decisions-20261016.md`）、`qa-resume-20261016-verify/deploy-evidence.json`、`.e2e/failures.json`；
  - 已确认环境约束：4 个 native repair 节点；Docker 形态可用但 Host 集成容器被 AppArmor 阻塞（FAIL-ENV-HOST-001）；产品无删除用户 API（角色类需隔离可销毁实例，2026-10-14 已有先例）；不得改 protected 节点状态（节点状态类用 owned child）；子 Agent 深度上限=1。
- 结论口径：`C1` 只放能在 ≤3 批内闭合者；`C2` 为需独占窗口或需授权（含产品/安全裁决）者；`C3` 为口径/接受类（设计不可达、产品行为已观测待裁决、证据充分可升档）。

## 0. 总览

| 分类 | 条数 | 构成 |
|---|---:|---|
| C1 可补（3 批） | **26** | B1 回归收口 6 + B2 自有应用 UI 生命周期 10 + B3 Web 小缺口清扫 10 |
| C2 需独占/需授权 | **40** | 隔离实例角色族 6、协议/帧注入与 Docker 独占 4、调度/停机窗口 4、缺陷与产品裁决 6、独占组合批 6、任务/条件/跨页夹具 13、瞬态样本注入 1 |
| C3 口径/接受类 | **9** | 升 PASS 建议 6（含 1 条按先例口径）+ 设计不可达/行为待裁决 3 |
| 合计 | 75 | 与台账 PARTIAL 总数一致 |

- 「建议升 PASS」：**6 条**（X06、E2E-15、FL-15、P04-07、FL-12、P17P18-04），另 2 条（P14P15-07、P14P15-16）在 B2 复核后可就地升档。
- 「建议转 FAIL / 新立缺陷」：**9 条**（6 条强建议：G04-01、X08、X14、SYS-40、P22-10、X16；3 条待产品裁决：G04-04、X19、P12P13-05）。
- 说明：真正"环境受限不可测"的条目大多已在 `BLOCKED` 桶（SYS-24/54/55、AR-31、INV-14、OIDC×4）或已由 `FAIL` 记录缺陷（SYS-27、AR-32 等），故 PARTIAL 侧无整案放弃项，C3 的实质是"设计不可达 + 口径收口"。

---

## 1. C1 批次计划（3 批，共 26 条）

### B1「修复回归收口批 RG-20261016」——6 条

与在飞的 `qa-resume-20261016-verify` 合并执行（修复版二进制已部署：hub `cd8b7f9d…`、agent `b04383bf…`，见 `qa-resume-20261016-verify/deploy-evidence.json`），**不重复建批**。

| # | case_id | 一句话理由 |
|---|---|---|
| 1 | INV-05 | 剩余维度即已修复的 `E2E-20261015-COMMITSYNC-FAILURE-DOUBLE-UNLOCK-PANIC`（P1-3），部署版回归复测即可闭合。 |
| 2 | INV-11 | 剩余维度即 P0-2 回滚毒行/绑定校验修复的 recommended_owner_case，回归复测恢复路径即可闭合。 |
| 3 | SYS-28 | last_execution「成功/失败/false 比较」受 P0-1 时钟偏移缺陷阻塞，修复版解锁后复测。 |
| 4 | E2E-10 | Remote 正向路径随 P0-1 修复解锁；「恢复」维度用 owned child 重连窗口补齐。 |
| 5 | P07_P08-11 | 超长/超时边界即已修复的 `TIMEOUT-CONTENT-VALIDATION-GAP`（P1-4），按新校验回归。 |
| 6 | G04-03 | 组重名 SQL 原文外泄由 P2-5 修复，回归复测 + 跨页错误文案只读扫描。 |

- 关联复测（非 PARTIAL，但同批）：`SYS-27`（FAIL→期望 PASS）、`AR-32`（FAIL→期望 PASS）、`SYS-11/SYS-02/E2E-16`（P1-3 回归集）、`SYS-48/SYS-51/SYS-56`（P0-2 回归集）。
- 夹具/方法：修复版已部署；按 `repair-plan-20261016.md` 的定向回归集执行；`E2E-10` 恢复维度用 owned child 停用/重连（`sys-b07` AR-02/AR-06 模式）。
- 预估风险：中。混版本协议（旧 Hub×新 Agent / 新 Hub×旧 Agent）与恢复语义变更必须按方案回归集逐项断言；若 SYS-27 仍失败需立即回报，不得沿用旧结论。
- 5 路并行：**否**（含全局状态与重连窗口），与 B2/B3 串行错开。

### B2「自有应用 UI 生命周期批 APP-UI-20261016」——10 条

| # | case_id | 一句话理由 |
|---|---|---|
| 1 | P14P15-06 | 缺的只是自有应用的创建后 Revision/Artifact Usage/Node Assignment 呈现。 |
| 2 | P14P15-07 | 编辑保存 Revision+1 与版本历史需在自有应用上按当前版复核（先排除 OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST）。 |
| 3 | P14P15-08 | 多节点分配/取消的写侧行为，随 owned app + owned child 一次完成。 |
| 4 | P14P15-13 | state 页 version/operation/health/error/updated_at 需自有应用分配后逐字段核对。 |
| 5 | P14P15-14 | 无状态/UNKNOWN/healthy/unhealthy/stopped 分支需自有应用部署/停止对照。 |
| 6 | P14P15-15 | systemd/TCP/HTTP/Command 四种健康检查需为自有应用分别配置后采集真实结果。 |
| 7 | P14P15-16 | 应用删除确认与关联 Assignment/State/Revision 清理，随自有应用删除流程核对。 |
| 8 | P14P15-18 | 部署失败恢复链已由 P0-2 修复改造，需在自有应用上重放失败/恢复（含 Journal/Unit/SHA）。 |
| 9 | FL-10 | Deploy→State/Execution→健康/版本/错误一致性需自有应用真实部署。 |
| 10 | FL-11 | Upgrade→Rollback→版本/备份/Unit/Config/Health/Journal 一致性随 P0-2 修复后复验。 |

- 夹具/方法：owned artifact（真实 ELF）→ owned 应用定义（含四类健康检查变体）→ 分配 owned child（`sys-b11/sys-b12/p-app` 已验证的模式）；UI 全链操作 + owned child 上文件 SHA/Unit/Journal oracle；失败注入只对 owned 进程（SIGSTOP）或坏定义。
- 预估风险：中高。fixture 复杂且写 owned child；先复现/排除 OBS-App-Editor-Save；严禁触碰 protected 节点、既有非 owned 应用与 Host 集成族。
- 5 路并行：**可**（每 lane 独立 owned 资源与 owned child；回滚/失败注入段独占其 owned child）。

### B3「Web 小缺口清扫批 WEB-GAP-20261016」——10 条

| # | case_id | 一句话理由 |
|---|---|---|
| 1 | X04 | 仅缺五字段↔原始表达式双向同步与非法表达式反馈的逐步 UI 断言。 |
| 2 | X20 | 仅缺长 ID/SHA/路径/参数/日志与剪贴板安全的小屏渲染核对。 |
| 3 | P16-02 | 上传弹层重试后逐项抓取名称/版本/架构(amd64/arm64)/文件。 |
| 4 | P16-04 | 重复/空文件/超大文件/上传中禁用四项边界随上传弹层一次执行。 |
| 5 | P05B-08 | 仅缺 Node Detail 的 Schedule 列表→Schedule 详情这一条链路点击。 |
| 6 | P03-02 | 仅缺非空「正在执行」行的 dashboard 行链接（一次运行中采样）。 |
| 7 | P05B-10 | 仅缺「任务名落 Task 详情」的一次直接点击证据（自有任务名行）。 |
| 8 | P22-07 | loading「创建中」与慢响应重复点击需一次延迟注入 + 真实 start。 |
| 9 | P22-09 | 「全终态且有失败→聚合 FAILED」需第 5 个自有传输（细粒度 1 次）。 |
| 10 | P22-10 | FAILED 侧改用可控失败目标（ENOSPC/非白名单）替代只接包不回报的 host001。 |

- 夹具/方法：自有脚本任务 1–2 次运行（P03-02/P05B-10）；自有 schedule（P05B-08）；自有 artifact 上传（P16-02/04）；≤3 次自有 Transfer（P22-07/09/10，失败目标用 P22-11/P22-14 已验证的 ENOSPC/非白名单法）。UI 断言为主，只读证据留档。
- 预估风险：低-中。Transfer 写不可删除历史（≤3 条，需登记并在报告中注明计数变化）；上传弹层此前打不开，若复现按"重试并如实记录"处理，不得改产品。
- 5 路并行：**可**（资源互不重叠；Transfer 段与历史计数错开登记）。

---

## 2. C2 清单（40 条，逐条给出所需授权/独占）

### 2.1 隔离实例·角色族（6 条）——授权：按 2026-10-14 先例启动隔离可销毁 authority（同二进制）+ 创建 owned 账号，用完整体销毁、零持久化残留

| # | case_id | 一句话理由 + 所需动作 |
|---|---|---|
| 1 | P04-06 | operator/viewer 全量受限操作（可见性/403/状态不变）需隔离实例受限角色会话——共享权威无删除用户 API，无法创建可清理账号。 |
| 2 | P14P15-12 | 受限角色真实部署/定义修改请求需隔离实例（含 1 个 owned 应用/child 可另授权）。 |
| 3 | P21-08 | Settings 的 GET/PUT 403 与按钮策略需隔离实例 operator/viewer 会话直接断言。 |
| 4 | P20-04 | 创建/重复用户名/错误输入需隔离实例账号；同时复核空密码缺陷（MAIN-USER-EMPTY-PASSWORD）在修复版是否已修。 |
| 5 | X13 | 多标签退出/Token 替换后保护页面与"实际收敛时间"需隔离实例多会话长窗观察。 |
| 6 | P03-09 | no-app/no-failed-execution 空态需隔离空 authority（无任何自有资源干扰）。 |

### 2.2 协议/帧注入与 Docker 独占（4 条）

| # | case_id | 一句话理由 + 所需授权/独占 |
|---|---|---|
| 7 | P11-08 | 同 Execution ID/Key 重复投递幂等无 UI/HTTP 触发路径，需**协议帧级伪造授权**（owned child 信道注入）。 |
| 8 | E2E-18 | Docker/Compose 重建 + B 恢复后 UI 读回需**独占 docker 窗口**（native-4 daemon；pinned 镜像按 digest 导入，不得 pull/rebuild/retag）。 |
| 9 | AR-04 | Docker 重建后的幂等与 Desired State 对账需同上独占 docker 批次（补齐 attempt02 未解释的 desired 1573 vs 本地 1575）。 |
| 10 | AR-08 | duplicate/lost/reordered 通知帧注入需**独占帧注入窗口**（owned child + 帧代理，sys-b02/03/04 同类 harness）。 |

### 2.3 调度/停机窗口（4 条）——授权：owned child 停用/断连/补偿窗口；不得动 protected 节点

| # | case_id | 一句话理由 |
|---|---|---|
| 11 | P12P13-05 | 过去时间 One-Time「未观察到拒绝」属待产品裁决观察（OBS-20261014-PAST-ONETIME-SAVE-ENABLED）；空 run_at 服务端拒绝口径一并确认。 |
| 12 | P12P13-06 | Hub-Owner vs Agent-Owner 的真实调度执行差异需独占调度窗口对照。 |
| 13 | P12P13-07 | allow_offline×hub_online_required 组合投递行为需 owned child 断连/停机窗口。 |
| 14 | P12P13-08 | run_once/skip 漏触发真实场景（停机后补偿）需 ≥1 个 misfire 窗口的补偿复现。 |

### 2.4 缺陷与产品裁决（6 条）

| # | case_id | 一句话理由 + 所需授权 |
|---|---|---|
| 15 | G04-01 | 剩余维度=编辑器保存 pending 未禁用→重复 PUT，即已开缺陷 `WEB-TASK-PENDING-SAVE`（OPEN，web/src/features/tasks/index.tsx:631）；需**产品修复授权或明确豁免**后复测。 |
| 16 | X08 | 同缺陷（affected_cases 含 X08）+ 停止/批量升级/吊销取消、慢响应重试需注入批。 |
| 17 | G04-04 | 「Enter 不提交 / Escape 焦点未返回（落 BODY）」需**产品裁决**是否属缺陷，再定键盘补测范围。 |
| 18 | X19 | 同 G04-04（同一组键盘观察，X19 是它的泛化用例）。 |
| 19 | X14 | 「Execution 跨页搜索」即已开缺陷 `WEB-EXEC-SEARCH-PAGE`（OPEN，仅过滤当前服务端页）；需**修复裁决**；批量目标选择漂移需大数据集独占批。 |
| 20 | X16 | secret 默认值/Revision/Execution/Audit 遮蔽与 Registration Token/Authorization/Cookie 泄漏面需**安全复测授权**；主运行缺陷 `WEB-SECRET-DEFAULT` 与修复版证据冲突，需一次复测定性。 |

（SYS-40 的 per-attempt 审计与 Docker/Compose 形态议题见 C3 §3.2，不重复计入 C2。）

### 2.5 独占组合批（6 条）——无新授权，但超出 3 批预算、需独立批次

| # | case_id | 一句话理由 |
|---|---|---|
| 21 | P19-03 | 六类动作（执行/取消/部署/升级/节点状态/角色密码）审计核对依赖多类夹具组合（owned 应用 + 节点状态 + 隔离账号）。 |
| 22 | X18 | 批量部分成功/Assign 失败可定位重试需失败注入，且写不可删除 Transfer 历史。 |
| 23 | X12 | 分配变化→跨页与下次执行、空目标/重叠组/离线/禁用/维护/Capability 失配需多次真实执行对照。 |
| 24 | X15 | 日志分片与断线恢复后的 Follow/搜索/流过滤/Copy 需长时运行 + 断连注入独占窗口。 |
| 25 | P09P11-08 | app_operation 四种操作逐项与执行需自有应用夹具批（可随 B2 夹具低成本完成，超 10 条上限）。 |
| 26 | P14P15-09 | Managed Applications→应用详情回链点击，随 B2 夹具低成本完成（超 10 条上限）。 |

### 2.6 任务/条件/跨页夹具批（13 条）——需自有任务/脚本/分组/条件 + owned child（含节点标签写）夹具批

| # | case_id | 一句话理由 |
|---|---|---|
| 27 | P09P11-07 | app_deploy 的应用选择控件逐项与真实部署执行需自有应用 + 可部署目标。 |
| 28 | P09P11-09 | 单节点/去选节点的单独断言（UI 小项，随任务目标 fixture）。 |
| 29 | P09P11-10 | 单组/多组/去选组需自有分组 fixture。 |
| 30 | P09P11-11 | 标签匹配节点变化需 owned child 节点标签写（protected 节点只读约束内不可做）。 |
| 31 | P09P11-12 | 类型/目标切换后保存 payload 无旧字段的写侧证据。 |
| 32 | P09P11-15 | 8 种 metric/6 种 operator 未逐项枚举，条件设置/清除实际效果待验。 |
| 33 | P09P11-16 | Remote 条件 last_execution/task_id 校验与节点校验未验证（修复版后一并复测）。 |
| 34 | P09P11-17 | AND 结构经编辑入口/API 的实际结构与执行效果未验。 |
| 35 | X09 | Task 四类型往返隐藏字段（script_id/application_id/app_operation）与 Node/Group/Label 往返未验。 |
| 36 | X11 | 条件编辑回显、AND 未设置语义、清除最后一个条件后的真实执行/阻断未验。 |
| 37 | FL-02 | Agents→Agent Detail→Task→节点任务过滤/回链/目标一致需自有任务跨页链路。 |
| 38 | FL-03 | Label→Label Group→Task Target→标签改变后跨页同步需标签/分组 fixture（含节点标签写）。 |
| 39 | FL-04 | Script CRUD→Task Script→副本独立/Revision/SHA/引用需脚本+任务跨页链路。 |

### 2.7 注入窗口·瞬态样本（1 条）

| # | case_id | 一句话理由 |
|---|---|---|
| 40 | P17P18-03 | PENDING（等待中）为瞬态且现网无样本，需帧注入延迟窗口采样；若只读接受则并入 C3 口径。 |

> C2 合计：6（2.1）+ 4（2.2）+ 4（2.3）+ 6（2.4）+ 6（2.5）+ 13（2.6）+ 1（2.7）= **40 条**。

---

## 3. C3 清单（9 条：状态建议 + 正式措辞）

### 3.1 口径收口·建议升 PASS（6 条）

| # | case_id | 建议状态 | 一句话理由 | 正式措辞（建议） |
|---|---|---|---|---|
| 1 | X06 | PASS（升档） | remaining 称三项"未执行"，但 covered 证据正是这三项（深链预选/内嵌上传失败与取消不覆盖/成功自动选中）。 | "三项维度已于 2026-10-04 批次实测通过（含失败/取消反向路径），原剩余描述为批次口径误差。" |
| 2 | E2E-15 | PASS（升档） | 创建/重复/三角色切换+重登/密码重置/旧会话降权均有真实 UI 证据，"未创建用户"与证据矛盾。 | "完整生命周期（创建→三角色→新会话→密码重置→外部降权旧 token）已由 SAFE21/自降权批次实测，账号不可删除属权威约束非用例缺口。" |
| 3 | FL-15 | PASS（升档） | 同上证据族（角色变更→新登录→旧会话权限即时生效）。 | "新会话权限与旧会话旧 token 即时降权均已实测。" |
| 4 | P04-07 | PASS（升档） | CR 证据已实测 Operator/Viewer 节点页 Add Node 不存在；仅非 admin POST 403 无直接断言。 | "按钮隐藏已由独立角色会话实测；后端 admin-only 门禁已核源代码，403 直接断言可作可选补强。" |
| 5 | FL-12 | PASS（升档） | maintenance→Dashboard 计数→Task Run 400 阻断→恢复全链已用 Hub-only 记录实测。 | "维护态全链已实测；未涉及连接 Agent 属该断言设计内（阻断发生在 Hub 侧）。" |
| 6 | P17P18-04 | PASS（升档，按先例） | 产品 `models.Trigger` 仅 manual/schedule，无 system 触发源=design-unreachable；筛选器与 URL 已验证。 | "system 触发源在产品中不存在（同为 design-unreachable，参照 TABLE-AUDIT-ERROR 先例记 PASS）；筛选器选项与 URL 已验证。" |

### 3.2 设计不可达 / 行为待裁决（3 条）

| # | case_id | 建议状态 | 一句话理由 | 正式措辞（建议） |
|---|---|---|---|---|
| 7 | SYS-29 | 保留 PARTIAL | 「过期」真实缓存路径不可达（全仓无 Agent 侧 MsgRemoteState 写入方），另 2 维度已验。 | "未观察/断连 fail-closed 已验证；TTL 过期因产品无缓存写入方不可构造，保留待产品裁决（可选以 ttl_remaining_sec≤0 注入变体另批补证）。" |
| 8 | SYS-40 | 保留 PARTIAL（转 FAIL 候选） | 重试次数/日志/终态已验；per-attempt 审计未满足 expected；Docker/Compose 执行形态属设计不存在。 | "重试链路与逐 attempt 双流日志已验证；per-attempt 审计缺失与 Docker/Compose 执行形态为产品设计事实，待立项裁决（见 §5）。" |
| 9 | X10 | 保留 PARTIAL | DST 边界无安全注入 seam（环境不可构造）；其余往返/裁剪/时区维度可补但未闭合。 | "Cron/One-Time/时区/字段裁剪维度可在后续调度批补齐；DST 边界列为环境不可构造维度，不新增拒绝/补偿契约。" |

---

## 4. 升 PASS 建议（问题 1，含证据指针）

> 口径：证据已覆盖用例全部标题维度，仅因批次口径保守/剩余描述过期而维持 PARTIAL。

| # | case_id | 置信度 | 证据指针 |
|---|---|---|---|
| 1 | X06 | 高 | `.e2e/runs/20260930-101945/repair/qa/full-feature-01-05-07-results.json`；`qa-resume-20261004/resume-20261004-gap_more-results.json`；`qa-resume-20261004/evidence/resume-20261004-gap_more/inline-artifact-upload.json` |
| 2 | E2E-15 | 中高 | `qa-resume-20261004/resume-20261004-safe01_21-results.json`；`qa-resume-20261004/evidence/resume-20261004-safe01_21/user-lifecycle-ui.json`；`qa-resume-20261004/user-role-downgrade-live.json`；`qa-resume-20261004/user-role-self-demote-ui.json`；`qa-resume-20261004/resume-20261005-cr-01-results.json`（`web/e2e/tests/rbac.spec.ts`） |
| 3 | FL-15 | 中高 | 同上证据族（`resume-20261004-safe01_21-results.json`、`user-role-self-demote-ui.json`、`resume-20261005-cr-01-results.json`） |
| 4 | P04-07 | 中 | `qa-resume-20261004/resume-20261005-cr-01-results.json` + `web/e2e/tests/rbac.spec.ts`（按钮不存在）；`qa-resume-20261014-root/p-web-review-20261014.json`（后端 canAdmin 门禁） |
| 5 | FL-12 | 中高 | `qa-resume-20261004/resume-20261004-gap_more-results.json`；`qa-resume-20261004/evidence/resume-20261004-gap_more/maintenance-dashboard-run-guard.json` |
| 6 | P17P18-04 | 高（按先例） | `qa-resume-20261014-root/p-web-review-20261014.json`（filter_options/api_distribution：manual 735/schedule 36/system 无样本）；产品侧 `internal/models/models.go:330-331`（仅 manual/schedule） |

- 另有 2 条就近候选（B2 复核后可升，未计入 6 条）：**P14P15-07**（`qa/full-feature-01-05-07-results.json` 已含"编辑 v2 保存 Revision2"；前置复核 OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST）、**P14P15-16**（`qa/full-feature-01-05-07-results.json` + `qa-resume-20261004/app-full08-host-cycle-retry.json` 已含删除确认/关联清理）。

## 5. 转 FAIL / 新立缺陷候选（问题 2）

### 5.1 强建议（6 条：行为已观测为不符合 expected，应挂缺陷而非"未测"）

| # | case_id | 理由 | 关联缺陷/证据 |
|---|---|---|---|
| 1 | G04-01 | 编辑器保存 pending 未禁用→重复 PUT（2 次请求），expected「所有提交按钮 pending 时禁用」未满足。 | `WEB-TASK-PENDING-SAVE`（OPEN，P2，`web/src/features/tasks/index.tsx:631`；evidence 在 `web/evidence/task-*`） |
| 2 | X08 | 同一 pending 缺陷维度 + 慢响应重复点击；其余取消零写维度已通过。 | 同上；`web-batch04` 等已有取消证据 |
| 3 | X14 | 「Execution/Audit 跨页搜索」仅过滤当前服务端页，expected 未满足。 | `WEB-EXEC-SEARCH-PAGE`（OPEN，P2；`web/safe-tables-results.json`、`evidence/execution-cross-page-search.json`） |
| 4 | SYS-40 | per-attempt 审计缺失是该用例 expected「审计」维度的未满足项。 | `backend-case-crossmap-review.json`；`next-runtime-batch/sys34-40`（仅 1 条 execute 审计） |
| 5 | P22-10 | Hub 无投递超时：目标可 12+ 分钟停留 DELIVERING 且永不终结（host001 只接包不回报），失败隔离缺"失败终结"面。 | `web-batch07-review-20261014.json`（中置信度） |
| 6 | X16 | 主运行缺陷 `WEB-SECRET-DEFAULT`（secret 默认值摘要明文）与修复版遮蔽证据冲突；默认值/Revision/Execution/Audit 遮蔽矩阵未逐项闭合。 | `WEB-SECRET-DEFAULT`（OPEN，P1）；对照 `resume-20261008` 遮蔽证据 |

### 5.2 待产品裁决（3 条：裁决为"接受"则走 C3 口径，裁决为"不接受"则转 FAIL）

| # | case_id | 待裁决点 |
|---|---|---|
| 7 | G04-04 | Escape 关闭确认框后焦点未返回触发按钮（落 BODY）；Enter 不提交（0 POST）是否为预期交互。 |
| 8 | X19 | 同上（键盘交互泛化用例）。 |
| 9 | P12P13-05 | 过去时间 One-Time 未被拒绝且保存可用（OBS-20261014-PAST-ONETIME-SAVE-ENABLED）是否为产品语义。 |

### 5.3 已确认缺陷（已修复，待回归，不计入上表）

- `E2E-20261014-TIMEOUT-CONTENT-VALIDATION-GAP`（P07_P08-11，P1-4）——回归通过则应转 PASS，不再转 FAIL。
- `E2E-20261015-SQL-ERROR-LEAK-ON-PUT-NOTFOUND` / `GROUP-EMPTY-NAME-AND-UPSERT-200`（G04-03 相关）——同上。
- `E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW`、`ROLLBACK-FAILED-STALE-BACKUP-RESTORE`、`COMMITSYNC-DOUBLE-UNLOCK-PANIC`——对应 SYS-28/E2E-10、INV-11、INV-05 的回归复测。

---

## 附录 A：75 条全量归类与一句话理由

> 图例：`C1-B1/B2/B3` 见 §1；`C2-*` 见 §2 子类；`C3-1/2` 见 §3。

| # | case_id | 类别 | 一句话理由 |
|---:|---|---|---|
| 1 | G04-01 | C2 缺陷 | 剩余维度=编辑器 pending 未禁用（已开缺陷 WEB-TASK-PENDING-SAVE），需修复/豁免授权。 |
| 2 | G04-03 | C1-B1 | 组重名 SQL 外泄已修复，回归复测+跨页文案扫描即可闭合。 |
| 3 | G04-04 | C2 裁决 | Escape 焦点未返回/Enter 语义需产品裁决，再定键盘补测。 |
| 4 | P03-02 | C1-B3 | 仅缺非空运行中行的 dashboard 行链接，一次运行中采样即可。 |
| 5 | P03-09 | C2 隔离 | no-app/no-failed-execution 空态需隔离空 authority。 |
| 6 | P04-06 | C2 隔离 | operator/viewer 全量受限操作需隔离实例受限会话（无删除用户 API）。 |
| 7 | P04-07 | C3 升档 | CR 证据已覆盖按钮隐藏，仅缺非 admin 403 直接断言（可选补强）。 |
| 8 | P05B-08 | C1-B3 | 仅缺 Node Detail→Schedule 详情一条链路点击。 |
| 9 | P05B-10 | C1-B3 | 仅缺「任务名落 Task 详情」一次直接点击证据。 |
| 10 | P07_P08-11 | C1-B1 | 超长/超时边界即已修复的校验缺陷，回归复测。 |
| 11 | P09P11-07 | C2 夹具 | app_deploy 选择控件与真实部署需自有应用+可部署目标批。 |
| 12 | P09P11-08 | C2 独占 | app_operation 四操作逐项与执行随自有应用夹具（超批上限）。 |
| 13 | P09P11-09 | C2 夹具 | 单节点/去选节点单独断言随任务目标 fixture。 |
| 14 | P09P11-10 | C2 夹具 | 单组/多组/去选组需自有分组 fixture。 |
| 15 | P09P11-11 | C2 夹具 | 标签匹配节点变化需 owned child 节点标签写。 |
| 16 | P09P11-12 | C2 夹具 | 切换后保存 payload 无旧字段的写侧证据。 |
| 17 | P09P11-15 | C2 夹具 | 8 metric/6 operator 枚举与条件设置/清除效果待验。 |
| 18 | P09P11-16 | C2 夹具 | Remote last_execution/task_id 校验未验（随修复版复测）。 |
| 19 | P09P11-17 | C2 夹具 | AND 结构实际结构与执行效果未验。 |
| 20 | P11-08 | C2 协议 | 重复投递幂等无 UI/HTTP 路径，需协议帧伪造授权。 |
| 21 | P12P13-05 | C2 裁决 | 过去时间 One-Time 未拒绝属待裁决；空 run_at 服务端拒绝待补。 |
| 22 | P12P13-06 | C2 窗口 | Owner 差异真实执行需独占调度窗口对照。 |
| 23 | P12P13-07 | C2 窗口 | offline 策略组合投递需 owned child 断连/停机窗口。 |
| 24 | P12P13-08 | C2 窗口 | misfire 补偿真实场景需停机窗口。 |
| 25 | P14P15-06 | C1-B2 | 缺自有应用创建后 Revision/Artifact Usage/Assignment 呈现。 |
| 26 | P14P15-07 | C1-B2 | Revision+1/历史需自有应用复核（先排除 APP-EDITOR-SAVE 观察）。 |
| 27 | P14P15-08 | C1-B2 | 多节点分配/取消写侧随 owned app+child 完成。 |
| 28 | P14P15-09 | C2 夹具 | 回链点击随 B2 夹具低成本完成（超 10 条上限）。 |
| 29 | P14P15-12 | C2 隔离 | 受限角色真实部署/编辑请求需隔离实例。 |
| 30 | P14P15-13 | C1-B2 | state 页字段需自有应用分配后逐字段核对。 |
| 31 | P14P15-14 | C1-B2 | 五种 state 分支需自有应用部署/停止对照。 |
| 32 | P14P15-15 | C1-B2 | 四类健康检查需为自有应用分别配置后采集。 |
| 33 | P14P15-16 | C1-B2 | 删除确认与关联对象清理随自有应用删除流程核对。 |
| 34 | P14P15-18 | C1-B2 | 部署失败恢复链随 P0-2 修复重放失败/恢复。 |
| 35 | P16-02 | C1-B3 | 上传弹层重试后逐项抓取四字段。 |
| 36 | P16-04 | C1-B3 | 上传四项边界随弹层一次执行。 |
| 37 | P17P18-03 | C2 注入 | PENDING 瞬态无样本，需帧注入延迟窗口（或转 C3 口径）。 |
| 38 | P17P18-04 | C3 升档 | 产品无 system 触发源（design-unreachable），按先例升 PASS。 |
| 39 | P19-03 | C2 独占 | 六类动作审计核对需组合夹具独占批。 |
| 40 | P20-04 | C2 隔离 | 创建/重复/错误输入需隔离实例账号（复核空密码缺陷）。 |
| 41 | P21-08 | C2 隔离 | Settings GET/PUT 403 需隔离实例受限会话。 |
| 42 | P22-07 | C1-B3 | loading/慢响应重复点击需一次延迟注入+真实 start。 |
| 43 | P22-09 | C1-B3 | 「全终态有失败→FAILED」需第 5 个自有传输。 |
| 44 | P22-10 | C1-B3 | FAILED 侧改用可控失败目标复测隔离。 |
| 45 | FL-02 | C2 夹具 | 跨页任务链路需自有任务 fixture。 |
| 46 | FL-03 | C2 夹具 | 标签→分组→目标同步需标签/分组 fixture（含标签写）。 |
| 47 | FL-04 | C2 夹具 | 脚本→任务副本/引用链路需脚本+任务 fixture。 |
| 48 | FL-10 | C1-B2 | Deploy→State/Execution 一致性需自有应用真实部署。 |
| 49 | FL-11 | C1-B2 | Upgrade→Rollback 一致性随 P0-2 修复复验。 |
| 50 | FL-12 | C3 升档 | maintenance 全链已由 Hub-only 记录实测。 |
| 51 | FL-15 | C3 升档 | 角色变更→新登录→旧会话降权均已实测。 |
| 52 | E2E-10 | C1-B1 | Remote 正向随 P0-1 修复解锁；恢复维度用 owned child 重连窗口。 |
| 53 | E2E-15 | C3 升档 | 完整生命周期证据充分（SAFE21/自降权）。 |
| 54 | E2E-18 | C2 独占 | Docker/Compose 重建+B 恢复 UI 读回需独占 docker 窗口。 |
| 55 | SYS-28 | C1-B1 | last_execution 比较受时钟缺陷阻塞，修复版复测。 |
| 56 | SYS-29 | C3 接受 | 「过期」真实缓存路径不可达（无写入方），保留 PARTIAL 待裁决。 |
| 57 | SYS-40 | C3 接受 | per-attempt 审计未满足 expected（缺陷候选）；Docker/Compose 域设计不存在。 |
| 58 | AR-04 | C2 独占 | Docker 重建幂等/Desired State 对账需独占 docker 批。 |
| 59 | AR-08 | C2 注入 | duplicate/lost/reordered 通知帧注入需独占窗口。 |
| 60 | INV-05 | C1-B1 | 剩余即 CommitSync 双重解锁修复的回归复测。 |
| 61 | INV-11 | C1-B1 | 剩余即 P0-2 回滚恢复修复的回归复测。 |
| 62 | X04 | C1-B3 | 仅缺五字段↔表达式双向同步与非法表达式的逐步断言。 |
| 63 | X06 | C3 升档 | 三项剩余维度与 covered 证据矛盾，证据已覆盖。 |
| 64 | X08 | C2 缺陷 | pending 缺陷维度+批量/吊销与慢响应重试注入批。 |
| 65 | X09 | C2 夹具 | 四类型往返隐藏字段需脚本/部署/应用操作夹具。 |
| 66 | X10 | C3 接受 | DST 边界不可构造；其余维度待调度批补（保留 PARTIAL）。 |
| 67 | X11 | C2 夹具 | 条件回显/AND 语义/清空后真实执行待验。 |
| 68 | X12 | C2 独占 | 分配变化→跨页与下次执行需多次真实执行对照。 |
| 69 | X13 | C2 隔离 | 多标签退出/Token 替换与收敛时间需隔离多会话。 |
| 70 | X14 | C2 缺陷 | 跨页搜索为已开缺陷 WEB-EXEC-SEARCH-PAGE；漂移需大数据集批。 |
| 71 | X15 | C2 独占 | 日志分片/断线恢复 Follow/搜索/过滤/Copy 需独占长窗。 |
| 72 | X16 | C2 安全 | secret 矩阵与凭据泄漏面需安全复测授权（挂缺陷复核）。 |
| 73 | X18 | C2 独占 | 批量/Assign 失败重试注入批（写不可删 Transfer 历史）。 |
| 74 | X19 | C2 裁决 | 同 G04-04 键盘观察，待产品裁决。 |
| 75 | X20 | C1-B3 | 长文本/窄屏/剪贴板小项 UI 补测。 |

## 附录 B：执行注意与声明

1. **与在飞批次的关系**：`qa-resume-20261016-verify` 已完成修复版部署（hub `cd8b7f9d…`/agent `b04383bf…`）并在跑回归；B1 与之合并，不重复取证，避免双口径。
2. **红线**：不得改 protected 节点状态（节点状态一律用 owned child）；不得用 privileged/unconfined 绕过 AppArmor；不得 pull/rebuild/retag docker 镜像；不得创建持久化用户（角色族仅走隔离实例）；Hub 重启/二进制替换/全局设置任何一项都需 Root 单列授权。
3. **本方案未修改任何台账状态**；C1 各批执行后仍需按项目惯例由 Root 复核证据再改 `feature-inventory.json` 状态。
4. **已知不确定性**：
   - P16-02/P16-04 依赖上传弹层可打开（此前失败），若复现为 harness 问题需换入口并如实记录；
   - P22-07/09/10 会新增 ≤3 条不可删除 Transfer 历史，需在报告中登记计数变化；
   - B2 的 P14P15-07 受 OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST 影响，需先复现/排除；
   - C2 §2.7 的 P17P18-03 若产品确认 PENDING 为瞬态不可稳定采样，可移入 C3 口径（措辞需同步修订）。
5. **未执行声明**：本任务仅产出本文件；未修改产品源码、未运行测试、未访问远程环境、未做 Git/Archive 操作。
