# P5 待决项落地报告：跨标签登出即时收敛 + CR-3 部署 journal 只读查询面（2026-10-18）

- 任务来源：`OBS-20261016-G1-X13-CONVERGENCE-SEMANTICS`（Root 裁决：实现跨标签即时收敛）与 CR-3 后续「部署 journal 只读查询面」；用户已授权全部实施，本批不做部署、不做 Git。
- 边界遵守：只改 `web/src/**` 与 `internal/**`；未改 `internal/agent/redact*.go`；未改 `.e2e/**` 状态文件（本报告为新增交付物）；未改 `docs/**`（文档同步见「后续批次」）；无 Git 写操作；未装依赖；未访问远程环境。
- 工作树既有未提交改动（`.e2e/observations.json`、`.gitignore` 等）属会话前状态，本次未触碰、未覆盖。

---

## 一、任务 1（前端）：跨标签登出即时收敛

### 1.1 现状

- 退出登录（`sign-out-dialog.tsx`）先 `POST /api/logout` 再 `auth.reset()` + 本标签跳 `/sign-in`；`auth.reset()` 只清本标签的 store / 内存 token / `localStorage`。
- 其它标签没有监听任何跨标签信号：`localStorage` 里的 `nodesteer_token` 虽被清空，但标签 B 的**内存会话（模块级 `authToken` + store）仍有效**，受保护页面不跳转、API 仍按旧 token 发出（X13 证据：`me_status` 仍可 200；被动 15s 窗口不跳，仅自身刷新才收敛）。
- 既有 `nodesteer-unauthorized`（401）事件只在**本标签**内收敛，同样不跨标签。

### 1.2 方案与取舍

**方案：`storage` 事件单一信号通道 + 收敛策略与信号解耦（两个小模块）。**

| 决策点 | 选择 | 理由 |
|---|---|---|
| 信号机制 | `localStorage` + `storage` 事件（未用 BroadcastChannel） | 与 token 持久化同一存储机制，无连接生命周期管理；信号值取「时间戳+随机数」，连续两次退出值必不同，`storage` 事件必触发；规范保证**发起标签自身不收到自己的事件**，天然满足「自身不重复跳转」 |
| 触发点 | `auth.reset()` 内部统一广播（`auth-store.ts:56`） | 覆盖全部生产失效路径：退出登录、`handleUnauthorized`（401）、`authenticated-layout` 的 `/me` 401；避免多处调用点遗漏。任务提示的「监听 POST /api/logout 成功与 reset()」由 reset 单点承担（吊销失败时本地会话同样已失效，也应让其它标签收敛） |
| 接收端 | 新模块 `installCrossTabSessionConvergence`（`session-convergence.ts`），`main.tsx:114` 安装 | 复用既有收敛路径 `convergeToSignIn()`（清 queryClient + `router.navigate('/sign-in', {redirect})`），与 401 路径行为一致，保留登录后回跳参数；未用 `window.location.assign` 整页刷新（避免额外加载，且收敛后内存态已彻底清空） |
| 未登录标签 | 接收端先查 store：无 `user` 且无 `accessToken` → 忽略信号 | 「未登录标签不误跳」；已收敛标签收到后续信号同样因无会话而幂等跳过 |
| 幂等 | 收敛即清 store，后续重复信号被守卫拦截 | 多标签同时退出 / 重复信号只收敛一次 |
| 回环 | 接收端收敛会经 reset 再次广播（回声），但其它标签此时已无会话 | 回声最多一轮即终止，不会无限回环 |

**新增信号键**：`nodesteer_session_invalidated`（值为一次性随机串，不作为状态读取，仅用于触发事件）。

### 1.3 改动文件:行

| 文件 | 位置 | 改动 |
|---|---|---|
| `web/src/lib/session-sync.ts` | 新增 1-34 | `SESSION_INVALIDATION_KEY`、`notifySessionInvalidated()`（写信号，存储不可用时静默降级）、`subscribeSessionInvalidated()`（过滤键的 storage 监听，返回退订） |
| `web/src/lib/session-convergence.ts` | 新增 1-22 | `installCrossTabSessionConvergence(onConverged)`：会话守卫 → `auth.reset()` → 回调（清缓存+跳转） |
| `web/src/stores/auth-store.ts` | `:3`、`:55-56` | `reset()` 末尾调用 `notifySessionInvalidated()`（`resetAccessToken` 未改：生产无调用点，保持现状） |
| `web/src/main.tsx` | `:15`、`:94-110`、`:112-120` | 抽出 `convergeToSignIn()`（原 `handleUnauthorized` 主体）；安装跨标签收敛；HMR dispose 同时退订 |
| `web/src/lib/session-sync.test.ts` | 新增 | 6 条浏览器测试（见 1.4） |

