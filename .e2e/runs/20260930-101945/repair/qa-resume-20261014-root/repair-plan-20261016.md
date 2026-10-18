# NodeSteer 7 条新增缺陷修复方案（供人工 Review）

- 文档日期：2026-10-16
- 范围：`.e2e/failures.json` → `repair_additional_failures` 中 7 条 `CONFIRMED_TEST_FAILURE_REPAIR_SCOPE_REVIEW_PENDING` 缺陷（P0×2、P1×2、P2×2、P3×1）
- 性质：**只读分析 + 本方案文件**。未修改任何产品源码、未执行 Git 操作、未访问远程环境。
- 依据：`.e2e/failures.json`、各 Root 复核件（`sys-b09/sys-b12/p-cond/p-app/p-sysmisc/p-web/p-api/web-batch05-users`）、`qa-resume-20261014-runtime|web|api` 原始证据，以及仓库源码逐处核对（下方每条给出 `文件:行`）。
- 说明：所有“建议改动”均为**方案级伪代码/diff 描述**，实施前仍需按全局 AGENTS.md 第 3 节完成 Requirement Review 确认；凡触碰公共 API 契约 / DB schema / Agent↔Hub 协议 / 恢复语义者已显式标注“**必须重新确认**”。

## 汇总表

| # | 缺陷 | 优先级 | 根因一句话 | 是否必须重新确认 | 建议回归用例数（定向） |
|---|------|--------|------------|------------------|------------------------|
| 1 | E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW | P0 | 新鲜度判定把 Hub 采样的绝对时间戳与 Agent 本地时钟比较，正偏移即恒 fail-closed | 必须（协议字段语义 + 判定位置） | 7 |
| 2 | E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE | P0 | 回滚失败遗留 ROLLING_BACK 行，重启恢复用“当前定义路径 + 陈旧 backup”重放，覆盖已成功部署 | 必须（Agent 本地 schema + 恢复语义 + 新 phase） | 8 |
| 3 | E2E-20261015-COMMITSYNC-FAILURE-DOUBLE-UNLOCK-PANIC | P1 | `CommitSync`/`RollbackSync` 在 `tx==nil` 时仍 `Unlock`，Commit 失败后 defer 触发双重解锁 panic | 否（纯缺陷修复） | 4 |
| 4 | E2E-20261014-TIMEOUT-CONTENT-VALIDATION-GAP | P1 | Script 定义缺少 timeout 负数与 content 体量校验（Task 侧已有对照实现） | 必须（对既有 200 输入改判 400；上限数值为新产品约束） | 3 |
| 5 | E2E-20261015-GROUP-EMPTY-NAME-AND-UPSERT-200 | P2 | groups 无 name 校验、Update 不检查命中行（0 行仍 200 并写孤儿成员、推进 revision） | 必须（API 状态码契约：upsert 200→404） | 4 |
| 6 | E2E-20261015-SQL-ERROR-LEAK-ON-PUT-NOTFOUND | P2 | 4 个资源 PUT 对“不存在的 id”走 400 + `err.Error()`，外泄 `sql: no rows in result set` | 必须（API 状态码契约：400→404） | 3 |
| 7 | E2E-20261014-AUDIT-METHOD-FALLBACK-200 | P3 | `handleAudit` 无方法分支，非 GET 也执行查询并 200 | 必须（公共 API 方法语义；含同类端点取舍） | 3 |

---

## P0-1 修复方案：E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW

### 0. 先确认真正的判定链路（按实际代码核对，非二手描述）

| 环节 | 事实 | 位置 |
|------|------|------|
| 谁发起查询 | Agent 条件引擎 `remote` 分支 → 注入的 `a.queryRemoteCondition` | `internal/agent/condition/engine.go:150-163`、`internal/agent/agent.go:149` |
| 谁提供 `observed_at` | **Hub**。`resolveRemoteState` 用 Hub 进程时钟生成：缓存命中返回“状态被 Hub 接收时刻”（`handleRemoteState` 写入时 `time.Now()`），实时分支返回“本次请求的 Hub now” | `internal/hub/gateway.go:561-571`、`590-618` |
| 谁提供 TTL | **Hub**。实时分支固定 60s；缓存分支 `int64(expires.Sub(observed).Seconds())`（完整窗口，非剩余），`expires = observed + ttl` | `internal/hub/gateway.go:596,603-616`、`internal/store/sqlite_executions.go:184-191` |
| 缓存路径是否可达 | **当前不可达**：全仓库没有 Agent 侧发送 `MsgRemoteState` 的代码路径（仅 Hub 有 handler），`resolveRemoteState` 实际恒走实时分支——与 p-cond `OBS-20261014-REMOTE-TTL-PATH-UNREACHABLE` 一致 | `gateway.go:261`（仅有 handler）、grep 全仓 `MsgRemoteState` 发送方为空 |
| 谁比较 | **Agent 本地时钟**：`queryRemoteCondition` 收到响应后调用 `validRemoteState(p, time.Now())` | `internal/agent/agent.go:1712-1725` |
| 比较点与失败后果 | `observed.After(now)` → 视为无效 → 条件 unknown → `ExecuteTask` 记 BLOCKED | `internal/agent/sync_validation.go:31-42`、`internal/agent/agent.go:1023-1027` |
| 实测偏移 | Hub 领先 native-1 **+0.1464s**（12 组交叉采样）；受控实验证明 Agent 实现本身正确、门禁对“未来时间戳”敏感（+0.5s→BLOCKED） | `sys-b09-review-20261014.json`、`p-cond-review-20261014.json` |

