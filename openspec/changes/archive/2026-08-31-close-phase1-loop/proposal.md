# Proposal: Close Cadentra Phase 1 End-to-End Loop

## Why

Cadentra 已具备一期 Hub、Agent、同步、执行、应用和 Web UI 的主要代码，但多条关键链路仍存在断点：配置目标变更不能完全收敛、执行结果没有可靠确认、Docker Host Integration 路径不安全、TLS 未接线，应用回滚和 UI 健康状态也不完整。需要把已确认的一期功能从“代码存在”收口为可恢复、可验证的端到端闭环。

## What Changes

- **BREAKING**：为 Hub Web/API 与 Agent Gateway 增加可配置 TLS，并让 Agent 支持 `wss://`。
- **BREAKING**：完善 Agent Credential 首次注册、持久化、重连认证和令牌撤销。
- 修复 Desired State 的目标、组、标签、应用分配变化后的删除与重同步，保证 revision、audit、change log、notification 事务顺序。
- 为执行结果增加可靠确认与重试，覆盖 Manual、Scheduled、Offline 和 Application Operation Journal。
- 统一 Artifact Cache 与应用部署下载链路，严格校验 HTTP、SHA256、路径和配置错误。
- 修复 Native/Container HostAdapter 映射、Host Inventory、allowlist、symlink/path escape、配置和 Unit 回滚。
- 使 Script 参数/环境变量、Settings、Application Health/History 和 Node Detail 展示真正来自有效后端状态。
- 补充单元、race、集成、Docker/Compose smoke 和可执行端到端验证。

## Capabilities

### New Capabilities

- `hub/core`: TLS、Agent Credential、事务性 Desired State 与执行确认。
- `agent/core`: 可靠同步、执行 Journal/ACK、离线恢复和本地配置执行。
- `agent/applications`: 统一 Artifact Cache、Host Integration 安全、部署回滚闭环。
- `web-ui`: 真实应用健康、执行历史、分配和节点目标状态展示。

### Modified Capabilities

- 无：当前项目没有根级 `openspec/specs/` 基线；本 change 将收口要求作为新 capability 记录。

## Impact

影响 `internal/hub`、`internal/agent`、`internal/protocol`、SQLite migration、`cmd/*`、Docker/systemd 配置、`web/src` 与测试。需要保持现有 SQLite 默认部署和一期排除项不变，不引入微服务、消息队列或新的外部基础设施。

