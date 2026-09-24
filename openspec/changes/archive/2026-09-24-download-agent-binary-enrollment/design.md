## Context

See `proposal.md`. The current POST `/api/nodes/enrollment` creates a pending node and returns three command strings, but the Native string assumes a local `./nodesteer-agent`. The Hub already exposes authenticated Artifact downloads, while enrollment needs a small public, fixed-file binary endpoint because a clean host has no Agent credential yet. The current Dockerfiles and workflow build/publish one architecture.

## Goals / Non-Goals

**Goals:**

- Keep binary download independent from enrollment authentication while retaining Token authentication for the Agent's first HELLO.
- Let the Native command detect the target architecture at execution time instead of asking the administrator to select it.
- Verify the downloaded Native binary before installation.
- Make local x86 development work with one configured binary and make CI Hub images serve both binaries.
- Keep the existing pending-node identity binding and persistent Agent state behavior.

**Non-Goals:**

- No version catalog, upgrade policy, CDN, object storage, or runtime release selection.
- No arbitrary file serving from the Hub.
- No architecture other than Linux amd64 and arm64.

## Decisions

1. **Embed payloads with a fixed-path fallback.** Standard builds append a small manifest and the amd64/arm64 Agent payloads to the Hub executable. The Hub reads its own executable trailer at startup. `agent_binary_amd64_path` and `agent_binary_arm64_path` remain fallback paths for unbundled local development instead of accepting a client-controlled path or general artifact lookup.

2. **Use a public exact endpoint.** Add `GET /api/agent/binary?architecture=amd64|arm64` outside the user/Agent auth middleware. The handler selects the configured path, streams it as `application/octet-stream`, and returns `Content-Length` and `X-Agent-Binary-SHA256`. No query value is used as a filesystem path.

3. **Compute the digest during enrollment generation.** POST enrollment checks the selected binary before creating the pending node, computes its SHA256, and embeds the expected value in the Native command. This prevents an unavailable binary from leaving an orphan pending node and lets the target verify the exact bytes it installs.

4. **Generate a self-contained Native command.** The command checks the target `uname -m`, downloads with `curl`, verifies with `sha256sum`, installs the binary, writes the existing service unit/config, and enables the service. Registration credentials remain only in the generated Agent config; the binary URL is public.

5. **Detect architecture at install time.** Native maps `uname -m` to `amd64`/`arm64`, requests the public binary endpoint, reads its SHA256 response header, and verifies the downloaded bytes. Docker and Compose rely on the published multi-architecture image instead of asking the administrator for a platform.

6. **Preserve the user gesture for copying.** On insecure HTTP origins, run the synchronous textarea fallback before awaiting `navigator.clipboard.writeText`; secure origins continue to prefer the asynchronous Clipboard API.

7. **Build both Agent payloads into Hub artifacts.** A shared bundle script appends both Agent payloads to each CI Hub binary and to the Hub Docker image's single executable. The Hub itself and the Agent image use BuildKit `TARGETARCH`; CI publishes `linux/amd64,linux/arm64` manifests. The ordinary local Go build bundles the host/x86 Agent.

8. **Keep the binary endpoint architecture-specific.** Enrollment generation remains architecture-neutral; only the generated command supplies `amd64` or `arm64` after target-side detection.

## Risks / Trade-offs

- [Public binary endpoint can be fetched by anyone] → Expose only fixed configured Agent binaries, never arbitrary paths; keep enrollment metadata and registration credentials administrator-only.
- [Hub image grows by two Agent binaries] → Accept the bounded duplication for self-contained enrollment; no artifact registry is introduced.
- [A stale configured binary may be served] → Include its SHA256 in the generated command and expose the same digest in response headers; release/build verification records hashes.
- [Target architecture mapping differs across Linux tools] → Accept `x86_64`/`amd64` for amd64 and `aarch64`/`arm64` for arm64, then fail closed for other values.

## Migration Plan

1. Add configuration defaults/overrides and the public endpoint; deploy Hub with the required binary paths.
2. Build/publish the multi-architecture Hub and Agent images and update deployment examples.
3. Deploy the Web/API change; existing registered Agents and pending records remain valid, while new enrollment requests must choose an architecture.
4. If a configured binary is missing, restore the previous Hub build/config; no database migration or existing Agent state change is required.
