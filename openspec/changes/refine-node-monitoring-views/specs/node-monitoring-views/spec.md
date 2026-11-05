## Purpose

Provide truthful, identity-preserving node monitoring history and usable node detail views while preserving compatibility with existing agents and historical samples.

## ADDED Requirements

### Requirement: Accurate host capacity and device identity
The Agent SHALL report filesystem total bytes as Blocks×Bsize and used bytes as (Blocks−Bfree)×Bsize per mount and device, and memory free bytes from MemFree; existing disk usage and memory used semantics SHALL remain unchanged. Inventory SHALL optionally classify actual block devices as disk, partition or unknown based on host sysfs and refresh classification with inventory updates. The UI SHALL show filesystem capacity in consistent units and IOPS only for confirmed whole devices; missing historic data and old classifications SHALL remain unknown.

#### Scenario: Reserved filesystem space
- **WHEN** Bfree differs from Bavail
- **THEN** capacity used reflects Bfree while existing disk usage continues to reflect Bavail

#### Scenario: Old agent or inaccessible sysfs
- **WHEN** device classification or new samples are absent
- **THEN** the UI indicates unknown or no data without inventing capacity or whole-device identity

### Requirement: Container identity and collection completeness
The Agent SHALL report each container with its stable ID and name and report discovered count, successfully sampled unique IDs and limit as three same-timestamp dimensionless gauges. Enumeration SHALL detect at least one container beyond the 100 limit. First-sample CPU or network baseline gaps SHALL NOT count as failed container stats. The UI SHALL distinguish legacy name-only history and SHALL show completeness only for fresh, aligned, finite nonnegative integer values with a positive limit and sampled no greater than both discovered and limit; all other cases are unknown. Partial results SHALL disclose sampled count and limit.

#### Scenario: Collection exceeds limit
- **WHEN** at least 101 containers are discovered with limit 100
- **THEN** completeness indicates partial and reports the sampled count and limit

#### Scenario: Same name recreated
- **WHEN** two container IDs share a name
- **THEN** their series remain separate and legacy name-only samples are identified as legacy

### Requirement: Node monitoring interactions and charts
The UI SHALL provide 1, 3, 6, 12 and 24 hour and 7 day presets plus custom range with the existing 30-day validation on node detail and legacy deep links. Noninteractive row areas SHALL navigate to node monitoring while preserving links, actions, text selection, keyboard and modified clicks. Node details SHALL show actual hostname from optional inventory hostname, uptime, distribution, kernel, CPU and memory, unknown for missing values; configured node names SHALL NOT be represented as actual hostnames, and only necessary stale or missing warnings; node metadata failure SHALL NOT block monitoring. Desktop header controls SHALL share a visual row and wrap on narrow screens.

#### Scenario: CPU and memory
- **WHEN** viewing node charts
- **THEN** CPU defaults to the actual total series with an entry to per-core detail, and memory displays independent cached, used, buffers, free and available byte curves without stacking

#### Scenario: Containers
- **WHEN** viewing container charts
- **THEN** every available sampled container has its own curve, More filters by stable ID, CPU may exceed 100 percent, and gaps and failures are not zero-filled

### Requirement: Bounded cancellable independent history
The UI SHALL reuse existing single-metric series history and cache with at most approximately six concurrent requests, no truncation of the series queue, and cache keys including node, metric, series, range and step. Node or range changes SHALL cancel active and queued work; individual failures SHALL be retryable independently and successful More views SHALL reuse cache. Stale responses SHALL NOT overwrite current state.

#### Scenario: Series request fails
- **WHEN** one of several history requests fails
- **THEN** other curves remain visible and the failed curve can be retried independently

#### Scenario: Missing or out-of-order completeness observations
- **WHEN** the latest counts are missing, stale, future-dated, misaligned, invalid or unavailable after an ingest or list failure
- **THEN** completeness is unknown and no missing value is replaced with zero

#### Scenario: Whole logical device and partition
- **WHEN** host sysfs confirms an md or dm whole device and a partition
- **THEN** IOPS includes the whole device and excludes the partition without guessing from its name

#### Scenario: Accurate capacity view and metadata
- **WHEN** a user opens node detail
- **THEN** filesystem charts are named by mount and display actual used and total capacity on the Y axis, while the header presents six host facts with unknown values and retains only necessary abnormal-data feedback