### 1.4 测试（vitest 浏览器，`--browser.headless`）

Vitest 浏览器模式无法真正开第二个标签页，按任务允许的方式用 **`StorageEvent` 注入**模拟「另一个标签写入 localStorage 后本标签收到的事件」；发起标签自身行为（不收到自己的事件）由浏览器规范保证，用「同页不派发事件」等价断言。测试文件：`web/src/lib/session-sync.test.ts`（6 条）：

1. 本标签退出（`reset`）会广播失效信号，连续两次信号值不同（信号写入契约）；
2. 另一标签退出后，本标签 **≤1s** 内清空会话（store + 内存 token + localStorage）并调用跳转回调（`expect.poll(..., {timeout: 1000})` + 实测耗时断言）；
3. 未登录标签收到信号不收敛、不误跳；
4. 同一标签自身退出不触发自身重复跳转；
5. 多标签同时退出 / 重复信号幂等：只收敛一次；
6. 其它 storage 键变化不触发收敛。

**反向验证（撤掉修复 → 测试失败，已完成并已还原）**：临时移除 `reset()` 中的广播调用、并把 `installCrossTabSessionConvergence` 的收敛体置空后运行同文件：
`Test Files 1 failed | Tests 4 failed | 2 passed (6)` —— 失败项为 1/2/4/5（信号未写、接收端不收敛），通过项为守卫类用例 3/6。还原后 6/6 全绿。

### 1.5 真实命令结果（与全仓验证合并见第三节）

- `npx vitest run --browser.headless src/lib/session-sync.test.ts` → ✅ 1 file / 6 tests 全通过。
- 关联既有测试（`auth-store.test.ts`、`sign-out-dialog.test.tsx`）→ ✅ 3 files / 16 tests 全通过。

### 1.6 对外可见行为变化（逐条）

1. **任一标签退出（含退出登录、401 会话失效）后，其它同源标签立即清空本地会话并跳转登录页**（storage 事件到达即同步处理；任务目标 ≤1s）。原行为：其它标签在被动窗口内不跳转、内存会话仍可用，仅自身刷新收敛。
2. 其它标签跳转保留 `redirect=<原页面>` 参数（与既有 401 收敛一致）。
3. 未登录标签收到信号不跳转；同标签自身不重复跳转；多标签同时退出幂等。
4. 新增 localStorage 键 `nodesteer_session_invalidated`（一次性随机值，不承载会话状态；旧版本忽略该键，无兼容要求）。
5. 收敛时清理 TanStack Query 缓存（与既有 401 路径一致），避免已登出标签残留展示受保护数据。

### 1.7 未验证项 / 限制

- **真实双标签浏览器端到端复测未执行**（本批不做部署）：storage 事件的跨文档投递延迟由浏览器保证（通常 <50ms），单页测试只能验证「事件到达后同步收敛 + ≤1s 断言」；建议部署新构建后按 X13 场景双标签复测（含内存会话 401 断言）。
- localStorage 写入失败（极少数隐私模式配置）时信号无法发出：本标签退出正常，其它标签不收敛（代码已注释说明）。未引入 BroadcastChannel 双通道（增加面且单页同样无法真实投递）。
- 「其它标签替换 token（非登出）」的被动收敛不在本次范围（原观测 `OBS-LOCALSTORAGE-TOKEN-NOT-CROSS-TAB` 的另一半），保持现状。

---

## 二、任务 2（Go/Agent）：CR-3 部署 journal 只读查询面

### 2.1 现状与方案调研（按任务给定优先级）

**现状**：CR-3 已把四类不可重放行落终态（`superseded` / `stale_binding` / `legacy_unbound` / `prepare_interrupted`），但只有恢复期日志可见；journal 无任何查询面（`ListAllDeployments` 无调用方），运维无法随时审计。

**① 心跳/状态 payload 透传核查（结论：需要 schema 迁移，降级）**：

- Agent 心跳 `protocol.HeartbeatPayload` 只有 `NodeID/GlobalRev/RunningExecs`；`RunningExecs` 在**全仓无消费方**（`grep` 仅定义处命中）。
- Hub `gateway.handleHeartbeat`（`internal/hub/gateway.go:427-444`）只消费 `NodeID`（更新 `last_seen`/在线状态）与 `GlobalRev`（sync 状态），**payload 其余字段不落库**。
- Hub `nodes` 表（`internal/store/migrate.go:92-110`）只有固定列 + `inventory_json/capabilities_json/labels_json`；无通用 metadata 列。要把 `deployments_summary` 透传到 UI 必须新增列 → **迁移 v10**，或仅存 Hub 内存（Hub 重启丢失、仍需 API+UI 改造）。两者均违背「无 schema 迁移」前提。
- 前端 `Node` 类型（`web/src/lib/api.ts`）亦无任何心跳附加字段。

