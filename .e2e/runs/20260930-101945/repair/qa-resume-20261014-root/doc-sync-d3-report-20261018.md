# 文档同步报告 D3：T2 补齐 4 项未纳入契约（2026-10-18）

- 任务：把上一轮（D2）标注为「未纳入清单」的 4 项 T2 修复契约补齐进 `docs/`
- 依据：
  - 实现报告：`go-tail-fix-report-20261017.md`（同目录，§3、§5⑥、§8、§4）
  - 源码逐处回核：
    - one_time：`internal/hub/validation.go:175-197`（`validateSchedule` 签名含 `previousRunAt`，`!s.RunAt.Equal(previousRunAt) && !s.RunAt.After(time.Now())` → `run_at must be in the future`）、`internal/hub/schedule.go:58`（Create 传零值）、`:120`（Update 传 `existing.RunAt`）；`internal/hub/schedule_test.go` `TestOneTimeScheduleRejectsPastRunAt`、`internal/hub/on_start_test.go`
    - 重复用户名：`internal/hub/api/api.go:2209-2224`（`GetUserByUsername` 预检 → 400；`store.IsUniqueViolation` 并发兜底 → 同文案；其余走 `writeInternalError`）、`internal/store/sqlite.go:122-133`；`internal/hubserver/api_contract_test.go:190-204`
    - 握手窗口：`internal/agent/connection/manager.go:80-83/166-186/238/313/387`（handshake 标志、Fail Closed、断开/新连接复位、HELLO_ACK 处更新）、`internal/agent/agent.go:150`（`SetOnlineFunc(a.conn.HandshakeComplete)`）、`:1562-1564`（`TriggerSchedule` 的 `Offline` 同源）、`internal/agent/scheduler/scheduler.go:76-82/150`（在线门禁判定）、`internal/agent/reconnect_gate_test.go`、`internal/agent/connection/manager_test.go`
    - nodes oracle：`internal/hub/api/api.go:906-1004`（GET 直查；DELETE → `canAdmin`、POST/PUT → `canWrite`，二者均仅 Administrator（`:2299-2313`）；鉴权先于 `GetNode` 存在性）、`internal/hubserver/api_contract_test.go`（`TestNodeDeleteExistenceOracleClosed`、`TestNodeUpdateMissingReturns404`）
