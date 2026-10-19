# P6 并发/鉴权矩阵缺陷修复报告（F1–F8，2026-10-18）

- 任务来源：`.e2e/runs/20260930-101945/repair/qa-resume-20261018-stress-authz/findings.json`（P1 压测 + 鉴权矩阵扫出的 7 条缺陷 + 1 条日志噪声），用户已授权全部修复。
- 边界遵守：只改 Go 源码与单测；未改 `internal/agent/redact*.go`；未改 `web/**`；未改 `.e2e/**` 状态文件（本报告为新增交付物）；无 Git 写操作；未装依赖；未访问远程环境；本批不做部署。
- 工作树既有未提交改动（`internal/agent/application.go`、`internal/agent/store.go`、`docs/**`、`web/**` 等）属会话前状态，本次未触碰、未覆盖。
- 验证环境：`go1.26.8 linux/amd64`。因 `/tmp` tmpfs 已达 81%，所有 `go` 命令以 `TMPDIR=/var/tmp/nodesteer-go-tmp` 执行（不影响结论）。

---

## 一、F1（P1）连接池死锁挂起

### 1.1 根因

`internal/store/sqlite_file_transfers.go` 的 `ListFileTransfers` 在 `rows.Next()` 循环内对每行调用 `listFileTransferTargets`：持有 1 条池连接（rows 未关闭）时再申请第 2 条连接。`OpenSQLite` 设置 `SetMaxOpenConns(8)`，≥8 个并发调用者各持 1 条等第 2 条 → 连接池死锁，只能靠客户端断开释放；挂起期间全库端点被拖死。`NodeManager.Delete`（`internal/hub/node.go:445`）内部调用它，因此 `DELETE /api/nodes` 并发同样触发。

### 1.2 改动

| 文件:行 | 改动 |
|---|---|
| `internal/store/sqlite_file_transfers.go:63-115` | `ListFileTransfers` 改为**单条 LEFT JOIN**（`file_transfers t LEFT JOIN file_transfer_targets tt ON tt.transfer_id = t.id ORDER BY t.created_at DESC, t.id, tt.node_id`），一次取回传输与目标；用 `sql.NullString/NullInt64` 处理无目标传输的 NULL 行 |
| `internal/store/sqlite_file_transfers.go:42-43,120` | `GetFileTransfer` / `listFileTransferTargets` 改用 `s.queryer(ctx)`（事务内不再借第二条连接，见第三节） |

关键 diff（节选）：

```go
// 旧：rows.Next() 内嵌套查询，持连接再借连接
t.Targets, _ = s.listFileTransferTargets(ctx, t.ID)

// 新：单条 JOIN，按 transfer id 聚合
FROM file_transfers t
LEFT JOIN file_transfer_targets tt ON tt.transfer_id = t.id
ORDER BY t.created_at DESC, t.id, tt.node_id
```

### 1.3 同类模式审计（全仓）

脚本化扫描 `internal/**/*.go`（非测试）全部 `for rows.Next() {` 循环体（32 处），检查循环体内是否出现 `Query/QueryRow/Exec/Prepare`、`s.queryer/execer`、`s.db.*` 或可能借连接的 store 方法调用：

- 命中 **1 处**：`internal/store/sqlite_file_transfers.go:64`（本次缺陷）→ 已修复；
- 其余 31 处（`internal/store/sqlite_users_nodes.go`、`sqlite_definitions.go`、`sqlite_executions.go`、`sqlite_artifacts_apps.go`、`migrate.go`、`internal/agent/store.go` 10 处）循环体内仅做 `Scan/append`，**安全**；
- 复核扫描当前代码：**0 处残留**。

### 1.4 新增测试

| 测试 | 文件 | 断言 |
|---|---|---|
| `TestListFileTransfersReturnsTargets` | `internal/store/sqlite_file_transfers_test.go` | 单条 JOIN 不丢目标：无目标传输 0 目标、双目标按 node_id 升序、mode/status/error/destination_path 字段完整 |
| `TestListFileTransfersConcurrentNoPoolDeadlock` | 同上 | 文件库（池=8）+ 31 条传输 + **16 并发** `ListFileTransfers`，20s 内全部返回 31 条且每条 1 目标 |
| `TestTransfersListConcurrentNoHang` | `internal/hub/api/concurrency_stress_test.go` | **16 并发 GET /api/transfers** 全部 200、每条带 1 目标；并发期间 `GET /api/tasks` 10s 内 200（旧实现被拖死） |
| `TestNodesDeleteConcurrentAllSucceed` | 同上 | **16 并发 DELETE /api/nodes/{id}** 全部 200，且 16 个节点全部删除 |

