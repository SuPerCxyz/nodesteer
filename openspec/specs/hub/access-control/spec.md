# hub/access-control Specification

## Purpose
Hub 提供 RBAC 角色（Administrator/Operator/Viewer）、用户认证与 Audit Log，记录关键安全与变更事件。
## Requirements
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

### Requirement: User 输入校验与失败原子性
Hub SHALL 拒绝创建空密码 User 以及创建/更新非法角色；全部校验和密码哈希成功前不得持久化任一修改。认证 SHALL 拒绝空凭据，包括旧版本遗留的空密码用户，而不改写历史用户或撤销已有会话。省略创建角色 SHALL 仍默认 Viewer，更新省略密码 SHALL 保持原密码。

#### Scenario: 空密码及非法角色
- **WHEN** 管理员提交空密码创建或非法角色创建/更新
- **THEN** 返回 400，用户、密码、角色与业务审计不产生部分修改；合法三角色和有效密码仍可使用

#### Scenario: 旧用户空密码登录
- **WHEN** 旧版本已持久化空密码用户并收到空密码登录请求
- **THEN** 返回401且不签发新会话，旧用户数据与已有会话不被批量修改

### Requirement: 入站连接权限与审计

Hub SHALL 限制节点 endpoint 修改、入站 bundle 下载/轮换和连接测试为 Administrator。Operator 与 Viewer SHALL 只能查看不含秘密的连接状态。Hub SHALL 记录 `node.endpoint_update`、`node.inbound_bundle_issue`、`node.inbound_bundle_rotate` 和 `node.connection_test` 审计事件，且 SHALL NOT 在审计、日志或 API 中记录入站令牌明文、私钥或证书私钥材料。

#### Scenario: 越权修改 endpoint
- **WHEN** Operator 或 Viewer 尝试修改节点 endpoint
- **THEN** 返回 403，endpoint 不变，不产生成功审计

#### Scenario: 下载 bundle
- **WHEN** Administrator 下载或轮换入站 bundle
- **THEN** 记录包含操作者、节点、动作和时间的审计
- **AND** 审计详情不包含令牌、私钥或证书 PEM

#### Scenario: 测试连接审计
- **WHEN** Administrator 发起连接测试
- **THEN** 记录 `node.connection_test` 审计
- **AND** 测试结果只包含阶段和错误码，不包含敏感凭据

#### Scenario: 查看权限
- **WHEN** Viewer 查看节点详情
- **THEN** 可以查看 endpoint、连接方式和证书指纹/有效期
- **AND** 不能查看令牌明文或执行管理操作

### Requirement: 节点重命名权限与审计

Hub SHALL 允许 Administrator 和 Operator 修改节点的 `display_name`，Viewer SHALL 只读。Hub SHALL 在值实际变化时记录 `node.rename` 审计，包含节点 ID、旧值、新值、操作者和时间；审计 SHALL NOT 包含 Agent credential、令牌或其他秘密。Viewer 的写请求 SHALL 返回 403，且不得产生部分更新或成功审计。

#### Scenario: Operator 重命名

- **WHEN** Operator 提交合法的 `display_name`
- **THEN** 更新成功并记录 `node.rename` 审计
- **AND** 审计包含 old/new 和操作者

#### Scenario: Viewer 只读

- **WHEN** Viewer 提交 `display_name`
- **THEN** 返回 403，节点名称不变
- **AND** 不写入成功审计

#### Scenario: 未变化不审计

- **WHEN** 用户提交与当前值相同的显示名
- **THEN** 请求成功但不新增 `node.rename`
- **AND** 不产生重复审计噪声

#### Scenario: 与节点其他更新并存

- **WHEN** 同一请求同时更新 status/labels 与 display_name
- **THEN** 所有字段先完成权限与格式校验，再原子更新
- **AND** 任一字段非法时不产生部分修改，重命名审计只在值实际变化时记录

#### Scenario: 审计不含秘密

- **WHEN** 查询重命名审计
- **THEN** 只看到节点 ID、旧/新显示名、操作者和时间
- **AND** 不包含 credential、令牌、证书或私钥

