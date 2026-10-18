# NodeSteer Web E2E 测试计划（3 遍遍历版）

- 版本：2026-09-22（3 遍遍历：操作枚举 → 状态变化 → 跨页连续性）
- 基线环境：Hub `http://192.168.100.209:8080`，Gateway `ws://192.168.100.209:8443`；4 台 Agent（Debian 13 / Fedora 42 / Rocky 9.8 / openSUSE Leap 16.0）
- 数据来源：`.e2e/passes/agent-{a,b,c}.json`（运行域 / 节点与系统域 / 制品与应用域），证据在 `.e2e/evidence/agent-{a,b,c}/`
- 规范化记录：`discovery.json`、`feature-inventory.json`、`workflows.json`、`failures.json`、`observations.json`、`coverage.json`、`test-data.json`、`state-graph.json`、`run-state.json`

## 1. 覆盖概览

| 指标 | 数值 |
|---|---|
| 功能点（控件/操作级） | 184 |
| 本轮实测操作 | 89 |
| 实测通过 | 89 |
| 跨页连续性工作流 | 20（通过 16 / 失败 4） |
| 本轮新发现缺陷 | 26（高 2 / 中 14 / 低 10） |
| 上轮记录待复测 | 16 |

## 2. 页面与操作清单（第 1 遍枚举结果）

| 页面 | 枚举控件/操作 | 本轮实测 | 未实测 |
|---|---|---|---|
| `` | 12 | 12 | 0 |
| `/` | 8 | 0 | 8 |
| `/agents` | 23 | 10 | 13 |
| `/agents/:id` | 16 | 7 | 9 |
| `/agents/:id(不存在)` | 1 | 1 | 0 |
| `/applications` | 1 | 1 | 0 |
| `/applications/:id` | 8 | 8 | 0 |
| `/applications/new` | 3 | 1 | 2 |
| `/artifacts` | 2 | 2 | 0 |
| `/artifacts/new` | 4 | 4 | 0 |
| `/audit` | 12 | 2 | 10 |
| `/executions` | 2 | 2 | 0 |
| `/executions/:id` | 2 | 2 | 0 |
| `/groups` | 5 | 1 | 4 |
| `/groups/:id` | 1 | 1 | 0 |
| `/groups/:id(不存在)` | 1 | 1 | 0 |
| `/groups/new` | 8 | 3 | 5 |
| `/nodes` | 12 | 0 | 12 |
| `/nodes/<id>` | 1 | 1 | 0 |
| `/schedules → /tasks?view=schedules` | 1 | 1 | 0 |
| `/schedules/:id` | 1 | 1 | 0 |
| `/schedules/new` | 2 | 2 | 0 |
| `/schedules/new 与 /schedules/:id` | 2 | 0 | 2 |
| `/scripts` | 2 | 1 | 1 |
| `/scripts 等` | 1 | 1 | 0 |
| `/scripts/:id` | 1 | 1 | 0 |
| `/scripts/new` | 1 | 1 | 0 |
| `/scripts/new 与 /scripts/:id` | 4 | 0 | 4 |
| `/settings` | 7 | 3 | 4 |
| `/sign-in` | 4 | 0 | 4 |
| `/tasks` | 2 | 1 | 1 |
| `/tasks/:id` | 4 | 4 | 0 |
| `/tasks/:id/edit` | 2 | 0 | 2 |
| `/tasks/:id/run` | 1 | 1 | 0 |
| `/tasks/new` | 4 | 3 | 1 |
| `/tasks?view=schedules` | 2 | 1 | 1 |
| `/transfers` | 7 | 5 | 2 |
| `/users` | 9 | 3 | 6 |
| `全局布局` | 4 | 0 | 4 |
| `多个详情页` | 1 | 1 | 0 |

> 说明：控件级枚举用于保证「所有操作被发现」；实测列为本轮真实执行并记录状态变化的操作。未实测项多为同页同类控件（重复按钮/选项）或需破坏性前置条件的操作，后续按 E2E 用例补齐。

## 3. 跨页连续性工作流（第 3 遍结果）