### 1.5 行为变化

- ≥8 并发 `GET /api/transfers`：永久挂起 → 正常完成；
- 并发 `DELETE /api/nodes/{id}`：挂起/仅删 1 个 → 全部成功；
- `GET /api/transfers` 空列表响应体由 `null` 变为 `[]`（与 scripts/tasks/users 等列表端点一致）。

### 1.6 反向验证（撤掉修复 → 必须失败，已还原）

| 命令 | 结果 |
|---|---|
| `go test ./internal/store/ -run TestListFileTransfersConcurrentNoPoolDeadlock` | ❌ `--- FAIL: TestListFileTransfersConcurrentNoPoolDeadlock (20.05s)` / `concurrent ListFileTransfers hung (connection pool deadlock)` |
| `go test ./internal/hub/api/ -run TestTransfersListConcurrentNoHang` | ❌ `--- FAIL: TestTransfersListConcurrentNoHang (20.14s)` / `concurrent GET /api/transfers hung (connection pool deadlock)` |

还原修复后两用例均 ✅（见第四节）。

---

## 二、F2（P1）schedule 并发删除 33% 失败 + 原文泄露 + 零日志

### 2.1 根因

1. `internal/hub/schedule.go:166`（旧行号）在 `runMutationTx`（`_txlock=immediate` 写事务）内用**外层 ctx** 调 `sm.store.GetSchedule(ctx, id)` 取审计 detail：持写锁同时向连接池申请第 2 条连接。16 并发时池（8）被等待写锁的事务占满，等待者 `busy_timeout 5s` 后 `SQLITE_BUSY`；
2. `internal/hub/api/api.go` 的 `handleScheduleByID` 删除分支用 `writeErr(409, err.Error())` 直返驱动原文，绕过 `writeInternalError` 的归一化与 error 日志。

### 2.2 改动

| 文件:行 | 改动 |
|---|---|
| `internal/hub/schedule.go:148-178` | 审计 detail 的 `GetSchedule` 读取**移到事务外**（事务内只保留 txctx 调用） |
| `internal/hub/api/api.go:1604-1650`（删除分支 `:1640-1644`） | 改为 `s.writeMutationError(w, r, 409, "schedule not found", err)`：BUSY → 503 `storage busy, please retry` + ERROR 日志；`sql.ErrNoRows` → 404；业务错误保持 409 文案 |

### 2.3 同类模式审计（全仓）

脚本化扫描全部 `runMutationTx(...)` / `WithTx(...)` 闭包体（22 个），检查闭包内是否用外层 ctx（而非 `txctx`）调用 store：

- 命中 **1 处**：`internal/hub/schedule.go:166`（本次缺陷）→ 已修复；
- 其余闭包（scripts/tasks/schedules/applications/nodes/groups 共 21 个）全部使用 `txctx`；
- 复核扫描当前代码：**0 处残留**（仅剩 `transaction.go`/`sqlite.go` 中事务实现自身的 2 处良性匹配）。

### 2.4 handler 审计：`writeErr(..., err.Error())` 直传

- 修复前 `internal/hub/api/api.go` 共 **26 处** `err.Error()` 直传；本次统一为归一化路径后仅剩 **4 处**，全部是纯输入校验（不可能含驱动原文）：
  - `api.go:349`（`writeStoreError` 的业务错误兜底分支，已先经驱动/BUSY 判定）、`:707` 纳管参数校验、`:816` 架构参数校验、`:2147` settings 校验。
- 新增归一化工具（`internal/hub/api/api.go:344-384`）：
  - `writeStoreError(w, r, status, err)`：BUSY → 503；驱动错误 → 500 `internal error`；目标 5xx 一律不回原文；业务错误保留既有 4xx 文案；
  - `writeMutationError(w, r, status, notFoundMsg, err)`：`sql.ErrNoRows` → 404 归一化文案，其余走 `writeStoreError`；
  - `writeUpdateError` / `writeApplicationSubresourceError` 改为方法并统一走上述路径。
- 覆盖到的既有端点（行为不变，仅新增驱动错误归一化）：groups/scripts/tasks/schedules/artifacts/applications 的 create/update/delete、nodes 的 update/delete、transfers create、task run、upgrade 等。
- 新增 `store.IsDriverError`（`internal/store/sqlite.go:142-167`）区分驱动错误与业务校验错误（`*sqlite.Error`、`sql:` 前缀、UNIQUE/约束类文案兜底）。

### 2.5 新增测试

