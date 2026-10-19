# P14 同类缺陷收尾报告（静默成功 / 未知子路径 / agent 405 / E 类遗留 + SCALE-02，2026-10-18）

- 任务来源：P6 报告第七节同类模式审计结论与第九节范围外观察；P1 findings 的 `observations[0]`（未知子路径宽容，需运行验证）；追加项 `OBS-20261018-SCALE-02`（心跳超时清理 ERROR 日志噪声）。用户已授权全部修复。
- 边界遵守：只改 Go 源码与单测；未碰 `internal/agent/redact*.go`；未改 `web/**`；未改 `.e2e/**` 状态文件（本报告为新增交付物）；无 Git 写操作；未装依赖；未访问远程环境；未做部署。
- 工作树既有未提交改动（`internal/agent/application.go`、`internal/agent/store.go`、`docs/**`、`web/**` 等）属会话前状态，本次未触碰、未覆盖。
- 验证环境：`go1.26.8 linux/amd64`；因 `/tmp` tmpfs 空间紧张，`go` 命令以 `TMPDIR=/var/tmp/nodesteer-go-tmp` 执行（不影响结论）。

---

## 一、静默成功审计（承接 F5 同类）

### 1.1 审计范围与方法

- 范围一（store 层）：`internal/store/*.go` 全部 `SQLiteStore` 写方法，逐条核对 `Update*` / `Delete*` / `Set*` 是否对「目标行 0 行命中」返回 `sql.ErrNoRows`（F5 引入的 `execExpectRow` 模式）。
- 范围二（handler 层）：`internal/hub/api/api.go` 全部 `PUT`/`POST`/`DELETE` handler 与 `internal/hub/*.go` 各 Manager 写路径，核对「不存在资源是否可能返回 200」及错误映射是否归一化 404。
- 方法：`rg` 全量枚举 + 调用点追踪（每个 store 写方法的非测试调用者逐一核对前置存在性检查与错误映射）+ 单测锁定。

### 1.2 命中与修复（store 层）

修复前：以下方法为纯 UPDATE/DELETE，**不校验 RowsAffected**。正常路径靠 handler/Manager 的 `Get*` 前置检查兜底，但在「前置检查通过后资源被并发删除」的窗口内会静默成功（返回 200、或为已不存在的定义写入孤儿历史版本）。统一接入 `execExpectRow`（`internal/store/sqlite_users_nodes.go:72`，F5 既有工具）：

| 方法 | 文件:行 | 修复前 | 修复后 |
|---|---|---|---|
| `UpdateNodeStatus` | `internal/store/sqlite_users_nodes.go:198` | 0 行 → nil | 0 行 → `sql.ErrNoRows` |
| `UpdateNodeSyncState` | `internal/store/sqlite_users_nodes.go:202` | 0 行 → nil | 同上 |
| `UpdateNodeHeartbeat` | `internal/store/sqlite_users_nodes.go:206` | 0 行 → nil | 同上（Gateway 据此回收孤儿会话） |
| `SetNodeLabels` | `internal/store/sqlite_users_nodes.go:217` | 0 行 → nil | 同上 |
| `DeleteNode`（最后一条 `DELETE FROM nodes`） | `internal/store/sqlite_users_nodes.go:224` | 0 行 → nil | 同上（关联清理语句保持原样） |
| `DeleteGroup`（`DELETE FROM node_groups`） | `internal/store/sqlite_users_nodes.go:377` | 0 行 → nil | 同上 |
| `UpdateScript` | `internal/store/sqlite_definitions.go:54` | 0 行 → nil，且继续插入 `script_revisions` 孤儿行 | `sql.ErrNoRows`，事务回滚，无孤儿行 |
| `DeleteScript` | `internal/store/sqlite_definitions.go:136` | 0 行 → nil | `sql.ErrNoRows` |
| `UpdateTask` | `internal/store/sqlite_definitions.go:209` | 0 行 → nil + 孤儿 `task_revisions` | 同 UpdateScript |
| `DeleteTask` | `internal/store/sqlite_definitions.go:293` | 0 行 → nil | `sql.ErrNoRows` |
| `UpdateSchedule` | `internal/store/sqlite_definitions.go:313` | 0 行 → nil | `sql.ErrNoRows` |
| `DeleteSchedule` | `internal/store/sqlite_definitions.go:372` | 0 行 → nil | `sql.ErrNoRows` |
| `DeleteArtifact` | `internal/store/sqlite_artifacts_apps.go:62` | 0 行 → nil | `sql.ErrNoRows` |
| `UpdateApplication` | `internal/store/sqlite_artifacts_apps.go:103` | 0 行 → nil + 孤儿 `application_revisions` | 同 UpdateScript |
| `DeleteApplication`（`DELETE FROM applications`） | `internal/store/sqlite_artifacts_apps.go:209` | 0 行 → nil | `sql.ErrNoRows` |

