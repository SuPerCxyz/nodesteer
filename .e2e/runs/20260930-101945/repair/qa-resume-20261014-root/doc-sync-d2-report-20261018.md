# 文档同步报告 D2：Go 尾部修复批对外契约（T2，2026-10-18）

- 任务：把 Go 尾部修复批（T2）与 Web 消费方同步后的对外契约变更同步进 `docs/`
- 依据：
  - 实现报告：`go-tail-fix-report-20261017.md`（同目录）
  - Web 侧已同步（仅作核对，未改）：`web/src/lib/api.ts:416-424`（`SessionInfo` 为 `token/user_id/username/role/expires` + 可选 `avatar`）、`web/src/stores/auth-store.ts`
  - 源码逐处回核：`internal/hub/auth/auth.go:20-32`、`internal/store/sqlite.go:74/109-142`、`internal/hub/api/api.go:205-217/310-323/1101-1132/1936-1940/2049-2053/2292-2293`、`internal/hub/execution.go:219-227`、`internal/hub/file_transfer.go:216-221`、`internal/agent/connection/manager.go:44-68/112-114/135`、`internal/agent/store.go:50-52/811-832`、`internal/agent/application.go:303-312`、`internal/store/sqlite_artifacts_apps.go:254-268`
- 边界：只改 `docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、`docs/TEST_PLAN.md` + 本报告；未改源码、配置、`web/**`、`.e2e/**` 既有状态文件；无 Git 操作
- 说明：任务清单中的 `internal/hub/api/validation.go` 不存在（定义校验实际在 `internal/hub/validation.go`）；`internal/agent/manager.go` 不存在（下载超时实现在 `internal/agent/connection/manager.go`）
- 基线提醒：`docs/` 在本次编辑前已含其他批次未提交改动（如 §20 定义校验、Remote State TTL、§42 `completed_kind` 等），本报告只描述 D2 自己改动的部分

---

## 一、逐条改动（11 条契约）

### 1. `/api/me` 与 login 响应键名 snake_case

- 文件/位置：`docs/PRODUCT.md` §46 新增「会话与响应键名」小节（改后 `:1384-1387`）；`docs/TEST_PLAN.md` §8.2（改后 `:502`）
- 改前要点：docs 全文无会话响应字段契约（无键名、无 login 响应结构描述）；消费方只能从代码猜测。
- 改后要点：
  - `/api/login`（本地密码登录）返回 `{"token": "...", "user": <会话对象>}`；`/api/me` 直接返回会话对象；
  - 会话对象键名固定 snake_case：`token`、`user_id`、`username`、`role`、`expires`，OIDC 登录可能附带 `avatar`（空值省略）；
  - 原 PascalCase 键（`Token`/`UserID`/`Username`/`Role`/`Expires`）**已废弃**，消费方必须按 snake_case 解析；
  - TEST_PLAN 增补对应断言（含「不得出现 PascalCase 键」）。
- 依据：`internal/hub/auth/auth.go:20-32`（json tag）；`api.go:485-487`（login 返回 token+user）、`:504-516`（me 直接序列化 Session）；Web `SessionInfo` 已同批切换。

### 2. 存储层错误语义（503 / 500 + error 日志）

- 文件/位置：`docs/PRODUCT.md` §46 状态码契约（改后 `:1393`）；`docs/ARCHITECTURE.md` §54（改后 `:1349-1351`）、§61 Observability（改后 `:1520`）；`docs/TEST_PLAN.md` §8.2（改后 `:501`）
- 改前要点：docs 无存储错误响应语义；既有契约仅零散写「不回传底层存储错误原文」（PUT 404 一行）。
- 改后要点：
  - SQLITE_BUSY/LOCKED 家族 → **503** `storage busy, please retry`（可重试语义）；其余内部错误 → **500** `internal error`（并如实标注 OIDC 启动失败等专用路径保留自身文案）；
  - 驱动原文只进 Hub 日志，不回响应体；Hub 对内部/存储错误写 error 级结构化日志（至少 method/path/error，不含 query）；
  - ARCHITECTURE §54 同时记录连接层 pragma，§61 记录日志义务。
- 依据：`api.go:310-323`（`writeInternalError`：日志 + BUSY→503 / 其余→500）、`:546`（OIDC 专用 500 文案，未归一化，已作为例外写入文档）；`sqlite.go:109-142`（`IsBusyError`）；`internal/hub/api/storage_error_test.go`（断言响应体不含 locked/sqlite/database，日志含 `level=ERROR`）。

### 3. 方法/不存在语义统一（405 + Allow；DELETE 不存在 → 404）

- 文件/位置：`docs/PRODUCT.md` §46 方法语义（改后 `:1378-1382`）、状态码契约（改后 `:1391-1392`）；`docs/TEST_PLAN.md` §8.2（改后 `:499-500`）
- 改前要点：仅列 `/api/audit`、`/api/me`、`/metrics`、`/api/logout` 四端点的 405 语义与 PUT 类 404；DELETE 不存在资源的语义、`Allow` 头要求、方法守卫与鉴权顺序均无描述。
- 改后要点：
  - 其余端点方法不支持一律 **405**；方法守卫先于角色鉴权与存在性判定，不因权限差异把 405 退化为 403（未认证仍 401）；
  - 405 **已附 `Allow`** 的端点按实际列出：`/api/users`（`GET, POST`）、`/api/users/{id}`（`PUT`）、`/api/executions/{id}`（`GET`）、`/api/nodes/{id}`（`GET, POST, PUT, DELETE`；`revoke-credential` 为 `POST`）、`/api/transfers/{id}`（`GET, POST`；动作子路径为 `POST`）；并如实标注 scripts/tasks/schedules/groups/artifacts/applications 等其余端点**暂不带 `Allow`**（现状保留，未纳入本次统一）；
  - `DELETE /api/scripts/{id}`、`/api/tasks/{id}`、`/api/schedules/{id}`、`/api/groups/{id}`、`/api/artifacts/{id}`、`/api/applications/{id}` 不存在 id → **404** + 归一化文案（不再静默 200）；客户端删除重试必须按 404 处理；
  - `/api/users/{id}`、`/api/executions/{id}` 不支持 `DELETE` → 405 + `Allow`（`Allow: PUT` / `Allow: GET`）。
- 依据：`api.go:1101-1132`（transfers 方法守卫 + Allow）、`:906-1004`（nodes：`revoke-credential` Allow: POST、default Allow: GET, POST, PUT, DELETE）、`:2233-2294`（users）、`:1948-2004`（executions default Allow: GET）、`:205-217`；6 个 DELETE 分支存在性前置检查（`:1343/:1398/:1543/:1176/:1615/:1888` 附近）；`internal/hubserver/delete_idempotent_test.go`
- 自查纠正：初稿曾写「其余端点方法不支持一律 405 + `Allow`」，逐行核对后确认 `Allow` 仅存在于上述端点（`awk` 扫描 38 处 405 站点），已按代码实际改写，未把未实现行为写成契约。

### 4. Transfer 自传拒绝

- 文件/位置：`docs/PRODUCT.md` §34.1（改后 `:1006`）、§46 状态码契约（改后 `:1394`）；`docs/TEST_PLAN.md` §12 新增 SYS-67（改后 `:621`）、§8.2（改后 `:505`）
- 改前要点：§34.1 只写「管理员指定源 Agent、多个目标」与传输能力，未写服务端自传校验；TEST_PLAN 只有 UI 级「源节点不能作为目标」（P22 `:415`）。
- 改后要点：目标=源时**整批拒绝**，`POST /api/transfers` → **400** `self transfer is not allowed`，不落任何目标记录；明确「前端裁剪不构成服务端保证」；测试计划新增 API 级断言。
- 依据：`internal/hub/file_transfer.go:216-221`；handler 400 映射（`api.go:1077-1080`）；`internal/hub/file_transfer_test.go`（混合同批整体拒绝且零落库）。

### 5. 禁用节点派发语义

- 文件/位置：`docs/PRODUCT.md` §17（改后 `:543`）、§46（改后 `:1395`）；`docs/ARCHITECTURE.md` §34 Execution State Machine（改后 `:825`）；`docs/TEST_PLAN.md` P11（改后 `:305`）
- 改前要点：docs 无 disabled 节点派发语义；TEST_PLAN 只有「offline 节点…明确 BLOCKED/错误」。
- 改后要点：disabled 节点返回 **400** `node <id> is disabled`；该判定先于 online/offline 通用判定，不产生执行记录，不得退化为 `not online` 文案；`maintenance`/`offline` 仍按 `not online` 处理（ARCHITECTURE 记为「派发前门禁顺序 disabled → online → capability」）。
- 依据：`internal/hub/execution.go:219-227`（disabled 前移，注释写明缺陷 OBS-20261017）；`internal/hub/pause_dispatch_test.go`（文案含 `is disabled`、不含 `not online`、不产生执行记录）。

### 6. `X-Total-Count` 边界

- 文件/位置：`docs/PRODUCT.md` §46 新增「列表分页与 `X-Total-Count`」小节（改后 `:1402-1404`）；`docs/TEST_PLAN.md` §8.2（改后 `:504`）
- 改前要点：docs 全文无 `X-Total-Count`（含拼写）与分页边界描述。
- 改后要点：服务端分页端点**只有** `/api/executions` 与 `/api/audit`，二者返回 `X-Total-Count` 并经 `Access-Control-Expose-Headers` 暴露；其余列表端点（nodes/tasks/scripts/applications/schedules/users/groups/transfers）全量返回、客户端分页、**不返回**该头；明确该边界为有意设计，新增分页属能力扩展。
- 依据：`api.go:1921-1940`（executions）、`:2034-2053`（audit）为仅有的两处 limit/offset 解析与计数；`internal/hubserver/api_contract_test.go` `TestListTotalCountOnlyOnPaginatedEndpoints`。

### 7. CORS 现状（按现状写明，未改代码）

- 文件/位置：`docs/PRODUCT.md` §46 新增「CORS 现状」小节（改后 `:1406-1409`）；`docs/ARCHITECTURE.md` §6 Hub Endpoint（改后 `:186`）
- 改前要点：docs 全文无 CORS 描述。
- 改后要点：`Allow-Origin: *`、Methods `GET, POST, PUT, DELETE, OPTIONS`、Headers 含 `Authorization` 与两个 `X-NodeSteer-Agent-*`、Expose `X-Total-Count`；**不发送** `Access-Control-Allow-Credentials`，认证走 `Authorization: Bearer`（无 Cookie 会话），因此不构成「通配来源 + 凭据」组合；按部署形态收紧需新增配置项与部署形态裁决，属独立变更。
- 依据：`api.go:205-217`（实际头）、`:2290+` `bearerToken` 只读 header（无 Cookie 路径）。

### 8. Artifact / 下载超时（Agent 侧）

- 文件/位置：`docs/PRODUCT.md` §33（改后 `:979`）；`docs/ARCHITECTURE.md` §44 Artifact Cache（改后 `:1050`）、§62 Artifact Download Failure（改后 `:1582`）；`docs/TEST_PLAN.md` P16（改后 `:360`）
- 改前要点：docs 只有「下载先写 `.tmp`，校验成功后 Atomic Rename」与「仅 `.tmp`，成功 SHA256 后才 rename」，无任何时限；隐含「服务端不发 body 可无限挂起」。
- 改后要点：Agent 共用载荷 HTTP 客户端设置 **15 分钟总时限**与 **30 秒响应头超时**；超时按失败处理，`defer` 清理 `.tmp`，不残留 0B 临时文件；明确该客户端覆盖 Artifact 下载、Agent 自升级二进制与文件中继载荷传输。
- 依据：`internal/agent/connection/manager.go:44-68/112-114/135`（超时常量与兜底/TLS 两条构造路径）；`internal/agent/agent.go:140/1273/1330`、`internal/agent/upgrade.go:181`（HTTPClient 调用点）；`internal/agent/artifact_test.go`（发头不给 body → 限时失败、无 `.tmp`）。

### 9. 部署 journal `from_version` = 实际安装版本

- 文件/位置：`docs/PRODUCT.md` §42 Deployment Journal（改后 `:1206`）；`docs/ARCHITECTURE.md` §52（改后 `:1246`）；`docs/TEST_PLAN.md` P14-P15（改后 `:346`）
- 改前要点：两份文档只列 `from_version` 字段名，未定义取值语义（实现此前取应用定义的期望版本，属缺陷）。
- 改后要点：`from_version` = 该应用本次部署前**实际安装**的版本，取最近一次成功部署（`phase=DONE` 且 `completed_kind=deployed`）的 `to_version`；无成功部署记录时为空；回滚成功（`rolled_back`）不构成已安装版本，不参与取值；不取应用定义的期望版本。
- 依据：`internal/agent/store.go:811-832`（`InstalledApplicationVersion`）、`internal/agent/application.go:303-312`；`internal/agent/application_test.go`（成功 1.0 + 回滚 2.0 后仍 1.0）。

### 10. `GET /api/applications/{id}/nodes` 无绑定返回 `[]`

- 文件/位置：`docs/PRODUCT.md` §46 端点清单（改后 `:1369`）与新增「响应形状」小节（改后 `:1399-1400`）；`docs/TEST_PLAN.md` §8.2（改后 `:503`）
- 改前要点：端点清单未列该端点；无响应形状契约。
- 改后要点：无绑定节点时返回 `[]`，不返回 `null`（严格消费者契约稳定）。
- 依据：`internal/store/sqlite_artifacts_apps.go:254-268`（`out := []string{}` + `rows.Err()`）；`internal/hub/api/app_nodes_shape_test.go`（原始响应体 `[]`）。

### 11. enrollment 并发：存储事务 DSN `_txlock=immediate`

- 文件/位置：`docs/ARCHITECTURE.md` §29 Agent SQLite（改后 `:718`）、§54 Hub 数据库（改后 `:1349`）；`docs/PRODUCT.md` §47（改后 `:1428`）；`docs/TEST_PLAN.md` SYS-01（改后 `:531`）、SYS-11（改后 `:544`）
- 改前要点：ARCHITECTURE §29 只有「SQLite + WAL」与「关键同步修改必须用事务」；PRODUCT §47 为「Agent SQLite 建议使用 WAL」；无忙锁/事务模式说明。
- 改后要点：Hub 与 Agent 两侧 SQLite 均以 WAL + `_txlock=immediate`（写事务 BEGIN 阶段即取写锁）+ `busy_timeout(5000)` + 外键约束打开；消除 WAL 下 deferred 事务「先读后写」升级写锁时不受 busy_timeout 保护的 `SQLITE_BUSY`/`BUSY_SNAPSHOT`；并发写按序等待；测试计划补并发纳管与 Agent 并发写断言。
- 依据：`internal/store/sqlite.go:66-74`（DSN 与注释）、`internal/agent/store.go:50-52`；`internal/hubserver/enrollment_test.go`（12 并发全 200；去掉 `_txlock` 反向 3/3 失败）、`internal/store/sqlite_busy_test.go`。

---

## 二、冲突旧表述改正（逐条）

| # | 文件/位置 | 改前要点 | 改后要点 | 原因 |
|---|-----------|----------|----------|------|
| 1 | `docs/TEST_PLAN.md` §8.2（改前 `:496`） | 「非法方法：405。」 | 「非法方法：405；`/api/me`、`/api/audit`、`/metrics`、`/api/logout`、`/api/users`、`/api/users/{id}`、`/api/executions/{id}`、`/api/nodes/{id}`、`/api/transfers/{id}` 须带 `Allow`；方法守卫先于角色鉴权与存在性判定（未认证仍 401）。」 | 旧表述不含 `Allow` 要求与守卫顺序；本次按代码实际（仅上述端点带 `Allow`）而非「全部端点」写实 |
| 2 | `docs/TEST_PLAN.md` §8.2（改前 `:499`） | 「重复请求的幂等性和并发安全。」 | 「重复请求的幂等性和并发安全（删除类重复请求按 404 语义校验）。」 | 旧句可被读作「删除重复请求仍 200」；实际已改为 DELETE 不存在 id → 404（`delete_idempotent_test.go` 重写） |
| 3 | `docs/PRODUCT.md` §47（改前 `:1394`） | 「Agent SQLite 建议使用 WAL。」 | 「Agent SQLite 使用 WAL，连接以 `_txlock=immediate` 打开写事务（BEGIN 阶段即取写锁），配合 `busy_timeout=5000` 与外键约束；避免…`SQLITE_BUSY`。」 | 「建议」与已落地实现不一致（`internal/agent/store.go:52` 已是固定 DSN），且未覆盖忙锁语义 |
| 4 | `docs/ARCHITECTURE.md` §29（改前 `:700-714`） | 「SQLite + WAL」＋「关键同步修改必须用事务」 | 追加「事务与锁」：`_txlock=immediate` + `busy_timeout=5000`，避免升级写锁的 `SQLITE_BUSY`/`BUSY_SNAPSHOT` | 属缺口补充：旧文未说明事务模式，读者无法从文档推出并发写语义（`sqlite.go:66-74`） |
| 5 | `docs/ARCHITECTURE.md` §62（改前 `:1566`） | 「仅 `.tmp`，成功 SHA256 后才 rename。」 | 追加「下载受 15 分钟总时限与 30 秒响应头超时约束，超时按失败处理并清理 `.tmp`。」 | 旧句未覆盖下载时限，隐含无限挂起；与 `manager.go:44-68` 不符 |
| 6 | `docs/REAL_TEST_REPORT.md` `:23` / `:43` / `:65` | 「Artifact DELETE 幂等…重复调用 DELETE，均返回 HTTP 200」「重复 DELETE 已返回 200」 | **未改动**（历史测试报告） | 该报告为 2026-09-15 的历史证据，改写会伪造历史；按 D1 批次同样原则（「不改历史报告」）保留原文。**冲突已登记**：现行契约为 DELETE 不存在 id → 404（PRODUCT §46），如需消除误导，建议由主 Agent 决定是否在该报告头部加一行 superseded 注记 |

---

## 三、核查过但未改动（无冲突 / 同向）

| 位置 | 原文要点 | 判定 |
|---|---|---|
| `docs/PRODUCT.md` §22（`:699`） | 「一期 Agent Offline 时 Manual Run 不进入长期等待队列，明确返回不可执行。」 | 与 disabled/offline 派发语义同向，不改 |
| `docs/PRODUCT.md` §17（`:539-541`） | 手工置在线与 `400 agent offline` 派发 | 与新增 disabled 段落衔接一致，不改（仅在其后追加） |
| `docs/PRODUCT.md` §46 既有 PUT 404 行（`:1390`） | 「错误体不回传底层存储错误原文」 | 与新增存储错误语义同向，保留原句 |
| `docs/ARCHITECTURE.md` §67 核心不变量 | 18 条不变量 | `_txlock` 与错误归一化属实现层约定，不属核心不变量，未加入 |
| `docs/TEST_PLAN.md` §8.3 三角色矩阵（`:510-516`） | Operator 修改类操作 403 等 | 针对受支持操作；405 守卫只改变「方法不支持」的优先级，不改变越权 403，无冲突 |
| `docs/TEST_PLAN.md` P22（`:415`） | 「源节点不能作为目标。」 | UI 级校验，与服务端 400 同向；本次仅在 §8.2/SYS-67 补 API 级断言，该行未动 |
| `docs/DEPLOYMENT.md`（`:188-189`） | nginx `proxy_read_timeout 3600s` 用于长连接与大文件传输 | 大于 Agent 客户端 15 分钟上限，不冲突（客户端先触发），未动 |
| `docs/IMPLEMENTATION_PROMPT.md`、`docs/TRACEABILITY_MATRIX.md`、`docs/TEST_ENV*.md` | 无本次 11 条契约的表述 | 全 docs 检索 `X-Total-Count`/`CORS`/`PascalCase`/`503`/`from_version`/`Allow:` 等关键词确认无命中，未动 |
| `internal/hub/api/api.go:546`（`/api/oidc/login` 启动失败 500） | 文档未描述 OIDC 500 文案 | 该路径未走 `writeInternalError`，返回 `failed to start oidc login`；已在 PRODUCT §46 与 TEST_PLAN §8.2 以「专用路径除外」如实标注，未把其写成 `internal error` |

---

## 四、未覆盖 / 需新增章节 / 待裁决

1. **实现报告中不在本次指派清单的行为，docs 当前完全未覆盖**（未擅自写入，待授权）：
   - one_time `run_at` 在过去 → 400 `run_at must be in the future`（PUT 仅在值实际变化时拒绝）：无对应位置，建议落 `docs/PRODUCT.md` §23/§46 + `docs/TEST_PLAN.md` P12-P13（`:317`）；
   - 重复用户名 → 400 `username already exists`：建议落 `docs/PRODUCT.md` §44/§46 + `docs/TEST_PLAN.md` P20（`:393`）；
   - 重连握手窗口（Dial 成功→HELLO_ACK accepted 前 `hub_online_required` 门禁关闭、窗口内执行 `offline=true`）：建议落 `docs/PRODUCT.md` §25 + `docs/ARCHITECTURE.md` §16/§30 + `docs/TEST_PLAN.md` SYS-14（`:547`）；
   - nodes 存在性 oracle 关闭（PUT 不存在 → 404 `node not found` 不再外泄 SQL 原文；operator/viewer DELETE 存在与不存在 id 均 403）：建议落 `docs/PRODUCT.md` §17/§46。
2. **无需新建顶层章节**：CORS、`X-Total-Count`、会话键名三项此前 docs 完全空白，已分别以 PRODUCT §46 内独立小节（「会话与响应键名」「列表分页与 `X-Total-Count`」「CORS 现状」）与 ARCHITECTURE §6 一句承载，保持既有文档组织，未新开章节。
3. **未执行验证**：未运行 Markdown lint / 文档构建（仓库无对应入口）；未部署真实环境复验并发纳管与下载超时；文档描述以源码逐处回核 + 实现报告中的单测结论为依据。
4. **残差风险提示（供主 Agent 判断是否写入 docs）**：15 分钟总时限按约 1 MiB/s 保守链路折算约 900 MiB，更慢链路 + 更大文件会下载失败；`docs/DEPLOYMENT.md` 的 nginx 3600s 超时长于该上限，行为由客户端先触发，二者不矛盾。

---

## 五、验证记录

- `git diff --stat -- docs/`（含其他批次既有未提交改动）：`docs/ARCHITECTURE.md` +59/-3、`docs/PRODUCT.md` +80/-1、`docs/TEST_PLAN.md` +26/-6；D2 本次为其中 22 处编辑（3 文件 / 11 条契约），其余为既有改动，未触碰。
- D2 新增/修改行号（改后实读）：
  - `docs/PRODUCT.md`：`:543`（§17 disabled）、`:979`（§33 下载超时）、`:1006`（§34.1 自传）、`:1206`（§42 from_version）、`:1369`/`:1378-1409`（§46 端点清单与四个新小节）、`:1428`（§47 SQLite）；
  - `docs/ARCHITECTURE.md`：`:186`（§6 CORS）、`:718`（§29 事务与锁）、`:825`（§34 门禁顺序）、`:1050`（§44 下载有界）、`:1246`（§52 from_version）、`:1349-1351`（§54 连接层/存储错误）、`:1520`（§61 日志）、`:1582`（§62 超时）；
  - `docs/TEST_PLAN.md`：`:305`（P11）、`:346`（P14-P15）、`:360`（P16）、`:499-505`（§8.2）、`:531`（SYS-01）、`:544`（SYS-11）、`:611`/`:621`（SYS-59 至 SYS-67）。
- `git status --short -- docs/`：仅上述三个 docs 文件被修改；工作树中源码/`web/**`/`.e2e/**` 既有未提交改动保持原样，未回退、未覆盖。
- 每条语义均已对照源码 `文件:行` 与实现报告证据逐处回核（见第一节「依据」）；未执行任何 Git 写操作。