| 测试 | 文件 | 断言 |
|---|---|---|
| `TestSchedulesDeleteConcurrentNoBusyLeak` | `internal/hub/api/concurrency_stress_test.go` | 16 并发 `DELETE /api/schedules/{id}` 全部 200（或 503 + `storage busy, please retry` 且有 ERROR 日志）；任何响应不得含 `SQLITE_BUSY`/`database is locked`/`sqlite`；删除后列表为空 |
| `TestWriteMutationErrorNormalization` | `internal/hub/api/normalized_error_test.go` | 缺行→404；BUSY→503+日志；UNIQUE 约束→500+日志且不外泄；业务错误→409 原文；5xx 目标不回原文 |
| `TestReadHelpersInsideTxDoNotBorrowSecondConnection` | `internal/store/sqlite_tx_read_test.go` | 池压到 **1 条连接**时，事务内（txctx）调用 GetTask/GetSchedule/GetApplication/ListApplications/GetApplicationNodes/GetFileTransfer/ListFileTransfers 必须正常返回（防同族回归） |

### 2.6 行为变化

- `DELETE /api/schedules/{id}` 并发下：`409 + "database is locked (5) (SQLITE_BUSY)"` → 正常 200；仅在真实 BUSY 时 503 `storage busy, please retry` 并写 ERROR 日志；
- 同批端点驱动错误一律不再回客户端原文（500 `internal error` + 日志）。

### 2.7 反向验证（撤掉修复 → 必须失败，已还原）

撤掉 `schedule.go` 事务外读取 + `api.go` 归一化（恢复 `writeErr(409, err.Error())`）后：

```
--- FAIL: TestSchedulesDeleteConcurrentNoBusyLeak (5.38s)
    schedule 1 leaked driver text: 409 {"error":"database is locked (5) (SQLITE_BUSY)"}
```

与线上现象一致；还原修复后 ✅。

---

## 三、F3–F5（P2/P3）存在性语义与原文泄露

### 3.1 F3 `POST /api/nodes` 不存在 id → 500（应 404）

- 根因：`handleNodes` POST 分支把 `SetNodeStatus` 的 `sql.ErrNoRows` 交给 `writeInternalError` → 500；仅标签路径因 UPDATE 不校验行数而静默 200。
- 改动：`internal/hub/api/api.go:639-690`——新增存在性前置检查（不存在 → 404 `node not found`）；状态/标签错误分别走 `writeMutationError` / `writeStoreError`。

### 3.2 F4 4 个子资源写端点 → 400 + `sql: no rows in result set`（应 404）

| 端点 | 改动位置（`internal/hub/api/api.go`） | 归一化文案 |
|---|---|---|
| `POST /api/nodes/{id}/revoke-credential` | `:961-963` | 404 `node not found` |
| `POST /api/transfers/{id}/retry`、`/cancel` | `:1190-1193` | 404 `transfer not found` |
| `POST /api/executions/{id}/cancel` | `:2038-2040` | 404 `execution not found` |

业务错误（如 transfer 已取消）仍保持 400 + 业务文案；错误路径补 ERROR 日志。

### 3.3 F5 `PUT /api/users/{id}` 不存在 → 200 静默成功（应 404）

- 根因：`UpdateUserRole` / `UpdateUserPassword` 为纯 UPDATE，无 `RowsAffected` 校验。
- 改动：`internal/store/sqlite_users_nodes.go:72-99` 新增 `execExpectRow`（零行命中 → `sql.ErrNoRows`），两个方法接入；`internal/hub/api/api.go:2347-2356` 将该错误映射为 404 `user not found`（其余持久化失败保持 500 语义，避免误伤既有 `TestUserUpdateRollsBackRoleOnPersistenceFailure`）。
- `UpdateUserAvatar` 保持原样：仅 SSO 同步路径调用（`internal/hub/auth/auth.go:210`，用户必然存在），无 API 可见的静默成功。

### 3.4 新增测试

| 测试 | 文件 | 断言 |
|---|---|---|
| `TestMissingNodeStatusUpdateIs404` | `internal/hub/api/method_contract_test.go` | 不存在 id（status / labels 两路径）→ 404 `node not found`；存在节点 → 200 |
| `TestSubresourceMissingIDIs404` | 同上 | 4 个端点 → 404 + 对应归一化文案，无 `sql:`/`no rows`/`sqlite` 泄露；存在节点 revoke、存在传输 cancel → 200 |
| `TestUserUpdateMissingIs404` | 同上 | 不存在用户 role/password 更新 → 404 `user not found`；存在用户 → 200 且角色落库 |

