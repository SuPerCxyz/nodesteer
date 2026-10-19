# P18 E2E 契约跟随报告：FAILED 传输不可取消（2026-10-18）

- 任务来源：`qa-resume-20261018-final2-verify/regression-results.json` 中 `P12-E2E-SUITE-FULL` 的差异归因 —— `P22-02` / `FULL-06` 仍断言 P16 终态冻结前的旧行为（FAILED 可取消→CANCELED），属待更新的测试资产，非产品回归。
- 新契约（P16 终态冻结）：传输进入终态（SUCCESS/FAILED/CANCELED）后不可取消；`POST /api/transfers/{id}/cancel` 对 SUCCESS/FAILED 返回 **400 `transfer already finished: <STATUS>`** 且不改写状态（`internal/hub/file_transfer.go:987-1024`、`internal/hub/api/api.go:1214-1225`）；前端 `/transfers` 对终态行隐藏取消入口，FAILED 行仅显示「重试」（`web/src/features/transfers/index.tsx:202-205,224-231`），Retry 是唯一恢复路径。契约依据：`docs/PRODUCT.md §46`（方法/状态码语义）。
- 修复边界：只改 `web/e2e/**`（2 个 spec 文件）；未改产品代码（`web/src/**`、Go）、未改 `.e2e/**` 状态文件、无 Git 操作、未装仓库依赖。

## 一、改动清单

### 1. P22-02（`web/e2e/tests/p22-transfers.spec.ts:13-82`）

- 旧：mock FAILED 传输 → 行菜单断言「取消传输」可见 → 点击 → 断言刷新为 CANCELED。
- 新（`P22-02 [UI_MOCK] FAILED 传输行仅提供重试、无取消入口`）：
  - 行菜单断言「重试」可见，且「取消传输」`toHaveCount(0)`（FAILED 行无取消入口）；
  - 点击「重试」→ 断言确实发出 1 次 `POST /api/transfers/{id}/retry`（mock 计数），且全程 **0 次 cancel 请求**；
  - cancel route mock 改为返回 400 `transfer already finished: FAILED`（防御性：若 UI 仍发 cancel 请求，会被 `cancelCalls` 计数暴露）。
- 注释同步更新：引用 P16 终态冻结 Go 测试 `TestFileTransferExplicitActionsOnTerminal`（旧注释引用的 FAIL-M-001「FAILED 可取消→节点删除解阻」场景已随契约变更失效）。

### 2. FULL-06（`web/e2e/tests/full-feature-ui.spec.ts:281,341-359`）

- 旧末段：UI 菜单「重试」→ 断言仍 FAILED → UI 菜单「取消传输」→ 断言 CANCELED。
- 新末段（中继目标前置逻辑与其余流程保持不变）：
  - 搜索 failedId、等待行状态徽标为「失败」后再取 UI 断言（终态稳定，规避 UI 每 3s 刷新与 API 状态的竞态）；
  - UI：行菜单仅「重试」，无「取消传输」（`toHaveCount(0)`）；
  - API：对 FAILED 调 `POST /api/transfers/{id}/cancel` → **400** 且错误体含 `transfer already finished`，状态保持 FAILED（终态不被改写）；
  - 恢复路径：UI 菜单「重试」后源文件仍缺失 → 再次终态化为 FAILED。
- 标题更新：`...失败重试取消` → `...失败重试与终态不可取消`。

## 二、实跑验证（repair 环境）

- 环境：`BASE_URL=http://192.168.100.209:18080`；admin 凭据取自 0600 文件（未明文落盘；产物经脱敏后扫描 admin 口令/注册令牌 **0 命中**）。
- 运行器：`~/p18-verify/run-p18.py`（沿用 final2/P12 运行器：per-file JSON、密钥脱敏、资源台账）。
- 节点：源 native-1 `91f44866…`；FULL-06 中继目标 `NODESTEER_E2E_TRANSFER_TARGET_IDS=611c02a5…(native-2),37989297…(native-3)`（与 P12 复验同组授权节点，环境可提供，未 skip）。

| 用例 | 首轮 | 复跑 | 备注 |
|---|---|---|---|
| `p22-transfers.spec.ts`（P22-01/P22-02） | 2 通过 / 0 失败 / 0 跳过 | 2 通过 / 0 失败 / 0 跳过 | P22-02 为 UI mock，无真实数据写入 |
| `full-feature-ui.spec.ts -g FULL-06` | 1 通过 / 0 失败 / 0 跳过 | 1 通过 / 0 失败 / 0 跳过 | 双目标真实中继 + 终态冻结断言全链路 |

- 运行时证据（首轮 `full06-repair`）：
  - 中继传输 `ee0a315c…` SUCCESS，双目标（native-3/native-2）均 SUCCESS，校验任务在双目标 stdout 含 marker；
  - 失败传输 `a9c7fcb7…` 终态 **FAILED**（未被改写为 CANCELED），目标 native-2 FAILED；审计序列 `create → cancel(400 探针) → retry` 与用例新断言顺序一致；
  - 复跑同构：`87c73d4a…` SUCCESS / `61955a92…` FAILED。
- 原始结果：`~/p18-verify/raw/{p22-repair,p22-repair-repeat,full06-repair,full06-repair-repeat}(-run).json`。

## 三、清理与干扰核验（GAP-18 教训）

- FULL-06 自建任务（`qa-p18-` 前缀）已由用例 `finally` 清理：`/api/tasks` **0 残留**；源码/目标节点 `/tmp` 文件清理执行 SUCCESS（stdout 含 `cleaned` 标记），executions 按套件惯例保留为历史。
- 保留 4 条传输记录（每次运行 SUCCESS+FAILED 各一，`retained_history`）：传输无删除 API，属套件既有惯例（环境原有 110 条历史）。
- 干扰评估：全套件中依赖真实 `/api/transfers` 列表的仅 SAFE-01（取首行 ID 做搜索/分页，任意行可用）、RBAC（仅断言角色按钮可见性）、GAP-16（route mock 空态）；无其它用例对传输列表做「0/N」断言 → 不构成 GAP-18 型干扰，无需复跑其它用例。
- 无新增用户/脚本/分组/调度/应用/制品残留（`qa-p18-` 前缀全族扫描为空）。

## 四、未做项与剩余风险

- 未跑全量套件（超出本次任务范围）；两条受影响用例已各自双跑通过。
- FULL-06 依赖 `NODESTEER_E2E_TRANSFER_TARGET_IDS` 提供 ≥2 个授权中继目标；未设置该变量的环境按设计显式 skip（行为不变）。
- P22-02 为 UI mock 层验证；Hub 端 400/终态冻结由已通过的 P16 运行时证据（`qa-resume-20261018-final2-verify/evidence/phase08-g8-terminal.json`）与 Go 测试覆盖，本报告不重复声称。
- 传输历史无删除能力，长期累积属产品现状，不在本次范围。

## 五、证据位置

- 用例改动：`web/e2e/tests/p22-transfers.spec.ts`、`web/e2e/tests/full-feature-ui.spec.ts`（`git diff -- web/e2e/` 可核；未提交）。
- 运行结果/台账：`~/p18-verify/raw/*.json`、`~/p18-verify/playwright-resources.jsonl`。
- 本报告：`.e2e/runs/20260930-101945/repair/qa-resume-20261014-root/p18-e2e-contract-followup-report-20261018.md`。
