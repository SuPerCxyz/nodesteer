## Purpose

定义 Managed Application 部署、健康检查、回滚和 Host Integration 的完整闭环。

## ADDED Requirements

### Requirement: Recoverable deployment

The Agent SHALL persist deployment phases, restore the previous binary/config/unit on failed health checks, and perform a post-rollback health check.

#### Scenario: Default config rollback

- **WHEN** an upgrade using the default config path fails its health check
- **THEN** the previous config and unit are restored along with the previous binary
- **AND** the deployment result reports rollback success or failure explicitly

### Requirement: Application state visibility

The Hub SHALL persist the latest per-node application health/deployment result and expose it to the Web UI together with application execution history.

#### Scenario: Health result visible

- **WHEN** an application deployment or health check completes on a node
- **THEN** the latest health and deployment result is persisted for that node
- **AND** the application page displays that result and its execution history
