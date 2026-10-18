# Go 侧缺陷修复报告（fixall，2026-10-17）

范围：只改 Go 源码与对应单测；未改 `web/**`、未改 `.e2e/**` 状态文件、未做任何 Git 操作、未装依赖、未访问远程主机。
不需要历史兼容（用户明确）：允许直接改语义/契约，行为变化在文末逐条列出。

| # | 缺陷 | 状态 |
|---|---|---|
| 1 | APPUI-20261016-ENVFILE-QUOTED-DASH（P1） | 已修复 + 单测 + 真实 systemd 端到端验证 |
| 2 | APPUI-20261016-ROLLBACK-FALSE-FAILURE（P2） | 已修复 + 单测 |
| 3 | E2E-20261016-ASSIGN-MISSING-APP-500-SQL-LEAK（P2） | 已修复 + 单测 + 真实 HTTP 验证 |
| 4 | E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING（P1） | 已修复 + 单测（运行时验证需实网 Hub/Agent） |
| 5 | OBS-20261008-EXECUTION-ZERO-EXIT-CODE（P2） | 已修复 + 单测 + 真实 HTTP 验证 |
| 6 | OBS-20261010-UI-LOGOUT-NOT-REVOKED（P1） | **归 Web 批次**（Go 侧已具备 POST-only + 撤销，未改动） |
| 7a | OBS-20261017-SCRIPT-PUT-TYPE-UNVALIDATED | 已修复 + 单测 + 真实 HTTP 验证 |
| 7b | OBS-20261016-G1-USER-INPUT-NOT-TRIMMED | 用户名部分已修复 + 单测；**密码最小长度未做**（理由见 §7） |

---

## 1. APPUI-20261016-ENVFILE-QUOTED-DASH（P1）

### 根因
`buildUnit` 用 `unitQuote("-"+p.ConfigPath)` 生成 `EnvironmentFile="-<绝对路径>"`。systemd 对
`EnvironmentFile=` 的值**不做词拆分**、且只在**值首字符**识别可选前缀 `-`：整体加引号后 `-` 不再被识别，
systemd 把 `"-/etc/x.conf"` 当作一个非绝对路径并报
`EnvironmentFile= path is not absolute, ignoring`，整个环境文件被静默忽略 → 应用「配置内容」运行期完全不生效。

本机 systemd 259 实测（user manager，实际启动服务读取变量）：

| 单元文件写法 | 运行期变量 | journal |
|---|---|---|
| `EnvironmentFile="-"/tmp/.../app.conf` | **未生效**（`A=` 空） | `path is not absolute, ignoring: "-"/tmp/.../app.conf` |
| `EnvironmentFile=-"/tmp/.../app.conf"` | 未生效 | `path is not absolute, ignoring: "/tmp/.../app.conf"` |
| `EnvironmentFile="/tmp/.../app.conf"` | 未生效 | `path is not absolute, ignoring` |
| `EnvironmentFile=-/tmp/.../app.conf` | **生效**（`A=1`） | 无告警 |

即：只有**完全不加引号**的 `-<绝对路径>` 形式正确；含空格路径同样成立（该指令不拆词，实测通过）。

### 改动
- `internal/agent/application.go:576`：`b.WriteString("EnvironmentFile=-" + p.ConfigPath + "\n")`（原为 `unitQuote("-"+p.ConfigPath)`），并在 `internal/agent/application.go:570-575` 注释实测结论。
- 顺带加固（同一 Unit 内嵌路径的边界）：新增 `validateUnitEmbeddedPath`（`internal/agent/application.go:863`），在**准备阶段**（`internal/agent/application.go:345-356`，任何宿主副作用之前）拒绝含控制字符的 `binary_path`/`config_path`——未加引号后换行会注入新的 Unit 指令行。

关键 diff：

```diff
-	if p.ConfigPath != "" {
-		b.WriteString("EnvironmentFile=" + unitQuote("-"+p.ConfigPath) + "\n")
-	}
+	if p.ConfigPath != "" {
+		// EnvironmentFile 的值必须是**未加引号**的 "-<绝对路径>"（实测 systemd 259）……
+		b.WriteString("EnvironmentFile=-" + p.ConfigPath + "\n")
+	}
```