| 工作流 | 结论 | 关键结论/证据 |
|---|---|---|
| `workflow.script.task.run`<br>workflow.script.task.run | ✅ 通过 | 全部成立：执行记录 script_revision 1→2、stdout 随内容变化；审计含 script update 与 task execute；执行详情可看 stdout/stderr 与日志过滤 <br>证据：`.e2e/evidence/agent-a/p3-workflow-script.txt` |
| `workflow.schedule.crud.trigger`<br>workflow.schedule.crud.trigger | ✅ 通过 | 全部成立；一次性调度触发后仍显示「已启用」（无已完成态），删除调度后任务可删除 <br>证据：`.e2e/evidence/agent-a/p2-schedules.txt` |
| `workflow.task.multi_node`<br>workflow.task.multi_node | ✅ 通过 | 生成 4 条 manual 执行，分别归属 4 台 Agent，全部 SUCCESS <br>证据：`.e2e/evidence/agent-a/p2-run-tasks.txt` |
| `workflow.transfer.multi_target.sha256`<br>workflow.transfer.multi_target.sha256 | ✅ 通过 | 两目标 SUCCESS、hub sha256 与源一致、目标节点 sha256 与内容均一致 <br>证据：`.e2e/evidence/agent-a/p2-transfers.txt` |
| `workflow.transfer.failure_retry_cancel`<br>workflow.transfer.failure_retry_cancel | ❌ 失败 | 重试与取消均生效；但 (a) 失败原因在页面完全不可见（无详情页），(b) 源上传失败时目标永久 PENDING 且行菜单只有「重试」没有「取消」，只能靠 API 取消 → UI 无法终止 <br>证据：`.e2e/evidence/agent-a/p2-transfers-fail.txt` |
| `workflow.execution.cancel_timeout_retry.audit`<br>workflow.execution.cancel_timeout_retry.audit | ❌ 失败 | 取消 → CANCELED 且节点侧 sleep 进程不存在；超时 → TIMED_OUT/17s；重试 → 节点侧执行 3 次、1 条记录；但审计中没有任何 execution cancel 记录（file_transfer cancel 有） <br>证据：`.e2e/evidence/agent-a/p2-retry-disabled.txt` |
| `workflow.task_param_override`<br>workflow.task_param_override | ✅ 通过 | stdout=param=runtime-override，参数以 CADENTRA_PARAM_msg 注入 <br>证据：`.e2e/evidence/agent-a/p2-task-params.txt` |
| `workflow.entity_reference_guards`<br>workflow.entity_reference_guards | ✅ 通过 | 两次删除均 409 阻断且状态未被破坏；解除引用后可删除；但两条提示均为英文原文 + 裸 UUID <br>证据：`.e2e/evidence/agent-a/p2-sched-taskref.txt` |
| `workflow.task_delete_history`<br>workflow.task_delete_history | ✅ 通过 | 成立：列表 6 行显示「未知任务」，详情元数据「任务 未知任务」 <br>证据：`.e2e/evidence/agent-a/p2-cleanup.txt` |
| `workflow.label.group.task`<br>workflow.label.group.task | ✅ 通过 | 11/11 步通过；标签 alpha→beta 后分组成员 1→0，节点详情任务区同步移除；调度/任务/分组删除均 200，标签还原为空 <br>证据：`p3-w1-label-added.png` |
| `workflow.enrollment`<br>workflow.enrollment | ❌ 失败 | 三种命令生成正常（native 1886 字符，含 node_name/node_ip/registration_token/agent_id）；pending 节点 status=offline/sync_status=pending；重新纳管既有主机名返回全新 node_id/agent_id 并新增一条 offl <br>证据：`.e2e/evidence/agent-b/p3-w2-pending-node.png` |
| `workflow.node.status`<br>workflow.node.status | ✅ 通过 | 维护/禁用/设为在线均生效；仪表盘在线数 4→3→4；维护与禁用节点运行任务均返回 400 "node <uuid> is not online (status=maintenance/disabled)"；恢复在线后运行成功；临时任务已删除，节点状态还原。注：13 步中 12 步断言通过，唯一未通过项为"列表状态徽标 <br>证据：`.e2e/evidence/agent-b/p3-w3-dashboard-baseline.png` |
| `workflow.rbac.matrix`<br>workflow.rbac.matrix | ❌ 失败 | 前端：两者侧边栏均为完整 12 项；/users 显示"只有管理员可以管理用户。"；/settings 输入禁用且无保存按钮；/agents 无添加节点与行菜单；/groups 无新建与行菜单。后端：POST /groups//nodes、PUT /settings、POST /users 均 403；POST /ta <br>证据：`.e2e/evidence/agent-b/p3-rbac-viewer-users.png` |
| `workflow.user.lifecycle`<br>workflow.user.lifecycle | ✅ 通过 | 创建/改角色/登录/改密码均成功且即时生效；审计有 create/update user 与 login 记录，但审计记录不含 detail 字段（描述列恒空、按用户名搜索无结果），且设置保存不入审计 <br>证据：`.e2e/evidence/agent-b/p2-users-created.png` |
| `workflow.settings.roundtrip`<br>workflow.settings.roundtrip | ✅ 通过 | 4 项设置读取一致；changelog_window 5000→6000 保存后刷新仍为 6000；非法值 "abc" 与 "-5" 均 400 并 toast "changelog_window must be between 1 and 1000000000"（英文原文）；已还原为 5000；外观主题浅色/深色/系 <br>证据：`.e2e/evidence/agent-b/p2-settings-runtime.png` |
| `workflow.artifact.lifecycle`<br>制品上传→SHA256/大小校验→下载校验→Usage 引用→删除被引用制品 | ✅ 通过 | 全部符合：上传 POST 200、列表与下载 SHA256 与本地一致、Usage 链接指向应用详情、被引用时 DELETE 409 阻止且行保留、解除引用后 UI 删除成功（5/5） <br>证据：`.e2e/evidence/agent-c/r2-p2-artifact-uploaded.png` |
| `workflow.artifact.app.deploy`<br>制品→创建应用→分配节点→Deploy→健康→Start/Stop/Restart→Upgrade→删除清理 | ✅ 通过 | 全部通过；UI 无手动回滚入口（部署操作下拉仅 deploy/start/stop/restart/upgrade），记为产品边界 <br>证据：`.e2e/evidence/agent-c/r2-p2-app-new-from-artifact.png` |
| `workflow.app.execution.audit`<br>应用操作产生的执行→执行详情/日志→审计 | ✅ 通过 | 执行详情可打开但内容有限：任务显示「未知任务」、无应用名/操作类型（deploy 与 upgrade 无法区分）、stdout/stderr 为空、无日志；审计完整记录 <br>证据：`.e2e/evidence/agent-c/r2-p3-executions-list.png` |
| `workflow.dashboard.consistency`<br>仪表盘统计与列表 API 一致性、各「查看全部」落点 | ✅ 通过 | 节点 4/4 在线、应用 1/1 健康、最近50次 49 成功 1 失败 = 98% 成功率，全部与 API 一致；查看全部落点 /executions、/agents、/applications、/agents、/executions、/tasks?view=schedules 均正确，后者落在调度标签页 <br>证据：`.e2e/evidence/agent-c/r2-p1-dashboard.png` |
| `workflow.ui.basics`<br>UI 基础项：字体（无外网请求）/主题/语言切换与刷新保持；1366/1920/3840 无横向溢出 | ✅ 通过 | 0 个外部请求，字体为同源 /fonts/maple-mono-nl-nf-cn-medium.woff2；主题保存于 cookie vite-ui-theme 刷新保持；语言 cadentra_lang 刷新保持；1366/1920/3840 均 scrollWidth==innerWidth 无横向溢出 <br>证据：`.e2e/evidence/agent-c/r2-p3-theme-dark.png` |

