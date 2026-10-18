# NodeSteer E2E 待决事项清单（2026-10-16）

> 用途：本轮 E2E 全量测试与修复实施过程中，凡**需要产品/用户决策**或**结论不确定**的事项，统一记录在此，供后续集中裁决。
> 状态源：`run-state.json`、`repair/feature-inventory.json`、`failures.json`、`observations.json`。
> 原则：不确定的不擅自定性，不粉饰、不代码化猜测。

## A. 需要你决定的产品/契约问题

| # | 事项 | 现状与证据 | 建议默认 | 影响 |
|---|---|---|---|---|
| A1 | `/api/logout` 收紧为 **POST-only** 是本轮修复的范围决策（方案原列"需单独确认"） | 修复已实施；仓库内调用方均用 POST、前端不调用该端点 | 接受（无既有行为破坏） | 若台账期望 GET 也注销，需回退该处 |
| A2 | **`script.timeout` 是否应参与执行** | 现状：Script 定义有该字段但执行不使用（本轮按要求只加校验、未改语义） | 明确文档化"暂不参与"，或另立需求让其生效 | 影响脚本任务的超时行为语义 |
| A3 | **content 上限 256 KiB** 的数值 | 本轮新增校验；来源是"Task 侧已有对照实现"的外推 | 保留 256 KiB 或按产品预期调整 | 既有超限脚本将从此被拒 |
| A4 | **`ROLLBACK_FAILED` 新 phase** 是否进入产品可见语义 | 本轮修复新增，用于区分"回滚完成但失败"与"回滚中崩溃" | 接受（否则 `ROLLING_BACK` 会永久悬挂） | 影响 UI/审计/文档 |
| A5 | **`P2-6` 未全面泛化**：`err.Error()` 原文仍可能出现在其他错误分支 | 本轮封闭了全部已观察到的 SQL 原文外泄路径（均为 `ErrNoRows`） | 另批做 manager 层错误分类（方案文件已建议 `store.ErrNotFound`） | 残留信息外泄面 |
| A6 | **`/api/me`、`/metrics`** 同批收紧为 GET-only | 与 `/api/audit` 同类；已实施 | 接受 | 若外部集成依赖其他方法需评估 |
| A7 | OIDC/SSO 4 例复测需要 **(a) 启用 OIDC 的配置变更 + (b) 可达测试 IdP** | 已记为 BLOCKED；`P01-08/09/10`、`X17` | 需要时单独授权后再测 | 一期范围外，但 baseline 曾 PASS 过 P01-09/10 |
| A8 | Host 集成族 4 例（`SYS-54/55`、`AR-31`、`INV-14`）能否放开安全策略 | 阻塞根因：`/proc/1/root` 在 docker-default AppArmor 下 Permission denied（FAIL-ENV-HOST-001） | 维持 BLOCKED，或提供 pinned policy 后另批 | 影响 Host Adapter 类能力验证 |
| A9 | `SYS-07` Full Resync 分支**算术不可达**（`since>0 && (current-since)>window`，本环境 `current≈2652`、`window=5000`） | 已给两次真实边界观测（`since=1`/`since=0`，均 `full_resync=false`+`snapshot=true`） | 接受为环境边界；如需真测须允许改全局 `changelog_window` 或推进 revision>2349 | 该用例无法在现配置下转 PASS |
| A10 | `TABLE-AUDIT-ERROR`/`TABLE-USERS-ERROR` 的"真空态"**产品性不可达**（登录即写审计、管理员恒存在） | 已按"1 项 design-unreachable"记 PASS | 接受该口径 | 口径一致性 |
| A11 | `OBS-20261014-APP-NODES-NULL-SHAPE`：`GET /api/applications/{id}/nodes` 返回 `null` 而非 `[]` | 两次独立再现（SYS-12 attempt04、batch04） | 需产品确认是否规范化为 `[]` | 可能影响调用方解析 |
| A12 | 注册 token/凭证**无 TTL**（`token "过期"` 在产品中不存在） | `gateway.go:351`、`node.go:154`；SYS-01 的"过期"维度按 N/A | 如属有意设计，建议文档化 | 影响安全评估口径 |

## B. 已确认缺陷（等待修复范围裁决，部分已修）