- 边界：只改 `docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、`docs/TEST_PLAN.md` + 本报告；未改源码、配置、`web/**`、`.e2e/**`；无 Git 操作
- 说明：任务清单中的 `internal/hub/api/validation.go` 不存在（校验实际在 `internal/hub/validation.go`）；`internal/agent/manager.go` 不存在（握手标志实际在 `internal/agent/connection/manager.go`）

---

## 一、逐条改动（4 项契约）

### 1. One-Time 过去 `run_at` → 400（Create 拒绝；Update 仅在值变化时拒绝）

- 文件/位置：
  - `docs/PRODUCT.md` §23 Schedule（改后 `:714`）；§46 状态码契约（改后 `:1397`）
  - `docs/TEST_PLAN.md` P12-P13（改后 `:317`）；§8.2（改后 `:506`）
- 改前要点：§23 只列 Cron/Interval/One-Time 与「必须保存 IANA Timezone」，无 `run_at` 时间约束；§46 无该端点状态码；TEST_PLAN 只列用例名「One-Time 未来时间、过去时间、时区、空 run_at」，无预期结果。
- 改后要点：
  - §23：One-Time 的 `run_at` 必须晚于当前时间；Create 时过去时间 → **400** `run_at must be in the future`；Update **仅在 `run_at` 实际变化时校验**，未修改的历史过去时间记录仍可编辑/禁用，不因该校验被锁死；
  - §46：同一语义写入状态码契约（`POST /api/schedules` + `PUT /api/schedules/{id}` 分别说明）；
  - TEST_PLAN：P12-P13 与 §8.2 补可执行断言（过去创建拒绝、历史值放行、改成新过去值拒绝）。
- 依据：`validation.go:191-197`（`previousRunAt` 相等即放行）；`schedule.go:58/:120`（Create 零值、Update 传既有值）；`schedule_test.go`（Create 拒绝 + 历史记录禁用成功 + 改新过去值拒绝）。

### 2. 重复用户名 → 400 `username already exists`（归一化，无数据库原文）

- 文件/位置：`docs/PRODUCT.md` §46 状态码契约（改后 `:1401`）；`docs/TEST_PLAN.md` P20（改后 `:393`）、§8.2（改后 `:507`）
- 改前要点：docs 无用户创建的错误契约；TEST_PLAN P20 只写「创建成功、重复用户名、错误输入」，未定义状态码与文案。
- 改后要点：`POST /api/users` 用户名重复 → **400** `username already exists`；不返回数据库层 UNIQUE 原文；并发创建由唯一索引兜底为同文案；测试计划补断言（响应体不含 unique/constraint/sqlite 原文）。
- 依据：`api.go:2209-2224`（预检 + `IsUniqueViolation` 兜底 + `writeInternalError`）；`api_contract_test.go:190-204`（断言 400 与归一化文案，且不含 unique/constraint/sqlite/users.username）。

### 3. 重连握手窗口门禁（HELLO_ACK accepted 前不视为在线）

- 文件/位置：
  - `docs/ARCHITECTURE.md` §16 Agent Gateway 与 Session Manager（改后 `:429`）、§30 Local Scheduler（改后 `:751`）、§56 Agent Protocol（改后 `:1431`）
  - `docs/PRODUCT.md` §25 Offline Policy（改后 `:755`）
  - `docs/TEST_PLAN.md` SYS-14（改后 `:550`）
- 改前要点：docs 无握手完成语义；§16 只写 Session 内存字段与「Session 不是业务状态唯一来源」；§30 只列调度器支持项与字段；§56 只列 HELLO 字段与 Registration Token；PRODUCT §25 只列两种策略；TEST_PLAN SYS-14 只写「Hub 不可用时不执行」。
- 改后要点：
  - ARCHITECTURE §16：**连接握手门禁**——HELLO_ACK `accepted` 之前 Agent 不视为在线；在线门禁取「握手完成」标志而非传输层连接状态；`rejected` 或负载解析失败按未接受处理（**Fail Closed**）；断开与新连接均复位标志；并说明该门禁与「对账完成才 READY」是两道独立关卡（READY 另用于拒绝 Hub 派发/部署）；
  - ARCHITECTURE §30：本地调度器对 `execution_owner=AGENT` 且 `offline_policy=hub_online_required` 的调度在触发前要求握手完成；重连窗口内不触发；窗口内本地触发的执行（如 Allow Offline）标记 `offline=true` 等重连对账补传；
  - ARCHITECTURE §56：补 HELLO_ACK 语义（仅 `accepted` 表示 Hub 接受本次连接）；
  - PRODUCT §25：按执行归属写明两处在线门禁落实点（HUB owner → Hub 下发前检查节点状态；AGENT owner → Agent 本地调度器检查握手门禁），并说明重连窗口不触发与 `offline=true` 标记；
  - TEST_PLAN SYS-14：补「重连窗口内不触发、握手完成后恢复触发」。
- 依据：`manager.go:166-186`（`HandshakeComplete`/`markHandshake`/`helloAckAccepted` 解析失败 Fail Closed）、`:238/:313`（断开/新连接复位）、`:387`（dispatch 收到 HELLO_ACK 时更新，handler 缺失也生效）；`agent.go:150`（门禁注入）、`:1562-1564`（`Offline` 同源）；`scheduler.go:150`（门禁判定）；`reconnect_gate_test.go`（反向验证：改回 `IsConnected` 用例失败）、`manager_test.go`（无 ACK 窗口 false / rejected false / accepted true、断开复位）。

### 4. nodes 存在性 oracle 关闭（先鉴权后存在性）

- 文件/位置：`docs/PRODUCT.md` §17 Node 管理（改后 `:545`）、§46 状态码契约（改后 `:1399`）；`docs/TEST_PLAN.md` §8.2（改后 `:508`）
- 改前要点：§17 无权限/存在性顺序语义；§46 无节点子资源状态码行；TEST_PLAN 无 oracle 断言。
- 改后要点：
  - `DELETE`/`POST`/`PUT /api/nodes/{id}` **先鉴权后存在性**：operator/viewer 对存在与不存在的 id **均 403**，不通过状态码差异泄露节点是否存在；
  - 管理员对不存在的 id → **404** `node not found`（不再返回 400 + 存储层 `sql: no rows` 原文）；
  - TEST_PLAN §8.2 补 oracle 断言（含 admin 404 无存储层原文）。
- 依据：`api.go:906-1004`（GET 直查；DELETE 先 `canAdmin`、POST/PUT 先 `canWrite`，之后才 `GetNode`；default 405 + `Allow: GET, POST, PUT, DELETE`；`revoke-credential` 非 POST 先 405 + `Allow: POST`）；`canAdmin`/`canWrite` 均为 Administrator（`:2299-2313`）；`TestNodeDeleteExistenceOracleClosed`（operator 存在/不存在均 403）、`TestNodeUpdateMissingReturns404`（PUT 不存在 → 404，无 SQL 原文外泄）。

---

## 二、冲突旧表述改正（逐条）

| # | 文件/位置 | 改前要点 | 改后要点 | 原因 |
|---|-----------|----------|----------|------|
| 1 | `docs/TEST_PLAN.md` P12-P13（改前 `:316`，现 `:317`） | 「One-Time 未来时间、过去时间、时区、空 run_at。」（只列用例名，无预期） | 补「过去时间创建 → 400 `run_at must be in the future`；历史过去值记录改其它字段/禁用放行，改成新的过去值拒绝」 | 旧行未定义预期结果，易被读作「过去时间可保存」；与 `validation.go:191-197` 不符 |
| 2 | `docs/TEST_PLAN.md` P20（改前 `:390`，现 `:393`） | 「创建成功、重复用户名、错误输入。」（无状态码/文案） | 补「400 `username already exists`，响应体不含 unique/constraint/sqlite 原文」 | 旧行未定义错误语义；重复用户名此前会外泄 SQLite UNIQUE 原文（缺陷 OBS-20261016-G1） |
| 3 | 无硬冲突项：本批 4 项在此前 docs 中**完全空白**（握手窗口、oracle、重复用户名文案、one_time 时间校验均无任何旧表述可矛盾） | — | — | 全 docs 检索 `握手`/`HELLO_ACK`/`oracle`/`存在性`/`重复用户名`/`run_at` 确认：除本次新增外无命中（旧句仅 P12-P13、P20 两处「只列用例名」的欠定义行，已按上表改正） |

---

## 三、核查过但未改动（无冲突 / 需说明）

| 位置 | 原文要点 | 判定 |
|---|---|---|
| `docs/PRODUCT.md` §24 Schedule Execution Owner（`:720-742`） | 「Hub Online Required：`execution_owner=HUB`」「Allow Offline：`execution_owner=AGENT`」 | 保留：这是产品侧的归属映射；Agent 侧门禁覆盖的是 `execution_owner=AGENT` + `hub_online_required` 组合（校验允许该组合，调度器按策略做防御性门禁）。本次 §25 已按「两处落实点」写清，未改 §24 映射 |
| `docs/PRODUCT.md` §13（`:451`） | 「对账完成之前不得视为 READY」 | 与握手门禁不矛盾：握手（HELLO_ACK accepted）早于对账完成；ARCHITECTURE §16 已明确两者是独立关卡（ARCHITECTURE §24 仅有对账流程，无该句，未动） |
| `docs/TEST_PLAN.md` §8.2（`:496`） | 「不存在资源：404。」 | 与 oracle 关闭不矛盾（403 是越权语义）；节点子资源的 403/404 细节已在新增行 `:508` 写明 |
| `docs/PRODUCT.md` §17（`:539-543`） | 管理员可 `PUT /api/nodes/{id}` 手工置在线；disabled/offline 派发语义 | 与 oracle 关闭一致（管理员仍可写；operator/viewer 403），未改 |
| `docs/TEST_PLAN.md` §8.3 三角色矩阵（`:518`） | 「节点状态/标签修改：Operator 403」 | 与 oracle 关闭同向（先鉴权后存在性只会让 403 更一致），未改 |
| `docs/UI_FULL_TEST_PLAN.md:310` | 「重复用户名、空字段、角色修改和登录权限」（UI 级用例，无错误码） | UI 计划未定义服务端文案，无冲突；不在三文件同步范围，未动 |

---

## 四、未执行 / 边界

1. 未运行 Markdown lint / 文档构建（仓库无对应入口）；未部署真实环境复验重连窗口与并发纳管（实现报告亦说明本批未部署，验证为本地单测 + `-race`）。
2. 未补充 `docs/UI_FULL_TEST_PLAN.md`（UI 级用例）与 `docs/REAL_TEST_REPORT.md`（历史证据）：前者无契约冲突且不在三文件范围；后者按「不改历史报告」原则保留（D2 已登记 Artifact DELETE 200 的过期表述，本批无新增历史冲突）。
3. 无新增顶层章节：4 项均落入既有章节（PRODUCT §17/§23/§25/§46、ARCHITECTURE §16/§30/§56、TEST_PLAN §8.2/P12-P13/P20/SYS-14）。

---

## 五、验证记录

- D3 新增/修改行号（改后实读）：
  - `docs/PRODUCT.md`：`:545`（§17 oracle）、`:714`（§23 run_at）、`:755`（§25 握手门禁）、`:1397`（§46 one_time）、`:1399`（§46 nodes oracle）、`:1401`（§46 重复用户名）；
  - `docs/ARCHITECTURE.md`：`:429`（§16 握手门禁）、`:751`（§30 调度门禁）、`:1431`（§56 HELLO_ACK 语义）；
  - `docs/TEST_PLAN.md`：`:317`（P12-P13）、`:393`（P20）、`:506-508`（§8.2 三条）、`:550`（SYS-14）。
- `git diff --stat -- docs/`（含其他批次既有未提交改动）：`docs/ARCHITECTURE.md` +65/-3、`docs/PRODUCT.md` +89/-1、`docs/TEST_PLAN.md` +35/-6；D3 本次为其中 11 处编辑（3 文件 / 4 项契约）。
- `git status --short -- docs/`：仅上述三个 docs 文件被修改；工作树中源码/`web/**`/`.e2e/**` 既有未提交改动保持原样，未回退、未覆盖。
- 每条语义均已对照源码 `文件:行` 逐处回核（见各条「依据」）；未执行任何 Git 写操作。
