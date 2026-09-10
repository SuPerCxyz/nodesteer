# hub/configuration Specification Delta

## ADDED Requirements

### Requirement: Explicit administrator credentials

The Hub SHALL not provide a default administrator password and SHALL refuse to start unless both administrator username and password are explicitly configured. Docker Compose SHALL require `ADMIN_PASSWORD` during configuration resolution.

#### Scenario: Missing administrator password

- **WHEN** the Hub starts without an explicit administrator password
- **THEN** startup fails with a non-sensitive configuration error

#### Scenario: Compose password gate

- **WHEN** Docker Compose configuration is resolved without `ADMIN_PASSWORD`
- **THEN** configuration resolution fails before the Hub service starts