### 新增测试
`internal/agent/unit_envfile_test.go`（新文件）
- `TestBuildUnitEnvironmentFileUnquoted`：普通路径 + 含空格路径，断言整行无引号且等于 `EnvironmentFile=-<path>`，并断言 `ExecStart` 仍按词拆分规则加引号。
- `TestBuildUnitOmitsEnvironmentFileWithoutConfigPath`：无配置路径不产生该行。
- `TestValidateUnitEmbeddedPathRejectsControlCharacters`：接受空格/中文路径；拒绝 `\n`/`\r`/`\t`/`\x00`。
- `TestDeployRejectsConfigPathWithControlCharacters`：带换行的 `config_path` 在准备阶段被拒（旧文件/旧服务不变，journal 收敛 `prepare_failed`）。

### 真实 systemd 端到端验证（非仅单测）
用 `buildUnit` **实际输出**的 Unit 文本（`EnvironmentFile=-/tmp/opencode/fixall/app.conf`，配置内容 `FROM_CONFIG=applied`）装进 user manager 启动，服务内实际读到：

```
ENV=yes CONFIG=applied          # Environment= 与配置内容都生效
```

---

## 2. APPUI-20261016-ROLLBACK-FALSE-FAILURE（P2）

### 根因
`deploy` 的失败闭包把**失败版本**的健康定义 `hc` 一路传给 `rollback`，回滚还原文件并拉起旧版本后用它复检。
旧版本与新版本的健康语义（HTTP 端口/命令/目标）不同，负向或新版本专属检查必然再失败 →
`health check failed; rollback failed`，并落 `ROLLBACK_FAILED`（文件其实已还原）。
`RecoverDeployments` 重放时同样用「当前应用定义（=新版本）」的健康定义，存在同一误判。

### 改动
- `internal/agent/application.go:428`：`failed` 闭包不再携带健康定义（原先唯一用途就是传给 `rollback`），全部调用点简化为 `failed("...")`。
- `internal/agent/application.go:663` + `:725`：`rollback` 去掉 `hc` 形参，还原后统一做**版本无关存活复检** `models.HealthCheck{Type: models.HealthTypeSystemd}`（恢复后的 Unit 必须 `active` 且保持 `active`）。
- `internal/agent/application.go:781`（`RecoverDeployments`）：重放回滚不再取应用定义的健康检查，同一存活复检。

语义：`ROLLBACK_FAILED` 只保留给**真的回滚失败**（备份不可读、还原写入失败、还原后服务起不来等）。

关键 diff：

```diff
-		if !am.rollback(ctx, ..., backupUnitPath, health) {
+		if !am.rollback(ctx, ..., backupUnitPath) {
@@ func (am *ApplicationManager) rollback(...)
-		return am.checkHealth(ctx, hc, unitName)
+		// 版本无关存活复检：恢复后的 Unit 必须 active 且保持 active。
+		return am.checkHealth(ctx, models.HealthCheck{Type: models.HealthTypeSystemd}, unitName)
```

### 新增/调整测试
`internal/agent/rollback_health_test.go`（新文件）
- `TestRollbackSucceedsWhenFailedVersionHealthCheckWouldFail`：v1 部署成功 → v2 用 TCP 负向健康检查（`127.0.0.1:1`）部署 → 断言 `rollback` 已发生、错误**不含** `rollback failed`、文件回到 v1、旧服务在跑、journal 收敛 `DONE/rolled_back`（不是 `ROLLBACK_FAILED`）。
- `TestRollbackHealthCheckIsVersionAgnostic`：直接调用 `rollback` 断言与失败版本定义无关。
- `TestRollbackLivenessCheckFailsWhenRestoredServiceCannotStart`：真失败（服务起不来）仍返回 false。
- `TestRecoverDeploymentsUsesVersionAgnosticHealthCheck`：应用定义带负向健康检查时恢复仍能收敛 `DONE`。

