# Go 侧尾部缺陷修复报告（qa-resume-20261017-tail）

- 批次：Go 尾部缺陷修复（用户授权「全部修复延伸」）
- 日期：2026-10-17 / 2026-10-18（+08）
- 范围：`internal/**` Go 源码与单测；未触碰 `internal/agent/redact.go`、`redact_test.go`、`redact_prop_test.go`、`web/**`、`.e2e/**` 状态文件；未执行 Git / 部署 / 依赖安装
- 判定依据：`.e2e/runs/20260930-101945/repair/qa-resume-20261017-tail-verify/case-result.json`
- 基线工作区：`git HEAD dddee4f` + 其它批次既有未提交改动（本报告只描述本批改动）

## 0. 四条验证命令真实结论（最终状态）

| 命令 | 结论 |
|---|---|
| `go build ./...` | 通过（exit 0，无输出） |
| `go vet ./...` | 通过（exit 0，无输出） |
| `go test ./internal/... -count=1` | 全通过（exit 0；agent 39.2s / connection 0.6s / hub 0.4s / hub/api 2.5s / hub/auth 0.8s / hubserver 4.6s / store 0.4s 等 12 包 ok，3 包无测试文件） |
| `gofmt -l internal/` | 无输出（exit 0，全部已格式化） |

补充：新增并发/竞态相关用例在 `-race` 下复跑通过（enrollment 并发 ×2、握手用例、store 忙锁用例）。

---

## 1. P2：enrollment 并发 500（`OBS-20261016-G1-ENROLL-TRANSIENT-500` + `OBS-20261017-ENROLL-INTERMITTENT-500-AGAIN`）

### 根因
- `WithTx` 用 `BeginTx(ctx, nil)`（deferred）：WAL 下事务先读（快照）后写需要升级写锁，一旦其它连接已推进快照立即返回 `SQLITE_BUSY` / `BUSY_SNAPSHOT`，**不经过 busy_timeout**。
- 驱动为 `modernc.org/sqlite v1.56.0`，`_txlock` 参数受支持（`sqlite.go:332-337`，`tx.go:23-24` 对非 ReadOnly 事务发 `begin <mode>`）。
- 次生问题：① 驱动原文直接回给客户端；② Hub 对存储层错误零日志（隔离实例仅 4 行 INFO）。

### 改动
| 文件:行 | 改动 |
|---|---|
| `internal/store/sqlite.go:74` | DSN 增加 `_txlock=immediate`：`path + "?_txlock=immediate&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)"`（注释 `:66-73` 说明升级竞态） |
| `internal/store/sqlite.go:109-142` | 新增 `IsBusyError`（`*sqlite.Error` code&0xff ∈ {SQLITE_BUSY=5, SQLITE_LOCKED=6} + 文案兜底）与 `IsUniqueViolation` |
| `internal/hub/api/api.go:310-323` | 新增 `writeInternalError(w, r, err)`：error 级日志（method/path，不含 query，避免 `agent_token` 等敏感值）+ 归一化响应 |
| `internal/hub/api/api.go` 25 处 | 所有 `writeErr(w, 500, err.Error())` → `s.writeInternalError(w, r, err)`（BUSY/LOCKED → **503** `storage busy, please retry`；其余 → **500** `internal error`） |
| `internal/agent/store.go:50-52` | 同一根因延伸：Agent 本地库 DSN 同样加 `_txlock=immediate`（证据指出同类竞态不止 enrollment；该库写路径有 mu 串行，属加固） |

关键 diff：
```go
// internal/store/sqlite.go
-dsn = path + "?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)"
+dsn = path + "?_txlock=immediate&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)"
```
```go
// internal/hub/api/api.go
func (s *Server) writeInternalError(w http.ResponseWriter, r *http.Request, err error) {
	s.logger.Error("api request failed", "method", r.Method, "path", r.URL.Path, "error", err)
	if store.IsBusyError(err) {
		writeErr(w, http.StatusServiceUnavailable, "storage busy, please retry")
		return
	}
	writeErr(w, http.StatusInternalServerError, "internal error")
}
```

