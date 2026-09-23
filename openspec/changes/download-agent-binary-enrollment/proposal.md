## Why

Native enrollment commands currently assume that `nodesteer-agent` and its unit file are already present on the target host. A clean VM therefore cannot be enrolled from the Nodes page, and the command cannot choose the Agent binary for the target CPU architecture.

## What Changes

- Add a public, read-only Hub endpoint that serves the embedded Agent binary for `amd64` or `arm64`.
- Make generated Native instructions detect `x86 (amd64)` or `ARM (arm64)` on the target host and generate matching Docker/Compose content without an architecture form field.
- Make Native instructions download the selected binary from Hub, verify its SHA256, install it, and configure the existing Agent service.
- Keep the registration token in the generated Agent configuration; binary download itself does not require a token.
- Keep configured binary paths as a fallback for unbundled local development while making standard Hub builds self-contained.
- Keep local Hub builds on the existing x86 path while making CI build/publish amd64 and arm64 Hub/Agent binaries and multi-architecture images.
- Add endpoint, runtime-architecture detection, command-generation, binary-integrity, clipboard, and build coverage.

## Capabilities

### New Capabilities

- `hub/agent-binary`: Public architecture-aware Agent binary serving with fixed configured sources and integrity metadata.

### Modified Capabilities

- `node-enrollment`: Generate runtime-architecture-aware, self-contained Native/Docker enrollment instructions and reliable command copying.

## Impact

- Hub API and configuration, enrollment command generation, Web Nodes dialog and localization.
- Hub and Agent Dockerfiles, GitHub Actions binary/image build matrix, and deployment examples.
- OpenSpec node-enrollment contract plus new Hub Agent binary contract.
- No new runtime dependencies or database migration.
