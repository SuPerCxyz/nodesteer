# NodeSteer 7 条缺陷修复实施报告

- 日期：2026-10-16
- 范围：`repair-plan-20261016.md` 中 7 条 `CONFIRMED_TEST_FAILURE_REPAIR_SCOPE_REVIEW_PENDING` 缺陷（P0×2、P1×2、P2×2、P3×1）的实施 + 单元测试
- 授权：用户已授权修改产品源码；本报告仅记录实施事实
- 未执行：Git 提交/推送/分支操作；未修改 `.e2e/**` 既有状态文件；未改配置/部署文件；未安装依赖；未访问远程主机
- 说明：工作树中已存在的其他历史修改（前批缺陷修复、web 源码改动等）未被覆盖或回退；本报告只描述本次改动

## 验证结果（真实执行）

| 命令 | 结果 |
|---|---|
| `go build ./...` | ✅ 通过（无输出错误） |
| `go vet ./...` | ✅ 通过（VET_OK） |
| `go test ./internal/...` | ✅ 全部通过：agent、agent/condition、agent/connection、agent/execution、agent/host、agent/scheduler、hub、hub/agentbundle、hub/api、hub/auth、hubserver、store 均 `ok` |
| `make test`（仓库入口 `go test -buildvcs=false ./...`） | ✅ 全部通过 |
| `gofmt -l`（本次改动文件） | ✅ 无未格式化文件 |

---

## P0-1 E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW（时钟偏移，混版本兼容）

**改动文件/函数**

- `internal/protocol/protocol.go`：`RemoteStatePayload` **只增不改**，新增 `TTLRemainingSec *int64 \`json:"ttl_remaining_sec,omitempty"\``（自 Hub 判定时刻起剩余有效秒数，ceil；指针类型用于精确区分“旧 Hub 未下发”与“下发但 ≤0”）。既有 `observed_at` / `ttl_sec` 语义不变。
- `internal/hub/gateway.go`：
  - `resolveRemoteState` 增加第 4 个返回值 `remaining`：缓存分支 `ceil(expires-now)`（`remaining<1` 防御性抬到 1），观测窗口 `ttl_sec` 仍按既有语义返回；实时分支返回 `60, 60`。
  - `handleRemoteStateReq` 同时下发既有字段与 `ttl_remaining_sec`。
- `internal/agent/sync_validation.go`：`validRemoteState` 优先使用 `TTLRemainingSec`（仅判定 `>0`，不做跨主机时钟比较）；字段为 nil（旧 Hub）时**回退**原 `ObservedAt + TTL` 绝对时间判定。空值/UNKNOWN/剩余≤0 仍 fail-closed。

**关键语义**：新 Hub+旧 Agent（旧字段照旧）、旧 Hub+新 Agent（回退绝对时间）、新 Hub+新 Agent（剩余 TTL）三组合均可用；`agent.go` 调用点签名不变。

**新增/更新测试**

- `internal/agent/sync_validation_test.go`：重写 `TestRemoteStateValidity`（9 条回退路径 + 5 条剩余 TTL 路径，含“Hub 时钟领先 1s 时 observed 在未来仍判定有效”“remaining=0/-1 fail-closed”“value 空/UNKNOWN”）；新增 `TestRemoteStatePayloadMixedVersionJSON`（旧报文解码 nil→回退、新报文剩余 42→在时钟偏移下可用）。
- `internal/hub/remote_state_test.go`（新增）：`TestResolveRemoteStateRemainingTTL`（实时分支 60/60；缓存剩余 0.4s→ceil 为 1、窗口仍 2；缓存过期回落实时分支）。
- `internal/hub/pending_status_test.go`：适配 `resolveRemoteState` 新签名。

**与方案的偏差**：按用户既定决策采用“替代 B（新增字段 + 回退）”，未采用方案主选项的“改写 `ttl_sec` 语义”；`observed_at` 保留为诊断字段。

---

## P0-2 E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE（回滚毒行/陈旧 backup）

**改动文件/函数**