### 新增测试
- `internal/hubserver/enrollment_test.go`：`TestNodeEnrollmentConcurrentWritesAllSucceed` —— 文件库 + WAL + 12 并发 `POST /api/nodes/enrollment`，断言全 200、无 `database is locked`、落库 12 节点。
  - **反向验证**：临时去掉 `_txlock=immediate` 后该用例 3/3 失败（`concurrent enrollment #N: status 503 body {"error":"storage busy, please retry"}`），恢复后 5/5 通过。
- `internal/store/sqlite_busy_test.go`：`TestWriteTxWaitsForBusyTimeout` —— 连接 A 持写事务，连接 B 先读后写 mutation 事务必须等待锁释放后成功（elapsed ≥ 200ms）。
  - **反向验证**：无 `_txlock=immediate` 时 B 立即失败 `database is locked (5) (SQLITE_BUSY)`，精确复现根因。
- `internal/hub/api/storage_error_test.go`：`TestEnrollmentStorageErrorNormalizedAndLogged` —— 注入 `database is locked (5) (SQLITE_BUSY)` → 503 + `storage busy, please retry` 且 body 不含 locked/sqlite/database；注入 `sql: no rows...` → 500 + `internal error` 且不含原文；两种都断言日志含 `level=ERROR`/`api request failed`/`/api/nodes/enrollment`。

### 行为变化
- 并发写不再返回 500（enrollment 及所有 `WithTx` mutation 路径）。
- 500 响应体由驱动原文 → `internal error`；忙锁类 → **503** `storage busy, please retry`（可重试语义）。
- 所有内部错误新增 error 级日志（此前零记录）。

---

## 2. `OBS-20261012-APP-NODES-NULL-SHAPE`

- **根因**：`GetApplicationNodes` 用 `var out []string`（nil slice），`json.Marshal(nil)` → `null`。
- **改动**：`internal/store/sqlite_artifacts_apps.go:254-268` —— `out := []string{}`，并把 `rows.Err()` 显式返回；无绑定节点时序列化 `[]`。
- **新增测试**：`internal/hub/api/app_nodes_shape_test.go`：`TestApplicationNodesEmptyIsArray`（原始响应体 `[]`）。
  - **反向验证**：临时恢复 `var out []string` → 用例失败（body `"null"`），恢复后通过。
- **行为变化**：`GET /api/applications/{id}/nodes` 无绑定时 `null` → `[]`（严格消费者契约稳定）。

---

## 3. `OBS-20261014-PAST-ONETIME-SAVE-ENABLED`（服务端）

- **根因**：`validateSchedule` 对 one_time 只校验 `RunAt` 非零。
- **改动**：`internal/hub/validation.go:175-197` —— 签名加 `previousRunAt time.Time`；one_time 分支增加 `if !s.RunAt.Equal(previousRunAt) && !s.RunAt.After(time.Now()) { return fmt.Errorf("run_at must be in the future") }`。`internal/hub/schedule.go:58`（Create 传零值）与 `:120`（Update 传 `existing.RunAt`）：**值未修改时放行**，修复前创建的过去时间记录仍可编辑/禁用，不被锁死。
- **新增测试**：`internal/hub/on_start_test.go`（扩展：过去拒绝/未改值放行/改新过去值拒绝/未来通过）+ `internal/hub/schedule_test.go`：`TestOneTimeScheduleRejectsPastRunAt`（Create 拒绝、未来创建成功、历史遗留记录禁用成功、改新过去值拒绝）。
- **行为变化**：`POST /api/schedules` type=one_time 且 run_at 在过去 → **400 `run_at must be in the future`**；`PUT` 仅在 run_at 实际变化时拒绝（前端内联校验由另一批负责）。

---

