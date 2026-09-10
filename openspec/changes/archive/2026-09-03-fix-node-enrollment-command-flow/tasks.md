## 1. OpenSpec and API contract

- [x] 1.1 Add the enrollment behavior delta and implementation design.
- [x] 1.2 Define POST enrollment response fields for the preassigned node identity.

## 2. Hub and Agent enrollment binding

- [x] 2.1 Add pending offline node creation with persisted Agent credential.
- [x] 2.2 Add POST enrollment handling and include the preassigned Agent ID in all generated methods.
- [x] 2.3 Load configured Agent ID from Native config and Docker environment, then persist the accepted identity.
- [x] 2.4 Use the GHCR Agent image for Docker and Compose output.

## 3. Web UI and copy behavior

- [x] 3.1 Submit enrollment generation through POST and refresh the Nodes list after success.
- [x] 3.2 Align Generate left / Copy right and copy the selected complete raw command.
- [x] 3.3 Preserve loading, validation, API error, HTTP fallback, and responsive behavior.

## 4. Verification

- [x] 4.1 Add Hub tests for pending record creation, Agent ID binding, and GHCR command output.
- [x] 4.2 Add Agent tests for configured Agent ID in the first HELLO.
- [x] 4.3 Run targeted Go tests, frontend lint/build, OpenSpec validation, and browser-level checks where environment access permits.
