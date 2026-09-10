## Why

Cadentra can currently download Hub-managed artifacts to Agents, but it cannot collect an arbitrary file from one Agent and deliver it to selected Agents. The Nodes page also only lists registered nodes, so operators have no guided way to enroll a new Native or Docker Agent into an existing Hub.

## What Changes

- Add a Hub-mediated file transfer workflow: source Agent upload, Hub staging, selected target Agent delivery, checksum verification, atomic destination replacement, per-target status, retry, cancel, persistence, and audit.
- Keep binary data off the control WebSocket; use authenticated HTTP data endpoints exposed through the Agent Gateway.
- Add Node enrollment instructions for Native, `docker run`, and standalone `docker compose`, using the configured Gateway URL and registration token.
- Add an administrator-only API and Nodes page entry point for generating and copying enrollment commands.
- Add persistent transfer records, recovery after Hub/Agent reconnect, and isolated target failures.
- Update the product and architecture documentation and add unit, integration, and end-to-end coverage.

## Capabilities

### New Capabilities

- `agent-file-relay`: Transfer a regular file from one Agent through Hub storage to one or more selected target Agents.
- `node-enrollment`: Generate authenticated Native, Docker, and Docker Compose Agent enrollment instructions from the Nodes page.

### Modified Capabilities

None.

## Impact

- Go protocol, Hub Gateway, Hub API, Hub persistence, Agent connection/host/file handling, and configuration.
- React Nodes page, API client, translations, and transfer status UI.
- Docker Compose and Native packaging examples.
- SQLite schema migration and cross-layer tests.
