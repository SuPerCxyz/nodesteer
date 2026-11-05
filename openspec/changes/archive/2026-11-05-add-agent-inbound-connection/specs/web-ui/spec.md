## ADDED Requirements

### Requirement: 节点入站连接 UI

Web UI SHALL 在添加节点表单中要求填写 `endpoint`（域名或 IP，默认端口 18444），并 SHALL 在节点列表/详情显示只读的连接方式（自动、当前出站/入站/离线）、endpoint、入站能力和证书诊断。UI SHALL NOT 提供 `connection_mode` 手动选择控件。

#### Scenario: 添加节点缺少 endpoint
- **WHEN** 用户提交添加节点表单但 endpoint 为空或非法
- **THEN** 表单阻止提交并显示字段错误
- **AND** 不会发送缺少 endpoint 的纳管请求

#### Scenario: 显示自动连接方式
- **WHEN** 用户查看节点列表或详情
- **THEN** 显示“自动”策略及当前/上次出站或入站方式
- **AND** 不存在强制入站或强制出站的下拉框

#### Scenario: 旧 Agent
- **WHEN** 节点 Agent 不支持入站
- **THEN** 详情显示“不支持入站”，不显示可用的测试或 bundle 操作
- **AND** 出站连接和既有节点操作保持可用

#### Scenario: 入站安装包
- **WHEN** Administrator 打开需要入站连接的节点
- **THEN** 提供下载/轮换入站安装包的入口和适用拓扑说明
- **AND** Operator/Viewer 看不到可执行的管理操作

### Requirement: 入站诊断与数据面反馈

UI SHALL 提供测试连接的分阶段结果（DNS、TCP、TLS、pin、HUB_HELLO、HELLO），并 SHALL 在文件传输、Artifact、应用部署、自升级因入站数据面不可用而返回 409/BLOCKED 时显示明确原因。UI SHALL NOT 把入站待命状态误显示为故障，也不得把 409 显示为泛化服务器错误。

#### Scenario: 测试连接结果
- **WHEN** Administrator 点击测试连接
- **THEN** UI 展示每个阶段的成功/失败和可读原因
- **AND** 已有会话时显示当前连接方向而不是重复测试结果

#### Scenario: 数据面不可用提示
- **WHEN** 用户尝试对缺少 `inbound_data_plane` 的节点创建传输、预取 Artifact、部署或升级
- **THEN** UI 在提交前显示不可用提示，或展示后端返回的稳定错误码
- **AND** 不出现长时间等待或无语义的超时

#### Scenario: 出站正常时的入站待命
- **WHEN** 节点通过出站连接且入站身份已配置
- **THEN** UI 显示入站“待命/未使用”，不标记为错误
- **AND** 仅在入站握手失败或证书异常时显示警告
