# NodeSteer Web E2E 并行深测 Run Info（2026-09-19）

本文件是本轮 3 个并行测试 Agent 的共享约定。所有操作以运行中的 Hub 为准。

## 环境

- Hub Web/API：`http://192.168.100.209:8080`
- Hub Gateway：`ws://192.168.100.209:8443`
- 管理员：`admin` / `1234qwer!1`
- 登录必须走页面表单（验证真实登录流）。

### 目标节点（当前全部 online）

| 节点 | 系统 | IP | 只读 SSH 校验 |
|---|---|---|---|
| nodesteer-agent-1 | Debian 13 | 192.168.100.217 | 允许 |
| nodesteer-agent-2 | Fedora 42 | 192.168.100.205 | 允许 |
| nodesteer-agent-3 | Rocky 9.8 | 192.168.100.216 | 允许 |
| nodesteer-agent-4 | openSUSE Leap 16.0 | 192.168.100.206 | 允许 |

只读 SSH：`ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o HostKeyAlias=<域名> -i /home/superc/.ssh/id_ed25519 root@<ip> '<命令>'`

## agent-browser 约定

- 每个 Agent 使用独立会话，避免互相干扰：`agent-browser --session e2e-a ...`（B 用 `e2e-b`，C 用 `e2e-c`；RBAC 子会话可用 `e2e-a-viewer` 等）。
- 常用命令：`open <url>`、`snapshot -i`、`click @eN`、`fill @eN "text"`、`select`、`press`、`wait --load networkidle`、`get url|text|count`、`upload <selector> <file>`、`console`、`screenshot <path>`、`viewport <w> <h>`、`eval --stdin`、`find <text>`、`close --all`。
- ref（@eN）每次页面变化后都会失效，操作前重新 `snapshot -i`。
- 每次提交/跳转后检查 `agent-browser console` 是否有错误，关键响应可用 `agent-browser network list` 查看。
- 截图命名：`<feature>-<state>.png`，存到各自证据目录。

## 证据与结果文件

- 截图：`.e2e/evidence/agent-a/`、`.e2e/evidence/agent-b/`、`.e2e/evidence/agent-c/`
- 控制台/网络日志：同目录下 `console-<page>.log`、`network-<page>.log`
- 结果 JSON：`.e2e/agents/result-agent-a.json`（B、C 同理）
- 只允许写自己的证据目录和结果文件；禁止修改 `.e2e/` 根目录下的规范化文件（discovery.json、feature-inventory.json、coverage.json、failures.json、test-data.json、workflows.json、state-graph.json、observations.json、run-state.json、final-report.md、ui-review/*）。

结果 JSON 结构（schema_version=1）：

```json
{
  "schema_version": 1,
  "agent": "a|b|c",
  "started_at": "", "finished_at": "",
  "summary": {"features_tested": 0, "features_passed": 0, "features_failed": 0, "features_blocked": 0,
              "workflows_executed": 0, "ui_findings": 0},
  "features": [{"id": "script.create", "page": "page.scripts", "status": "passed|failed|blocked",
                "expected": "", "actual": "", "evidence": [".e2e/evidence/agent-a/xxx.png"],
                "notes": ""}],
  "workflows": [{"id": "workflow.script.task.lifecycle.001", "name": "", "status": "passed|failed|partial|blocked",
                 "steps": [], "result": "", "evidence": []}],
  "failures": [{"id": "FAIL-A-001", "severity": "high|medium|low", "feature_id": "", "title": "",
                "preconditions": [], "steps": [], "expected": "", "actual": "",
                "reproducible": true, "reproduction_count": 1,
                "console_errors": [], "network_errors": [], "screenshots": [],
                "suspected_area": [], "notes": ""}],
  "observations": [{"id": "OBS-A-001", "type": "possible_gap|environmental|ux", "description": "",
                    "related_features": [], "follow_up_required": true, "resolved": false}],
  "ui_findings": [{"id": "UI-A-001", "page": "/scripts", "viewport": "1440x900", "theme": "light|dark",
                   "severity": "high|medium|low", "category": "layout|consistency|overflow|contrast|feedback|a11y|i18n|responsive",
                   "title": "", "evidence": [], "description": "", "status": "open|resolved"}],
  "test_data": [{"id": "data.b.script.001", "type": "script", "name": "e2e-b-20260919-xxxx",
                 "created_at": "", "current_state": "deleted|exists", "safe_to_delete": true, "deleted": true,
                 "cleanup_note": ""}]
}
```

## 测试规则

1. **所有操作通过页面完成**；只读 SSH 仅用于核对真实效果（进程、文件、systemd 单元、systemctl 状态）。
2. 测试数据统一前缀：A 用 `e2e-a-20260919-<4位随机>`，B 用 `e2e-b-...`，C 用 `e2e-c-...`；不得操作其他前缀资源。
3. 结束时清理自己的测试数据（删除脚本/任务/调度/分组/发布包/应用/传输/用户），并在 `test_data` 记录；无法清理的标注 `cleanup_note`。
4. 禁止：删除节点、撤销 Agent 凭据、改 admin 用户/密码、修改 Hub 的 gateway/base_url/registration_token/TLS/端口等会中断环境的设置（设置页只做校验类/外观类测试）；不要停用/卸载不属于本轮测试的 systemd 服务。
5. 调度测试必须保证结束前禁用或删除，避免持续触发。
6. 每个失败项必须有可复现步骤和证据；无法复现的记为 observation，不得当 failure。
7. UI/UX 检查：1440x900 必测，各页面至少补一个窄视口（1024x768 或 820x1180）；深浅主题、中英文至少各覆盖一次关键页面；检查布局对齐、截断、空态、加载态、错误提示、确认对话框、按钮禁用态、表单校验、键盘 Enter/Tab 基础可用性、控制台错误。
8. 区分「代码缺陷」与「产品/UX 建议」：前者进 failures，后者进 ui_findings 或 observations。
