## ADDED Requirements

### Requirement: Endpoint 绑定的节点纳管

节点纳管 API 与 UI SHALL 要求提供 `endpoint`，并将其规范化为 `host:port`（默认 18444）。纳管响应 SHALL 区分出站安装命令与入站 bundle：出站命令假设 Agent 能访问 `hub_address`；Hub 在内网、Agent 在公网时 SHALL 提示使用入站 bundle 手动安装，不得声称出站命令在该拓扑可直接执行。

#### Scenario: 必填校验
- **WHEN** 调用纳管 API 时缺少 endpoint、包含 scheme/path 或非法端口
- **THEN** 返回 400，且不创建节点记录

#### Scenario: 出站拓扑
- **WHEN** Agent 能访问 Hub，管理员选择出站命令
- **THEN** 生成既有 native/docker 纳管命令，endpoint 仅作为 Hub 拨号备用地址保存
- **AND** 命令内容不因 endpoint 而改变既有注册语义

#### Scenario: 入站拓扑
- **WHEN** Hub 在内网、Agent 在公网且出站不可达
- **THEN** 纳管响应提供入站 bundle 下载入口和手工安装说明
- **AND** 说明包含 18444 端口、证书/令牌权限和回滚步骤

#### Scenario: 旧 Agent 纳管
- **WHEN** 管理员为一个尚未升级的 Agent 创建节点
- **THEN** endpoint 仍必填并保存
- **AND** 若已签发 bundle，Hub 可按 endpoint 做首次入站引导；HELLO 确认不支持后停止拨号
- **AND** 未签发 bundle 时系统不拨号