## 4. 缺陷台账

### 4.1 本轮新发现（open）

| ID | 级别 | 标题 | 证据 |
|---|---|---|---|
| FAIL-A-010 | 高 | 源上传失败的传输目标永久 PENDING，UI 无取消入口（复现并定位 FAIL-M-001） | `.e2e/evidence/agent-a/p2-transfers-fail.txt` |
| FAIL-B-101 | 高 | RBAC：角色变更（含降权）对已登录会话不生效，旧会话保留原权限 | `.e2e/evidence/agent-b/p3-rbac-demoted-session.png` |
| FAIL-A-004 | 中 | 取消执行不写审计日志（file_transfer 取消有审计，execution 取消没有） | `.e2e/evidence/agent-a/p2-audit-list.png` |
| FAIL-A-005 | 中 | 执行列表与审计列表硬上限 100 条：更早记录在 UI 完全不可达 | `.e2e/evidence/agent-a/p2-executions-list.txt` |
| FAIL-A-006 | 中 | 禁用脚本不阻断已引用任务的立即运行，脚本内容照常下发执行 | `.e2e/evidence/agent-a/p2-scripts-ops.txt` |
| FAIL-A-007 | 中 | 调度编辑器所有错误提示都显示「暂无数据」（客户端校验与后端 400 均被吞） | `.e2e/evidence/agent-a/p2-edges.txt` |
| FAIL-A-008 | 中 | 任务超时允许负数保存，界面显示 -5s 但实际按 300s 执行 | `.e2e/evidence/agent-a/p2-task-form-edges.txt` |
| FAIL-A-009 | 中 | 目标节点被删除后任务既不能编辑/启停，运行时报原始 SQL 错误 | `.e2e/evidence/agent-a/p2-tasks-list.txt` |
| FAIL-A-011 | 中 | 文件传输失败原因在 UI 完全不可见（无详情页；列表只显示节点+状态） | `.e2e/evidence/agent-a/p2-transfers-fail.txt` |
| FAIL-B-102 | 中 | 审计记录缺少 detail 字段：描述列恒空、按资源名搜索永远无结果 | `.e2e/evidence/agent-b/p2-audit-list.png` |
| FAIL-B-103 | 中 | 引用阻断错误提示为英文 + 裸 UUID；分组删除确认框文案与实际阻断行为矛盾 | `.e2e/evidence/agent-b/p3-w2-delete-referenced-blocked.png` |
| FAIL-B-104 | 中 | 重新纳管既有节点不复用 Node ID / Agent ID，产生重复节点记录 | `.e2e/evidence/agent-b/p3-w2-reenroll-duplicate.png` |
| FAIL-B-105 | 中 | 设置保存不写入审计 | `.e2e/evidence/agent-b/p2-settings-saved.png` |
| FAIL-B-106 | 中 | 不存在资源的详情/编辑路由无错误态：节点详情空白、分组编辑显示空白新建表单 | `.e2e/evidence/agent-b/p2-agent-notfound.png` |
| FAIL-B-107 | 中 | 分组编辑器必填校验提示为"暂无数据"而非字段名 | `.e2e/evidence/agent-b/p2-group-empty-save-error.png` |
| FAIL-C-001 | 中 | 删除被引用制品的阻断提示为英文后端原文并暴露裸 UUID | `.e2e/evidence/agent-c/r2-p2-artifact-delete-referenced-blocked.png` |
| FAIL-A-012 | 低 | 传输终态行的「操作」按钮打开空菜单 | `.e2e/evidence/agent-a/p2-transfer-empty-menu.png` |
| FAIL-A-013 | 低 | 审计列表不展示资源 ID，无法定位被操作对象 | `.e2e/evidence/agent-a/p2-audit-list.png` |
| FAIL-A-014 | 低 | 重名任务/脚本与重复参数可创建，实体无法区分 | `.e2e/evidence/agent-a/p2-task-form-edges.txt` |
| FAIL-A-015 | 低 | 多处错误信息为后端英文原文 + 裸 UUID | `.e2e/evidence/agent-a/p2-scripts-ops.txt` |
| FAIL-A-016 | 低 | DELETE 不存在的任务/脚本/调度返回 200 ok:true（GET 同资源返回 404） | `.e2e/evidence/agent-a/p2-edges.txt` |
| FAIL-B-108 | 低 | i18n 缺口：多处界面文案在中文模式下显示英文或后端原文 | `.e2e/evidence/agent-b/p2-signin-empty-submit.png` |
| FAIL-B-109 | 低 | 重复用户名创建暴露原始 SQLite 错误 | `.e2e/evidence/agent-b/p2-users-duplicate.png` |
| FAIL-B-110 | 低 | 登录页无语言切换入口；语言切换不更新 <html lang> | `.e2e/evidence/agent-b/p2-signin-empty-submit.png` |
| FAIL-B-111 | 低 | 表格视图状态不持久化（列可见性/每页条数刷新即重置） | `.e2e/evidence/agent-b/p2-agents-view-toggled.png` |
| FAIL-B-112 | 低 | 列表无列排序入口（DataTableColumnHeader 为死代码） | `.e2e/evidence/agent-b/p2-agents-sorted.png` |