### 3.5 行为变化

- 上述 5 条路径：500/400 + SQL 原文 → 404 归一化文案；
- `POST /api/nodes` 携带非法 status 字符串：500 → 400（`invalid node status: X`，与 `PUT /api/nodes/{id}` 一致）；
- `POST /api/nodes` 仅标签更新不存在节点：静默 200 → 404。

---

## 四、F6（P3）405 缺 `Allow` 头

- 根因：各 handler `default` 分支 `writeErr(405)` 未设置 Allow（仅部分端点设置）。
- 改动：`internal/hub/api/api.go:305-309` 新增 `methodNotAllowed(w, allow)` 统一设置 Allow；替换/补齐全部 405 响应点。
- 覆盖 findings 列举的 14 个端点：`/api/tasks`、`/api/tasks/{id}`、`/api/settings`、`/api/groups/{id}`、`/api/scripts/{id}`、`/api/schedules/{id}`、`/api/artifacts/{id}`、`/api/nodes`、`/api/nodes/upgrade`、`/api/transfers`、`/api/executions/{id}/cancel`、`/api/applications/{id}/state`、`/api/oidc/state`、`/api/login`。
- 静态复核：当前 api.go 共 **44 个 405 响应点**，**44 个均带 Allow**，缺失 0。
- 测试：`TestMethodNotAllowedCarriesAllowHeader`（14 行表驱动，逐端点校验 405 + 精确 Allow 值）。
- 行为变化：仅新增响应头，状态码与响应体不变。

---

## 五、F7（P3）方法未拒绝

| 端点 | 修复前 | 修复后 | 位置 |
|---|---|---|---|
| `/api/login` | 任意方法可登录（GET/PATCH 400 invalid request 但携带凭据可建会话） | 非 POST → 405 + `Allow: POST` | `api.go:476-483` |
| `GET /api/tasks/{id}/run` | 进入 RunNow（GET 也执行） | 非 POST → 405 + `Allow: POST` | `api.go:1437-1451` |
| `DELETE /api/applications/{id}/revisions` | 200（返回版本列表） | 非 GET → 405 + `Allow: GET` | `api.go:1817-1823` |
| `DELETE /api/applications/{id}/nodes` | 200（返回节点列表） | 非 GET → 405 + `Allow: GET` | `api.go:1893-1899` |
| `GET /api/applications/{id}/assign`、`/deploy` | 404/400 分支 | 非 POST → 405 + `Allow: POST` | `api.go:1836-1842`、`:1862-1868` |

测试：`TestLoginRejectsNonPostMethods`（GET/PATCH/PUT → 405 + Allow: POST，且不返回 token；POST 仍 200）、`TestTaskRunRejectsNonPostMethods`（GET/PUT → 405 + 零执行记录；POST 仍进入 RunNow 并产生执行记录）、`TestApplicationSubresourcesRejectUnsupportedMethods`（5 条方法用例 + 正常 GET 200 回归）。

前端用法核对（只读，未改 `web/**`）：`/tasks/${id}/run` 为 `api.post`；`/applications/${id}/revisions|nodes` 为 `api.get`；`assign/deploy` 为 `api.post`——与收紧后的方法契约一致，无前端回归。

---

## 六、F8（P3）客户端取消记 ERROR 日志

- 根因：`writeInternalError` 对所有错误无条件 `logger.Error`，客户端断开（`context canceled`）也产生 ERROR 噪声（hub.log 中 7+16+6 条）。
- 改动：`internal/hub/api/api.go:320-342`——`errors.Is(err, context.Canceled)` 时降级为 **Debug** 日志（`api request canceled by client`，仅 method/path），其余错误保持 ERROR 语义与 500/503 响应不变。
- 测试：`TestClientCancelNotLoggedAsError`（已取消 ctx 的真实 `GET /api/nodes` 链路：响应仍 500、日志零 ERROR；对照注入真实错误仍必须 ERROR + 含路径）。
- 行为变化：仅日志级别（ERROR → DEBUG），HTTP 响应不变。

---

## 七、同类模式审计总结（除已知位置还有几处、如何处理）