### 现状与根因

`internal/agent/sync_validation.go:31-42`：

```go
observed, err := time.Parse(time.RFC3339Nano, p.ObservedAt)
if err != nil || observed.After(now) { // ← 跨主机时钟比较点
    return false
}
age := float64(now.Unix()-observed.Unix()) + float64(now.Nanosecond()-observed.Nanosecond())/1e9
return age < float64(p.TTL)
```

`observed` 是 Hub 时钟采样、`now` 是 Agent 时钟。只要 Hub 领先 Agent 一点点，`observed.After(now)` 恒真 → `queryRemoteCondition` 恒返回 `("", false, nil)` → `agent.go:1025` 恒写 `BLOCKED / condition unknown: <nil>`。**Fail-closed 判定本身被时钟偏移劫持**，13 条 Remote 条件全 BLOCKED（其中 11 条本应可判定，含 `online==offline` 本应 SUCCESS）。

### 建议改动（主方案：把新鲜度判定收归时间产生方）

Hub 侧 `resolveRemoteState`：`ttl_sec` 语义由“观测窗口长度”改为“**Hub 判定时刻起剩余有效秒数**”（字段名/类型不变）：

```go
// internal/hub/gateway.go resolveRemoteState（示意）
if value, observed, expires, err := g.nodes.store.GetRemoteState(...); err == nil {
    if expires.After(now) {
        remain := int64(math.Ceil(expires.Sub(now).Seconds())) // 剩余，向上取整防 0<remain<1 截断为 0
        if remain > 0 {
            return value, observed, remain
        }
    }
}
// 实时分支：observed=now，ttl=60（窗口 == 剩余）
```

Agent 侧 `validRemoteState` 去掉时间戳比较（签名去掉 `now` 参数）：

```go
func validRemoteState(p protocol.RemoteStatePayload) bool {
    // TTL 由 Hub 在判定时刻计算；Agent 不再解释绝对时间戳
    return p.Value != "" && p.Value != protocol.RemoteStateUnknown && p.TTL > 0
}
```

配套：`agent.go:1715` 调用点同步改签名；`sync_validation_test.go` 的 `future/expired` 用例按新语义重写；`ObservedAt` 保留为诊断字段（日志/UI），不再参与授权判定。

### 为什么这样改（架构一致性）

- 与 `docs/ARCHITECTURE.md` §40（Remote State：Hub 保存 `observed_at/expires_at`，TTL 内才可用）一致：TTL 归属 Hub 保存与判定，天然消除跨主机时钟依赖；与 §67.1“Hub 是 Desired State 唯一权威源”一致。
- §67.9 / `PRODUCT.md` §3.5、§28“UNKNOWN/过期 → BLOCKED”**保持**：未知值、空值、TTL≤0 仍 fail-closed。
- 与文档无冲突；需在 §40 与 `PRODUCT.md` §28 补一句“TTL 为 Hub 计算的剩余有效秒数”，或在 `protocol.go:336` 字段注释中写明。

### 影响面

- **Agent↔Hub 协议**：`RemoteStatePayload.ttl_sec` 语义变化（结构不变）；需 Hub/Agent 同版本或以新 Agent 对新 Hub 生效；旧 Agent 未升级前仍受偏移影响（发布说明需注明）。
- 无 DB schema、无 Hub 公共 REST API、无前端影响（前端不经该协议）。
- 缓存分支当前无写入方；一旦未来启用，必须保留“剩余 TTL 向上取整”的实现细节。

### 是否需要用户重新确认

**必须重新确认**：协议字段语义变更 + fail-closed 判定位置变更。

### 回归验证方案（定向）

- 重跑：`SYS-27`（主）、`SYS-28`、`SYS-29`、`SYS-30`、`E2E-10`、`INV-09`、`SYS-26`（AND/多层 + Remote 组合），共 **7 条**。
- 新增边界用例：
  1. 受控 fake-Hub 注入“Hub 时钟领先 Agent ≥1s”：`online==online`/`online==offline` 均得到真实判定（SUCCESS/SKIPPED），而非 BLOCKED（不依赖真实主机时钟偏移）；
  2. 缓存态剩余 TTL：`remain=0.4s → 向上取整为 1 → 可判定`；`有值但已过期 → 走实时分支`；
  3. 负向：`value=""`、`UNKNOWN`、`TTL<=0` 仍 BLOCKED；`ObservedAt` 非法字符串不再影响判定（或保留容错解析）；
  4. `last_execution` 属性同样覆盖（SYS-28 当前 PARTIAL 的剩余维度）。

### 风险与替代方案

