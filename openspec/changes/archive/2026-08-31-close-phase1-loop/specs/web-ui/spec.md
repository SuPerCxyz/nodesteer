## Purpose

定义一期 Web UI 必须展示真实后端状态并完成关键操作闭环。

## ADDED Requirements

### Requirement: Real application and node state

The Web UI SHALL display real application health, deployment results, execution history, current assignments, and tasks/schedules matching a node by node, group, or label target.

#### Scenario: Node detail

- **WHEN** a user opens a node detail page
- **THEN** the page lists tasks and schedules whose node, group, or label target matches the node
- **AND** displays current application assignments and health from the API

### Requirement: Action feedback

The Web UI SHALL surface API failures and successful state changes, and SHALL not report configured health-check presence as current health.

#### Scenario: Failed action

- **WHEN** an operation fails at the API or Agent
- **THEN** the UI displays the returned failure reason
- **AND** does not display the operation as successful