### 1.3 handler 层配套映射（改动）

- `internal/hub/api/api.go:699`（`handleNodes` POST 标签路径）与 `internal/hub/api/api.go:1045`（`handleNodeByID` POST/PUT 标签路径）：`SetLabels` 错误由 `writeStoreError(500)` 改为 `writeMutationError(500, "node not found", err)`——并发删除后的零行命中 → 404 `node not found`，不再 500/原文；真实存储错误仍 500。
- 其余 handler 的 ErrNoRows → 404 映射为 F5/P6 既有路径（`writeMutationError`/`writeUpdateError`），本次新增的 store 校验全部落在既有映射上，无需再改：
  - `DELETE /api/nodes|groups|scripts|tasks|schedules|artifacts|applications/{id}` → 409 参数位 + `writeMutationError` → `sql.ErrNoRows` 归一 404；
  - `PUT /api/scripts|tasks|schedules|applications/{id}` → `writeUpdateError` → 404；
  - `POST /api/nodes` 状态路径 → `writeMutationError` → 404。

### 1.4 「已核对安全」清单（不改代码及理由）

| 对象 | 结论与理由 |
|---|---|
| `UpdateUserRole` / `UpdateUserPassword` | 已用 `execExpectRow`（F5 修复，本次回归测试覆盖） |
| `UpdateUserAvatar` | 仅 SSO 同步路径调用（`internal/hub/auth/auth.go:210`），调用前用户由 `GetUserByUsername`/SSO 创建保证存在；无 API 可见的静默成功（P6 已裁决保留） |
| `UpdateFileTransfer` / `UpdateFileTransferTarget` | 文件传输内部状态机：行对象均先 `GetFileTransfer` 取出后更新；传输无 API 删除路径；写失败会置回/终态化，无「不存在资源返回 200」面 |
| `UpdateExecution` | 内部执行生命周期写（创建后更新）；执行记录无 API 删除路径 |
| `DeleteSetting` / `DeleteTombstone` / `DeleteApplicationNodeState` / `PruneChangeLog` / `RemoveGroupMember` | 幂等删除语义（重复删除成功是契约，例如解绑、清凭证标记） |
| `AddGroupMember` / `SetApplicationAssignment`（INSERT OR IGNORE）/ `SetApplicationNodeState` / `SetSetting` / `SetRemoteState` / `UpsertNode` / `RecordTombstone` | 插入/upsert 语义，无行数校验问题 |
| `AppendChangeLog` / `AppendLogChunk` / `AddAudit` / `Create*`（Script/Task/Schedule/Artifact/Application/Execution/FileTransfer/User/Group） | 插入语义；重复由唯一约束兜底（如用户名 UNIQUE → 400 归一化文案） |
| `internal/agent/store.go`（LocalStore） | Agent 本地状态库，**无对外 HTTP handler**；`Delete*`/`Update*` 由同步应用/清理路径调用，语义为幂等同步簿记，不在 Hub API 面 |
| handler 前置检查面 | `POST /api/nodes/{id}/revoke-credential`（GetNode）、`POST /api/transfers/{id}/retry|cancel`（GetFileTransfer）、`POST /api/executions/{id}/cancel`（GetExecution）、`POST /api/tasks/{id}/run`（GetTask）、`POST /api/applications/{id}/assign|deploy`（requireApplication）、`POST /api/nodes/upgrade`（逐节点 GetNode）、`POST /api/transfers`（源/目标 GetNode）、`POST /api/schedules`（GetTask）、`PUT /api/settings`（upsert）、`POST /api/users`（UNIQUE 归一）——全部有存在性前置或插入语义，无静默成功 |

