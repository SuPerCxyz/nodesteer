# Tasks: Fix Real Test Findings

## Backend

- [x] B1 修复 systemd health check 启动竞态并补单测。
- [x] B2 修复 Artifact DELETE 幂等行为并补 API/manager 测试。
- [x] B3 移除 CLI 默认管理员口令并增加空凭据启动门禁。
- [x] B4 更新 Docker Compose 管理员口令门禁。

## Web UI

- [x] W1 修复节点纳管弹窗关闭重置。
- [x] W2 增加权限 hooks 并收紧所有列表/编辑/运行入口。
- [x] W3 修复 Label Group 实时成员数。
- [x] W4 补充 RBAC 页面断言和弹窗回归测试。

## Docker/KVM

- [x] K1 按 KVM 流程刷新模板、资源、网络、QGA、SSH 和 Docker 预检。
- [ ] K2 若预检通过，创建隔离 Docker 测试 VM，验证 enrollment、persistent state、restart/recreate。
- [x] K3 若预检不通过，记录阻塞原因，不以 Native 结果代替 Docker 结果。

## Verification

- [x] V1 Go test/vet。
- [x] V2 Web test/lint/build/E2E list。
- [x] V3 KVM Web/API/Agent 真实复验并更新 docs/REAL_TEST_REPORT.md。
- [x] V4 清理本轮创建的测试资源，保留既有历史和 VM。
