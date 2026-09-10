## Purpose

定义 Hub 在一期完整闭环中的安全、版本、一致性和执行确认行为。

## ADDED Requirements

### Requirement: Secure Agent transport

The Hub SHALL support configurable TLS for Web/API and Agent Gateway endpoints, and SHALL authenticate an Agent with the registration token only for first registration and a persisted unique credential thereafter.

#### Scenario: Credential reconnect

- **WHEN** an Agent has received and persisted a Hub credential
- **THEN** its next HELLO uses the credential without requiring the registration token
- **AND** a revoked credential is rejected

### Requirement: Transactional desired state

The Hub SHALL commit object or target-membership changes, global revision, change log, audit, and required deletion records before sending notifications.

#### Scenario: Target membership changes

- **WHEN** a group, label, or application assignment changes
- **THEN** affected Agents receive a revision notification after commit
- **AND** the desired revision is durable even if notification delivery fails

### Requirement: Complete reconciliation

The Hub SHALL provide enough desired-state information for an Agent to remove objects no longer assigned to it, including full resync.

#### Scenario: Removed target

- **WHEN** a task target changes so that an Agent no longer matches it
- **THEN** the next sync response identifies the stale task and related schedule for deletion
- **AND** the Agent can converge without a manual restart

### Requirement: Idempotent execution acknowledgement

The Hub SHALL acknowledge execution result uploads and process duplicate uploads by execution ID or scheduled slot without duplicate history.

#### Scenario: Duplicate result upload

- **WHEN** the same execution result is uploaded more than once
- **THEN** the Hub returns an acknowledgement for each accepted duplicate
- **AND** stores only one execution history record