`internal/agent/application_test.go`（改写 `TestRollbackFailureMarksRollbackFailedPhase`）：原用例断言的是**旧缺陷行为**（"失败版本 health 复检必然不过 → 落 ROLLBACK_FAILED"）。现改为注入**真实还原写入失败**（`failingRestoreHost`，第 3 次 `AtomicReplace`：v1 安装、v2 安装、回滚还原），断言 `rollback failed` + `ROLLBACK_FAILED` + 二进制仍是 v2，随后恢复重放收敛且还原为 v1。

---

## 3. E2E-20261016-ASSIGN-MISSING-APP-500-SQL-LEAK（P2）

### 根因
`/api/applications/{id}/assign|deploy` 直接进业务层：`AppManager.Assign/Deploy` 内部 `store.GetApplication`
返回裸 `sql.ErrNoRows`，API 层分别以 `500` / `400` 把 `sql: no rows in result set` 原文回给调用方。
同类泄漏点还有 `AppManager.Deploy/DeployTask` 里节点不存在时的裸 `GetNode` 错误。

### 改动
- `internal/hub/api/api.go:316` 新增 `requireApplication`（存在性前置检查 → `404 {"error":"application not found"}`）；`assign`/`deploy` 两个分支在进入业务逻辑前调用（`internal/hub/api/api.go:1708`、`:1735`）。
- `internal/hub/api/api.go:327` 新增 `writeApplicationSubresourceError`：`sql.ErrNoRows` → 404 归一化文案；其余为业务校验文案（节点不存在/离线/缺能力），不再外泄存储层原文。`assign` 由 500 改为该映射。
- `internal/hub/application.go:162`、`:236`、`:291`：节点不存在归一化为 `node <id> not found`（业务文案）。

关键 diff：

```diff
 	case "assign":
+			// 存在性前置检查：不存在的应用必须先归一化为 404，
+			// 否则存储层 sql.ErrNoRows 原文会以 500 外泄。
+			if !s.requireApplication(w, r, id) {
+				return
+			}
 			...
 			if err := s.apps.Assign(r.Context(), id, req.NodeIDs, !req.Remove); err != nil {
-				writeErr(w, http.StatusInternalServerError, err.Error())
+				writeApplicationSubresourceError(w, err)
```

### 新增测试
`internal/hub/api/application_subresource_test.go`（新文件）
- `TestApplicationSubresourceMissingAppReturns404`：两条路径均 404 + `application not found`，并显式断言响应体不含 `sql`/`no rows`/`sqlite`/`select `。
- `TestApplicationSubresourceExistingAppStillWorks`：已存在应用 assign 仍 200；不存在节点 deploy → 400 `node missing-node not found` 且无 `no rows`。

### 真实 HTTP 验证（本地构建 hub + 临时 DB，端口 28180）
```
POST /api/applications/00000000-...-0000000000ff/assign → HTTP 404 {"error":"application not found"}
POST /api/applications/00000000-...-0000000000ff/deploy → HTTP 404 {"error":"application not found"}
```

---

## 4. E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING（P1）

### 根因（两处叠加）
1. `internal/hub/gateway.go` 的 `wsConn.writeLoop` 写失败后**静默 return**：`send()` 仍返回成功（消息塞进 256 长缓冲区但永不写出），缓冲区满后发送方**永久阻塞**；上层无法感知通道不可用 → Hub 认为 `FILE_DELIVERY_REQUEST` 已下发，目标被写成 `DELIVERING` 而 Agent 从未收到。
2. `dispatchTarget` 没有任何**投递超时**：目标停在 `DELIVERING` 后既无回报也无超时，传输永久非终态 →
   `DELETE /api/nodes/{id}` 被 `409 has active file transfer` 永久阻断。

### 改动
(a) 写失败可观测 + 通道不可用对上层可见：
- `internal/hub/gateway.go:754`：写失败记 `logger.Error("agent websocket write failed; closing connection", node/type/error)`。
- `internal/hub/gateway.go:736`：新增 `markUnavailable()`（`doneOnce` 幂等关闭 `done`），写失败时调用并 `CloseNow()` 断开底层连接 → 读循环立即返回 → `handleDisconnect` 回收会话 → Agent 重连后 `ResumeNode` 重投。
- `internal/hub/gateway.go:709`：`send` 先探测 `done`（避免 `select` 随机把消息静默入队），通道不可用时返回 `errConnUnavailable`（原为 `fmt.Errorf("conn closed")`，且仅在 `done` 已关闭时生效）。
- `internal/hub/gateway.go:122-125`：handler 的 defer 改用 `markUnavailable()`（避免与写失败路径重复 close panic）。