- `internal/agent/store.go`：
  - `deployments` 表新增 `app_revision INTEGER`、`binary_path`、`config_path`、`unit_name`（CREATE TABLE + `migrate()` 幂等 `ALTER TABLE`，旧行默认空/0，**无破坏性迁移**）。
  - `DeploymentRow` 增字段；`CreateDeployment` 写入；新增 `SetDeploymentBinding`；新增 `ListDeploymentsByApplication`（含 DONE，供 superseded 判定）；`GetActiveDeployments`/`ListAllDeployments` 改用统一列清单+扫描。
- `internal/agent/application.go`：
  - 新增终态 `PhaseRollbackFailed = "ROLLBACK_FAILED"`；`failed()` 中 `rollback()` 失败后落 `ROLLBACK_FAILED`（不再遗留 `ROLLING_BACK`）。
  - `deploy()` 在路径解析后、任何宿主副作用之前调用 `SetDeploymentBinding`（`p.AppRevision` 缺失时回退本地定义 revision）。
  - `RecoverDeployments` 双重防护：①`supersededByLaterDoneDeployment`（同 app 存在 `started_at` 更晚的 DONE 行，时间按 `time.RFC3339Nano` 解析比较，避免字符串序错误）→ 跳过且**不改动文件、不写新相位**（journal 原样保留并记日志）；②`deploymentBindingMatches`（revision + 三路径与当前定义全等；旧行缺字段=未知）→ 不匹配即不重放。通过校验后以 **journal 记录路径**重放。恢复重放失败同样落 `ROLLBACK_FAILED`。
- 旧行为（缺绑定）**不重放**（用户明确要求：未知→不重放，不可误覆盖）。

**新增/更新测试**

- `internal/agent/application_test.go`：
  - 新增 `TestRecoverySkipsSupersededStaleDeployment`（AR-32 主链：毒行 + 更晚 DONE → 文件/服务/phase 全不变）；
  - 新增 `TestRecoverySkipsBindingMismatch`（缺绑定、revision 漂移、binary/unit 漂移 4 子例 → 一律不重放）；
  - 新增 `TestRecoveryReplaysMatchingIncompleteDeployment`（绑定匹配、无更晚 DONE → 仍重放并收敛 DONE，SYS-51 类不回退）；
  - 新增 `TestRecoveryReplaysWhenLaterDeploymentNotDone`（边界：更晚行未 DONE 不构成 superseded，记录为已知限制）；
  - 新增 `TestRollbackFailureMarksRollbackFailedPhase`（回滚失败→`ROLLBACK_FAILED`；重启后绑定匹配仍可重放收敛）；
  - 更新 `TestFirstDeploymentRecoveryUsesAbsentMarkers`（补绑定字段）、`TestRecoveryMissingBackupNeverDeletesOrCompletes`（legacy-empty→不重放保持 VERIFYING；missing-file→尝试后 `ROLLBACK_FAILED`）。
- `internal/agent/store_test.go`：新增 `TestDeploymentBindingRoundTrip`（绑定持久化/读回、旧行空值解码、按应用查询）。

**与方案的偏差（有据）**：方案测试 4 原建议“旧行空绑定保持既有行为”；用户最终决策为“旧行缺新字段按未知→不重放”，本次按用户决策实施。方案可选的 `SUPERSEDED` 相位未新增（被取代行保留原 phase 不写新值，避免额外相位扩张）。`rollback()` 结尾仍用失败版本 health 定义复检（方案替代 3 未纳入本轮），因此“文件已还原但报回滚失败→ROLLBACK_FAILED”仍可能出现，属既有已知限制。

---

## P1-3 E2E-20261015-COMMITSYNC-FAILURE-DOUBLE-UNLOCK-PANIC（双重解锁 panic）

**改动文件/函数**：`internal/agent/store.go` — `CommitSync`/`RollbackSync` 的 `tx==nil` 分支直接 `return nil`（不再 `Unlock`）；写锁只由 `BeginSync` 获取、只释放一次。

**新增测试**（`internal/agent/store_test.go`）：

