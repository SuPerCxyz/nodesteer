# agent/applications Specification Delta

## MODIFIED Requirements

### Requirement: Recoverable deployment

The Agent SHALL treat a systemd application health check as successful only after two consecutive `active` samples separated by a short settling interval. A transient `active` state followed by `activating`, `auto-restart`, `failed`, or a non-zero exit result SHALL fail the deployment and trigger the existing rollback path.

#### Scenario: Stable systemd health

- **WHEN** a managed systemd unit reports `active` for both health samples
- **THEN** the deployment may complete successfully

#### Scenario: Default config rollback

- **WHEN** an upgrade using the default config path fails its health check
- **THEN** the previous config and unit are restored along with the previous binary
- **AND** the deployment result reports rollback success or failure explicitly

#### Scenario: Transient systemd health

- **WHEN** a managed systemd unit reports `active` and then leaves the active state during the settling interval
- **THEN** the deployment is marked failed
- **AND** the previous binary, config, and unit are restored and checked
