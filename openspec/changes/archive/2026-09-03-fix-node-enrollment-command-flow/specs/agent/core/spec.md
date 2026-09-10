## ADDED Requirements

### Requirement: Persisted enrollment identity

The Agent SHALL load a preassigned Agent ID from generated configuration or environment variables when no persisted Agent ID exists, include it in the first HELLO, and persist the Hub-issued identity and credential after acceptance.

#### Scenario: Generated enrollment configuration

- **WHEN** an Agent starts with a preassigned Agent ID and registration token but no local identity database
- **THEN** its first HELLO includes the preassigned Agent ID and registration token
- **AND** after acceptance it persists the returned Node ID, Agent ID, and Agent Credential