- `TestSyncTxCommitFailureRollbackIsNoop`：Commit 失败（事务已结束）→ `CommitSync` 返回错误、随后 `RollbackSync` no-op 不 panic、锁已释放、可再次成功同步（revision 前进）。
- `TestSyncTxCallsWithoutBeginAreNoops`：无 `BeginSync` 直接 Commit/Rollback 返回 nil、幂等、不影响后续真实事务。

---

## P1-4 E2E-20261014-TIMEOUT-CONTENT-VALIDATION-GAP（Script 校验）

**改动文件/函数**

- `internal/hub/validation.go`：新增 `maxScriptContentBytes = 256*1024` 与 `validateScriptDefinition`（`timeout<0`→错误；`timeout==0`→归一 300，对齐 Task/DB/前端 min=0；`len(content) > 上限`→错误，按字节判定）。
- `internal/hub/definitions.go`：`ScriptManager.Create/Update` 顶部接入校验。
- 按用户要求**未**让 `script.timeout` 参与执行超时（不扩大范围）。

**新增测试**

- `internal/hub/definitions_test.go`：`TestScriptDefinitionValidation`（负数 timeout Create/Update 均拒；0→300 且回读一致；262144 接受 / 262145 拒绝；多字节 UTF-8 262146 字节拒绝；失败不落库、不推进 revision）。
- `internal/hub/api/contract_test.go`：API 级 400 文案回归（`timeout:-5`）。

---

## P2-5 E2E-20261015-GROUP-EMPTY-NAME-AND-UPSERT-200（groups 校验）

**改动文件/函数**

- `internal/hub/api/api.go`：`handleGroups` POST 增加 `TrimSpace(name)==""` → 400 `name required`；`handleGroupByID` PUT 增加 name 校验 → 400、`GetGroup` 预检 → 404 `group not found`、`sql.ErrNoRows` → 404。
- `internal/store/sqlite_users_nodes.go`：`UpdateGroup` UPDATE 后检查 `RowsAffected()==0` → 返回 `sql.ErrNoRows`（先于成员 DELETE/INSERT，杜绝孤儿成员与无效 revision）。
- `internal/hub/node.go`：新增 `validateGroupName`（非空 + 忽略大小写/首尾空白唯一，排除自身），`CreateGroup/UpdateGroup` 接入；重名返回归一化 `group name already exists: X`。

**新增测试**

- `internal/hub/api/contract_test.go`：`TestGroupsContractValidation`（空名/纯空白 400 不落库；重名 400 且无 UNIQUE 原文；PUT 不存在 id → 404、组数/成员/revision 不变；正常 POST/PUT/GET 200）。
- `internal/hub/node_test.go`：`TestGroupNameValidationAndMissingUpdate`（manager 层缺失→`sql.ErrNoRows`、无孤儿成员、revision 不前进；空名/重名归一化）。

**说明**：PUT 更新错误状态码由 500 调整为 400（与同文件其它 PUT 校验错误口径一致），正常副作用不变。

---

## P2-6 E2E-20261015-SQL-ERROR-LEAK-ON-PUT-NOTFOUND（SQL 外泄）

**改动文件/函数**

- `internal/hub/api/api.go`：新增 `writeUpdateError`；`PUT /api/schedules|applications|scripts|tasks {id}` 四处统一接入：`errors.Is(err, sql.ErrNoRows)` → 404 归一化文案（`schedule/application/script/task not found`），不再回传 `sql: no rows in result set`；既有校验错误保持 400 文案。

**新增测试**（`internal/hub/api/contract_test.go`）

- `TestDefinitionPutNotFoundContract`：4 资源 × {不存在 id + 合法 body → 404 且 body 无 `sql`/`no rows`；存在 id → 200；非法字段 → 400 且文案保留}。

**与方案的偏差（需知悉）**：“其余错误不把 `err.Error()` 原文回给调用方”未做全面泛化——方案回归标准明确要求“非法字段 → 400 且文案保留”，而校验文案本身就是 `err.Error()`；本轮封闭了全部已观察到的 SQL 原文外泄路径（均为 `ErrNoRows`）。彻底方案（manager 层包装 `store.ErrNotFound`/错误分类）方案文件已建议另批处理。

