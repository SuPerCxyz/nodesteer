# node-enrollment Specification

## Purpose
Give administrators a single Nodes-page workflow for enrolling Native and Docker Agents into an existing Hub using the real registration protocol and copyable commands.
## Requirements
### Requirement: Show enrollment methods

The Nodes page SHALL provide an administrator-only Add Node workflow with required node name and node address fields accepting an IPv4/IPv6 address or DNS hostname, an editable Hub address defaulted to the current web page origin, and Native, `docker run`, and standalone `docker compose` methods. A successful generation SHALL persist one pending offline node record before returning the enrollment metadata. The Web UI SHALL place “Generate install command” on the left and the Copy action on the right of the same action row after commands are generated, and Copy SHALL copy the complete exact text for the selected method on secure and ordinary HTTP origins without placing credentials in persistent browser storage.

#### Scenario: Open Add Node

- **WHEN** an administrator opens Add Node
- **THEN** the page shows required node name and node address inputs, a Hub address prefilled from the current page origin, and the three enrollment methods

#### Scenario: Generate enrollment commands

- **WHEN** the administrator submits a valid node name, node address, and Hub address
- **THEN** the Hub creates and persists one pending offline node record
- **AND** returns commands containing the node identity, preassigned Agent ID, registration token, supplied address unchanged, and an Agent Gateway URL derived from the supplied Hub address and configured Gateway port
- **AND** the Web UI refreshes the node list so the pending record is visible

#### Scenario: Reject invalid node identity

- **WHEN** the administrator submits an empty/invalid node name or an invalid node address
- **THEN** the Hub rejects the request without generating commands or creating a node record

#### Scenario: Generate failure

- **WHEN** node persistence fails while generating enrollment metadata
- **THEN** the Hub returns an error and the Web UI does not report generation as successful

#### Scenario: Copy enrollment command

- **WHEN** the administrator selects Native, `docker run`, or `docker compose` and clicks Copy
- **THEN** the complete command or Compose document returned for that method is copied without truncation

#### Scenario: Clipboard API unavailable

- **WHEN** the browser does not expose or rejects `navigator.clipboard.writeText`
- **THEN** the UI uses a browser-native fallback and reports an error only if both copy paths fail

### Requirement: Use the existing registration flow

Enrollment instructions SHALL configure the existing Agent binary/container with the Hub URL, registration token, preassigned Agent ID, node name, and node address, preserve persistent Agent state, and bind the first registration to the pending node record.

#### Scenario: Agent reports configured identity

- **WHEN** an Agent starts with generated enrollment configuration
- **THEN** its first HELLO reports the configured node name and node address, falling back to local discovery only when those values are absent

#### Scenario: First Agent connection

- **WHEN** an Agent starts with generated enrollment configuration and sends its first HELLO
- **THEN** the Hub updates the matching pending node record with the real Agent metadata and marks it online
- **AND** no duplicate node record is created

#### Scenario: Native enrollment

- **WHEN** an administrator runs the Native instructions on a Linux host
- **THEN** the Agent starts with the generated configuration, sends HELLO, and the pending node becomes the connected real node

#### Scenario: Docker enrollment

- **WHEN** an administrator runs the Docker or Compose instructions against an existing Hub
- **THEN** the container uses a persistent `/var/lib/cadentra` volume, connects to the configured Gateway, and binds to the pending node record

#### Scenario: Docker enrollment image

- **WHEN** the administrator selects Docker or Compose enrollment
- **THEN** the generated content uses `ghcr.io/supercxyz/cadentra-agent:latest`
- **AND** preserves the `/var/lib/cadentra` persistent volume

### Requirement: Protect enrollment metadata

The enrollment metadata endpoint SHALL require administrator authorization and SHALL return a clear error when the Hub Gateway URL is not configured or derivable.

#### Scenario: Unauthorized enrollment metadata

- **WHEN** a Viewer, unauthenticated caller, or invalid session requests enrollment metadata
- **THEN** the Hub returns an authorization error and does not expose the registration token