### 1.5 新增测试

| 测试 | 文件 | 断言 |
|---|---|---|
| `TestWriteMethodsMissingRowReturnErrNoRows` | `internal/store/sqlite_rows_affected_test.go` | 18 个单行 Update/Delete 方法对不存在 id 全部返回 `sql.ErrNoRows`（含 F5 两个方法回归） |
| `TestWriteMethodsMissingRowWriteNoOrphanRows` | 同上 | 三个定义 Update 零行命中后 `script_revisions`/`task_revisions`/`application_revisions` 计数 = 0（不写孤儿行） |
| `TestWriteMethodsExistingRowStillSucceed` | 同上 | 正向对照：存在资源时同一批方法（含 DeleteNode/DeleteGroup 多语句）仍全部成功 |

### 1.6 行为变化（对外可见）

- `PUT /api/scripts|tasks|schedules|applications/{id}` 与 `DELETE /api/{nodes|groups|scripts|tasks|schedules|artifacts|applications}/{id}`：在「检查后并发删除」窗口内由 **200 静默成功** 变为 **404 归一化文案**（正常路径行为不变）。
- `POST /api/nodes` / `PUT /api/nodes/{id}` 标签路径：并发删除窗口由 200（或 500 原文）变为 **404 `node not found`**。
- 不存在的定义不再产生孤儿历史版本行（内部数据一致性）。
- Agent 心跳：对已被删除节点的心跳更新返回 `sql.ErrNoRows`，Gateway 经 `dropOrphanSession` 回收孤儿会话（此前静默成功、靠后续查询兜底）。

### 1.7 反向验证（撤掉修复 → 必须失败，已还原）

临时还原 `DeleteTask` 为无校验版本：

```
--- FAIL: TestWriteMethodsMissingRowReturnErrNoRows (0.00s)
    --- FAIL: TestWriteMethodsMissingRowReturnErrNoRows/DeleteTask (0.00s)
        sqlite_rows_affected_test.go:61: DeleteTask missing row err = <nil>, want sql.ErrNoRows
```

---

## 二、未知子路径宽容（P1 观察，运行验证后修复）

### 2.1 修复前运行实测矩阵（真实探针：全量 Manager + 文件库 + 完整 `Routes()`）

修复前逐族 × 方法请求 `/api/<res>/{id}/bogus`（探针输出，2026-10-18）：

| 资源族 | GET | POST | PUT | DELETE | 危险行为确认 |
|---|---|---|---|---|---|
| nodes | 200 节点本体 | 200 **改写 status** | 200 | 执行删除（测试中被 409 引用拦截） | 是 |
| tasks | 200 任务本体 | 405 | 200 **改名落库** | 执行删除（409 引用拦截） | 是 |
| scripts | 200 脚本本体 | 405 | 200 **改名落库** | 200 **脚本被删除** | 是 |
| applications | 200 应用本体 | 405 | 200 **改名落库** | 200 **应用被删除** | 是 |
| artifacts | 200 元数据 | 405 | 405 | 200 **artifact 被删除** | 是 |
| executions | 200 执行记录本体 | 405 | 405 | 405 | 读取本体（低危） |
| schedules | 404 | 405 | 404 | 404 | 否（id 含子路径，查无行） |
| groups | 404 | 405 | 404 | 404 | 否 |
| users | 405 | 405 | 404 | 405 | 否 |
| transfers | 405 | 404 | 405 | 405 | 否 |

结论：**6 个资源族存在「未知子路径被当作基础资源」的误读/误写/误删**（P1 静态观察成立）；4 个族已安全（无 200、无误执行）。

