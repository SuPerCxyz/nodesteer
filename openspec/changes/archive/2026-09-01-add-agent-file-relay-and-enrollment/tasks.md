## 1. Specification and configuration

- [x] 1.1 Validate proposal, design, and capability specs
- [x] 1.2 Add Gateway public-base configuration and command/image defaults
- [x] 1.3 Add additive Hub transfer schema and store methods

## 2. File transfer protocol and Hub

- [x] 2.1 Add transfer models and versioned control message payloads
- [x] 2.2 Add durable transfer manager with upload staging, checksum validation, target dispatch, retry, cancel, and reconnect recovery
- [x] 2.3 Add authenticated Gateway HTTP upload/download/cancel handlers
- [x] 2.4 Add transfer REST endpoints and audit integration

## 3. Agent file path and transfer handling

- [x] 3.1 Extend HostAdapter with streaming read and atomic streaming replace
- [x] 3.2 Add Agent upload/delivery/cancel handlers with range resume and checksum validation
- [x] 3.3 Enforce regular-file, absolute-path, allowlist, size, temporary-file, fsync, and atomic-rename rules
- [x] 3.4 Persist/recover active transfer state and reconnect behavior

## 4. Node enrollment

- [x] 4.1 Add administrator-only enrollment metadata API
- [x] 4.2 Add Nodes-page Add Node UI with Native, Docker run, and external Compose commands
- [x] 4.3 Add copy actions, translations, refresh feedback, and route compatibility
- [x] 4.4 Update Native/Docker packaging examples for an external Hub

## 5. Tests and documentation

- [x] 5.1 Add protocol, store, host streaming, and path-policy unit tests
- [x] 5.2 Add Hub/Agent WebSocket and HTTP transfer integration tests
- [x] 5.3 Add multi-target isolation, offline retry, restart, checksum, cancel, and authorization tests
- [x] 5.4 Update PRODUCT, ARCHITECTURE, README, and test-environment documentation
- [x] 5.5 Run format, lint, typecheck, Go tests, frontend build, and OpenSpec validation
- [x] 5.6 Build and deploy Hub/Agents to KVM2 and validate enrollment plus a real transfer
