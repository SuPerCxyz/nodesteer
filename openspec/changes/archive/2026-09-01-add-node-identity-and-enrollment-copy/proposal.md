## Why

The Add Node workflow currently produces commands without a user-specified node identity, and the generated Hub address can be wrong when the UI is accessed through a different host. Copying commands also fails on the HTTP deployment used by the test environment because it relies only on the secure-context Clipboard API.

## What Changes

- Require node name and node IP in the Add Node workflow.
- Default the editable Hub address to the current web page origin and derive the Agent Gateway endpoint from the configured Gateway port.
- Include node identity and the registration token in Native, `docker run`, and standalone `docker compose` enrollment instructions.
- Allow Agent configuration to override the reported hostname and IP through YAML/environment values.
- Add a browser-native clipboard fallback for insecure HTTP deployments and reuse it for existing copy actions.
- Keep administrator authorization and the existing registration protocol; no synthetic node is created.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `node-enrollment`: Enrollment commands now require and carry node identity, use a browser-derived Hub address, and support reliable copying in HTTP deployments.

## Impact

- Agent configuration and HELLO payload generation.
- Hub enrollment metadata API and command generation.
- Nodes page enrollment dialog and shared clipboard actions.
- Enrollment and Agent unit tests; no new dependency.