### 2.2 修复

统一路径解析工具 `splitResourcePath`（`internal/hub/api/api.go:315`）：解析 `/api/<resource>/{id}[/<subpath...>]`，尾部斜杠归一（不产生空段）；handler 对**未被显式支持的子路径一律 404**，已知子路径保留既有方法契约（405 + Allow）。

| handler | 文件:行 | 修复内容 |
|---|---|---|
| `handleNodeByID` | `internal/hub/api/api.go:968` | 未知子路径 → 404 `node not found`；`revoke-credential` 必须恰好 1 段 |
| `handleScriptByID` | `internal/hub/api/api.go:1363` | 未知子路径 → 404；`revisions` 至多 2 段且仅 GET（此前 `PUT /revisions` 会静默改写脚本本体） |
| `handleTaskByID` | `internal/hub/api/api.go:1479` | 未知子路径 → 404；`run` 必须恰好 1 段（此前 `/run/extra` 仍执行 RunNow） |
| `handleScheduleByID` | `internal/hub/api/api.go:1649` | 显式未知子路径 → 404 `schedule not found`（原经 id 查无行，语义相同，POST 由 405 归一到 404） |
| `handleArtifactByID` | `internal/hub/api/api.go:1745` | 未知子路径 → 404（download 由独立路由处理） |
| `handleApplicationByID` | `internal/hub/api/api.go:1848` | 未知子路径 → 404；已知子路径必须恰好 1 段 |
| `handleExecutionByID` / `handleExecutionCancel` | `internal/hub/api/api.go:2079` / `:2125` | 未知子路径 → 404；`logs`/`cancel` 必须恰好 1 段 |
| `handleGroupByID` | `internal/hub/api/api.go:1266` | 显式未知子路径 → 404（此前靠 id 含子路径查无行） |
| `handleUserByID` | `internal/hub/api/api.go:2375` | 显式未知子路径 → 404（此前 GET/POST/DELETE 为 405） |
| `handleTransferByID` | `internal/hub/api/api.go:1159`（早检 `:1171`） | 未知动作先于方法守卫判定 → 404（任意方法）；已知动作 `retry`/`cancel` 方法契约不变（PATCH 仍 405 `Allow: GET, POST`，既有契约测试锁定） |

### 2.3 修复后契约（真实进程复测，见 2.4）

- `/api/<res>/{id}/<未知子路径>`：**GET/POST/PUT/DELETE 一律 404 + 归一化文案**（10 个资源族全部一致）；
- 已知子路径 + 额外段（`/run/extra`、`/revoke-credential/extra`、`/revisions/1/extra`、`/state/extra`、`/logs/extra`、`/retry/extra`）→ 404；
- 已知子路径 + 不支持方法 → 405 + `Allow`（契约不变）；
- 末尾斜杠（`/api/tasks/{id}/`、`/api/groups/{id}/` 等）→ 归一为基础资源。

### 2.4 真实进程复测（临时 hub，测完销毁）

- 方式：`go build ./cmd/hub` → 临时配置（`127.0.0.1:18099`，临时 data/artifact 目录）→ 启动 → admin 登录 → 运行探针脚本 → 停止进程 → 删除临时目录。
- 结果：**全部通过**（探针输出 50+ 项 `ok`）：10 族 × 4 方法未知子路径全部 404；5 个资源本体在探针后未被改写/删除（`task/script/application/group` 名称不变、user 角色不变）；`/run/extra` 未触发 RunNow；末尾斜杠 200；`GET /run|cancel` 405 `Allow: POST`；`POST /artifacts/{id}/download` 405 `Allow: GET, HEAD`；`PATCH /api/tasks` 405 `Allow: GET, POST`；hub.log 中 `level=ERROR` 计数 = 0。
- 临时环境：`/tmp/opencode/p14-hub` 已整体删除，进程已停止。

### 2.5 新增测试

