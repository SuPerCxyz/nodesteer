## Context

The current protocol has control messages for execution, logs, synchronization, and artifact prefetch, but no file-transfer messages. Agent registration is performed by the Agent sending `HELLO` with the Hub registration token; `POST /api/nodes` only updates an existing node. The Agent Gateway accepts Agent-initiated WebSocket connections, while the current Artifact flow uses authenticated HTTP downloads.

## Goals / Non-Goals

**Goals:**

- Preserve the Agent-initiated connection model and support Hub-mediated transfer when Agents are in different reachable networks.
- Make transfers durable across Hub restarts and Agent reconnects, with independently tracked target results.
- Prevent partial or unverified files from becoming visible at the destination.
- Give administrators copyable Native, `docker run`, and external-Hub Compose enrollment commands.

**Non-Goals:**

- Agent-to-Agent direct connections, P2P, hole punching, overlay networking, or Agent Mesh.
- Directory recursion, symlink transfer, owner/group replication, or a general remote file browser.
- Changing existing Artifact/Application semantics.

## Decisions

### D1: Hub-mediated HTTP data plane

Control WebSocket messages carry transfer metadata and lifecycle notifications only. Source Agents upload to authenticated HTTP endpoints on the Agent Gateway; target Agents download from the same Gateway. This keeps binary payloads out of the control channel and works behind NAT when Agents can maintain their existing Hub connection or reach the Gateway data endpoint.

### D2: Durable transfer state and immutable staged blobs

Hub SQLite stores one transfer record and one target record per destination. Files are written to a transfer-specific `.part` file, validated for size and SHA256, then atomically renamed to an immutable staged blob. Target delivery remains pending when a target is offline and is retried after reconnect.

### D3: Resume by byte offset

Upload and download requests carry an offset/range. A reconnect resumes from the durable temporary-file size where possible. A failed checksum discards the staged blob and leaves the transfer failed; it never dispatches unverified content.

### D4: HostAdapter owns file I/O

Agent source reads and target atomic writes use streaming HostAdapter methods. Native and Docker Host Integration path policy is applied before access; target writes use a same-directory temporary file followed by fsync and rename.

### D5: Existing registration is the enrollment mechanism

The Nodes page does not create a fake node. It obtains administrator-authorized enrollment metadata and renders commands that configure the existing Agent binary with `hub_url` and `registration_token`. Successful `HELLO` registration creates the real node and refreshes the list.

### D6: Explicit public Gateway URL

The Hub has a configurable `gateway_base_url` for externally reachable Agent WebSocket/data URLs. When unset, the server derives a local URL from `base_url` and the Gateway listen port. Generated commands and transfer URLs use this value, avoiding the existing problem of giving public Agents a private Web/API URL.

## Risks / Trade-offs

- Arbitrary file transfer is sensitive → restrict creation to administrators, bind upload identity to the source node, validate both path policies, cap transfer size, and audit lifecycle actions.
- Hub disk and bandwidth are consumed by staged files → use immutable per-transfer storage, explicit limits, and cleanup only after retention policy is later introduced; do not silently delete active data.
- Public-network interruption can leave partial state → use durable offsets, temporary files, checksum verification, and reconnect retries.
- Existing Agents do not understand new messages → increment the protocol version only if compatibility cannot be preserved; unknown transfer messages must not affect existing execution handling, and deployment will update Hub/Agent together for the test environment.

## Migration Plan

1. Apply the additive SQLite migration; existing nodes, tasks, artifacts, and executions remain unchanged.
2. Deploy the new Hub, then update Agents to the compatible version.
3. Configure `gateway_base_url` when the externally reachable Gateway address differs from the Web/API address.
4. Rollback by stopping the new binaries and restoring the previous binaries; transfer records/blobs are additive and can remain unused by the previous version.