## 4. `OBS-20261016-G23-RECONNECT-WINDOW`

- **根因**：`m.connected=true` 在 WS Dial 成功、HELLO 发送前设置，被 `SetOnlineFunc(a.conn.IsConnected)` 直接用作 `hub_online_required` 门禁。
- **改动**：
  - `internal/agent/connection/manager.go:169-194` 新增握手标志 `handshake` + `HandshakeComplete()` / `markHandshake(accepted)` / `helloAckAccepted`（解析失败 Fail Closed）；`:238` 断开复位、`:313` 新连接复位；`dispatch` 中 HELLO_ACK 先更新标志再交业务 handler（handler 缺失也生效）。
  - `internal/agent/agent.go:150`：`a.sch.SetOnlineFunc(a.conn.HandshakeComplete)`。
  - `internal/agent/agent.go:1564`：`TriggerSchedule` 的 `Offline` 标记同源（窗口内为 true，等重连对账补传）。
  - `internal/agent/scheduler/scheduler.go:76-82`：新增只读 `Online()`（供测试/诊断，不改门禁语义）。
- **新增测试**：
  - `internal/agent/connection/manager_test.go`：`TestHandshakeFlagOnlyAfterAcceptedAck`（无 ACK 窗口 false / rejected false / accepted true）、`TestHandshakeResetsOnReconnect`（断开复位）。
  - `internal/agent/reconnect_gate_test.go`：`TestSchedulerGateUsesHandshakeNotTransport`（真实 WS 服务端，ACK 前门禁关闭、accepted 后放行）。
    - **反向验证**：临时把门禁改回 `IsConnected` → 用例失败（`gate must stay closed in the reconnect window`），恢复后通过。
- **行为变化**：重连窗口（Dial 成功→HELLO_ACK accepted）内 `hub_online_required` 调度不再触发；窗口内本地触发的执行 `offline=true`（可对账补传）。始终拿不到 accepted 时该策略调度不触发——符合策略本意。

---

## 5. `OBS-20261015-API-CONTRACT-DEVIATIONS-LOW`（6 条仍开放）

### ① transfers 非 POST → 405（`internal/hub/api/api.go:1101-1132`）
- 方法守卫先于鉴权与存在性：非 GET/POST → 405 + `Allow: GET, POST`；`GET /{id}/{action}` → 405 + `Allow: POST`；POST 缺动作段 → 404；canAdmin 在方法守卫之后。
- 测试：`internal/hubserver/api_contract_test.go` `TestTransfersMethodGuardIs405`。
- 行为变化：`PUT/DELETE /api/transfers/{id}` 403 → **405**（带 Allow）；`GET /{id}/cancel` 403 → **405**。

### ② DELETE 缺 id / 不存在 id 统一 404
| 端点 | 改前 | 改后 |
|---|---|---|
| `/api/scripts/{id}` `/api/tasks/{id}` `/api/schedules/{id}` `/api/groups/{id}` `/api/artifacts/{id}` `/api/applications/{id}` | 200（静默成功） | **404 + 归一化 not found 文案** |
| `/api/nodes/{id}` | 404 | 404（不变） |
| `/api/users/{id}` `/api/executions/{id}` | 405（无 Allow） | **405 + Allow**（方法不支持统一 405） |

- 实现：各 DELETE 分支在 `canWrite/canAdmin` 之后先做存在性检查（`scripts.Get`/`tasks.Get`/`schedules.Get`/`store.GetGroup`/`artifacts.Get`/`apps.Get`），不存在即 404（`api.go:1343`、`:1398`、`:1543`、`:1176`、`:1615`、`:1888` 附近）。
- 测试：重写 `internal/hubserver/delete_idempotent_test.go`（`TestDeleteMissingResourceIs404` 7 端点、`TestDeleteUnsupportedMethodIs405`）；更新 `internal/hubserver/api_test.go`（artifact 二次删除 200→404）。
- 行为变化：删除不存在资源由「幂等 200」→ **404**；客户端重试删除需按 404 处理（前端由主 Agent 侧确认）。