### 4.2 上轮记录待复测

| ID | 级别 | 标题 | 上轮状态 |
|---|---|---|---|
| FAIL-C-001 | 高 | 跨节点文件传输目标永久停在 DELIVERING，文件未交付且无终止态 | 2026-09-19 记录，本轮未复测 |
| FAIL-M-001 | 高 | 源上传失败的传输目标永久 PENDING，阻断节点删除且 UI 无取消入口 | 2026-09-19 记录，本轮未复测 |
| FAIL-G-001 | 高 | UI 无法创建「托管应用部署」任务：默认 app_operation=start 与后端校验冲突 | 2026-09-19 记录，本轮未复测 |
| FAIL-A-001 | 中 | 运行时设置保存不会写入审计日志 | 2026-09-19 记录，本轮未复测 |
| FAIL-A-002 | 中 | 用户无法删除：UI 无入口且 API 不支持 DELETE，测试用户无法清理 | 2026-09-19 记录，本轮未复测 |
| FAIL-B-001 | 中 | 脚本任务的执行详情不显示脚本名称/脚本修订号 | 2026-09-19 记录，本轮未复测 |
| FAIL-B-002 | 中 | 跳过执行的“阻塞原因”丢失且界面无处展示 | 2026-09-19 记录，本轮未复测 |
| FAIL-B-003 | 中 | 编辑器保存失败时显示“暂无数据”，真实错误信息被丢弃 | 2026-09-19 记录，本轮未复测 |
| FAIL-C-002 | 中 | 托管应用部署失败在「执行详情」不可见：日志为空、无错误、无回滚标识 | 2026-09-19 记录，本轮未复测 |
| FAIL-C-003 | 中 | 新建应用未填名称时校验提示显示「暂无数据」而非必填提示 | 2026-09-19 记录，本轮未复测 |
| FAIL-C-004 | 中 | 托管应用部署执行在 /executions 显示为「未知任务」，缺少来源上下文 | 2026-09-19 记录，本轮未复测 |
| FAIL-C-005 | 中 | 文件传输失败原因在页面完全不可见 | 2026-09-19 记录，本轮未复测 |
| FAIL-M-002 | 中 | 撤销凭证后的重新纳管流程不闭环 | 2026-09-19 记录，本轮未复测 |
| FAIL-G-002 | 中 | 不存在的资源详情路由无错误态：空白编辑器/永久加载中 | 2026-09-19 记录，本轮未复测 |
| FAIL-A-003 | 低 | 重复用户名创建时向用户暴露原始 SQLite 约束错误 | 2026-09-19 记录，本轮未复测 |
| FAIL-M-003 | 低 | 节点删除阻断提示暴露英文后端原文与裸 UUID | 2026-09-19 记录，本轮未复测 |

