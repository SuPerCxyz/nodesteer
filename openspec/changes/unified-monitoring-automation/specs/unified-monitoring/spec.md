## Purpose

Provide native monitoring for NodeSteer-managed Linux nodes and external services, using one Agent for metric collection and automation, Hub-owned metric history, and the existing NodeSteer page templates.

## ADDED Requirements

### Requirement: Unified monitoring presentation
The system SHALL present node monitoring, history and external service monitoring within the existing NodeSteer page templates, components and styling, without reusing the previous Nocturne page style or requiring a Nocturne page.

#### Scenario: Node metrics
- **WHEN** an authorized user opens a monitored node
- **THEN** the system shows its current metrics, history, events and related task records using the existing NodeSteer interface

#### Scenario: Node operation
- **WHEN** the user chooses a node action from monitoring
- **THEN** the system navigates to the existing node detail at `/agents/{id}` rather than a monitoring-specific node page

### Requirement: Single Agent metric collection
The system SHALL collect CPU, memory, load, disk and network metrics through the same NodeSteer Agent that executes tasks. The Agent SHALL declare a `node_metrics` capability, default to a 30-second interval, and support a bounded interval range of 5 to 300 seconds.

#### Scenario: Native Agent reports metrics
- **WHEN** a Native or Docker Host Integration Agent with metrics enabled connects
- **THEN** it declares `node_metrics` and reports samples on its configured interval

#### Scenario: Metrics unavailable
- **WHEN** metrics are disabled or the deployment mode cannot read host metrics
- **THEN** the Agent does not declare `node_metrics` and the system shows metrics as unavailable instead of fabricating values

#### Scenario: Old Agent
- **WHEN** a connected Agent does not declare `node_metrics`
- **THEN** the node remains manageable and executable, but its monitoring status is unavailable and metric-based rules do not trigger

### Requirement: Native metric protocol and quality
The system SHALL accept `NODE_METRICS` batches containing `node_id`, `sequence`, `timestamp`, `metric`, `dimensions`, `value` and `quality`. Quality `1` SHALL mean a valid value and quality `2` SHALL mean missing with a null value. A real zero SHALL remain a valid sample and SHALL NOT be treated as missing.

#### Scenario: Missing sample
- **WHEN** a collector cannot read a metric
- **THEN** the Agent reports a missing sample with a null value and quality `2`, or no sample for that round, rather than reporting zero

#### Scenario: Zero value
- **WHEN** CPU usage is genuinely 0 percent
- **THEN** the sample is stored and displayed as a valid zero, distinct from a gap or missing value

#### Scenario: Out-of-order or duplicate samples
- **WHEN** a batch is retried, duplicated or arrives out of order
- **THEN** the Hub deduplicates and orders samples without overwriting a confirmed valid point with conflicting data

### Requirement: Bounded buffering and reconnect
The Agent SHALL buffer metric batches in a bounded queue while disconnected and retransmit them in order after reconnect. The system SHALL preserve visible gaps when buffered data is dropped or unavailable.

#### Scenario: Disconnect and reconnect
- **WHEN** the Hub connection is lost and later restored
- **THEN** the Agent retransmits buffered batches in order, and the system shows any dropped or missing interval as a gap rather than a continuous line

### Requirement: Native metric history, aggregation and retention
The Hub SHALL store native metric samples in SQLite and provide queryable raw and aggregated history. The default retention SHALL be raw 7 days, 1-minute 90 days, 5-minute 1 year and 1-hour 2 years.

Current implementation uses fixed code constants; retention configuration is not yet available.

#### Scenario: Range query
- **WHEN** an authorized user requests a node metric over a time range
- **THEN** the Hub selects an appropriate aggregation tier and returns points, tier, step and gaps for that range

#### Scenario: Late or corrected sample
- **WHEN** a late or corrected sample arrives for an existing bucket
- **THEN** the affected aggregation bucket is recomputed and the query reflects the corrected data

#### Scenario: Retention cleanup
- **WHEN** samples exceed their configured retention
- **THEN** the Hub removes expired data without deleting unexpired tiers or unrelated monitoring records

### Requirement: Node overview and history
The system SHALL build the monitoring overview by traversing NodeSteer nodes directly, showing online status, CPU, memory, load, disk, network, latest sample time and alerts. It SHALL NOT require a separate monitoring-node binding.

Current implementation returns a placeholder alert count of 0; rule alert aggregation is not yet implemented.

#### Scenario: Overview without bindings
- **WHEN** a NodeSteer node exists and has native samples
- **THEN** it appears in the monitoring overview with its current metrics and latest sample time without any binding step

#### Scenario: History drill-down
- **WHEN** the user selects a node metric in the overview
- **THEN** the system shows its raw or aggregated history and preserves gaps and missing values

#### Scenario: Stale or absent data
- **WHEN** a node has no recent samples or no metric capability
- **THEN** the overview shows unavailable, waiting or stale state rather than zero values

### Requirement: External targets and observations
The system SHALL manage external HTTP(S), TCP and ICMP targets with probe location, timeout, current and historical status and response time. It SHALL reserve target kinds and validation branches for future database and message queue adapters, without claiming implemented adapters.

#### Scenario: Probe failure
- **WHEN** a configured target cannot be reached
- **THEN** the system shows the failed observation, time and reason rather than fabricating a zero or healthy value

### Requirement: Generic HTTP response values
The system SHALL support authenticated HTTP collection and extracting JSON fields as state, number, boolean or timestamp, with explicit freshness and expiry semantics; credentials SHALL be protected and excluded from logs.

#### Scenario: Approaching expiry
- **WHEN** an HTTP response contains the configured expiry timestamp
- **THEN** the system records the actual timestamp and makes it available to expiry rules

#### Scenario: Missing field
- **WHEN** the requested field is missing or invalid
- **THEN** the system records an error or unknown observation rather than inventing a value

### Requirement: Metric history integrity
The system SHALL preserve distinctions between zero and missing data, sampling times and gaps, and SHALL use stable NodeSteer node identity without guessing from names or addresses.

#### Scenario: Node reconnects
- **WHEN** monitoring data is delayed or missing while a node disconnects and later reconnects
- **THEN** historical results preserve the gaps and any valid backfilled observations

#### Scenario: Conflicting samples
- **WHEN** two samples for the same node, metric, dimensions and timestamp conflict
- **THEN** the system records the conflict or rejects the later sample according to the quality rules, and does not silently choose a value

### Requirement: Nocturne removal
The system SHALL NOT require Nocturne for node metrics, history, bindings or monitoring pages. Nocturne binding, proxy, page entry, environment variables and deployment guidance SHALL be removed. Existing Nocturne history SHALL NOT be migrated or displayed as current NodeSteer monitoring data.

#### Scenario: No Nocturne configured
- **WHEN** NodeSteer is deployed without any Nocturne service
- **THEN** native node monitoring and external target monitoring remain available according to their implemented capabilities

#### Scenario: Existing Nocturne history
- **WHEN** a deployment previously used Nocturne
- **THEN** old Nocturne history is not imported, backfilled or shown in the new native monitoring views