### ③ `/api/me` 与 `/api/login.user` 键名 snake_case（`internal/hub/auth/auth.go:20-32`）
- `Session` 补 json tag：`token` / `user_id` / `username` / `role` / `avatar,omitempty` / `expires`。
- 测试：`internal/hub/api/api_test.go`（`TestMeReturnsAvatar` 改断言 + 旧 PascalCase 键不得出现）、`internal/hubserver/api_contract_test.go` `TestSessionKeysAreSnakeCase`（login.user 与 /api/me 双端点）。
- 行为变化：**破坏性键名变更**。Web 消费方（`web/src/lib/api.ts` 的 `SessionInfo`、`web/src/stores/auth-store.ts` 读 `session.UserID/Username/Role/Expires`）必须同批切换，否则登录后会话解析失败。本批按指示只改 Go，**已标注需主 Agent 同步 Web**。

### ④ X-Total-Count 端点清单与取舍
- 核查：Go API 中带 `limit/offset` 分页参数的列表端点只有 **executions、audit**（`api.go` 仅两处解析；`store.ExecutionFilter`/`AuditFilter` 是唯一带 Limit/Offset 的过滤器），两者已返回 `X-Total-Count` 且 `Access-Control-Expose-Headers` 已包含该头。
- 取舍：**不新增**。nodes/tasks/scripts/applications/schedules/users/groups/transfers 均无服务端分页参数（Web 也是全量拉取后客户端分页），按要求「无分页的不加」；新增分页属能力扩展而非缺陷修复。
- 测试：`internal/hubserver/api_contract_test.go` `TestListTotalCountOnlyOnPaginatedEndpoints`（7 个无分页端点断言该头缺省，锁定边界）。

### ⑤ CORS 核查结论（`internal/hub/api/api.go:205-217`，未改代码）
- 实际配置：`Access-Control-Allow-Origin: *`、Methods `GET, POST, PUT, DELETE, OPTIONS`、Headers `Content-Type, Authorization, X-NodeSteer-Agent-Token, X-NodeSteer-Agent-ID`、Expose `X-Total-Count`。
- **无 `Access-Control-Allow-Credentials`**；认证走 `Authorization: Bearer`（`bearerToken` 只读 header，无 Cookie 路径），浏览器不会随请求携带凭据 → **不构成「通配来源 + 凭据」泄露**（第三方站点无法凭用户 Cookie 读取 API）。
- 结论：条件不满足「若为通配+凭据则收紧」，本批不改；若产品后续要求按部署形态收紧（同源部署可不发 CORS 头或白名单），需要新增配置项与部署形态裁决，属独立变更。

### ⑥ 存在性 oracle（nodes）（`internal/hub/api/api.go:904-1004`）
- 重排：`GetNode` 从方法分支前移到各分支内；DELETE/POST/PUT 先 `canAdmin/canWrite` 再存在性，GET 直接查。
- `revoke-credential` 子路径：非 POST 先 405（Allow: POST）再鉴权；`default` 405 带 `Allow: GET, POST, PUT, DELETE`。
- 测试：`TestNodeDeleteExistenceOracleClosed`（operator 对存在/不存在 id 均 403）、`TestNodeUpdateMissingReturns404`（PUT 不存在 → 404，无 `sql: no rows` 外泄）。
- 行为变化：operator/viewer DELETE 存在 id 403 / 不存在 id 由 404 → **403**（不可枚举）；admin PUT/POST 不存在 id 由 400 + `sql: no rows in result set` → **404 `node not found`**。

---

## 6. `OBS-20261014-ARTIFACT-DOWNLOAD-NO-READ-TIMEOUT`

