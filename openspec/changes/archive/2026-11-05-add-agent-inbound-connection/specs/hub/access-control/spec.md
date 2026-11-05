## ADDED Requirements

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