| 测试 | 文件 | 断言 |
|---|---|---|
| `TestUnknownSubpathIs404ForAllResources` | `internal/hub/api/subpath_contract_test.go` | 10 族 × 4 方法 × `/bogus` → 404 + 精确归一化文案 + 无存储原文；随后核对 10 个资源本体全部未被改写/删除 |
| `TestKnownSubpathExtraSegmentsAre404` | 同上 | 7 条「已知子路径 + 额外段」→ 404；`/run/extra` 后执行记录数不变（RunNow 未触发） |
| `TestValidSubpathsStillWork` | 同上 | `revisions`/`state`/`logs` GET 200；`GET /run` 405 Allow POST；`PUT /revisions` 405 Allow GET 且脚本未被改写 |
| `TestTrailingSlashIsBaseResource` | 同上 | 末尾斜杠归一：task/group GET 200、user PUT 200 且角色落库 |

### 2.6 行为变化（对外可见，逐条）

1. `GET/POST/PUT/DELETE /api/{nodes,tasks,scripts,applications,artifacts,executions}/{id}/<未知>`：200/误执行 → 404 归一化文案；
2. `POST /api/schedules|groups|users/{id}/<未知>`：405 → 404（`users` 的 GET/POST/DELETE 同）；`/api/transfers/{id}/<未知>` 的 GET/PUT/DELETE：405 → 404（POST 原为 404 不变）；
3. `/api/tasks/{id}/run/extra`、`/api/nodes/{id}/revoke-credential/extra`、`/api/scripts/{id}/revisions/1/extra`、`/api/applications/{id}/state/extra`、`/api/executions/{id}/logs/extra`：200/误执行 → 404；
4. `PUT /api/scripts/{id}/revisions`：旧实现 `revisions` 分支只在 GET case 内判定，PUT 落入基础更新分支返回 200（静态路径确认）→ 405 `Allow: GET`；
5. 末尾斜杠 `/api/groups/{id}/`、`/api/schedules/{id}/`、`/api/users/{id}/`：404 → 归一为基础资源（`/api/tasks/{id}/` 等原本即如此）；
6. 已知子路径的 405 + `Allow`、各正常读写路径不变（全部既有测试通过）。

### 2.7 反向验证（撤掉修复 → 必须失败，已还原）

临时还原 `handleTaskByID` 旧实现：

```
--- FAIL: TestUnknownSubpathIs404ForAllResources
    GET /api/tasks/.../bogus = 200 {"id":"...","name":"subpath-task",...} , want 404
--- FAIL: TestKnownSubpathExtraSegmentsAre404
    POST /api/tasks/.../run/extra = 400 {"error":"no target nodes"} , want 404 "task not found"
```

与修复前实测现象一致；还原修复后 ✅。

---

## 三、Agent 侧 405 缺 Allow（P6 范围外观察）

### 3.1 修复

`internal/hub/file_transfer.go`（`HandleAgentHTTP`）：

| 位置 | 修复前 | 修复后 |
|---|---|---|
| `internal/hub/file_transfer.go:367` | `GET/PUT/DELETE /agent/transfers/{id}/upload` → 405 无 Allow | 405 + `Allow: POST` |
| `internal/hub/file_transfer.go:374` | `POST/PUT/DELETE /agent/transfers/{id}/download` → 405 无 Allow | 405 + `Allow: GET` |

### 3.2 顺带核对：agent 可达端点的「方法未拒绝」路径

| 端点 | 核对结果 |
|---|---|
| `/agent/transfers/{id}/upload` | 仅 POST（405 + Allow）；GET 等不触发上传 |
| `/agent/transfers/{id}/download` | 仅 GET（405 + Allow）；POST 等不返回文件 |
| `/agent/transfers/{id}/<未知动作>` / 多余段 | 404 `transfer endpoint not found`（无方法触发面） |
| 认证 | 方法守卫之前先要求 Agent 凭据，缺失 → 401（语义不变） |
| `/api/artifacts/{id}/download`（Agent 部署下载，Hub 侧提供） | **命中**：此前无方法守卫，任意方法都会经 `http.ServeContent` 返回文件内容 → 已修：仅 GET/HEAD，其余 405 + `Allow: GET, HEAD`（`internal/hub/api/api.go:1785`） |

