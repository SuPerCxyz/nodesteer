## MODIFIED Requirements

### Requirement: Secure Agent transport

The Hub SHALL support configurable TLS for Web/API and Agent Gateway endpoints, and SHALL authenticate an Agent with the registration token only for first registration and a persisted unique credential thereafter.

#### Scenario: Credential reconnect

- **WHEN** an Agent has received and persisted a Hub credential
- **THEN** its next HELLO uses the credential without requiring the registration token
- **AND** a revoked credential is rejected

#### Scenario: Rejected HELLO does not leave node online

- **WHEN** an Agent HELLO is rejected because the credential is invalid, the Agent is not registered, or the registration token does not match
- **AND** the rejected HELLO can be attributed to an existing node
- **THEN** a node that is currently online with no active Agent session is transitioned to offline before the rejection closes the connection
- **AND** a node with an active Agent session keeps its status unchanged
- **AND** maintenance and disabled statuses are preserved