- **根因**：Agent 下载客户端来自 `http.DefaultClient`（无读/总超时），服务端发头后不给 body → `io.Copy` 永久阻塞、0B `.tmp` 长期残留。
- **改动**：`internal/agent/connection/manager.go:44-68` —— 新增 `defaultHTTPTimeout = 15 * time.Minute`（总时限）与 `defaultHTTPResponseHeaderTimeout = 30 * time.Second`（响应头）；`HTTPClient()` 兜底改返回 `defaultAgentHTTPClient`（不再 `http.DefaultClient`），`SetTLSCA` 构造的客户端同样带超时（`:112-114`）。选择理由写入代码注释：Go 无现成读空闲超时；总时限 15 分钟按 1 MiB/s 保守链路可传完约 900 MiB（与文件传输的保守速率下限一致），30s 响应头超时与默认拨号超时同量级。coder/websocket 会把 `Client.Timeout` 转为拨号 ctx 超时并清零客户端超时，WS 长连接不受影响。
- 失败清理：`Prefetch` 既有 `defer os.Remove(tmpPath)` 在超时失败路径生效，超时后不再残留 0B `.tmp`。
- **新增测试**：`internal/agent/artifact_test.go` `TestArtifactCacheStalledBodyTimesOut`（发头不给 body → 200ms 注入超时内失败、无 `.tmp`、未缓存）；`internal/agent/connection/manager_test.go` `TestHTTPClientHasDownloadTimeouts`（兜底与 TLS 两条路径的总时限/响应头超时）。
- **行为变化**：artifact 下载与 Agent 自升级二进制下载在有界时间内失败（原可无限挂起）。

---

## 7. `OBS-20261016-JOURNAL-FROM-VERSION`

- **根因**：`deploy()` 用 `app.Version`（Hub 当前应用定义 = 期望版本）写 `deployments.from_version`，不是节点实际安装版本。
- **改动**：
  - `internal/agent/store.go:816-832` 新增 `InstalledApplicationVersion(ctx, appID)`：最近一次 `phase='DONE' AND completed_kind='deployed'` 的 `to_version`；无记录返回空串。回滚成功（rolled_back）不构成已安装版本。
  - `internal/agent/application.go:303-312`：`fromVersion, err := am.store.InstalledApplicationVersion(...)`；读取失败按准备阶段失败处理（无宿主副作用）；`FromVersion: fromVersion`。
- **新增测试**：`internal/agent/application_test.go` `TestDeploymentJournalRecordsInstalledFromVersion`（无部署→空；成功 1.0 + 回滚 2.0 后仍 1.0；定义 3.0 的新部署 journal `from_version=1.0`、`to_version=3.0`）。
- **行为变化**：Agent 本地部署 journal 的 `from_version` 语义由「定义期望版本」→「实际安装版本」（无对外 API 契约变化）。

---

## 8. `OBS-20261016-G1-USER-DUP-SQL-MESSAGE`

- **根因**：`POST /api/users` 直接回传 `CreateUser` 的 SQLite UNIQUE 原文。
- **改动**：`internal/hub/api/api.go:2207-2222` —— 先 `GetUserByUsername` 预检，命中 → 400 `username already exists`（与 groups 风格一致）；并发兜底 `store.IsUniqueViolation(err)` 同文案；其余错误走 `writeInternalError`。
- **新增测试**：`internal/hubserver/api_contract_test.go` `TestDuplicateUsernameNormalized`（400 归一化文案 + 断言不含 unique/constraint/sqlite/users.username）。
- **行为变化**：重复用户名错误文案归一化，不外泄数据库细节。

---

## 9. `OBS-20261017-XFER-API-NO-SELF-TRANSFER-CHECK`

- **根因**：Transfer API 无自传校验（仅前端裁剪）。
- **改动**：`internal/hub/file_transfer.go:216-221` —— `Create` 目标循环内 `req.NodeID == sourceNodeID` → `self transfer is not allowed: target node %s is the source node`（handler 归一化为 400）。
- **新增测试**：`internal/hub/file_transfer_test.go` `TestFileTransferRejectsSelfTransfer`（单目标自传拒绝、混合同批整体拒绝且零落库）。
- **行为变化**：`POST /api/transfers` 自传 → **400** 明确文案。