### 3.3 测试

| 测试 | 文件 | 断言 |
|---|---|---|
| `TestAgentTransferEndpoint405CarriesAllow` | `internal/hub/agent_transfer_method_test.go` | upload 的 GET/PUT/DELETE → 405 `Allow: POST`；download 的 POST/PUT/DELETE → 405 `Allow: GET`；未知动作/多余段 → 404；缺凭据 → 401；GET download 不存在传输 → 404（方法守卫放行） |
| `TestArtifactDownloadMethodGuard` | `internal/hub/api/download_method_test.go` | 真实文件：GET 200 内容一致、HEAD 200；POST/PUT/DELETE/PATCH → 405 `Allow: GET, HEAD` 且不返回文件内容；不存在 artifact GET → 404 |

### 3.4 行为变化（对外可见）

- `/agent/transfers/*` 与 `/api/artifacts/{id}/download` 的 405 新增 `Allow` 头（状态码、响应体不变）；
- `/api/artifacts/{id}/download` 非 GET/HEAD 由「200 返回文件」变为「405」——Agent 实际只用 GET（`internal/agent/artifact.go:83`），前端用法（`web/src/features/catalog/index.tsx:87`）为 GET，无消费方回归。

---

## 四、E 类遗留（只记录，不改代码）

### 4.1 复核结果（脚本化，只读）

复核 P6 第七节 E′ 结论（脚本：提取 `SQLiteStore` 方法体匹配 `s.db.(Query|QueryRow|Exec|Prepare)`，提取全部 `runMutationTx`/`WithTx` 闭包体匹配 `.store.X(` 调用，求交集）：

- 使用 `s.db` 直连的方法：**33 个**（读为主 + `UpdateExecution`/`UpdateFileTransfer`/`UpdateFileTransferTarget` 等事务外写）；
- 事务闭包内调用的 store 方法：24 个；
- **交集 = 空（安全）**，与 P6 的「intersection=0」结论一致（计数 33 vs P6 的 34 为脚本口径差异，不影响结论）。

### 4.2 现有守卫测试覆盖确认

- `internal/store/sqlite_tx_read_test.go` 的 `TestReadHelpersInsideTxDoNotBorrowSecondConnection`（P6 新增）：把连接池压到 **1 条**，验证事务内（txctx）调用已修复的 7 个读方法不再借第二条连接——**覆盖 E 已修复族**，本次复核该测试仍通过。
- 上述 33 个 `s.db` 直连方法**没有逐一守卫**，当前安全依赖「调用点全在事务外」这一审计事实；本次复核未发现交集，结论维持「安全」。
- 本次修复的 `execExpectRow` 路径均在事务内使用 `s.execer(ctx)`（txctx 或 db），不新增借连接面。

### 4.3 统一改造建议（供后续立项，本次不做）

1. **机制统一**：`internal/store` 全部方法统一走 `s.queryer(ctx)` / `s.execer(ctx)`；仅 `sqlite.go` 的 `BeginTx`/`OpenSQLite` 允许直接使用 `s.db`；把 33 个直连方法一次性迁移（行为不变，可分批）。
2. **静态防回归**：CI 增加规则（grep/自定义 linter）禁止 `internal/store` 内出现 `s.db.(Query|QueryRow|Exec|Prepare)`；`s.db.BeginTx` 白名单。
3. **运行时守卫**：把池=1 守卫测试扩展为「事务内调用全部 store 方法」的表驱动用例（迁移完成后），从机制上关闭「事务内二次借连接」整类风险。
4. **风险等级**：低（当前交集=0）；建议与下一次 store 层重构合并立项，不单独排期。

---

## 五、SCALE-02 心跳超时清理 ERROR 日志噪声（追加项）

### 5.1 定位