| ID | 优先级 | 状态 |
|---|---|---|
| `E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW` | P0 | **本轮已实施修复**（协议只增不改 + Agent TTL 优先），待回归确认 |
| `E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE` | P0 | **本轮已实施修复**（superseded 判定 + 版本/路径绑定 + `ROLLBACK_FAILED`），待回归 |
| `E2E-20261015-COMMITSYNC-FAILURE-DOUBLE-UNLOCK-PANIC` | P1 | **本轮已实施修复**，待回归 |
| `E2E-20261014-TIMEOUT-CONTENT-VALIDATION-GAP` | P1 | **本轮已实施修复**，待回归 |
| `E2E-20261015-GROUP-EMPTY-NAME-AND-UPSERT-200` | P2 | **本轮已实施修复**，待回归 |
| `E2E-20261015-SQL-ERROR-LEAK-ON-PUT-NOTFOUND` | P2 | **本轮已实施修复**（部分泛化，见 A5），待回归 |
| `E2E-20261014-AUDIT-METHOD-FALLBACK-200` | P3 | **本轮已实施修复**，待回归 |
| `E2E-20261010-UI-LOGOUT-NOT-REVOKED` / `OBS-20261008-EXECUTION-ZERO-EXIT-CODE` | P1/P2 | 早前授权修复范围，本轮未动 |
| 既有 FAIL 用例 4 条（`G03-10`、`P03-10`、`P17P18-07`、`TABLE-TASKS-ERROR`） | — | 仍 FAIL，未列入本轮 7 条 |

## C. 待复核候选（未定性）

| ID | 事项 | 建议动作 |
|---|---|---|
| `OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST` | 对 API 创建的 Application，编辑器"保存"点击 3 次不发请求 | 独立二次复核后再决定是否立项 |
| `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED` | 部分编辑器 pending 未禁用 → 重复 POST（疑只修了 Task 编辑器） | 逐编辑器复核是否有实际副作用 |
| `OBS-20261014-AR26-ROLLBACK-UNCLOSED` | 证据原文 `rollback failed`、`ROLLING_BACK` 未闭合 | 与 P0-2 修复一并复核 |
| `OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED` | 间隔 0/-1 仅提示、保存仍可用 | 确认服务端是否拒绝 |
| `OBS-20261014-PAST-ONETIME-SAVE-ENABLED` | 过去时间 One-Time 无内联错误、保存可用 | 同上 |
| `OBS-20261014-ARTIFACT-DOWNLOAD-NO-READ-TIMEOUT` | Agent 下载无读/idle 超时，`.tmp(0B)` >24s 不清理 | 健壮性项，评估是否立项 |
| `OBS-20261014-ARTIFACT-CONCURRENT-PREFETCH-NO-DEDUP` | 同 SHA 并发预取重复下载 | 观测项 |
| `OBS-20261015-CONCURRENT-DUPLICATE-PREFETCH-ENOENT` | 并发预取输方 rename ENOENT + WARN | 同上 |
| `OBS-20261014-NAV-ROLE-UNFILTERED` | 侧边栏对 operator/viewer 仍显示"用户"入口 | UI 体验项 |
| `OBS-20261014-TRANSFER-STATUS-BADGE-NOT-LOCALIZED` / `OBS-20261014-STATUSBADGE-MAINTENANCE-DISABLED-NO-ZH` | 状态徽标英文枚举未本地化 | UI 文案项 |
| `OBS-20261015-API-CONTRACT-DEVIATIONS-LOW` | 8 条低危契约偏差（方法开关/幂等/键名/`X-Total-Count`/节点存在性 oracle） | 批量评估 |
| `OBS-20261012-APP-NODES-NULL-SHAPE` | 见 A11 | 见 A11 |

## D. 环境/流程事项（非产品）

| # | 事项 | 现状与建议 |
|---|---|---|
| D1 | **libvirt 域名 ≠ native 编号**（`nodesteer-agent-3` = `.204` = native-1） | 曾导致误重启 native-1；后续一律按 MAC/IP 核实目标域 |
| D2 | **repair 单元 `enabled=disabled`** | VM 重启后不会自动拉起，造成节点掉线；重启类操作后必须显式恢复并复核 4 节点 |
| D3 | **1 GiB 气球内存的 VM 上做 tmpfs ENOSPC 测试** | 曾致 native-3 内存压力、sshd 无响应；需先 `virsh setmem` 升气球或降低填充量 |
| D4 | **子 Agent 深度上限 = 1** | 批次内不能再派并行子任务；由 Root 直接铺平批次 |
| D5 | **子 Agent 反复"启动后台运行后提前结束回合"** | 已在指令中强制"一个回合内完成 + 带上限轮询"；Root 遇此先查现场再接手 |
| D6 | **`agent-browser close --all` 会误关并行批次会话** | 一律只用 `--session <自己的>` |
| D7 | **浏览器会话与 chromium 进程残留**（多个历史会话、约 185 个进程） | 建议在无并行批次时统一清理 |
| D8 | **`node_modules/` 与多个新源文件为 untracked** | 交付前需明确 `.gitignore` 与 `git add` 清单（今日新增 `sync_validation.go` 等 4 个文件） |
| D9 | **注册令牌曾在终端回显**；管理会话令牌曾在只读勘察中打印 | 用户已决定不轮换注册令牌；会话令牌随 Hub 重启失效；证据目录扫描 CLEAN |
| D10 | **`openspec/changes/repair-e2e-confirmed-defects` 仍 21/27** | 第 5 节（验收/清理/Code Review/Archive）全未勾选；需在修复回归通过后再评估 |
| D11 | **75 个 PARTIAL 的剩余缺口** | 主要为环境受限（Docker/Compose、受限角色会话、Host `/host`、跨构建版本号、长时停机）；逐条理由在台账 `remaining_dimensions` |