- **替代 A（Agent 单侧容差窗口，最小改动）**：`validRemoteState` 仅把 `observed.After(now)` 改为 `observed.Sub(now) > skewTolerance`（如 3–5s），允许负 age。**取舍**：改动 1 行、无协议变更；但没有根除“跨时钟比较”，偏移大于容差或短 TTL 场景仍会误判，属缓解而非根治。
- **替代 B（协议新增 `ttl_remaining_sec` + 兼容字段）**：新 Agent 优先用剩余 TTL，缺失时回退旧逻辑。**取舍**：兼容滚动升级，但组合状态多（旧 Hub×新 Agent 仍需容差兜底），实现复杂度高。
- **替代 C（Hub 返回 `fresh: bool`）**：与主方案等价但丢失剩余量，无法诊断“即将过期”，不推荐。
- 主要风险：判定权完全交给 Hub 后，故障 Hub 可放宽新鲜度——但 Hub 本就是权威源，风险可接受；测试需覆盖“Hub 侧 ceil 边界”。

---

## P0-2 修复方案：E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE

### 现状与根因

1. `failed()` 仅在回滚成功时把 journal 推进 DONE，失败则永停 ROLLING_BACK（`internal/agent/application.go:395-407`）：

```go
journalErr := am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseRollingBack, backupPath)
if !am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, health) {
    res.Error += "; rollback failed"
} else if journalErr == nil {
    journalErr = am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
}
```

2. `rollback()` 结尾用**失败版本自己的 health 定义**复检（`application.go:673-678`），任何非 systemd 负分支（tcp/http/command 不可达）都必然不过 → 即使文件其实已还原也返回 false（SYS-48 链，留 3 条毒行）。
3. `GetActiveDeployments` 取 `phase NOT IN ('DONE')`（`internal/agent/store.go:709-711`）→ ROLLING_BACK 行永续存在。
4. `RecoverDeployments`（`application.go:682-741`）重启时用**当前 app 定义**解析 binary/config/unit 路径（`:692-710`），再拼**该行当时的 backup** 重放回滚（`:733`）并写 DONE（`:737`）：

```go
if err := am.store.UpdateDeploymentPhase(ctx, d.ID, PhaseRollingBack, d.BackupPath); err != nil { ... }
if !am.rollback(ctx, app.BinaryPath, d.BackupPath, unitName, configPath, d.BackupConfigPath, d.BackupUnitPath, hc) {
    am.logger.Error("deployment rollback failed; journal retained", "id", d.ID)
    continue
}
if err := am.store.UpdateDeploymentPhase(ctx, d.ID, PhaseDone, d.BackupPath); err != nil { ... }
```

5. `DeploymentRow`（`store.go:674-685`）只有 `from_version/to_version/backup_path/...`，**没有记录 app revision 与部署时解析的路径**，恢复时无从判断“backup 属于哪个定义状态”。
6. 实测链条（AR-32，`sys-b12/case-result.json`）：symlink 目的地被 AtomicReplace 拒 → 其 rollback 亦必然失败（EPERM）→ 毒行 `22ce1540`（ROLLING_BACK，10:06:25）→ 释放 seam 后成功部署 v5.0（10:06:31 之后多条 DONE）→ 重启时陈旧 backup 覆盖 v5.0（SHA `60b4808f…`→`f601cb64…`）、unit inactive、Desired≠Actual。

### 建议改动（三段，可裁剪启停）

**A. 恢复前“被取代”判定（核心，无需 schema）**

`RecoverDeployments` 对每条 active 行先判定是否已被更新部署取代：同 `application_id` 存在 `started_at > d.started_at` 且 `phase='DONE'` 的行 → **跳过重放**（不触碰任何文件），落终态标记（见 C 的 `SUPERSEDED` 或写 DONE 并注明）并记日志。

**B. journal 记录部署上下文并在恢复前比对（回答“陈旧 backup 与当前定义不匹配如何检测”）**

- `deployments` 表新增 `app_revision INTEGER`、`binary_path TEXT`、`config_path TEXT`、`unit_name TEXT`（本地 schema 迁移，旧行为空=兼容）；
- `deploy()` 在 `CreateDeployment`（`application.go:281-284` / `store.go:687-694`）写入当时的 app 定义 revision 与解析后的三路径；
- `RecoverDeployments` 比对：**记录非空且与当前定义（revision 或任一路径）不一致 → 跳过重放**，日志 `"journal binding mismatch; requires inspection"`；一致才重放，且重放以 **journal 记录路径**为准（不再用当前定义路径拼接）。

**明确回答检测方法**：陈旧 backup 的判据 = **(i) 同 app 存在更晚的 DONE 部署行（superseded）**，或 **(ii) journal 记录的 `app_revision`/路径与恢复时的当前定义不一致（binding 失效）**。任一命中即禁止重放回滚。

**C. 状态区分（可选，观测性）**：`failed()` 中 `rollback()` 返回 false 时，把 phase 从遗留 ROLLING_BACK 改为 `ROLLBACK_FAILED`（区别于“回滚中途崩溃”的 ROLLING_BACK；后者必须重放）。恢复逻辑对 `ROLLBACK_FAILED` 仍需（通过 A/B 校验后）尝试重放，保留 §42“恢复到明确状态”的恢复路径。

### 为什么这样改（架构一致性）