`OBS-20261018-SCALE-02` 日志点：`internal/hub/gateway.go:754`（旧行号）`wsConn.writeLoop` 的 `logger.Error("agent websocket write failed; closing connection", ...)`。心跳超时清理（`SessionManager.StartHeartbeatChecker` → `c.Close()`，`internal/hub/session.go:88-118`）先关闭底层连接，在途写以 `failed to write msg: use of closed network connection` 进入该分支 → ERROR，造成「无 ERROR 日志」巡检假阳性。状态机结果（markUnavailable → 读循环失败 → 会话回收）本身正确。

### 5.2 判定函数（新增，可复用）

`internal/hub/connection_errors.go:19`：

```go
func IsConnectionGone(err error) bool {
    if err == nil { return false }
    if errors.Is(err, net.ErrClosed) || errors.Is(err, io.EOF) || errors.Is(err, context.Canceled) { return true }
    msg := strings.ToLower(err.Error())
    return strings.Contains(msg, "use of closed network connection") ||
        strings.Contains(msg, "connection reset by peer") ||
        strings.Contains(msg, "broken pipe")
}
```

- 判定风格与 F8（`context.Canceled` → Debug）一致，并集中两处复用；
- 明确排除 `io.ErrUnexpectedEOF`（真实截断，保持 ERROR）。

### 5.3 改动（两处复用）

| 文件:行 | 改动 |
|---|---|
| `internal/hub/gateway.go:744`（新增 `logWriteFailure`） | 连接已关闭/对端消失（`IsConnectionGone`）→ **WARN** `agent websocket write aborted; connection already closed`；真实写失败 → 保持 **ERROR** `agent websocket write failed; closing connection` |
| `internal/hub/gateway.go:771`（`writeLoop` 调用点） | 由内联 `logger.Error` 改为 `w.logWriteFailure(env.Type, err)`；连接关闭/回收逻辑不变 |
| `internal/hub/api/api.go:338`（`writeInternalError`） | 判定由 `errors.Is(err, context.Canceled)` 扩展为 `hub.IsConnectionGone(err)` → **Debug**（日志文案 `api request canceled or connection gone`）；HTTP 响应（500/503）不变 |

### 5.4 测试

| 测试 | 文件 | 断言 |
|---|---|---|
| `TestIsConnectionGoneClassification` | `internal/hub/connection_gone_test.go` | 12 例判定表：`net.ErrClosed`（含 wrapped/文本形态）、`io.EOF`、`context.Canceled`、reset/broken pipe → true；`io.ErrUnexpectedEOF`、真实写失败、存储错误 → false |
| `TestWriteLoopClosedConnectionLogsWarnNotError` | 同上 | 真实 WebSocket 连接对 + 先 `CloseNow` 再送在途消息：writeLoop 必须 WARN `write aborted`、**零 ERROR**，并正常 markUnavailable 结束 |
| `TestLogWriteFailureGenuineErrorStaysError` | 同上 | 真实错误 → ERROR `agent websocket write failed`；`net.ErrClosed` → WARN、非 ERROR |
| `TestWriteInternalErrorConnectionGoneNotError` | `internal/hub/api/download_method_test.go` | API 侧：`net.ErrClosed`/关闭文本 → 500 响应但零 ERROR；真实错误 → 仍 ERROR 且含路径 |

### 5.5 行为变化

- 仅日志级别：Gateway 写循环对连接关闭类错误 ERROR → **WARN**；API 归一化对连接关闭类错误 ERROR → **DEBUG**（F8 的 `context.Canceled` 行为保持）。
- HTTP 响应、连接回收、重投语义完全不变。

### 5.6 反向验证（撤掉修复 → 必须失败，已还原）

临时让 `logWriteFailure` 对所有错误记 ERROR：

```
--- FAIL: TestWriteLoopClosedConnectionLogsWarnNotError (3.01s)
    level=ERROR msg="agent websocket write failed; closing connection" ... error="failed to write msg: use of closed network connection"
--- FAIL: TestLogWriteFailureGenuineErrorStaysError
    closed-connection write must not be ERROR: ... error="use of closed network connection"
```

