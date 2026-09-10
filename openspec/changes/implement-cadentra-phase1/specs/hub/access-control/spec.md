## Purpose

Hub 提供 RBAC 角色（Administrator/Operator/Viewer）、用户认证与 Audit Log，记录关键安全与变更事件。

## ADDED Requirements

### Requirement: 用户认证
系统 SHALL 支持用户名密码认证并签发会话凭证；密码不得明文存储。

#### Scenario: 登录成功
- **WHEN** 用户提供正确的用户名与密码
- **THEN** 系统签发会话凭证并记录 Login Audit 事件

#### Scenario: 登录失败
- **WHEN** 用户提供错误的密码
- **THEN** 系统拒绝登录并返回错误

### Requirement: RBAC 角色
系统 SHALL 支持 Administrator、Operator、Viewer 三种角色：Administrator 拥有全部权限；Operator 可查看、Run Task、查看日志、Deploy/操作 Managed Application；Viewer 只读。

#### Scenario: Viewer 只读
- **WHEN** Viewer 尝试创建或修改资源
- **THEN** 系统拒绝写操作

#### Scenario: Operator 执行任务
- **WHEN** Operator 触发 Task 执行或部署
- **THEN** 系统允许该操作

### Requirement: Audit Log
系统 SHALL 记录 Login、Script Create/Modify/Delete、Task Create/Modify/Delete/Execute、Schedule Modify、Artifact Upload、Application Create/Modify/Deploy/Upgrade、Node Enable/Disable 事件，包含操作者与时间。

#### Scenario: 记录 Script 修改审计
- **WHEN** 用户修改 Script
- **THEN** Audit Log 新增一条含用户、时间、动作的记录

#### Scenario: 记录节点禁用审计
- **WHEN** 管理员禁用 Node
- **THEN** Audit Log 记录该操作

### Requirement: 审计查询
系统 SHALL 提供 Audit Log 查询 API，支持按操作者/动作/时间过滤。

#### Scenario: 查询审计日志
- **WHEN** 调用审计查询 API
- **THEN** 返回符合过滤条件的审计记录列表