- 与 §52 Deployment Journal（“Crash 后读 Journal 判断继续还是 Rollback；一期默认优先安全恢复 Previous”）一致：只在部署确为“半完成且未被取代、绑定仍有效”时恢复 Previous。
- 与 §67.1 一致：防止本地恢复把 Hub 已推进的 Desired State 倒退；与 §67.11（恢复路径存在）一致：未 superseded 的行照旧重放，被取代的行保留记录 + 重新部署即恢复（AR-32 后段实测“再次部署即修复”）。
- 需指出：若采纳 C，`ARCHITECTURE.md` §52 / `PRODUCT.md` §42 的 Phase 枚举需增补 `ROLLBACK_FAILED`（文档变更，随方案一起评审）。

### 影响面

- Agent 本地 SQLite schema（`deployments` 表加列/加 phase 取值）；无 Hub DB、无 REST API、无协议变化；前端不直连 Agent DB。
- 行为变化：部分历史毒行不再每次重启回滚；被取代行只保留记录。
- `GetActiveDeployments` 若引入终态（如 `SUPERSEDED`）需同步查询条件（仅 `RecoverDeployments` 与测试使用该函数，影响面已核实）。

### 是否需要用户重新确认

**必须重新确认**：本地 DB schema 变更 + 恢复语义变更（+ 若采纳 C 的新 phase 与文档枚举变更）。

### 回归验证方案（定向）

- 重跑：`AR-32`（主，FAIL→期望 PASS）、`SYS-56`、`SYS-48`、`INV-11`（recommended owner）、`SYS-51`（升级逐阶段崩溃恢复）、`E2E-13`、`AR-30`、`E2E-12`，共 **8 条**（可加 `FL-11`/`P14P15-18` 观察项）。
- 新增单测（沿用 `internal/agent/application_test.go` 的 `recoveryFixture`/`TestRecoveryMissingBackupNeverDeletesOrCompletes` 模式）：
  1. 毒行 + 更晚 DONE 行 → `RecoverDeployments` 不改动文件（SHA 不变、不 StartService），不产新 DONE；
  2. journal 绑定（revision/路径）与当前定义不一致 → 不重放；
  3. 无后续部署的半完成行 → 仍按现有路径成功恢复（SYS-51 类）；
  4. 旧行（绑定列空值）保持既有行为，`TestRecoveryMissingBackupNeverDeletesOrCompletes` 不回归；
  5. 边界：更晚行存在但仍 active（未 DONE）→ 行为需明确（建议本期保持现有“逐条重放”并作为已知限制记录）。

### 风险与替代方案

- **替代 1（零 schema，最小）**：仅做 A（supersession）——可闭合 AR-32 实测链；但“定义路径漂移 + 无重部署 + 重启”这一相关风险仍在。取舍：改动小、无迁移，建议至少落 A。
- **替代 2（仅 C）**：把回滚失败行改为终态不再重放——可防覆盖，但放弃“回滚失败后重启可重试”的恢复语义，且对“回滚中途崩溃的 ROLLING_BACK”仍无防覆盖能力，不单独采用。
- **替代 3**：回滚时改用“上一版本自己的 health 定义”（需 journal 记录上一版 health/definition）——语义更正确但依赖更大 schema 扩展，且 hub 侧定义仍在，Agent 无历史定义；本期不建议。
- 风险：A 必须以 `application_id` 严格匹配、`started_at` 用统一 UTC 字符串排序；B 的迁移需与 Agent 现有 `migrate()` 幂等模式一致（`internal/agent/store.go:177`）。

---

## P1-3 修复方案：E2E-20261015-COMMITSYNC-FAILURE-DOUBLE-UNLOCK-PANIC

### 现状与根因

`internal/agent/store.go:244-265`：

```go
func (s *LocalStore) CommitSync() error {
    if s.tx == nil { s.mu.Unlock(); return nil }      // ← tx==nil 也解锁
    err := s.tx.Commit(); s.tx = nil; s.mu.Unlock(); return err
}
func (s *LocalStore) RollbackSync() error {
    if s.tx == nil { s.mu.Unlock(); return nil }      // ← 双重解锁点（panic 现场 store.go:258）
    err := s.tx.Rollback(); s.tx = nil; s.mu.Unlock(); return err
}
```

`applySync` 用 `defer + synced 标志`（`internal/agent/agent.go:797-804`），`CommitSync` 失败后（`agent.go:917-920`）函数返回 err，defer 再调 `RollbackSync` → `s.tx` 已被 `CommitSync` 置 nil → 对未持有的 mutex 再 `Unlock` → `fatal panic: sync: unlock of unlocked mutex`。p-sysmisc 注入真实 SQLITE_FULL 复现（失败事务未落盘、对象未落地；腾空间重启后同步成功）。依据：`p-sysmisc-review-20261015.json`（`store.go:258 ← agent.go:800`）。

### 建议改动（最小 diff）

```go
func (s *LocalStore) CommitSync() error {
    if s.tx == nil { return nil }              // 无活动事务：不持锁，不得 Unlock
    err := s.tx.Commit(); s.tx = nil; s.mu.Unlock(); return err
}
func (s *LocalStore) RollbackSync() error {
    if s.tx == nil { return nil }              // Commit 失败后 Tx 已结束，Rollback 必须是 no-op
    err := s.tx.Rollback(); s.tx = nil; s.mu.Unlock(); return err
}
```

