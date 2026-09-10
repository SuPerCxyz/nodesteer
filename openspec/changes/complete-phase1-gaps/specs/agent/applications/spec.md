## Purpose

补齐 Managed Application 相关缺口：systemd Unit Registry、Docker Host Integration Inventory。

## ADDED Requirements

### Requirement: systemd Unit Registry
系统 SHALL 将 Managed Application 与 systemd Unit 建立 Registry，仅允许操作已登记 Unit。

#### Scenario: 操作已登记 Unit
- **WHEN** 对已部署应用的 Unit 执行 start/stop/restart
- **THEN** 操作成功执行

#### Scenario: 操作未登记 Unit
- **WHEN** 收到针对未登记 Unit 的操作请求
- **THEN** 系统拒绝并返回错误

### Requirement: Docker Host Integration Inventory
系统 SHALL 在 Docker Host Integration 模式下报告宿主机 Inventory，而非容器自身状态。

#### Scenario: Host Integration 上报宿主机信息
- **WHEN** Agent 以 Docker Host Integration 模式运行
- **THEN** Inventory 读取 `/host` 挂载下的宿主机 OS/Kernel/CPU/Memory/Filesystem/Network

#### Scenario: 无法可靠获得
- **WHEN** 宿主机信息无法可靠读取
- **THEN** 返回 UNAVAILABLE，不伪装容器状态为宿主机状态