与 SCALE-02 现象一致；还原修复后 ✅。

---

## 六、真实命令结果（最终冻结状态）

| 命令 | 结果 |
|---|---|
| `gofmt -l internal/` | ✅ 输出为空 |
| `go build ./...` | ✅ exit=0 |
| `go vet ./...` | ✅ exit=0 |
| `go test ./internal/... -count=1` | ✅ 全绿（最终一轮 EXIT=0）：agent / agent/condition / agent/connection / agent/execution / agent/host / agent/scheduler / hub / hub/agentbundle / hub/api / hub/auth / hubserver / store 全部 `ok` |
| `go test ./internal/hub/ ./internal/hub/api/ ./internal/store/ -race -count=1 -run '<本批新增用例>'` | ✅ 全部 PASS，`-race` 无数据竞争报告 |

说明（如实记录）：

- 全量测试共跑 3 轮，其中 **1 轮 `internal/agent` 出现一次 FAIL**（失败明细因输出截断未捕获）；随后 `internal/agent` 单独复跑 3 次与全量复跑 1 次均 `ok`。`internal/agent` 不 import `internal/store` / `internal/hub`（`rg` 核验），本次改动不可能影响该包，判定为**会话前既有间歇性 flake**，与本批无关；如需定位建议后续专项复跑。
- `go test` 期间 `/tmp` tmpfs 空间不足，已用 `TMPDIR=/var/tmp/nodesteer-go-tmp` 规避；该目录被系统清理时需先 `mkdir -p`。
- 真实进程探针：临时 hub（`127.0.0.1:18099`，临时 DB）50+ 项断言全部通过，hub.log 零 ERROR，进程已停止、临时目录已删除。

---

## 七、未做 / 建议后续项

1. **未部署、未跑隔离实例复测**：按边界只做代码与单测 + 本地真实进程探针；QA 可在下一次部署后对隔离实例复跑未知子路径矩阵（探针脚本逻辑见本报告 2.1/2.4）。
2. **未改 `.e2e/**` 状态文件**：`findings.json` 的观察项状态未标记；如需关闭请由 QA 批次统一处理。
3. **未改文档**：`docs/PRODUCT.md` 第 1388 行「其余端点暂不带 Allow（现状保留）」为 P6 前状态（P6 已补齐 44/44），且本批新增「未知子路径 → 404」「下载端点仅 GET/HEAD」契约，建议由文档批次同步（本批边界：只改 Go 源码与单测）。
4. **E 类统一改造**（见第四节 4.3）：建议后续立项，一次性迁移 33 个 `s.db` 直连方法 + 静态防回归规则。
5. **`internal/agent` 间歇性 FAIL**：本批 5 轮全量中出现 1 次，建议后续专项定位（与本批改动无因果关系）。
6. **写端点全量并发复测**：本批 store 层新增零行校验后，建议 QA 在压力矩阵中保留「删除后再 PUT/DELETE」的竞态用例（本批已用单测覆盖语义，未做真实并发竞态复现）。

---

## 附：本批改动文件清单

**源码**

- `internal/store/sqlite_users_nodes.go`、`internal/store/sqlite_definitions.go`、`internal/store/sqlite_artifacts_apps.go`（15 个写方法接入 `execExpectRow`）
- `internal/hub/api/api.go`（`splitResourcePath` + 10 个 handler 子路径契约 + 2 处标签错误映射 + `handleArtifactDownload` 方法守卫 + `writeInternalError` 复用判定）
- `internal/hub/file_transfer.go`（agent 405 Allow）
- `internal/hub/gateway.go`（`logWriteFailure` + writeLoop 调用）
- `internal/hub/connection_errors.go`（新增 `IsConnectionGone`）

**单测**

- `internal/store/sqlite_rows_affected_test.go`（新增）
- `internal/hub/api/subpath_contract_test.go`（新增）
- `internal/hub/api/download_method_test.go`（新增）
- `internal/hub/connection_gone_test.go`（新增）
- `internal/hub/agent_transfer_method_test.go`（新增）
