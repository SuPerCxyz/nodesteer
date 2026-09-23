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