依据 `database/sql` 语义：`Tx.Commit()` 返回错误后该 Tx 已结束，不能再 `Rollback`；因此 `CommitSync` 错误后 defer 的 `RollbackSync` 正确行为是 no-op（当前实现反而 panic）。

### 为什么这样改（架构一致性）

- 维持既定不变量：“写锁由 `BeginSync` 成功获取、只由 `Commit/Rollback` 释放一次”（`store.go:231-241` 注释语义）。
- 与 §26 Atomic Sync 一致：Commit 失败 → revision 不前进（`agent.go:913-920` 顺序保证）、回 SyncAck `OK:false`（`agent.go:749-754`）、Agent 保持 not-ready 待下一次同步；不再 panic 重启。
- 无文档冲突。

### 影响面

- 仅 Agent 本地锁/事务语义；行为变化：Commit 失败从 fatal panic 变为可报告错误（`logger.Error("apply sync failed")` + SyncAck false）。
- 调用方仅 `applySync` 与测试（已核实）；无契约/schema/协议/前端影响。

### 是否需要用户重新确认

**不需要契约级重新确认**（纯缺陷修复；随本轮方案 Review 一并确认即可）。

### 回归验证方案（定向）

- 重跑：`INV-05`（主）、`SYS-11`、`E2E-16`、`SYS-02`，共 **4 条**。
- 新增单测（`internal/agent/store_test.go` / `sync_validation_test.go` 模式）：
  1. Commit 失败注入（关闭底层 DB 或在只读/限容 tmpfs 上写大负载）→ `CommitSync` 返回 err、随后 `RollbackSync` 返回 nil 且不 panic；
  2. 连续两次 `CommitSync`/`RollbackSync` 幂等无 panic；
  3. 无 `BeginSync` 直接 `CommitSync`/`RollbackSync` 返回 nil（不误 Unlock）；
  4. `applySync` 级：Commit 失败 → 返回错误、revision 不变、可再次成功同步（对应 p-sysmisc 的“腾空间重启后同步成功”）。

### 风险与替代方案

- **替代**：在 `applySync` 的 defer 中改为 `if !synced && a.store.InSyncTx() { RollbackSync() }`。取舍：单点修 defer 也能止血，但 `store.go` 的 `tx==nil` 分支仍留隐患（任何未来调用方/并发路径再踩），且 `InSyncTx` 读 `s.tx` 无锁属既有约定风险；建议两层都做（store 必修 + defer 判断可选加固）。
- 风险：无。等待 `tx.Commit()` 的错误路径已由 Go `database/sql` 定义清楚。

---

## P1-4 修复方案：E2E-20261014-TIMEOUT-CONTENT-VALIDATION-GAP

### 现状与根因

- `ScriptManager.Create/Update`（`internal/hub/definitions.go:34-69`、`72-109`）只做参数与名称唯一校验，**无 timeout/content 校验**；handler `POST /api/scripts`（`internal/hub/api/api.go:1146-1162`）也只有 name（由 manager 的 `validateUniqueName` 结果 400 兜住）。
- 对照 Task 侧已有明确规则（`definitions.go:225-230`）：`timeout==0 → 300`、`timeout<0 → ErrInvalidTask("timeout cannot be negative")`；Agent 执行侧同样 `<=0 → 300s`（`internal/agent/agent.go:1070-1073`）。
- p-web 实测（`P07_P08-11`，`qa-resume-20261014-web/p-web/results.json`）：`POST /api/scripts` 携带 `timeout=-5` → HTTP 200 且持久化 `-5`；`timeout=0` 接受；307200 字节 content 接受（"无 256KiB 上限拦截"）。
- 补充事实（本轮核对）：`script.timeout` 目前**不参与执行超时**（Hub 下发执行用 `task.Timeout`，`internal/hub/execution.go:313`；Agent 亦不读取 script timeout），因此负值属“定义质量/契约”缺陷，非执行安全缺陷；“0 是否必须拒绝”属产品语义选择。
- 前端 `web/src/features/editors/index.tsx:526-536` 已有 `min={0}`，但无上限与 maxLength。

### 建议改动

在 `internal/hub/validation.go` 新增脚本定义校验并接入 `ScriptManager.Create/Update` 顶部：

```go
const maxScriptContentBytes = 256 * 1024 // 256 KiB（数值需产品确认）

func validateScriptDefinition(s *models.Script) error {
    if s.Timeout < 0 {
        return fmt.Errorf("timeout cannot be negative")
    }
    if s.Timeout == 0 {
        s.Timeout = 300 // 与 task 一致：0 = 默认 300s（可选，见替代方案）
    }
    if len(s.Content) > maxScriptContentBytes {
        return fmt.Errorf("script content too large: %d bytes (max %d)", len(s.Content), maxScriptContentBytes)
    }
    return nil
}
```

配套：`editors/index.tsx` 的 Textarea 可加软提示（可选）；API/PRODUCT 文档注明 timeout 语义（0=默认 300s）与 content 上限。

### 为什么这样改（架构一致性）

