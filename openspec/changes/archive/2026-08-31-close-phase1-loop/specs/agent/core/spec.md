## Purpose

定义 Agent 在同步、执行、离线恢复和本地配置执行中的完整行为。

## ADDED Requirements

### Requirement: Atomic complete local desired state

The Agent SHALL apply objects and deletions in one local transaction and SHALL advance its revision only after the complete desired state is durable.

#### Scenario: Object no longer matches

- **WHEN** a sync response omits an object that was previously assigned to the Agent
- **THEN** the Agent removes the stale local object in the same transaction
- **AND** it stops any removed schedule

### Requirement: Reliable execution journal

The Agent SHALL persist every execution before starting work, retain unsynced results across restart, and mark them synced only after Hub acknowledgement.

#### Scenario: Hub unavailable during completion

- **WHEN** an execution finishes while Hub is unavailable
- **THEN** its result remains in the local Journal
- **AND** reconnect reconciliation retries it until acknowledged

### Requirement: Unified host and artifact safety

The Agent SHALL verify artifact HTTP status and SHA256 before installation, and SHALL apply path validation consistently for Native and Container HostAdapters.

#### Scenario: Invalid artifact

- **WHEN** an artifact download returns a non-success HTTP status or a mismatched SHA256
- **THEN** the Agent keeps the temporary file out of the install path
- **AND** the application deployment fails without installing the artifact