## E. 本轮修复实施摘要（供追溯）

- 方案：`repair-plan-20261016.md`；实施报告：`implementation-report-20261016.md`
- 改动源码：`internal/{protocol,hub,agent,store}` 共 10 个文件；新增测试 4 个文件
- Root 复核：`go build ./...`、`go vet ./...`、`go test ./internal/...`、`gofmt -l` 全部通过（由 Root 亲自执行）
- 部署与定向回归：见 `qa-resume-20261016-verify/`（进行中）

---

## F. 75 个 PARTIAL 的收口分类（2026-10-16 新增，来自 partial-closeout-plan-20261016.md）

**三类计数**：C1 可补 26 / C2 需独占或需授权 40 / C3 环境受限接受类 9

**C1 建议批次（3 批，均在 5 路并行下安全）**：
- `RG-20261016`（6 条）：INV-05、INV-11、SYS-28、E2E-10、P07_P08-11、G04-03 —— 与在飞的 `qa-resume-20261016-verify` 合并
- `APP-UI-20261016`（10 条）：P14P15-06/07/08/13/14/15/16/18、FL-10、FL-11（自有应用真实部署/升级链）
- `WEB-GAP-20261016`（10 条）：X04、X20、P16-02/04、P05B-08、P03-02、P05B-10、P22-07/09/10

**C2（40 条）需授权的典型**：重启 Hub 段、改全局设置、替换二进制、创建持久化账号、放开 Host 安全策略、跨构建版本号等。

### F1. 建议转 FAIL / 新立缺陷（9 条，**待你裁决，未入台账**）

| 用例 | 方案意见 | 说明 |
|---|---|---|
| `G04-01` | 强建议转 FAIL | 编辑器 pending 未禁用致重复 POST（疑只修了 Task 编辑器） |
| `X08` | 强建议转 FAIL | 凭据撤销/失败重试/取消编辑 Revision/Audit/重复等分支 |
| `X14` | 强建议转 FAIL | 跨过滤/排序/分页/列表变更的选择语义与末页删除语义 |
| `SYS-40` | 强建议转 FAIL | per-attempt 审计缺失 |
| `P22-10` | 强建议转 FAIL | 目标失败隔离的 FAILED 侧始终不可达（host001 只接包不回报、Hub 无投递超时） |
| `X16` | 强建议转 FAIL | Revision/Audit/error 遮蔽与凭据跨交付面 |
| `G04-04`、`X19` | 待产品裁决 | Enter 不提交 / Escape 焦点未返回触发按钮 |
| `P12P13-05` | 待产品裁决 | 过去时间 One-Time 无内联错误、保存可用 |

> 我（Root）的处置：**暂不改台账状态**，先由独立核验子 Agent 复核证据（见 `status-upgrade-verification-20261016.md`），确认后再与你一并裁决；避免仅凭批次自述改状态。

### F2. 建议升 PASS（6+2 条，核验中）
`X06`、`E2E-15`、`FL-15`、`P04-07`、`FL-12`、`P17P18-04`（另 `P14P15-07`、`P14P15-16` 需先补测）

---

## G. 独立代码评审发现（2026-10-16，code-review-20261016.md）

评审结论：**0 blocker / 2 major / 5 minor / 5 nit**，可进入回归验收。

### G1. 必须闭环（交付前）
- **CR-2 [major]** 新 phase `ROLLBACK_FAILED` 与 P1-4 的 256 KiB / timeout 语义**未同步文档**：需更新 `docs/ARCHITECTURE.md` §52（Phase 枚举）与 `docs/PRODUCT.md` §42。**这是交付前必做项**（评审引用 `docs/ARCHITECTURE.md:1203-1224`）。

### G2. 需你裁决的语义（影响 P0-2 修复正确性）
- **CR-1 [major]** superseded 判定**只看 `phase='DONE'`**，而 `prepareFailed`（准备失败、未做任何替换）与"回滚成功"也会写 DONE → 可能把**可恢复的半完成行**误判为"已被成功部署取代"而**永久跳过重放**（`internal/agent/application.go:808-816`）。
  - 我的建议（待你确认）：把 superseded 谓词收窄为"**成功完成且版本更新**的 DONE 行"（排除 prepareFailed / 回滚成功这两类 DONE），并为这两类补边界测试。这样既不误覆盖（原缺陷），也不误跳过（CR-1）。
  - 若不裁决：回归仍可继续，但该场景（半完成 + 有更晚 DONE 行）会停留在"永久跳过"，属已知限制。

