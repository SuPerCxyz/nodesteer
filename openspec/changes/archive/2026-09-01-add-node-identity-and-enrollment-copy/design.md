## Context

The Nodes page currently calls `GET /api/nodes/enrollment`, while the Hub builds static Native/Docker commands from server configuration. Agents report the operating system hostname and discovered local IP from `HELLO`. Browser command copying calls `navigator.clipboard.writeText` directly, which is unavailable or rejected on an HTTP origin.

## Goals / Non-Goals

**Goals:**

- Carry an operator-selected node name and IP through generated installation instructions into the first Agent HELLO.
- Make the current browser origin the default operator-facing Hub address while preserving the separate Web and Agent Gateway ports.
- Make command copying work on both secure and ordinary HTTP origins.
- Validate input at the API boundary and cover the user-visible registration path.

**Non-Goals:**

- Changing the registration handshake or node identity model.
- Automatically installing an Agent from the Hub.
- Adding token rotation or pre-created node records.

## Decisions

1. **Use enrollment query parameters rather than a new endpoint.** The existing administrator-only metadata endpoint remains the single source for all three commands. `node_name`, `node_ip`, and optional `hub_address` are supplied by the current dialog; callers without `hub_address` retain the configured Gateway fallback.
2. **Derive Gateway URL from the browser Hub address.** The UI sends its current origin. The Hub keeps the configured Gateway port and replaces only the host/scheme source used for command generation, so a Web UI on `:8080` still produces an Agent URL on `:8443`.
3. **Use explicit Agent config overrides.** Native YAML uses `node_name`/`node_ip`; Docker variants use `CADENTRA_NODE_NAME`/`CADENTRA_NODE_IP`. The Agent falls back to the existing hostname/IP discovery when these values are absent.
4. **Use Clipboard API with DOM fallback.** Try `navigator.clipboard.writeText` first, then use a temporary readonly textarea and `document.execCommand('copy')` for HTTP origins. No dependency is added.

## Risks / Trade-offs

- [The configured Gateway port may not match a reverse proxy topology] → The Hub address remains editable and explicit `gateway_base_url` remains authoritative when configured.
- [The generated registration token is visible to an administrator in the command] → The endpoint remains administrator-only and the token is never stored in browser storage.
- [Legacy Agents ignore new environment/config keys] → New keys are additive and absent values preserve existing discovery behavior.

## Migration Plan

1. Deploy the Hub and Agent binaries together or upgrade the Hub first; the registration protocol remains version 1 and new configuration fields are optional.
2. Use the Nodes page to generate commands with the desired node identity.
3. If a generated command is invalid for a reverse proxy, edit the Hub address field to the externally reachable web origin; the configured Gateway port is retained.

## Open Questions

None.
