# 交付前门禁报告（qa-resume-20261018-gate）

- 时间：2026-10-18 21:12–21:57（+0800）
- 环境：repair authority `http://192.168.100.209:18080`，构建 `dddee4f0-e2e-repair-20261018-final2`，仓库 HEAD `aa71e82`
- 纪律：只跑不改（未改产品/测试代码）；未碰 `:8080` 与 `nodesteer-agent.service`；未重启 Hub/Agent/VM；未执行任何 Git 操作
- 凭据：仅从 0600 文件读取；产物目录经 16 项密钥扫描 0 命中

## 门禁结论：**可交付（0 失败）**

| 套件 | 结果 | 数字 |
|---|---|---|
| Playwright E2E 全量（20 文件） | **PASS** | 124 用例：**116 通过 / 0 失败 / 8 跳过**（exit 0，703.5s） |
| vitest 全量（web/src） | **FLAKE_RESOLVED**（基建 flake） | 49 文件 / **249 用例全部通过，0 失败**（全量命令命中已登记基建 flake 挂起，逐文件复跑全覆盖） |
| Go | **PASS** | `go build ./...` ✅、`go vet ./...` ✅、`go test ./internal/... -count=1` ✅（39.8s，internal/agent 未命中已知 flake，无复跑） |

对比上一轮：113/2/9 → **116/0/8**。增量来源：P22-02 与 GAP-18 修复生效、FULL-06 在提供中继目标后由 skip 转为真实执行并通过；跳过数 9→8。

## E2E 全量

- 命令：`BASE_URL=http://192.168.100.209:18080 node node_modules/playwright/cli.js test --config e2e/playwright.config.ts --reporter=json`（workers=1、retries=0，首轮即真相）
- 关键用例确认通过：`P22-02`（终态冻结新断言）、`FULL-06`（双目标中继 + 终态不可取消）、`GAP-18`（0/1 仪表盘链）、`SAFE-01/21/24`、`ROLE-01` 等
- **失败：0 条**（无产品缺陷、无 harness 失败、无红线失败）
- 跳过 8 条，全部为环境/红线门控（非本轮可执行项，沿用既定边界）：
  1. E2E-11 用户生命周期 —— 用户无删除 API，主控统一创建（未设 `NODESTEER_E2E_ALLOW_USER_CREATE`）
  2. FULL-08 / 3. FULL-09 / 4. ROLE-02 —— 需授权客户机 SSH 与 ELF/检查器夹具（`APP_ELF/GUEST_INSPECT/APP_DIR`，红线外）
  5. GAP-15 —— 需 Root 侧编排物理离线窗口（红线外）
  6. P21-02 / 7. RBAC-05 —— 共享 Settings 由主控串行验证，默认套件不修改（红线）
  8. SAFE-23 —— 需 Agent 本地脚本缓存检查器（需 SSH 访问 Agent，红线外）
- 运行参数：源节点 native-1 `91f44866…`；标签节点 native-2 `611c02a5…`；中继目标 native-2/native-3 `37989297…`（均在线）

## vitest 全量

- 命令：`cd web && npx vitest run --browser.headless`（`fileParallelism:false` 已配）
- 全量命令连续 3 次尝试均在浏览器 provider 层挂起（同一 flake 类，挂起点不一致：启动期 / `nav-user.test.tsx (0 test)`），非断言失败；处置按任务约定「逐文件复跑」：
  - 全量尝试证据：`vitest-attempt-notes.log`、`vitest-attempt2/attempt2-full.log`、`vitest-full.log`
  - 49 个真实测试文件逐一复跑：**249/249 通过，0 失败**（日志 `vitest-rerun-*.log`；其中 `next-runs.test.ts` 首个复跑被我方进程清理信号误杀 exit 137，已干净复跑 11/11）
- 分类：**基建 flake（INFRA_BROWSER_PROVIDER_HANG），产品缺陷 0**。依据：`web/vite.config.ts` 注释登记的同类问题（真实导航破坏共享 iframe）、前序轮次同型证据（a11y 轮 `final-vitest-first-attempt-flaky.log`、final2 轮 `vitest-run2.log` 挂起于 `navigation.test.ts`）、同套件当日早些时候曾全量 49/49 通过、全部文件单跑均通过且挂起点随机
- 复跑总数：249 通过（basis 记录于 `gate-summary.json.suites.vitest.basis`）

## Go

- `go build ./...` exit 0（0.9s）；`go vet ./...` exit 0（0.2s）；`go test ./internal/... -count=1` exit 0（39.8s，全部包 ok）
- `internal/agent` 未命中会话前既有间歇 flake，无复跑；`failing_packages_first_run: []`

## 清理核验（final-cleanup-oracle：PASS）

- 套件自清 134 项 fixture；我方前缀扫描（`qa-gate-`）0 残留；无需追加删除
- 全集合回到基线：nodes 5、applications 0、tasks 0、groups 0、schedules 0、scripts 2、artifacts 1（基线脚本/制品/节点逐一核验完好）
- 保留项（产品无删除 API，属套件既有惯例）：3 个自建账号（`qa-gate-role-user-8fa27301`、`qa-gate-user-bac1cdc9`、`qa-gate-self-role-user-4099d03e`）密码已轮换（HTTP 200）；传输历史 +2（FULL-06 SUCCESS/FAILED，`retained_history`）
- GAP-18 教训核验：应用数为 0（无 `0/N` 断言干扰残留）；hub healthz/readyz 200

## 产物

| 文件 | 说明 |
|---|---|
| `gate-summary.json` | 门禁汇总（DELIVERABLE_0_FAILURES，blockers=[]） |
| `e2e-suite.json` | E2E 124 用例明细（116/0/8，跳过逐条原因） |
| `vitest.json` | 全量尝试历史 + 逐文件复跑 249/249 + flake 分类 |
| `go-tests.json` | 三命令退出码/耗时/输出尾部 |
| `fixture-ledger.json` | 资源台账（created 163 / deleted 134 / retained 3 用户 + 2 传输） |
| `final-cleanup-oracle.json` | 清理核验（oracle_pass=true，14 项检查全过） |
| `preflight.json` / `playwright-resources.jsonl` / `evidence/` / `raw/` / `artifacts/` | 基线、资源日志、证据与原始报告 |

## 未验证项与剩余风险

- 8 条红线/环境门控 skip 未执行（同上逐条原因），与本轮边界一致，非门禁阻塞
- vitest 全量命令本身在当前会话 3 次均命中基建 flake；结论以「49 文件逐文件复跑 249/249」为覆盖依据，属已登记基建问题，非产品缺陷
- 传输/执行历史无删除 API，长期累积属产品现状