(b) 投递超时（`internal/hub/file_transfer.go`）：
- `:43` 阈值常量与说明：`defaultDeliveryStallTimeout = 30 * time.Minute`，另按保守下限 `1 MiB/s` 折算下载时间（`deliveryStallTimeout(size)`，10 GiB ≈ 2.8h）。**只作用于 `DELIVERING`**（指令已下发、等回报）；`PENDING`（等源暂存/等节点重连）是正常可恢复状态，不终结。
- `:142` `SweepStalledDeliveries(ctx, now)`：把超时未回报的 `DELIVERING` 目标置 `FAILED`，错误可定位：`delivery timed out after 30m0s without a result from the target agent`；再聚合传输状态（`:619` `updateTransferAggregate`，由 `HandleDeliveryResult` 抽出复用）→ 传输进入终态 → 节点删除 409 释放。
- `:119` `StartDeliveryWatchdog(ctx, interval)`：后台周期扫描（间隔 60s）。
- `internal/hubserver/hub.go:329`：`Start` 中启动看门狗（`h.transfers.StartDeliveryWatchdog(h.ctx, time.Minute)`）。

设计说明（与任务描述的对应关系）：超时判定放在周期性 sweep 而不是 `dispatchTarget` 内同步等待——`dispatchTarget` 在 `completeUpload` 路径上持有 `m.mu`，同步等待会持锁阻塞整个管理器。可观测语义与要求一致：超时 → 目标 `FAILED` + 可定位错误 + 传输进入终态。

### 新增测试
`internal/hub/file_transfer_timeout_test.go`（新文件）
- `TestDeliveryStallTimeoutTerminalizesTargetAndReleasesNode`：真实上传链路把目标停在 `DELIVERING` → **边界**（未到时限不终结、节点删除仍 409）→ 越线扫描后目标/传输 `FAILED`、错误含 `delivery timed out`、二次扫描幂等、源/目标节点删除成功（409 释放）。
- `TestDeliveryStallSweepLeavesPendingTargetsAlone`：`PENDING` 目标不被终结。
- `TestDeliveryStallTimeoutScalesWithSize`：阈值随大小线性放宽。
- `TestWriteLoopMarksConnUnavailableOnWriteFailure`：真实 websocket 对端断开后，`writeLoop` 必须把通道标记为不可用，且此后 `send` 立即返回 `errConnUnavailable`（不再静默入队/永久阻塞）。

---

## 5. OBS-20261008-EXECUTION-ZERO-EXIT-CODE（P2）

### 根因
`models.Execution.ExitCode int json:"exit_code,omitempty"`：合法成功退出码 `0` 被 `omitempty` 省略，UI 显示 `-`。
同时注意存储层 `executions.exit_code` 为 `NOT NULL DEFAULT 0`，**不能**用「去掉 omitempty」一刀切：`PENDING/RUNNING/SKIPPED/BLOCKED` 的默认 0 会被伪装成「已知成功退出码」（E2E 观察 `OBS-20261008` 明确要求保留未执行状态的未知语义，且不迁移 schema）。

### 改动
- `internal/models/models.go:308`：`ExitCode *int json:"exit_code,omitempty"`（可空）。
- `internal/models/models.go:339` `ExecutionExitCode(status, code)`：`SUCCESS` 恒有（含合法 0）；`FAILED/TIMED_OUT/CANCELED` 需非 0（Agent runner 对这三态一律写非 0，0 只出现在「进程没启动」的 Hub 侧失败）；`PENDING/RUNNING/SKIPPED/BLOCKED` → `nil`（JSON 省略）。
- `internal/models/models.go:352` `ExitCodeValue(*int) int`：写回 `NOT NULL` 列的取值。
- `internal/store/sqlite_executions.go:26,40`（写）、`:59,73`（读，唯一 scan 点按状态判定）。
- `internal/hub/execution.go:374`（`MarkFinished` 新建）、`:413`（`MarkFinished` 更新）、`:634`（`UpsertFromAgent`）。