---

## P3-7 E2E-20261014-AUDIT-METHOD-FALLBACK-200（方法语义）

**改动文件/函数**（`internal/hub/api/api.go`）

- `handleAudit`：非 GET → 405 `method not allowed` + `Allow: GET`（查询逻辑不变；CORS `OPTIONS` 仍由 `withCORS` 先行 204）。
- 同批：`handleMe`、`handleMetrics` 非 GET → 405 + `Allow: GET`；`handleLogout` 非 POST → 405 + `Allow: POST`（POST 保持既有注销语义）。`GET /api/me`、`/metrics` 与前端/E2E 既有 GET/POST 用法不变。

**新增测试**（`internal/hub/api/contract_test.go`）：`TestAuditAndReadEndpointsMethodSemantics`（audit POST/PUT/DELETE/PATCH→405+Allow、OPTIONS→204、GET→200 且保留 `X-Total-Count`；me/metrics 方法矩阵；logout GET→405 且不注销、POST→200 且注销生效）。

**决策说明**：方案文件把 `/api/logout` 列为“需单独确认语义”的同类观察；用户本轮明确“同批处理”。实现取 REST 正确语义（POST-only）——仓库内所有调用方（web e2e fixtures、E2E 用例）均使用 POST，前端不调用该端点（OBS-20261010 另案），故不产生既有行为破坏；GET 注销这一被记录的缺陷行为被移除。

---

## 总改动文件清单

**源码（10）**

1. `internal/protocol/protocol.go`（P0-1 新增字段）
2. `internal/hub/gateway.go`（P0-1 剩余 TTL 下发）
3. `internal/agent/sync_validation.go`（P0-1 判定优先级 + 回退）
4. `internal/agent/store.go`（P1-3 锁修复；P0-2 schema/绑定/superseded 查询）
5. `internal/agent/application.go`（P0-2 deploy/failed/RecoverDeployments）
6. `internal/hub/validation.go`（P1-4 脚本校验）
7. `internal/hub/definitions.go`（P1-4 接入 ScriptManager）
8. `internal/store/sqlite_users_nodes.go`（P2-5 RowsAffected 防御）
9. `internal/hub/node.go`（P2-5 组名校验）
10. `internal/hub/api/api.go`（P2-5 组 handler、P2-6 四个 PUT、P3-7 方法语义）

**测试（8）**

1. `internal/agent/store_test.go`（P1-3、P0-2 store 层）
2. `internal/agent/application_test.go`（P0-2 恢复链路）
3. `internal/agent/sync_validation_test.go`（P0-1 Agent 判定/混版本）
4. `internal/hub/remote_state_test.go`（新增，P0-1 Hub 侧 ceil）
5. `internal/hub/pending_status_test.go`（适配签名）
6. `internal/hub/definitions_test.go`（P1-4）
7. `internal/hub/node_test.go`（P2-5 manager 层）
8. `internal/hub/api/contract_test.go`（新增，P2-5/P2-6/P3-7 API 契约）

**报告**：`.e2e/runs/20260930-101945/repair/qa-resume-20261014-root/implementation-report-20261016.md`（本文件）

## 未完成 / 需上级裁决点

1. P2-6 “其余错误不回 `err.Error()` 原文”未全面泛化（理由见上，与“400 文案保留”回归标准冲突）。
2. P3-7 `/api/logout` 收紧为 POST-only 属本批新增的范围决策，请确认与 E2E 台账更新计划一致。
3. P0-2 未按方案“替代 3”改用上一版本 health 定义，`rollback()` 复检失败误报仍存在（本轮非目标）。
4. P0-2 边界：同 app 更晚但未 DONE 的部署不阻断旧行重放（已用测试记录为已知限制）。
5. 运行时/联调级验证（真实 Hub-Agent 混版本重启、AR-32 环境复现）不在本任务环境内执行；本报告仅覆盖单测与编译/静态检查，需在 E2E 回归批中复核。
