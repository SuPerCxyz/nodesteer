## Purpose

Connect native NodeSteer node metrics and external service observations to auditable existing node tasks, with controlled execution, multi-node orchestration and explicit recovery.

## ADDED Requirements

### Requirement: Manual and automatic task invocation
The system SHALL allow authorized users to manually run existing tasks from monitoring and configure rules that trigger existing task execution for selected nodes based on fresh observations, thresholds, duration or expiry time.

#### Scenario: Switch backend before expiry
- **WHEN** a configured HTTP expiry rule is met and its execution policy permits a trigger
- **THEN** the system invokes the configured node script task and records the observation, rule and execution result

#### Scenario: Observation cannot be interpreted
- **WHEN** an observation is stale, missing or invalid
- **THEN** the system does not interpret it as a valid business status and only triggers collection-failure actions if explicitly configured

### Requirement: Native node metric source
The system SHALL evaluate `node_metric` rules from NodeSteer native samples stored by the Hub. A node metric target SHALL reference a NodeSteer node, metric and dimensions, and SHALL NOT depend on a Nocturne node, series or binding.

#### Scenario: Native threshold rule
- **WHEN** a `node_metric` rule's threshold and duration are met by fresh native samples
- **THEN** the system triggers the configured task and records the sample time, value, rule snapshot and execution

#### Scenario: No native capability
- **WHEN** the target node's Agent does not declare `node_metrics` or has no fresh sample
- **THEN** the rule does not treat the node as healthy or failing and does not trigger a metric-based action

### Requirement: Freshness, gaps and duplicate handling
The system SHALL preserve the existing rule semantics for freshness, maximum sample age, data gaps, out-of-order or conflicting samples, trigger deduplication, cooldown and condition recovery. Missing, stale or invalid data SHALL NOT be interpreted as a valid business value.

#### Scenario: Gap during an active condition
- **WHEN** a gap or conflicting sample interrupts an active condition
- **THEN** the system interrupts the in-progress event and requires fresh continuous samples before triggering

#### Scenario: Repeated observation
- **WHEN** repeated observations represent the same active incident within the configured cooldown
- **THEN** the system prevents duplicate execution according to the configured rule

### Requirement: Trigger safety and audit
The system SHALL persist rule state, cooldown and deduplication information, protect credentials, record trigger context and provide an enable/disable control. External data SHALL NOT be concatenated into shell commands.

#### Scenario: Rule disabled
- **WHEN** a rule is disabled while an execution is pending
- **THEN** further batches, verification and automatic compensation stop, and the already dispatched execution is not falsely reported as cancelled

### Requirement: Multi-node orchestration and compensation
The system SHALL support sequential or batched node tasks, explicit verification and compensation tasks, pause on failure and persistent execution state across restart. It SHALL NOT infer arbitrary script reversibility.

#### Scenario: Batch fails
- **WHEN** a step or its verification fails
- **THEN** further steps pause and configured compensation outcomes, including failures, are visible

#### Scenario: Restart with unknown outcome
- **WHEN** the outcome of a remote action cannot be established after restart
- **THEN** the system avoids blindly repeating it and exposes the state for resolution

### Requirement: Unknown and failed event resolution
The system SHALL distinguish a known failure from an unknown remote outcome. An unresolved event SHALL remain visible and SHALL require an explicit administrator confirmation or abandonment before the rule resumes automatic triggering.

#### Scenario: Known failure
- **WHEN** an action fails with a definitive outcome
- **THEN** the event is recorded as failed and the rule waits for confirmation rather than reporting success or retrying blindly

#### Scenario: Unknown outcome
- **WHEN** the Hub restarts before an execution outcome is recorded
- **THEN** the event is marked unknown, non-terminal executions remain protected, and the administrator can confirm or abandon it explicitly