### 新增测试
`internal/hub/api/application_subresource_test.go` 内 `TestExecutionExitCodeSerialization`：`SUCCESS/0`、`FAILED/3`、`TIMED_OUT/-1` 必须出现；`PENDING`、`RUNNING`、`SKIPPED`、`FAILED/0`（未启动进程）必须缺省；列表端点共享同一规则。
`internal/hub/execution_test.go`：`TestUpsertFromAgentIdempotent` 适配可空字段（`models.ExecutionExitCode("FAILED", 3)` / `models.ExitCodeValue`）。

### 真实 HTTP 验证（本地 hub，直接向 DB 插入不同状态行后读 API）
```
fixall-success        → status=SUCCESS exit_code_present=True  value=0     ← 缺陷本体
fixall-failed3        → status=FAILED  exit_code_present=True  value=3
fixall-pending        → status=PENDING exit_code_present=False
fixall-running        → status=RUNNING exit_code_present=False
fixall-failed-norun   → status=FAILED  exit_code_present=False            ← 下发失败（没跑进程）
```

---

## 6. OBS-20261010-UI-LOGOUT-NOT-REVOKED（P1）→ 归 Web 批次

Go 侧无需改动：`/api/logout` 已是 POST-only 且服务端撤销会话（`internal/hub/api/api.go:122`；既有 `internal/hub/api/contract_test.go` 已覆盖「GET 405 且不注销 / POST 200 且旧 token 变 401」）。
缺陷本体是 **Web 侧退出只清本地 token、不调用 `/api/logout`**，按边界未改 `web/**`，请由 Web 批次处理（`web/src/components/sign-out-dialog.tsx`、`web/src/stores/auth-store.ts` 调用 POST `/api/logout`）。

---

## 7. 可选项

### 7a. OBS-20261017-SCRIPT-PUT-TYPE-UNVALIDATED（已修复）
- 根因：`validateParameters` 只校验名称非空/不重复，不校验 `Parameter.Type`，`PUT /api/scripts/{id}` 与 `POST` 都接受任意值（`type=not-a-type` → 200）。
- 改动：`internal/hub/validation.go:21` 新增白名单 `string|number|bool|secret`（与 `models.Parameter` 注释、前端下拉一致）；`internal/hub/definitions.go:441-460` 校验类型，**空类型归一为 `string`**（与同文件 `interpreter` 缺省 `shell` 的既有风格一致）。`ScriptManager.Create/Update` 与任务参数校验共用该函数，故 `POST/PUT /api/scripts`、任务定义一并生效。
- 选择的规则：非法值 → `400 {"error":"invalid parameter type: <v>"}`；空值 → 归一 `string`；大小写敏感（与前端下拉值一致）。
- 测试：`internal/hub/api/script_user_validation_test.go` `TestScriptParameterTypeValidated`（POST/PUT 非法拒绝且不改写定义、五种合法/空值通过、空值归一）。
- 真实 HTTP：`PUT /api/scripts/{id}` + `type=not-a-type` → `HTTP 400 {"error":"invalid parameter type: not-a-type"}`。

### 7b. OBS-20261016-G1-USER-INPUT-NOT-TRIMMED（用户名已修复；密码最小长度未做）
- 用户名：`internal/hub/api/api.go:2109` 创建时 `strings.TrimSpace`，纯空白 → `400 username and password required`；`internal/hub/auth/auth.go:116` 登录拒绝纯空白用户名（`401`），但**仍按原样精确匹配**（不改写历史账号名，避免存量账号被锁）。测试：`TestUserWhitespaceUsernameRejected`（纯空白 400 且不落库、首尾空白裁剪为 `padded-user` 且可登录、纯空白登录 401）。真实 HTTP 同上：400 / `'fixall-padded'` / 401。
- **密码最小长度：未做**。理由：① 阈值属产品策略，E2E 记录里该条状态是 `OBSERVED_LOW / 待产品裁决`；② 现网证据把 1 字符密码的创建记作 `200`（`.e2e/runs/.../qa-resume-20261014-api/p-api/runners/probe_auth.py:38,150`），自行加阈值会翻转已被记录的对外契约，且会连带要求改 `internal/hubserver/rbac_audit_test.go`（`ops123`）等他人用例；③ 现有边界已覆盖空密码（API + `auth.Login` 拒绝空凭据）与 bcrypt 72 字节上限。若产品确定阈值（建议 ≥8），可在 `handleUsers` 与 `handleUserByID` 同一处补齐。

