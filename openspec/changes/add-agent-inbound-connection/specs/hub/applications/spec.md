## ADDED Requirements

### Requirement: Inbound 应用部署

当应用部署目标为 inbound 节点时，Hub SHALL 先通过数据面把所需 Artifact 推送到 Agent，再发送 `DEPLOY_REQUEST` 或 `RUN_EXECUTION`。部署请求 SHALL 把 `artifact_url` 置空并标明 `artifact_transport=inbound_push`；Agent SHALL 只从本地 Artifact 缓存取文件。Artifact 缺失或推送失败时，部署 SHALL 置为 `BLOCKED`，不得尝试访问不可达的 Hub URL 或等待到超时。

#### Scenario: Inbound 部署成功
- **WHEN** inbound 节点收到已推送 Artifact 的部署请求
- **THEN** Agent 使用本地缓存完成既有部署流程、健康检查和 Journal 记录
- **AND** 部署结果与 outbound 模式保持相同语义

#### Scenario: Artifact 未暂存
- **WHEN** 部署请求要求 inbound Artifact，但本地缓存不存在对应 SHA
- **THEN** Agent 返回 `BLOCKED artifact_not_staged`
- **AND** 不下载、不替换现有应用、不报告成功

#### Scenario: 无 Artifact 的应用
- **WHEN** 应用定义不包含 Artifact，仅包含脚本/配置/unit
- **THEN** 部署只依赖控制消息并正常执行
- **AND** 不要求数据面可用

#### Scenario: 推送失败
- **WHEN** Hub 在部署前推送 Artifact 失败
- **THEN** Hub 不发送部署请求，并把执行标记为 `BLOCKED`
- **AND** 失败原因可被 UI 和审计查询
