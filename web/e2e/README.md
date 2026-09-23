# NodeSteer Web E2E 测试

完整测试计划、页面关系、API/RBAC、Agent、故障恢复和验收标准见 [docs/TEST_PLAN.md](../../docs/TEST_PLAN.md)。自动化覆盖说明见 [TEST_COVERAGE.md](./TEST_COVERAGE.md)。

## 测试入口

| 用途 | 命令 |
|---|---|
| Vitest Browser 单元/组件测试 | `cd web && npm test` |
| Playwright Web E2E | `cd web && npm run test:e2e` |
| agent-browser 探索式测试 | 使用受控会话登录后按测试计划操作 |

## 环境变量

```text
BASE_URL=http://<hub-web>:8080
NODESTEER_E2E_USERNAME=<administrator username>
NODESTEER_E2E_PASSWORD=<administrator password>
NODESTEER_E2E_OPERATOR_USERNAME=<operator username>
NODESTEER_E2E_VIEWER_USERNAME=<viewer username>
NODESTEER_E2E_RBAC_PASSWORD=<RBAC test password>
```

凭据只能来自受控 Secret/Vault，不得写入 Git、测试报告、命令日志或截图。

## 测试原则

- 通过页面操作验证真实业务效果，不只验证 URL 或 `body` 非空。
- 必测按钮不存在时测试必须失败，不得静默跳过。
- 对每个创建、修改、删除、运行、部署、取消操作记录请求、状态码、对象状态、Revision、Audit、Agent 和客户机证据。
- 删除操作验证确认框的取消和确认两个分支。
- 页面 UI、REST API、WebSocket、Agent 本地状态和 Linux 客户机状态分别记录。
- 测试数据使用唯一前缀并在完成后清理。

## 当前页面入口

- 登录：`/sign-in`
- 节点：`/agents`，兼容入口 `/nodes`
- 分组：`/groups`
- 脚本：`/scripts`
- 任务：`/tasks`
- 调度：`/schedules`
- 应用：`/applications`
- 制品：`/artifacts`
- 执行：`/executions`
- 文件传输：`/transfers`
- 审计：`/audit`
- 用户：`/users`
- 设置：`/settings`

## 结果要求

完整验收必须输出：

1. 用例执行清单。
2. 页面按钮与联动证据。
3. API/RBAC 结果。
4. Hub/Agent/VM 状态。
5. 缺陷和阻塞项。
6. 测试数据清理结果。