---

## 10. `OBS-20261017-EXEC-DISABLED-BRANCH-UNREACHABLE`

- **根因**：`createAndDispatch` 先判 `node.Status != online` 返回，disabled 专用分支不可达。
- **改动**：`internal/hub/execution.go:219-227` —— disabled 检查前移到 online 通用检查之前。
- **新增测试**：`internal/hub/pause_dispatch_test.go` `TestRunManualDisabledNodeAccurateMessage`（文案含 `is disabled`、不含 `not online`、不产生执行记录；maintenance 用例保持 not online 语义）。
- **行为变化**：手动运行被禁用节点 → 400 `node X is disabled`（原 `node X is not online (status=disabled)`）。

---

## 11. 对外可见行为变化汇总（客户端适配清单）

1. 存储层错误响应：500 原文 → 500 `internal error`；BUSY/LOCKED → **503** `storage busy, please retry`；新增 error 日志。
2. `GET /api/applications/{id}/nodes` 空绑定：`null` → `[]`。
3. `POST /api/schedules` one_time 过去 run_at → **400 `run_at must be in the future`**（PUT 仅值变化时拒绝）。
4. `/api/transfers/{id}` 方法错误：403 → **405**（`Allow: GET, POST`；动作段 `Allow: POST`）。
5. DELETE 不存在 id：scripts/tasks/schedules/groups/artifacts/applications 200 → **404**；users/executions 405 补 `Allow`。
6. `/api/me`、`/api/login.user` 键名 → `token/user_id/username/role/expires`（**破坏性，Web 必须同批改**）。
7. `PUT/POST /api/nodes/{id}` 不存在 → **404**（原 400 + SQL 原文外泄）；operator/viewer DELETE 存在/不存在 id 均 **403**。
8. 重复用户名 → **400 `username already exists`**。
9. Transfer 自传 → **400 `self transfer is not allowed...`**。
10. 手动运行 disabled 节点 → **400 `node X is disabled`**。
11. Agent 下载（artifact/自升级）引入 15min 总时限 + 30s 响应头超时（超时失败并清理 `.tmp`）。
12. Agent 部署 journal `from_version` = 实际安装版本。
13. Hub/Agent SQLite DSN `_txlock=immediate`（内部并发语义，无外部契约变化）。

## 12. 未做 / 需协调项

| 项 | 说明 |
|---|---|
| Web 键名切换（③） | `web/src/lib/api.ts` `SessionInfo`、`web/src/stores/auth-store.ts` 仍读 PascalCase；**必须与本批 Go 变更同批上线**，否则登录态解析失败。按指示由主 Agent 后续同步。 |
| Web DELETE 404 适配（②） | 前端删除 mutation 对 404 会走 onError；若存在「删除重试/并发删除」路径需确认提示文案（本批只改 Go）。 |
| X-Total-Count（④） | 未新增任何端点（无分页参数的列表端点不加）；已用契约测试锁定边界。 |
| CORS（⑤） | 核查后不改（无 Allow-Credentials + Bearer 认证，非「通配+凭据」）；如需按部署形态收紧属独立变更（需配置项与产品裁决）。 |
| `internal/agent/util.go` `httpGet*` | 无生产调用点的死代码，本批未清理（避免扩大范围）。 |
| 运行环境复验 | 本批不部署：未在真实 Hub/Agent 环境复验并发纳管、重连窗口、artifact 超时；验证为本地单测 + `-race`。建议下一构建部署后按原观察复验（enrollment 并发 12 全 200；重连窗口内 hub_online_required 不触发）。 |
| 未触碰 | `redact*.go`（另一批次）、`web/**`、`.e2e/**` 状态文件、Git、依赖、远程环境。 |
