# hub/applications Specification

## Purpose
Hub 管理不可变 Artifact 与 Managed Application 定义、Assignment 与部署触发，Artifact 通过 HTTP(S) 下载，Binary Payload 不经过控制长连接。
## Requirements
### Requirement: Artifact 管理
系统 SHALL 支持 Artifact 上传与查询，Artifact 为不可变对象（ID、Name、Version、Architecture、Filename、Size、SHA256、Storage Location），内容变化必须创建新 Artifact；一期支持 amd64 与 arm64。

#### Scenario: 上传 Artifact
- **WHEN** 管理员上传 Artifact 文件
- **THEN** 系统计算 SHA256、持久化元数据并存储文件，生成 Artifact 记录

#### Scenario: Artifact 内容变化
- **WHEN** 相同 Name/Version 上传不同内容
- **THEN** 系统创建新的 Artifact 记录而非覆盖旧记录

### Requirement: Artifact HTTP 下载
系统 SHALL 提供 HTTP(S) Artifact 下载端点供 Agent 获取二进制，控制通道只传输元数据与 Prefetch 指令。

#### Scenario: Agent 下载 Artifact
- **WHEN** Agent 需要 Artifact 二进制
- **THEN** Agent 通过 HTTP(S) 端点下载并校验 SHA256

### Requirement: Artifact Prefetch
系统 SHALL 支持在真正执行前向 Agent 发送 ARTIFACT_PREFETCH 指令，用于 Scheduled Application Deployment 与 Offline Deployment。

#### Scenario: 部署前 Prefetch
- **WHEN** Hub 下发 Scheduled Application Deployment
- **THEN** Hub 提前发送 Prefetch，Agent 下载并缓存 Artifact 以备执行

### Requirement: Managed Application 定义
系统 SHALL 支持创建/编辑 Managed Application（Name、Version、Artifact、Binary Path、Arguments、Environment、Configuration、systemd Unit、Health Check）并维护 Revision History；Definition 更新不等于自动 Deploy/Upgrade。

#### Scenario: 更新定义不自动部署
- **WHEN** 管理员更新 Application Definition
- **THEN** 系统同步定义到 Agent 并 Prefetch，但不自动触发部署，部署由 Manual 或 Schedule 触发

### Requirement: Application Assignment
系统 SHALL 支持将 Application 分配到指定 Node，Sync Manager 据此计算 Agent Desired State。

#### Scenario: 分配应用
- **WHEN** 管理员将 Application 分配给目标 Node
- **THEN** 该 Node 的 Agent 同步 Application Definition 与 Artifact 元数据

### Requirement: 应用操作触发
系统 SHALL 支持通过 Task（Application Deploy/Operation）触发部署与 Start/Stop/Restart/Upgrade。

#### Scenario: 触发部署
- **WHEN** 管理员执行 Application Deploy Task
- **THEN** Hub 下发 Execution 到目标 Agent，Agent 执行部署流程

### Requirement: Health Check 状态展示
系统 SHALL 支持查看 Managed Application 的 Health 状态（Healthy/Unhealthy）。

#### Scenario: 查看应用健康
- **WHEN** Agent 完成部署并执行 Health Check
- **THEN** Hub 展示应用当前健康状态

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