→ 按任务优先级规则，**降级为 ③：Agent 写只读文件 `<data_dir>/deployments-summary.json`（恢复后刷新）**。

**③ 实现要点**：不改 Hub、不改协议、不改 schema、不新增持久化结构；只读快照 + 原子替换（先写 `.tmp` 再 rename，读方不会看到半截文件）；成功写入不打日志（保持 CR-3 既有「重启不重复告警」的日志契约），失败记 ERROR 且不影响恢复主流程。

### 2.2 输出内容（≥ 任务要求）

```json
{
  "generated_at": "2026-10-18T06:41:34Z",
  "done_by_kind": {
    "superseded": 1,
    "stale_binding": 1,
    "legacy_unbound": 1,
    "prepare_interrupted": 1,
    "rolled_back": 1,
    "prepare_failed": 1,
    "unknown": 1
  },
  "active": [
    {
      "id": "missing-app",
      "application_id": "missing-app-def",
      "phase": "ROLLING_BACK",
      "started_at": "2026-10-18T06:41:29.958090034Z"
    }
  ]
}
```

- `done_by_kind`：**非 `deployed` 的 DONE 行按 `completed_kind` 计数**（含 CR-3 四类与 `rolled_back`/`prepare_failed`；旧行缺成因归一为 `unknown`；成功部署不计入）。计数用 SQL `GROUP BY` 全量统计，不受 `ListAllDeployments` 的 LIMIT 100 影响。
- `active`：**仍活动行 id / 应用 / 阶段**（+ `started_at` 便于判断悬挂时长），取自 `GetActiveDeployments`（无 LIMIT）。
- `generated_at`：快照时间（RFC3339 UTC）。
- 无任何行时也生成文件：空计数 + `active: []`（确定空态，非 null）。

### 2.3 改动文件:行

| 文件 | 位置 | 改动 |
|---|---|---|
| `internal/agent/deployment_summary.go` | 新增 1-98 | 汇总结构与契约注释、`buildDeploymentsSummary`（计数 + 活动行）、`writeDeploymentsSummary`（原子写；失败仅记日志） |
| `internal/agent/store.go` | `:849-869` | 新增只读查询 `CountClosedDeploymentsByKind`（`phase='DONE' AND completed_kind <> 'deployed'` GROUP BY；不改变既有查询） |
| `internal/agent/application.go` | `:861-863` | `RecoverDeployments` 末尾调用 `writeDeploymentsSummary`（恢复后刷新） |
| `internal/agent/deployment_summary_test.go` | 新增 | 5 条单测（见 2.4） |

**零改动确认**：`internal/agent/store.go` 既有方法、`internal/protocol/**`、`internal/hub/**`、`web/**` 均未改（心跳、schema、UI 无变化）。

### 2.4 测试（Go 单测，真实 SQLite + 真实文件系统）

1. `TestRecoveryWritesDeploymentsSummary`：一次恢复同时覆盖四类关闭行 + 历史终态（`rolled_back`/`prepare_failed`/缺成因 DONE/成功 DONE）+ 一条保持活动行（应用定义缺失）；断言计数逐项、成功部署不计入、`active` 行的 id/应用/阶段/`started_at`、`generated_at` 可解析。
2. `TestDeploymentsSummaryEmptyState`：无 journal 行时生成空态文件（空 map + 空数组，非 null）。
3. `TestDeploymentsSummaryRefreshedOnEachRecovery`：第二次恢复覆盖刷新，反映第一次之后新增的终态行。
4. `TestDeploymentsSummaryWriteFailureDoesNotBreakRecovery`：用同名目录占位使 rename 失败 → 恢复照常关闭行、记录 `replace deployments summary failed` 错误、清理 `.tmp`、不重放。
5. `TestCountClosedDeploymentsByKind`：SQL 计数语义（排除 deployed 与活动行、保留空成因键）。

### 2.5 真实命令结果

- `go test ./internal/agent/ -count=1 -run 'DeploymentsSummary|CountClosedDeployments'` → ✅ `ok`（新增 5 例全过；样例输出见 2.2）。
- 全量结果见第三节。

### 2.6 对外可见行为变化（逐条）

