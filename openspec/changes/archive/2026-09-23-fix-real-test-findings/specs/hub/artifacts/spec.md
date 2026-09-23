# hub/artifacts Specification Delta

## ADDED Requirements

### Requirement: Idempotent artifact deletion

The Hub SHALL treat deletion of an already absent Artifact as an idempotent success while continuing to report storage failures for existing objects.

#### Scenario: Retry deletion

- **WHEN** a client repeats DELETE for an Artifact that was already deleted
- **THEN** the Hub returns a successful response
- **AND** no Artifact record or storage file is recreated