| 模式 | 扫描范围 | 命中 | 处理 |
|---|---|---|---|
| A. `rows.Next()` 循环体内再发起查询/借连接 | 全仓 `internal/**` 非测试代码 31 个循环 | 1（`sqlite_file_transfers.go:64`） | 已修复（单条 JOIN）；其余 30 处标注安全（仅 Scan/append，无嵌套借连接） |
| B. 事务闭包内用外层 ctx 调 store（持写锁再借连接） | 全仓全部 `runMutationTx`/`WithTx` 闭包 | 1（`schedule.go:166`） | 已修复（读取移出事务）；其余全部使用 `txctx` |
| C. `writeErr(..., err.Error())` 直传（原文外泄面） | `internal/hub/api/api.go` | 26 处 | 22 处改为归一化路径；保留 4 处纯输入校验（已逐条标注理由） |
| D. 405 响应缺 `Allow` | `internal/hub/api/api.go` | findings 14 个端点 | 全部补齐；静态复核 44/44 带 Allow |
| E. 读方法绕过 `txctx`（潜在同类隐患，本次审计新发现） | `internal/store/*.go` 全部 `*SQLiteStore` 方法 | 定义/应用/传输族 7 个读方法 | **已防御性修复**为 `s.queryer(ctx)`（事务外行为完全不变，事务内不再借第二条连接）；新增池=1 守卫测试 `TestReadHelpersInsideTxDoNotBorrowSecondConnection`（撤掉修复即 10s 死锁失败） |
| E′. 其余 `s.db` 直读方法（executions/audit/logs/sync/revisions/artifacts/scripts 等） | 同上 | 34 个方法（读 + 少量事务外写） | **标注安全**：脚本复核这 34 个方法的调用点全部在事务之外（与事务闭包内调用集合求交集 = 0），行为与现状一致；如需彻底消除该类隐患，建议后续批次统一改为 `queryer(ctx)`（独立变更，本次不扩大范围） |

审计脚本（可复现，只读）：按括号配对提取循环/闭包体后匹配 `Query|QueryRow|Exec|Prepare|s.db.` 与 `fn(ctx, ...)` 调用（详见本报告执行过程）。

---

## 八、真实命令结果

| 命令 | 结果 |
|---|---|
| `go build ./...` | ✅ exit=0 |
| `go vet ./...` | ✅ exit=0 |
| `gofmt -l internal/` | ✅ 输出为空 |
| `go test ./internal/... -count=1` | ✅ 全绿：agent / agent/condition / agent/connection / agent/execution / agent/host / agent/scheduler / hub / hub/agentbundle / hub/api / hub/auth / hubserver / store 全部 `ok`（无 `FAIL`） |
| `go test ./internal/store/ ./internal/hub/api/ ./internal/hub/ -run 'TestListFileTransfers…|TestReadHelpersInsideTx…|TestTransfersListConcurrentNoHang|TestNodesDeleteConcurrentAllSucceed|TestSchedulesDeleteConcurrentNoBusyLeak|TestClientCancelNotLoggedAsError|TestWriteMutationErrorNormalization' -race -count=1` | ✅ 全部 PASS（`-race` 无数据竞争报告） |

说明：`go test` 期间 `/tmp` tmpfs 空间不足（`disk quota exceeded`），已用 `TMPDIR=/var/tmp/nodesteer-go-tmp` 规避；该目录被系统 tmp 清理掉时需先 `mkdir -p`。

既有用例调整（1 处，缺陷驱动的契约更新）：`internal/hubserver/rbac_audit_test.go:133-137` 中「operator 取消不存在执行 → 400（证明权限已授予）」按 F4 新契约改为断言 **404**（仍非 403，RBAC 语义不变）。

---

## 九、未做 / 需协调项

1. **未部署、未跑隔离实例复测**：按本批边界只做代码与单测；`evidence/scripts/` 中的压测脚本（`deadlock_probe.py`、`repro.py`、`stress.py`）可在下一次部署后对隔离实例复跑，确认线上 16 并发与 8 并发场景。
2. **未改 `.e2e/**` 状态文件**，未更新 `findings.json` 的缺陷状态；如需标记已修复请由 QA 批次统一处理。
3. **范围外观察（本次未修）**：
   - Agent 侧文件传输端点（`internal/hub/file_transfer.go` 的 `/agent/transfers/*`）405 未带 Allow（不在 F6 端点清单内）；
   - findings 的 `observations` 项「未知子路径按基础资源处理」（如 `DELETE /api/tasks/{id}/bogus` 会删除任务）仍存在，属独立的契约收紧任务；
   - `writeErr` 之外的 `writeTransferJSON(... err.Error())`（agent 端点）未纳入本次归一化。
4. **后续建议**：把第七节 E′ 的 34 个 `s.db` 直连方法统一改为 `queryer(ctx)`/`execer(ctx)`，从机制上关闭「事务内二次借连接」整类风险（本次已用池=1 守卫测试锁定已修复族）。
