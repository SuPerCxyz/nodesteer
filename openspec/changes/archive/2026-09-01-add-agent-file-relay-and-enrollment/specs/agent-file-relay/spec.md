## Purpose

Provide a durable, authenticated Hub-mediated way to move a regular file from one Cadentra Agent to selected target Agents without requiring direct Agent-to-Agent connectivity.

## ADDED Requirements

### Requirement: Create a file transfer

The system SHALL allow an authorized administrator to create a transfer specifying one source Agent, one absolute source path, and one or more target Agent/destination-path pairs.

#### Scenario: Valid transfer creation

- **WHEN** an administrator submits a valid source Agent, source path, and target list
- **THEN** the Hub creates a durable transfer with independently pending target records and begins source upload when the source Agent is available

#### Scenario: Invalid target list

- **WHEN** a request has no targets, duplicate target entries, a missing destination path, or an unknown Agent
- **THEN** the Hub rejects it without creating a transfer

### Requirement: Upload through Hub

The source Agent SHALL upload file bytes to an authenticated Hub data endpoint, and the Hub SHALL bind the upload to the transfer's configured source Agent.

#### Scenario: Authorized source upload

- **WHEN** the configured source Agent uploads the requested file
- **THEN** the Hub accepts only the expected transfer bytes, persists progress, and reports the durable offset or staged result

#### Scenario: Wrong Agent upload

- **WHEN** another Agent or invalid credential attempts the upload
- **THEN** the Hub rejects the request and leaves the transfer unchanged

### Requirement: Verify and stage the source file

The Hub SHALL verify the final byte count and SHA256 before making a file available for delivery.

#### Scenario: Verification succeeds

- **WHEN** the uploaded file reaches the declared size and its SHA256 is computed successfully
- **THEN** the Hub atomically promotes the temporary blob to staged state and starts target delivery

#### Scenario: Verification fails

- **WHEN** the upload is truncated, oversized, or otherwise fails verification
- **THEN** the Hub marks the transfer failed, removes the incomplete staged blob, and sends no delivery request

### Requirement: Deliver to selected target Agents

The Hub SHALL deliver the verified staged file only to the selected target Agents, and each target SHALL verify the file before atomically replacing its destination path.

#### Scenario: Multi-target delivery

- **WHEN** two or more target Agents are selected and the staged file is valid
- **THEN** each target receives an independent delivery request and successful targets do not wait for or mask another target's failure

#### Scenario: Target is offline

- **WHEN** a target Agent is offline when the staged file is ready
- **THEN** its target record remains pending and delivery is retried after that Agent reconnects

#### Scenario: Destination verification fails

- **WHEN** a target download has a size or SHA256 mismatch
- **THEN** the target leaves the existing destination unchanged, reports failure, and removes its temporary file

### Requirement: Transfer lifecycle controls

The system SHALL expose transfer status and allow an administrator to retry failed work or cancel an active transfer.

#### Scenario: Retry failed target

- **WHEN** an administrator retries a failed target
- **THEN** only that target returns to pending and a new delivery attempt is made when the target is available

#### Scenario: Cancel active transfer

- **WHEN** an administrator cancels an active transfer
- **THEN** the Hub marks pending work canceled, asks active Agents to stop, and no later reconnect resumes the canceled work

### Requirement: Recover transfer state

The system SHALL preserve transfer and target state across Hub restart and Agent reconnect.

#### Scenario: Hub restart during upload or delivery

- **WHEN** the Hub restarts with a partial upload or pending target
- **THEN** the transfer resumes from durable state or remains visibly failed/pending without exposing a partial destination file

### Requirement: Enforce file-transfer security

The system SHALL authenticate source and target Agents, enforce configured source and destination path policy, reject non-regular files, cap transfer size, and record audit events for create, retry, cancel, success, and failure.

#### Scenario: Path or size policy violation

- **WHEN** a source or destination path is outside policy, is not a regular file, or exceeds the configured limit
- **THEN** the operation is rejected before bytes are committed

