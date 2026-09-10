## 1. Hub configuration and binary endpoint

- [x] 1.1 Add amd64/arm64 Agent binary path fallback configuration, defaults, environment overrides, and deployment examples.
- [x] 1.2 Implement public architecture-validated `/api/agent/binary` streaming with embedded/fallback payloads, size, SHA256 metadata, and missing-binary errors.
- [x] 1.3 Add Hub tests for public access, architecture selection, digest/size headers, invalid architecture, and unavailable binary paths.

## 2. Enrollment contract and commands

- [x] 2.1 Remove architecture from enrollment input and keep pending-node preparation architecture-neutral.
- [x] 2.2 Generate self-contained Native commands with runtime architecture detection, public binary download, response-header SHA256 verification, binary installation, service unit, and Agent config.
- [x] 2.3 Generate Docker and Compose content using the multi-architecture Agent image without a platform selector.
- [x] 2.4 Add enrollment tests for runtime detection command contents and registration identity preservation.

## 3. Web UI

- [x] 3.1 Remove the architecture selector and submit only node identity/address/Hub fields.
- [x] 3.2 Update enrollment response types and localized copy UI.
- [x] 3.3 Add frontend checks for complete command rendering and clipboard fallback behavior.

## 4. Build and release artifacts

- [x] 4.1 Update Hub and Agent Dockerfiles for BuildKit target architecture and bundled amd64/arm64 Agent payloads.
- [x] 4.2 Update GitHub Actions to bundle amd64/arm64 payloads and publish multi-architecture Hub/Agent images.
- [x] 4.3 Keep local Go build behavior x86-compatible and document bundled/fallback binary behavior.

## 5. Verification and specification sync

- [x] 5.1 Update product/architecture and enrollment documentation for public binary downloads and architecture selection.
- [x] 5.2 Run targeted Go tests, frontend lint/typecheck/build, Dockerfile/build checks, and OpenSpec validation.
- [ ] 5.3 Verify the complete clean-host path: select architecture → generate command → public binary download → checksum → Agent start/pending-node bind.
