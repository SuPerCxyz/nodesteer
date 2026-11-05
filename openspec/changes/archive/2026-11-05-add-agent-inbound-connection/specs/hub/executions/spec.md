## ADDED Requirements

### Requirement: Inbound 执行下发与阻塞状态

Hub SHALL 在执行下发前检查目标节点的当前会话方向、`inbound_data_plane` 能力和阶段可用性。对于 inbound 节点，文件传输类、Artifact 类、应用部署类和自升级类执行 SHALL 在数据面不可用时进入 `BLOCKED` 终态并带稳定 `block_reason`，不得进入 RUNNING 后静默超时，也不得报告 SUCCESS。

#### Scenario: 数据面不可用
- **WHEN** 目标 inbound 节点没有 `inbound_data_plane` 或会话已断开
- **THEN** 执行记录为 `BLOCKED`，`block_reason` 为 `inbound_data_plane_unavailable` 或既有离线原因
- **AND** 不发送执行开始消息，不占用 Agent 执行槽

#### Scenario: 阶段未实现
- **WHEN** inbound 的某类执行阶段尚未实现
- **THEN** 执行记录为 `BLOCKED`，`block_reason` 指出阶段
- **AND** 调用方收到可展示的错误，不等待超时

#### Scenario: 正常 inbound 执行
- **WHEN** inbound 节点具备数据面且所需 Artifact/文件已暂存
- **THEN** 执行按既有状态机进入 RUNNING、SUCCESS/FAILED，并保持 Journal 与幂等语义
- **AND** 执行结果与 outbound 模式一致

#### Scenario: 自升级数据面
- **WHEN** inbound 节点触发自升级
- **THEN** Hub 在发送 `RUN_EXECUTION` 前推送并校验 Agent 二进制
- **AND** 推送失败时执行 `BLOCKED`，不替换 Agent 二进制

#### Scenario: 幂等与审计
- **WHEN** 同一幂等键的 inbound 执行被重复触发
- **THEN** 系统返回既有执行记录，不重复下发
- **AND** 阻塞与失败原因按既有审计关联规则可查询
