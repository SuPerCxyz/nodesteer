# Proposal: Fix Real Test Findings

## Why

KVM2 真实验收发现节点纳管弹窗状态未重置、systemd 失败升级被误判成功、Artifact 删除非幂等、RBAC 页面展示越权入口、Label Group 成员数失真以及 Hub 弱默认管理员口令等问题；Docker Agent/Host Integration 还缺少可重复的隔离验收环境。

## What Changes

- 关闭节点纳管弹窗时重置表单、错误、结果和复制状态。
- systemd 健康检查要求服务稳定处于 running/active，失败升级必须返回失败并执行回滚。
- Artifact 删除对已不存在对象按幂等操作处理，不返回内部错误。
- Web UI 按角色隐藏无权限的创建、编辑、删除、保存、纳管和运行入口；保留 operator 允许的执行/应用操作/日志查看。
- Label Group 列表按当前节点 Label 实时计算成员数。
- 移除 Hub CLI 内置管理员口令；无显式管理员口令时拒绝启动；Compose 要求显式提供管理员口令。
- 测试工具只从受控认证 profile/环境读取凭据，不写入仓库。
- 在满足 KVM 预检时创建独立 Docker 测试 VM，验证 Docker Agent 持久化和重建；不满足时保留环境阻塞。

## Impact

- 后端：Agent systemd health check、Hub Artifact delete。
- 前端：RBAC action visibility、Node enrollment reset、Label Group count。
- CLI/部署：默认配置和 Compose 密钥门禁。
- 测试：新增单测、页面权限断言、真实 KVM 回归。
- 不删除既有 VM、历史执行记录或业务数据，不执行 Git 交付。
