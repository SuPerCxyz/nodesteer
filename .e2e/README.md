# `.e2e/` 测试记录索引

本文件是项目 `AGENTS.md` 的分片：仓库中的自主 Web 全功能覆盖测试状态统一记录在 `.e2e/`。

## 记录文件

- `.e2e/run-state.json`：测试阶段、断点和恢复提示。
- `.e2e/discovery.json`：页面、路由、菜单、资源和 API 发现结果。
- `.e2e/feature-inventory.json`：功能清单、优先级、测试结果和证据引用。
- `.e2e/state-graph.json`：状态与状态迁移模型。
- `.e2e/workflows.json`：连续业务工作流及执行结果。
- `.e2e/coverage.json`：由清单、状态图和工作流计算的覆盖缺口。
- `.e2e/failures.json`：结构化缺陷、复现步骤和证据。
- `.e2e/test-data.json`：测试数据归属、状态和清理结果。
- `.e2e/observations.json`：待跟进的运行时观察和潜在缺口。
- `.e2e/final-report.md`：最近一次自主 Web E2E 测试报告。
- `.e2e/evidence/`：截图、控制台、网络和其他测试证据。
- `.e2e/ui-review/`：页面 UI/UE 检查的页面矩阵、响应式测量、问题记录和截图证据。
- `.e2e/agents/`：多执行单元的探索与结果 JSON 及 run-info.md。
- `.e2e/passes/`：各执行单元的通过记录 JSON。
- `.e2e/continuity-map.md`：跨页连续性工作流映射。
- `.e2e/test-plan.md`：本轮测试计划。
- `.e2e/ui-review/screenshots/`：UI 巡检截图证据。

## 复测规则

继续或复测 Web E2E 前，先读取 `.e2e/run-state.json`、`.e2e/coverage.json`、`.e2e/workflows.json`、`.e2e/failures.json` 和 `.e2e/test-data.json`，不得把历史报告或上下文记忆当作当前测试状态。