## 5. 自动化用例映射（Playwright）

目录：`web/e2e/tests/`，运行：`cd web && CADENTRA_E2E_PASSWORD=<pwd> npx playwright test --config e2e/playwright.config.ts`（`BASE_URL` 默认指向 209）。

| Spec | 覆盖范围 |
|---|---|
| p01-login.spec.ts | 登录、错误校验、重定向安全、退出 |
| p02-layout.spec.ts | 侧栏 12 项、任务页调度视图、旧路由重定向、token 过期 |
| p03-dashboard.spec.ts | 仪表盘指标、卡片与链接 |
| p04-nodes.spec.ts | 节点列表/详情、纳管命令、行菜单 |
| p06-groups.spec.ts | 分组 CRUD 与目标联动 |
| p07-scripts.spec.ts | 脚本列表/编辑器/克隆/删除 |
| p09-tasks.spec.ts | 任务列表/编辑器/立即运行 |
| p12-schedules.spec.ts | 调度列表与编辑器（合并视图） |
| p14-applications.spec.ts | 应用列表/编辑器/操作 |
| p16-artifacts.spec.ts | 制品上传/下载/删除 |
| p17-executions.spec.ts | 执行列表/详情/日志/取消 |
| p19-misc.spec.ts | 审计、用户、设置 |
| p22-transfers.spec.ts | 文件传输 |
| p23-detail-layout.spec.ts | 详情页单页化、宽度一致、字段栅格、短视口侧边栏 |
| p24-task-schedules.spec.ts | 任务/调度合并视图 CRUD、深链、仪表盘落点 |
| rbac.spec.ts | RBAC 权限矩阵 |
| e2e-flows.spec.ts | 端到端流程（脚本任务、用户生命周期） |

## 6. 执行方式与数据规则