- 与既有 Task 校验模式一致（同文件同风格，已有 `TestTaskTimeoutValidation` 单测模式），与 `scripts.timeout INTEGER NOT NULL DEFAULT 300`（`internal/store/migrate.go:137`）的默认语义一致。
- 文档冲突提示：`PRODUCT.md` §41 Script 架构**未定义 content 上限与 timeout 边界**——256KiB 属**新增产品约束**，必须在文档中明确，否则就是“无据硬改”。
- 负值拒绝无冲突；`0=默认` 与 UI `min={0}`、Task 语义、DB 默认值三方一致。

### 影响面

- 公共 API 行为收紧：此前 200 的输入（负数 timeout、超限 content）改判 400；0 保留接受（若选择默认化，则存储值 0→300 变化可见于 UI/API 回读）。
- 无 schema/协议变化；前端正常表单不触发新拒绝（已 min=0），超限时需展示后端错误文案（现有通用错误组件可承载）。

### 是否需要用户重新确认

**必须重新确认**：对既有 200 输入改判 400 属公共 API 契约收紧；且 256KiB 上限数值与“0=默认是否落库为 300”均为产品决策。

### 回归验证方案（定向）

- 重跑：`P07_P08-11`（主）、`P07_P08-04`（既有字段往返）、`API-CONTRACT`，共 **3 条**。
- 新增边界用例：
  1. `timeout=-1/-5` POST/PUT → 400 且未落库、无 revision 变化；
  2. `timeout=0` → 200（按默认语义），回读值符合选定策略（0 或 300）；
  3. `content` 262144 / 262145 字节边界 → 200/400；含多字节 UTF-8 的边界按字节数判定；
  4. Update 路径同样拒绝，且失败不产生 revision/审计副作用。

### 风险与替代方案

- **替代 1**：上限不做硬校验，仅文档化“无上限”（则 `P07_P08-11` 期望须改为“接受”并更新用例台账）。取舍：零契约变化，但保留了无边界定义。
- **替代 2**：上限走 settings（类似 `max_log_bytes`）可配置。取舍：更灵活，扩大设置面与前端配置 UI 范围。
- **替代 3**：`0` 也拒绝（要求显式 timeout）。取舍：与 Task 语义/DB 默认/UI min=0 冲突，不推荐。
- 风险：`script.timeout` 存而不用的现状若不澄清，用户可能误以为脚本超时生效——建议在本次文档化中一并说明（或另立 change 把 script timeout 接入 task 默认值，属独立产品决策）。

---

## P2-5 修复方案：E2E-20261015-GROUP-EMPTY-NAME-AND-UPSERT-200

### 现状与根因

- `POST /api/groups`（`internal/hub/api/api.go:1066-1083`）无 name 校验，空名直接落库；schema `node_groups.name TEXT UNIQUE NOT NULL`（`internal/store/migrate.go:112-120`）允许第一个 `''`，第二个空名触发未归一化的 SQLite UNIQUE 错误（400 原文）。
- `PUT /api/groups/{id}`（`api.go:1099-1114`）直接调 `UpdateGroup`；`UpdateGroup`（`internal/store/sqlite_users_nodes.go:322-352`）执行 `UPDATE node_groups ... WHERE id=?` 后**不检查 RowsAffected**：

```go
if _, err := execer.ExecContext(ctx, `
    UPDATE node_groups SET name=?, description=?, type=?, label_key=?, label_value=? WHERE id=?`,
    g.Name, g.Description, g.Type, g.LabelKey, g.LabelValue, g.ID); err != nil {
    return err
}
if _, err := execer.ExecContext(ctx, `DELETE FROM node_group_members WHERE group_id = ?`, g.ID); err != nil { ... }
for _, m := range g.Members { /* INSERT members with 不存在的 group_id */ }
```

  → 0 行命中仍返回 200，且会为不存在的组写 `node_group_members` 孤儿行（该表无 FK，`migrate.go:122-126`），并让 `NodeManager.UpdateGroup`（`internal/hub/node.go:298-315`）在事务里推进无用 Desired Revision。
- 实测：`p-api/evidence/API-CONTRACT-required-fields.json`（PUT 不存在 id → 200；空名 POST → 200 落库）。对照：scripts/tasks/applications 均在 handler 层 400 `name required`，重名由 `validateUniqueName` 归一化（`definitions.go:400-426`）。

### 建议改动

1. handler `handleGroups` POST：`if strings.TrimSpace(g.Name) == "" { writeErr(400, "name required") }`（与 scripts/tasks/applications 对齐）。
2. handler `handleGroupByID` PUT：
   ```go
   if strings.TrimSpace(g.Name) == "" { writeErr(w, 400, "name required"); return }
   if _, err := s.store.GetGroup(r.Context(), id); err != nil { writeErr(w, 404, "group not found"); return }
   ```
   （或等价地由 store 层 `RowsAffected()==0 → sql.ErrNoRows` 让 handler 映射 404；两者择一，建议 store 层防御 + handler 预检并存）。
3. 重名归一化：`NodeManager.CreateGroup/UpdateGroup` 增加“忽略大小写/首尾空白、排除自身”的唯一性检查，返回 `"group name already exists: X"`（复用 `validateUniqueName` 思路，泛化或复制）。
4. store `UpdateGroup`：UPDATE 后 `RowsAffected()==0 → 返回 sql.ErrNoRows`，避免孤儿成员写入与无效 revision。

