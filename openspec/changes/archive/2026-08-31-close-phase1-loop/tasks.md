# Tasks: Close Phase 1 End-to-End Loop

## 1. Hub consistency and security

- [x] H1 Fix Compose build context and deployment configuration.
- [x] H2 Add configurable TLS for Web/API and Agent Gateway; support `wss://` Agent.
- [x] H3 Complete stable Agent Credential authentication, persistence, and revocation.
- [x] H4 Make Desired State revision/change/audit/notification ordering transactional.
- [x] H5 Make group/label/assignment changes advance revision and notify affected Agents.
- [x] H6 Fix target matching and full-resync removal of stale objects.

## 2. Agent sync and execution

- [x] A1 Add reliable execution result acknowledgement and retry/reconciliation.
- [x] A2 Complete Journal lifecycle for application operations and offline runs.
- [x] A3 Apply Script Environment and parameter validation/values during all execution paths.
- [x] A4 Make runtime Settings consumption explicit and safe.

## 3. Applications and Host Integration

- [x] P1 Unify application downloads with Artifact Cache and strict SHA/HTTP checks.
- [x] P2 Fix ContainerHostAdapter mapping, allowlist, symlink/path escape and host inventory.
- [x] P3 Make config/unit installation failures terminal and rollback default paths correctly.
- [x] P4 Persist application health/deployment state and expose application execution history.

## 4. Web UI and validation

- [x] W1 Fix application assignment editing, health/history display, and Node Detail target filtering.
- [x] W2 Add/adjust tests for every repaired behavior and update traceability.
- [x] W3 Run Go tests, race, vet, frontend build, OpenSpec validate, Docker smoke, and available E2E.