### G3. 运维可观测性（建议但非必须）
- **CR-3 [minor]** legacy 缺绑定 / 绑定失效 / 被取代 / PREPARING 四类行**只记日志、不落终态**，会永久留在 active 集合、每次重启重复告警，缺少清理出口（`internal/agent/application.go:743-761`）。建议增加一个明确终态或提供一个只读的"待处理部署"查询面。

### G4. 我的排期（不打断当前回归）
1. 等 `qa-resume-20261016-verify` 回归结论回来 → 汇入台账
2. 再执行 **CR-1 收窄 + CR-2 文档同步 + 边界测试** → 重建 → **只重跑 AR-32 家族的 8 条**
3. CR-3 若你同意，与 2 同批做；否则记入长期待办

---

## H. CR-1 已关闭（2026-10-16）

**决定**：按方案 A 收窄 superseded 谓词。**已实施并验证**：
- 谓词现要求 `phase=DONE && completed_kind='deployed'`；journal 新增 `completed_kind`（`deployed`/`rolled_back`/`prepare_failed`），四处 DONE 写入改走单条原子 UPDATE
- **双向区分性验证通过**：① 成功部署仍被更晚成功部署取代（原防护保留）② `prepare_failed`/`rolled_back`/成因未知 的 DONE **不再误跳过**（三例均重放并收敛）③ 对照：定义漂移仍 `binding mismatch; skip replay`（未放松绑定防护）
- 历史行审计：候选风险**不可达**（涉及的测试应用已删，恢复在绑定校验前即跳过；两次重启行相位一致）→ 决定不清理
- 构建切换：`dddee4f0-e2e-repair-20261016-cr1`（hub unbundled 与上版逐字节相同，CR-1 为纯 agent 侧改动）

**仍未决**：CR-3（四类悬挂行是否加终态/只读查询面，建议同批做，默认不动）。

---

## I. G7 裁决简报结论（2026-10-17，g7-decision-brief-20261017.md）

**已结案**：`G04-01`（已 FAIL + 已立缺陷；修复范围由证据定为 **Schedule/Script/Group/Application 四个编辑器**，tasks 已有参照实现）

**仍待决（3 个裁决点，均"一句话可定"）**
1. `G04-04` + `X19`：Escape 关闭确认框后**焦点落 BODY**（取消与确认都丢焦点）→ 建议**立项小改**：在共享 `ConfirmDialog` 里回焦触发元素（**单组件覆盖 8 处调用点，无写风险**）
2. `G04-04`：Enter 不提交（编辑器非 `<form>`，0 POST）→ 建议**接受为设计 + 文档化**
3. `P12P13-05`：过去时间 One-Time 可保存且无内联提示 → 建议**接受为设计**，并文档化 `misfire_policy` 语义（run_once=立即补跑 / skip=死调度）+ 补 1 条空 `run_at` 服务端 400 的端到端断言
4. `X08`：是否把"慢响应重复点击"口径**扩到创建表单**（其余维度已被核验证实有证据，原 remaining 措辞过期）→ 建议采用**广义口径**（随 G04-01 一起处理）

**两个附带发现（建议复测时一并核）**
- `schedule.go:39` / `application.go:44` **无名称去重**（application 的 `unit_name` 由名称派生）→ 双击保存可能产生重复定义；建议断言真实 POST 计数并复核严重性
- `G04-01` 证据里 `post_lines` 两行时间戳几乎相同，**疑为同一请求的请求行/响应行** → 建议用 CDP/服务端审计计数独立复核（**结论方向不变**：源码已证明无 in-flight 守卫且按钮无 pending 禁用）

---

## J. 兼容性前提已取消（2026-10-17，用户指示）

用户明确：**不需要与历史版本考虑兼容性**。据此：
- **A1/A2/A6 等因"跨版本兼容/同版本发布"而生的前提不再需要裁决**
- **P0-1 已改为最优解**：`validRemoteState` 只认 `ttl_remaining_sec`（缺失或 ≤0 → fail-closed），**跨时钟比较路径已从代码中彻底删除**；文档同步为"不承诺跨版本兼容"
- **混版本兼容验证**（此前作为用户要求项）**已不再是验收条件**；相关的 `MIXED-VERSION-newHub-oldAgent` 观测保留为历史记录
- 该缺陷 `E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW` 状态更新为 **FIXED_AND_VERIFIED**（受控探针 ±1h/epoch0 下 ttl>0 仍 SUCCESS ⇒ 判定不读时钟）
- 构建切换：`dddee4f0-e2e-repair-20261017-p01`（hub `029f3f4e…`、agent `449af1d5…`），备份可回滚