---

## 验证（真实命令结果）

```
$ go build ./...
exit=0

$ go vet ./...
exit=0

$ gofmt -l internal/
（无输出，exit=0）

$ go test ./internal/... -count=1
ok  	github.com/SuPerCxyz/nodesteer/internal/agent	30.748s
ok  	github.com/SuPerCxyz/nodesteer/internal/agent/condition	0.010s
ok  	github.com/SuPerCxyz/nodesteer/internal/agent/connection	0.362s
ok  	github.com/SuPerCxyz/nodesteer/internal/agent/execution	5.057s
ok  	github.com/SuPerCxyz/nodesteer/internal/agent/host	0.019s
ok  	github.com/SuPerCxyz/nodesteer/internal/agent/scheduler	0.004s
ok  	github.com/SuPerCxyz/nodesteer/internal/hub	0.579s
ok  	github.com/SuPerCxyz/nodesteer/internal/hub/agentbundle	0.005s
ok  	github.com/SuPerCxyz/nodesteer/internal/hub/api	2.664s
ok  	github.com/SuPerCxyz/nodesteer/internal/hub/auth	1.079s
ok  	github.com/SuPerCxyz/nodesteer/internal/hubserver	4.453s
?   	github.com/SuPerCxyz/nodesteer/internal/metrics	[no test files]
?   	github.com/SuPerCxyz/nodesteer/internal/models	[no test files]
?   	github.com/SuPerCxyz/nodesteer/internal/protocol	[no test files]
ok  	github.com/SuPerCxyz/nodesteer/internal/store	0.103s
exit=0
```

补充运行验证（超出四条命令，均为本机真实执行）：
- systemd 259 user manager 实际启动由 `buildUnit` 输出改造的 Unit：`ENV=yes CONFIG=applied`（配置内容运行期生效）。
- 本地构建 hub（临时 DB/端口 28180，验证后已停止）：
  - assign/deploy 不存在应用 → 404 `application not found`（无 SQL 原文）；
  - `exit_code` 五态矩阵如上表；
  - 纯空白用户名 400 / 首尾空白裁剪 / 纯空白登录 401；
  - `type=not-a-type` 400。

## 改动文件清单

改动（Go 源码）：
`internal/agent/application.go`、`internal/hub/api/api.go`、`internal/hub/application.go`、`internal/hub/auth/auth.go`、`internal/hub/definitions.go`、`internal/hub/execution.go`、`internal/hub/file_transfer.go`、`internal/hub/gateway.go`、`internal/hub/validation.go`、`internal/hubserver/hub.go`、`internal/models/models.go`、`internal/store/sqlite_executions.go`

改动（单测）：`internal/agent/application_test.go`、`internal/hub/execution_test.go`
新增（单测）：`internal/agent/unit_envfile_test.go`、`internal/agent/rollback_health_test.go`、`internal/hub/api/application_subresource_test.go`、`internal/hub/api/script_user_validation_test.go`、`internal/hub/file_transfer_timeout_test.go`

未触碰：`web/**`、`.e2e/**` 状态文件、`docs/**`、OpenSpec artifact（`openspec/changes/repair-e2e-confirmed-defects/` 未改，本次改动与该 change 的 spec delta 无字段级冲突，但**未同步 spec/文档**，按边界由 Root/文档批次处理）。

## 对外可见的行为变化（逐条）

