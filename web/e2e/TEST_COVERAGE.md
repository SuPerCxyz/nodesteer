# Cadentra Web E2E 覆盖说明

完整用例以 [docs/TEST_PLAN.md](../../docs/TEST_PLAN.md) 为唯一来源。本文件只记录自动化分层、运行方式和真实覆盖边界，不能用“脚本执行成功”替代业务验收。

## 自动化分层

| 层级 | 工具 | 覆盖内容 | 通过要求 |
|---|---|---|---|
| 单元/组件 | Vitest Browser | React 组件、状态、工具函数 | 测试断言通过 |
| Web UI | Playwright | 页面、表单、弹窗、按钮、链接、页面联动 | 必须断言控件存在和业务结果 |
| 探索式 UI | agent-browser | 真实页面逐按钮操作、截图、控制台和网络 | 每条用例保留证据 |
| API | curl/Go/Playwright request | 状态码、响应结构、鉴权、持久化 | API 与数据库/页面状态一致 |
| Agent 集成 | WebSocket + Linux VM | 同步、执行、日志、Heartbeat、Reconciliation | 客户机实际效果正确 |
| 系统恢复 | KVM/libvirt/SSH/QGA | 重启、断网、进程、磁盘、systemd | 恢复后无丢失、重复或半状态 |

## 真实自动化要求

- 默认测试地址通过 `BASE_URL` 设置，不把环境凭据写入仓库。
- 管理员凭据通过 `CADENTRA_E2E_USERNAME` / `CADENTRA_E2E_PASSWORD` 注入。
- RBAC 测试用户通过 `CADENTRA_E2E_OPERATOR_USERNAME`、`CADENTRA_E2E_VIEWER_USERNAME`、`CADENTRA_E2E_RBAC_PASSWORD` 注入。
- 不能使用 `if (isVisible())` 静默跳过必测控件；控件缺失必须失败。
- 创建类用例必须验证 POST 后的对象、页面刷新和下游效果。
- 删除类用例必须验证确认框取消、确认后 DELETE、列表消失和下游 Tombstone。
- 真实 Agent 用例必须验证 Hub 状态、Agent 本地状态和 Linux 客户机效果。
- E2E 用例必须清理本轮可识别的临时对象；无法清理的对象记录在报告中。

## 页面覆盖矩阵

| 模块 | 页面/入口 | 必测自动化范围 |
|---|---|---|
| G | 全局 Header/Sidebar/DataTable | 语言、主题、Profile、退出、导航、搜索、Reset、列、分页、Loading/Error/Empty |
| P01 | `/sign-in` | 正常、错误、空提交、显示密码、回跳、Token 过期、OIDC 边界 |
| P02 | 全局布局 | 13 项导航、桌面/移动 Sidebar、前进后退、刷新 |
| P03 | `/` | 4 个指标、6 类 Dashboard 区块、所有 View All/详情链接、空态 |
| P04/P05 | `/agents`、`/nodes`、详情 | 状态、标签、Inventory、Capability、Tabs、纳管命令、复制、pending→online |
| P06 | `/groups` | Static/Label 创建、编辑、成员变化、删除确认 |
| P07/P08 | `/scripts`、编辑器 | 三种解释器、参数、环境、Clone、Revision、启停、删除确认 |
| P09-P11 | `/tasks`、Run Now | 四种任务、三种目标、条件、参数、Retry、Offline、运行/取消 |
| P12/P13 | `/schedules`、编辑器 | Cron/Interval/One-Time、Owner、Misfire、Timezone、启停、删除 |
| P14/P15 | `/applications`、编辑器 | Artifact、分配/取消分配、五种操作、四种健康检查、升级/回滚 |
| P16 | `/artifacts` | 上传、必填、架构、SHA、下载、Usage、删除确认 |
| P17/P18 | `/executions`、详情 | 查询、状态/触发筛选、日志 Tab、搜索/流/Wrap/Follow/Copy、取消 |
| P19 | `/audit` | 主要增删改查、执行、取消、部署、用户和设置审计 |
| P20 | `/users` | 创建、角色、密码 API、权限可见性、无删除能力边界 |
| P21 | `/settings` | Runtime/Appearance、保存、校验、Agent 消费 |
| P22 | `/transfers` | 多目标、增删目标、重试、取消、部分成功、文件和 SHA |

## 当前自动化与计划差异

当前仓库 `web/e2e/tests` 中只有有限页面冒烟和少量流程用例。完整计划中的 FL、E2E、RBAC、SYS 用例必须逐条补齐；列表页测试不能只断言 `body` 非空，页面按钮测试不能在控件不存在时自动跳过。

## 运行命令

```bash
cd web
npm test
CADENTRA_E2E_PASSWORD='<secret-from-controlled-source>' npm run test:e2e
```

真实环境的 Agent、Hub、QGA、SSH、systemd 和文件证据不属于纯 Web E2E，按 `docs/TEST_PLAN.md` 第 9 至 12 节执行并单独记录结果。