### 为什么这样改（架构一致性）

- 与其他四类资源的既有校验/状态码约定一致（该缺陷本身就是“不一致”指控）：`api.go:1151-1154 / 1256-1259 / 1588-1591`、`definitions.go:400-412`。
- 避免无效写推进 Desired State Revision（§67.1 权威源不应因无效写而前进）；修正后 `recordTargetChangeDetail` 只在真实变更时执行。
- 无文档冲突（文档未授权 upsert 语义）。

### 影响面

- 公共 API 契约：POST 空名 400（新拒绝）；PUT 不存在 id 由 200-upsert → 404；PUT 重名由原始 SQL 错误 → 400 归一化文案。无 schema 变化、无协议变化。
- 前端 groups 表单已有 name 必填，正常流程不变；异常路径错误提示更明确。
- 附带修复：不再产生孤儿 `node_group_members` 行与无效 revision。

### 是否需要用户重新确认

**必须重新确认**（公共 API 状态码语义变化：upsert 200→404、新增 400 校验）。

### 回归验证方案（定向）

- 重跑：`P06-02`（Static Group 新建）、`P09P11-10`（Task target group）、`ROUTE-09`（/groups/{id} 直达/错误空态）、`API-CONTRACT`，共 **4 条**（可加 `X12` 观察跨页收敛）。
- 新增边界用例：
  1. `POST {}` / `{"name":"   "}` → 400 `name required`，无落库；
  2. `PUT /api/groups/{不存在id}` → 404，group 数不变、`node_group_members` 无新行、revision 不前进；
  3. 重名（含大小写差异）→ 400 归一化文案；
  4. 正常 POST/PUT/GET/DELETE 回归（与 API-CONTRACT 现有断言一致）。

### 风险与替代方案

- **替代 1（保留 upsert 语义）**：PUT 作为幂等创建——需同时把 scripts/tasks/schedules/applications 全部改成 upsert 才自洽，违背现有 404 约定与既有用例，不推荐。
- **替代 2（只补 name 校验，不碰 PUT）**：不满足缺陷主体（upsert 200）且保留孤儿成员行，不推荐。
- 风险：若存在未知调用方依赖 upsert（仓库内前端不使用该语义），仅需在发布说明标注。

---

## P2-6 修复方案：E2E-20261015-SQL-ERROR-LEAK-ON-PUT-NOTFOUND

### 现状与根因

`PUT /api/schedules/{id}`（`internal/hub/api/api.go:1448-1452`）与 `PUT /api/applications/{id}`（`api.go:1713-1717`）把 manager 错误一律 400 + 原文：

```go
sc.ID = id
if err := s.schedules.Update(r.Context(), &sc); err != nil {
    writeErr(w, http.StatusBadRequest, err.Error())   // ← 外泄 "sql: no rows in result set"
    return
}
```

manager 先查存在性：`ScheduleManager.Update`（`internal/hub/schedule.go:85-89`）与 `AppManager.Update`（`internal/hub/application.go:71-75`）直接透传 `GetSchedule/GetApplication` 的 `sql.ErrNoRows`（`internal/store/sqlite_definitions.go:319-327`、`sqlite_artifacts_apps.go:141-158`）。实测（`API-CONTRACT-required-fields.json`）：两个端点 400，body `"sql: no rows in result set"`。

**同类潜在缺陷（本轮核对发现）**：`PUT /api/scripts/{id}`（`api.go:1215-1218` + `definitions.go:83-86`）与 `PUT /api/tasks/{id}`（`api.go:1304-1307` + `definitions.go:324-327`）在“body 合法 + 不存在 id”时同样会 400 + SQL 原文（空 body 时被 name 校验先挡下，所以 p-api 未观察到）。

### 建议改动

四个 PUT handler 统一先做 not-found 映射（`api.go` 已 import `errors`）：

```go
if err := s.schedules.Update(r.Context(), &sc); err != nil {
    if errors.Is(err, sql.ErrNoRows) {// 需要 import database/sql；或 manager 层包装 sentinel
        writeErr(w, http.StatusNotFound, "schedule not found")
        return
    }
    writeErr(w, http.StatusBadRequest, err.Error())
    return
}
```

更彻底的可选做法（建议不同批）：在 manager 层把存储 not-found 包装为 `store.ErrNotFound` 再统一映射，避免“是否 SQL 错误”的判断泄漏到 handler。

### 为什么这样改（架构一致性）

- 与同文件 GET 的 404 语义对齐（`api.go:1432-1436`、`1696-1700`）；不把底层实现细节（SQL 驱动错误文本）暴露给 API 消费方。
- 保持既有 400 校验错误兼容（name/task_id/字段校验文案不变）。
- 无文档冲突。

### 影响面

- 公共 API 契约：不存在 id 的 PUT 由 400 → 404（4 个端点）；错误体不再含 SQL 文本。无 schema/协议/前端影响（前端 toast 文案变友好）。

### 是否需要用户重新确认

**必须重新确认**（公共 API 状态码契约变化）。

### 回归验证方案（定向）