1. **systemd Unit**：`EnvironmentFile="-<path>"` → `EnvironmentFile=-<path>`。应用「配置内容」从「运行期完全不生效」变为生效。
2. **部署准备阶段新增拒绝**：`binary_path`/`config_path` 含控制字符（`\n`、`\r`、`\t`、`\x00`、DEL）时部署直接失败（`prepare_failed`，错误 `invalid deployment path: control character in path`），不产生宿主副作用。正常路径（含空格、中文）不受影响。
3. **回滚语义**：回滚成功不再因失败版本的健康定义被判失败；`ROLLBACK_FAILED` 仅用于真回滚失败。回滚复检改为「恢复后的 Unit 必须 active 且保持 active」（会真实拉起并确认，可能新增 ~0.25s 稳定性等待）。
4. **`POST /api/applications/{id}/assign`**：不存在应用 500 + `sql: no rows in result set` → **404** `{"error":"application not found"}`。
5. **`POST /api/applications/{id}/deploy`**：不存在应用 400 + SQL 原文 → **404** 同一文案；存在应用但节点不存在 400 + SQL 原文 → **400** `node <id> not found`。
6. **`exit_code` 序列化契约**：字段变为可空语义 —— `SUCCESS` 恒出现（含 `0`）；`FAILED/TIMED_OUT/CANCELED` 仅在非 0 时出现；`PENDING/RUNNING/SKIPPED/BLOCKED` 以及「FAILED 但进程没启动」时**省略该字段**。Web 侧 `web/src/lib/api.ts:382` 的 `exit_code: number` 建议改为可选（`exit_code?: number`），现有 `?? '-'` 渲染逻辑无需改动（属 Web 批次）。
7. **文件传输终态化**：目标停在 `DELIVERING` 超过 `30 分钟 + size/1MiB·s` 时被置 `FAILED`（错误 `delivery timed out after ... without a result from the target agent`），传输随之进入终态（`FAILED`）；此后节点删除不再 409。迟到的目标成功回报仍会把目标改回 `SUCCESS` 并重新聚合（真实结果优先于定时器，未加粘滞终态）。
8. **Hub→Agent 通道错误语义**：写失败后连接被立即关闭并记 ERROR 日志（Agent 会重连）；该连接上后续 `Send` 立即返回 `conn unavailable`（原先可能继续成功入队、最终永久阻塞）。会话被回收后节点会短暂 offline，重连由 `ResumeNode` 重投未完成传输。
9. **脚本/任务参数 `type`**：非法值由 200 改为 **400** `invalid parameter type: <v>`；空类型由「原样空串」归一为 `"string"`。
10. **用户**：创建时用户名首尾空白被裁剪；纯空白用户名由可创建改为 **400**；纯空白用户名登录由可登录改为 **401**（历史账号名不做改写，`x ` 之类仍可精确登录）。
11. **新增后台任务**：Hub 启动时多一个 60s 周期的交付看门狗 goroutine（无外部接口变化）。

## 未做 / 不建议做的项

- **OBS-20261010-UI-LOGOUT-NOT-REVOKED**：归 Web 批次（见 §6），Go 侧已具备撤销能力。
- **密码最小长度**：未做，理由见 §7b（属产品裁决 + 会翻转已被 E2E 记录的 200 契约）。
- **文档/spec 同步**：按边界未改 `docs/**` 与 `openspec/**`；本报告第 4 条已给出 `exit_code` 契约变化，供文档批次与 Web 批次同步。
- **运行时验证缺口（如实说明）**：
  - 传输「写失败 → 重连重投」与「30 分钟超时」只在单测层面验证（真实 websocket 断连 + 注入 `now` 的 sweep）；**未**在实网 Hub/Agent 上跑完整链路，需 E2E 批次在部署新版本后验证（超时阈值较长，建议验证路径走「写失败 → Agent 重连 → `ResumeNode` 重投」，或临时用单测注入的小阈值）。
  - Agent 侧 Unit 部署与回滚的**真机 systemd 集成**（真实 `systemctl` 拉起 NodeSteer 托管服务）未执行；本机只验证了生成文本 + user manager 环境变量加载。
  - `exit_code` 未回填历史行（无 schema 迁移，按观察结论「不改历史记录」执行）。
- **未改** `.e2e/**` 状态文件、`web/**`、Git 历史（无 commit/push），未装任何依赖。