1. **Agent 数据目录新增只读文件 `<data_dir>/deployments-summary.json`**，每次启动恢复（`RecoverDeployments`）完成后原子刷新；内容为 journal 的「非 deployed 终态按成因计数 + 仍活动行 id/应用/阶段」。
2. Agent 日志新增两种**失败**输出（成功写入无日志）：
   - `build deployments summary failed` / `write deployments summary failed` / `replace deployments summary failed`（Error；恢复流程继续，下次恢复重试）。
3. Hub、心跳协议、SQLite schema、前端 UI、既有 journal 行状态与恢复语义**均无变化**；`deployments-summary.json` 是恢复时刻快照，启动后新发起的部署不会实时刷新（正常在途部署以 Hub UI 为准）。

### 2.7 未验证项 / 限制

- 只读文件在真实生产 Agent 数据目录上的落盘未演练（本批不做部署）；单测使用真实 SQLite + 真实文件系统覆盖等价路径。
- 快照仅在恢复后刷新；若产品要求「运行期实时」，需在部署完成路径追加刷新（属新批次）。

---

## 三、真实命令结果（最终冻结状态，主 Agent 亲自执行）

| 命令 | 结果 |
|---|---|
| `go build ./...` | ✅ `exit=0`（无输出） |
| `go vet ./...` | ✅ `exit=0`（无输出） |
| `go test ./internal/... -count=1` | ✅ `exit=0`，全部 `ok`：agent 39.3s、agent/condition、agent/connection、agent/execution、agent/host、agent/scheduler、hub、hub/agentbundle、hub/api、hub/auth、hubserver、store（metrics/models/protocol 无测试文件） |
| `gofmt -l internal/` | ✅ 无文件列出（空输出） |
| `cd web && npx tsc -b` | ✅ `exit=0` |
| `cd web && npm run lint` | ✅ `exit=0`，`0 errors / 3 warnings`；3 条 warning 均为既有文件（`features/shared/data-table.tsx`、`features/shared/ui.tsx`），非本次改动 |
| `cd web && npx vitest run --browser.headless` | ⚠️→✅ 第一次运行 1 file failed + 1 unhandled error（`cookies.test.ts` 动态导入 fetch 失败、`cron/schedule-payload.test.ts` iframe 连接失败，均与本批改动无关）；单跑这两文件 ✅ 通过；**重跑全量 ✅ 48 files / 228 tests 全部通过**。判定为浏览器 provider 偶发（import/iframe 连接）而非代码缺陷 |
| 定向（新用例） | ✅ `npx vitest run --browser.headless src/lib/session-sync.test.ts`（6/6）；✅ `go test ./internal/agent/ -count=1 -run 'DeploymentsSummary|CountClosedDeployments'` |
| 反向验证（任务 1） | ✅ 撤掉修复后同文件 `4 failed | 2 passed`（失败项即收敛链路用例）；已还原并复跑全绿 |

---

## 四、需要后续批次的点（本批未做，如实列出）

1. **真实双标签 E2E 复测**：部署新前端构建后，按 X13 场景在两个真实标签页复测「退出 → 另一标签 ≤1s 清会话 + 跳转 + 内存会话 401」；本批未部署、未改 `.e2e` 状态文件，`OBS-20261016-G1-X13-CONVERGENCE-SEMANTICS` 的状态更新由 Root 裁决批次处理。
2. **Hub/UI 查询面（若产品要 UI 展示）**：需 `nodes` 表迁移（v10）+ 心跳协议只增字段 + 节点详情 UI；本次按「无 schema 迁移」优先级规则降级为 Agent 本地文件，未实现 Hub 侧。
3. **文档同步（超出本批可改范围 `web/src`、`internal`）**：`docs/ARCHITECTURE.md` §28 与 `docs/PRODUCT.md` §47 的 Agent 本地目录清单需加入 `deployments-summary.json`；如需，再补跨标签登出语义与 CR-3 查询面的文档化。
4. **运行期实时刷新（可选增强）**：如需 `deployments-summary.json` 反映启动后新部署，需在部署终态写入路径追加刷新。
5. **token 被其它标签替换的被动收敛**（原观测 `OBS-LOCALSTORAGE-TOKEN-NOT-CROSS-TAB` 的另一半）：不在「退出/会话失效」范围，未实现。
6. **`resetAccessToken` 未广播**：该方法当前无生产调用点（仅测试），保持现状；若未来用于生产路径需一并接入信号。

---

## 五、结论

- 任务 1：✅ 实现 + 浏览器测试 + 反向验证通过；真实双标签 E2E 待部署批次复测。
- 任务 2：✅ 实现（方案 ③，含 ① 的完整降级论证）+ Go 单测；Hub/UI 查询面与文档同步列为后续批次。
- 全部规定命令已真实执行；失败/未验证项已如实列出，未修改 `.e2e` 状态文件、未做 Git 操作、未部署。
