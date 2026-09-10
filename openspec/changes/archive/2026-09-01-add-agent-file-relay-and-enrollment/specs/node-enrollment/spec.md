## Purpose

Give administrators a single Nodes-page workflow for enrolling Native and Docker Agents into an existing Hub using the real registration protocol and copyable commands.

## ADDED Requirements

### Requirement: Show enrollment methods

The Nodes page SHALL provide an Add Node action with Native, `docker run`, and standalone `docker compose` methods.

#### Scenario: Open Add Node

- **WHEN** an administrator opens the Nodes page and selects Add Node
- **THEN** the page displays the three methods and commands populated from the Hub Gateway URL, registration token, and Agent image/version configuration

#### Scenario: Copy enrollment command

- **WHEN** the administrator copies a displayed command or Compose document
- **THEN** the exact command text is copied without placing credentials in persistent browser storage

### Requirement: Use the existing registration flow

Enrollment instructions SHALL configure the existing Agent binary/container with the Hub URL and registration token, preserve persistent Agent state, and not create a synthetic node.

#### Scenario: Native enrollment

- **WHEN** an administrator runs the Native instructions on a Linux host
- **THEN** the Agent starts with the generated configuration, sends `HELLO`, and the resulting real node appears in the Nodes list after acceptance

#### Scenario: Docker enrollment

- **WHEN** an administrator runs the Docker or Compose instructions against an existing Hub
- **THEN** the container uses a persistent `/var/lib/cadentra` volume, connects to the configured Gateway, and registers as a real node

### Requirement: Protect enrollment metadata

The enrollment metadata endpoint SHALL require administrator authorization and SHALL return a clear error when the Hub Gateway URL is not configured or derivable.

#### Scenario: Unauthorized enrollment metadata

- **WHEN** a Viewer, unauthenticated caller, or invalid session requests enrollment metadata
- **THEN** the Hub returns an authorization error and does not expose the registration token