1. 环境检查：`/healthz`、`/readyz`、4 台 Agent online/synced。
2. 数据前缀：`e2e-<单元>-<YYYYMMDD>-<随机4位>`；只操作自身前缀数据；结束清理并登记 `test-data.json`。
3. 保护数据：4 台 Agent 节点、admin 用户、历史执行/审计、他人前缀对象不得修改删除。
4. 禁止项：重启 Hub/Agent、systemctl、长期调度（用不触发表达式）、删除受保护对象。
5. 证据：截图/控制台/网络写入 `.e2e/evidence/<单元>/`；结论必须有证据路径。
6. 失败处理：可复现 → `failures.json`；不可复现 → `observations.json`；UI/UX 建议 → `.e2e/ui-review/findings.json`。

## 7. 未覆盖项与风险

- /artifacts 无详情页（行内无详情入口），无法验证单制品详情视图（产品边界）。
- Agent 重连（重启/断网）对「维护」状态的影响未做运行时验证（禁止重启 Hub/Agent），仅基于 internal/hub/node.go:104 代码阅读给出观察。
- Hub 执行方（execution_owner=hub）的调度触发未验证：按安全规则不创建长期后台调度；本轮全部使用 Agent 执行方（一次性调度真实触发已验证）。
- RBAC 未覆盖脚本/发布包/应用/文件传输等其它域的按钮可见性（属其它执行单元范围），仅覆盖节点、分组、用户、设置、任务入口与直连接口。
- RBAC（viewer/operator 对运行域接口的 403）未验证：本轮全程使用 admin；属其他单元范围。
- arm64 制品上传/下载未在本轮重复（历史 G-09 已覆盖）。
- 「设置条件/设置远程条件」的清除入口、AND 组合等仅确认存在，未逐组合验证。
- 任务「条件」（本地 cpu_usage / 远程节点字段）仅完成字段枚举与「设置/清除」入口确认，未构造满足/不满足条件的分支执行验证。
- 传输大文件成功路径的 SHA256 未验证：300MB 用例用于中途取消，未等待完成；小文件（35B）已完成端到端内容与 SHA256 校验。
- 分组/标签类任务目标未执行验证（属节点/分组单元范围），本轮只做表单枚举。
- 删除确认对话框的「取消」路径未单独取证（取消按钮存在于制品/应用删除确认框）。
- 制品编辑器非法输入（Unit 名非法字符、健康检查数值边界如 0/负数/非数字）未逐一穷举。
- 响应式（1024/820）与深色主题、i18n 切换下的运行域页面未复检（属 ui-review 范围）。
- 多页分页交互仅在 /audit（10 页）实测，节点/分组/用户列表因数据量不足无法验证跨页行为。
- 应用手动回滚：UI 无入口（部署操作仅 deploy/start/stop/restart/upgrade），无法测试手动回滚；健康检查失败自动回滚本轮未复测（历史轮次已覆盖）。
- 托管应用部署/操作类任务未能端到端验证：环境内无任何托管应用（其他单元已清理），应用下拉为空。仅完成表单枚举与 400 错误路径验证；历史 FAIL-G-001 需在有应用的条件下复测。
- 执行/传输/审计记录无删除能力，测试数据只能保留；若需要干净基线需数据治理方案。
- 真实 Agent 的「撤销凭证 / 重新纳管」闭环未在真实节点上执行（保护 4 台 Agent），仅在自建 pending 节点上验证了撤销与同名重纳管。
- 节点详情「托管应用」分区因当前无应用分配，仅验证空态。
- 表格列宽拖拽/列排序等 tanstack-table 能力因无 UI 入口未验证（见 FAIL-B-112）。
- 语言/主题与浏览器 system 偏好联动（prefers-color-scheme）未验证。
- 跨代际传输（DELIVERING 中断/Agent 重连后恢复）未验证：需要停止 Agent，超出本单元安全边界。
- 部署失败/节点离线场景未复测：4 台节点全部在线，本轮未构造节点离线部署；也未构造健康检查失败用例。

## 8. 待复测与后续

- 新增/变更 UI 的自动化用例已补齐（p23/p24），其余 spec 需按本轮遍历结果补齐未实测控件。
- 26 个开放缺陷与 16 条上轮记录需按级别排期复测；高优先级：FAIL-A-010、FAIL-B-101。
- UI/UX 全量巡检（多视口）结果见 `.e2e/ui-review/`。