- 重跑：`API-CONTRACT`（主）、`P12P13-02`（schedule 编辑）、`P14P15-01`（application 编辑入口），共 **3 条**。
- 新增边界用例：4 资源 × {不存在 id + 合法 body → 404 且 body 无 `sql`/`no rows` 字样；存在 id → 200；非法字段 → 400 且文案保留}；`DELETE`/`GET` 行为不回退。

### 风险与替代方案

- **替代**：在 `internal/store` 层把 `GetScript/GetTask/GetSchedule/GetApplication` 的 `ErrNoRows` 统一包装为 `store.ErrNotFound`。取舍：语义更干净，但触点大（execution/node 等已有 `ErrNoRows` 分支，`gateway.go:211/228/235`、`hub/execution.go:364`），需要更大回归面，本期建议 handler 最小改。
- 风险：判定依赖 `errors.Is(err, sql.ErrNoRows)` 透传；若 manager 未来包装错误需用 `%w`（当前是直接 return，安全）。

---

## P3-7 修复方案：E2E-20261014-AUDIT-METHOD-FALLBACK-200

### 现状与根因

`internal/hub/api/api.go:1865-1893` 的 `handleAudit` 无方法分支，任何方法（GET/POST/PUT/DELETE/PATCH）都会执行查询并 `200 + list`；无写副作用（`P19-05` 不可变性 PASS 仍成立），但方法语义误导。对照：`handleSettings` 有 switch（`api.go:1897+`），全站其余 handler 默认 405。同类观察（**不在本 7 条范围**）：`/api/me`、`/metrics` 任意方法 200，`/api/logout` GET 亦可注销（`p-api` 的 `CONTRACT-ME-METRICS-METHOD-200`、`CONTRACT-LOGOUT-ANY-METHOD`）。

### 建议改动

```go
func (s *Server) handleAudit(w http.ResponseWriter, r *http.Request) {
    if r.Method != http.MethodGet {
        writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
        return
    }
    // ...现有查询逻辑不变...
}
```

### 为什么这样改（架构一致性）

- 与 REST 方法语义及同文件其它 handler 模式一致；数据面零变化。CORS 预检由 `withCORS`（`api.go:204-216`）先行处理，不受影响。
- 无文档冲突；`p-api` 将该行为定性为“低危、方法语义误导”。

### 影响面

- 公共 API：非 GET → 405 + `{"error":"method not allowed"}`（此前 200 + 审计列表）；前端仅用 GET，无影响。
- 无 schema/协议变化。

### 是否需要用户重新确认

**必须重新确认**（公共 API 方法语义变化；并且需一并决定是否同批处理 `/api/me`、`/metrics`、`/logout` 同类观察——三者不在已确认的 7 条内，属范围扩张，须显式确认或明确列为非目标）。

### 回归验证方案（定向）

- 重跑：`API-CONTRACT`（方法矩阵）、`P19-05`（审计不可变性）、`FL-07`（取消 → Audit 一致），共 **3 条**。
- 新增边界用例：GET 200 + `X-Total-Count` 保留；POST/PUT/DELETE/PATCH → 405 且不执行查询（可用 count 不变断言）；OPTIONS 仍 204（CORS）。

### 风险与替代方案

- **替代 1（仅文档化，不改）**：低危、无副作用；但契约测试会持续记录“方法语义误导”，且与全站 405 约定不一致。取舍：若本批追求最小改动面可采用，但需在 `PRODUCT.md`/API 文档显式说明。
- **替代 2（路由层统一 405 中间件）**：一次性覆盖 `/api/me`、`/metrics`、`/logout` 等同类端点。取舍：更系统，但触及鉴权中间件行为（logout 的 GET 有副作用，需单独确认语义），超出本缺陷范围。
- 风险：无（行为收紧且无写入路径）。

---

## 附：跨缺陷事项与非目标

1. **本方案未包含**（`repair_additional_unconfirmed_failures` / 其它观察项）：`OBS-20261008-EXECUTION-ZERO-EXIT-CODE`、`OBS-20261010-UI-LOGOUT-NOT-REVOKED`（早前已授权，另行实施）；`CONTRACT-ME-METRICS-METHOD-200`、`CONTRACT-LOGOUT-ANY-METHOD`、`CONTRACT-DELETE-IDEMPOTENT-200`、`CONTRACT-TRANSFER-NONPOST-403`、`CONTRACT-SESSION-KEYS-CAMEL`、`CONTRACT-X-TOTAL-COUNT-PARTIAL`、`AUTHZ-NODE-EXISTENCE-ORACLE`（P3 以下观察，建议单独决策，避免顺手扩大范围）。
2. **测试台账联动**：缺陷 4/5/6/7 修复后，`API-CONTRACT`、`P07_P08-11` 等用例的“期望值/断言”需同步更新（属 E2E artifact，不属产品源码）。
3. **统一实施顺序建议**：3（零风险止血） → 1、2（P0，需协议/schema 确认） → 4、5、6（API 契约收紧，需一次 Requirement Review 合并确认） → 7（连带同类端点取舍）。
4. **未执行项声明**：本任务仅产出本文件；未修改产品源码、未运行测试、未做 Git 操作、未访问远程环境。
