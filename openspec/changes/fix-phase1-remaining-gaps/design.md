# Design: Fix Phase 1 Remaining Gaps

## 概述

基于三路核查（Agent/Hub/Web UI）发现的一期缺口修复。核心是正确性项（Fail Closed、RunUser 接线、App 部署链路）与前端展示补齐。

## U1 Remote Condition Fail Closed

**现状**：Hub `resolveRemoteState` 对不可解析状态返回字符串 `"UNKNOWN"`（`gateway.go:373-386`），Agent `queryRemoteCondition` 只对空串返回 `false`（`agent.go:907-910`），`engine.go` 的 remote 分支只在 `err != nil || !ok` 时 Fail。导致 `UNKNOWN` 被当普通值参与比较。

**修复**：
- `queryRemoteCondition`：Hub 返回值为 `"UNKNOWN"` 时返回 `("", false, nil)`（触发 engine 的 Fail Closed 路径）。
- 同时将 `"UNKNOWN"` 常量集中定义（protocol 包），Hub 与 Agent 共用。

## U2 RunUser 接线

**现状**：`runner.go` 有 `RunUser` 底层支持，但模型/协议/Hub 下发/Agent 设置全链路缺失。

**修复**：
- `models.Task` / `models.Script` 增加 `RunUser string`。
- `protocol.RunExecutionPayload` 增加 `RunUser string,omitempty`。
- Hub `execution.go` 构建 payload 时从 Task/Script 填充。
- Agent `executeTask` 设置 runner `cfg.RunUser`。
- 前端 Task/Script 编辑表单增加 RunUser 输入。

## U3 Agent-owned 调度 app deploy/upgrade

**现状**：`HandleRunOperation`（`application.go:69-84`）只调用 `operate`（start/stop/restart），deploy/upgrade 无分支；`deploy` 依赖 `DeployRequestPayload` 的 ArtifactURL/BinaryPath/UnitName/Config 等字段，而 Agent 调度构建的 `RunExecutionPayload` 只有 AppID/AppOperation。

**修复**：
- `HandleRunOperation` 增加 deploy/upgrade 分支：从本地应用定义（`loadApplication`）解析 binary_path/unit/config/health_check，并从本地缓存 artifact 加载二进制。
- 若 artifact 不在缓存，通过 Hub 下发 pre_download/artifact 下载（Agent 有 agentToken，可直接访问 Hub artifact 下载端点）。
- 复用 `deploy` 主流程（构造完整 `DeployRequestPayload`）。

## U4 Prefetch 覆盖 scheduled/offline

**现状**：Hub 仅在手动 Deploy/Upgrade 时发 PREFETCH（`application.go:147-150`）。

**修复**：
- Agent-owned Schedule 触发 app deploy/upgrade 时，Agent 内部先确保 artifact 缓存（等价于 prefetch）。
- Hub 在同步 app 定义给 Agent 时不下发完整 binary（保持定义/部署分离），由 Agent 部署时按需下载。
- 协议不变，Agent 部署时通过 `downloadArtifact` 复用缓存机制。

## U5 Settings 运行期消费

**现状**：`hub.go:167-179` 写默认值，`api.go:1190-1219` GET/PUT，但运行期用 `cfg.*`。

**修复**：
- Hub 各管理器从 `store.GetSetting` 读取运行值（带 fallback 到 cfg 默认）。
- `changelog_window` → SyncManager 读取；调度 tick 精度独立配置。
- heartbeat 超时读取 `heartbeat_interval_sec` 等。

## U6 stderr 分片标记

`agent.go:663` 恒 `Stream:"stdout"` → 按实际输出流设置。

## U7 Inventory

`inventory.go` 增加 OSVersion（`/etc/os-release`）采集；CPU MHz 从 `/proc/cpuinfo` 读取。

## U8 App 部署 Capability 校验

`application.go` dispatchDeploy 前校验节点 capability（仿 `execution.go:106-110`）。

## U9 Rollback 完整性

`rollback`（`application.go:414-425`）补：恢复 config、恢复 unit、daemon-reload、回滚后 Health Check。

## U10-U13 前端

见 spec；后端补 `application_revisions` 读取 API、`/executions?task_id=` 前端调用、Group 更新 API（已有 UpdateGroup store 方法，需 API 端点）。

## U14

- Hub 调度 tick 独立（默认 5s）或从 settings 读取。
- changelog 启动清理 + 窗口读取。
- Sync Error 状态：心跳/同步异常时 gateway 写 `"error"`。
