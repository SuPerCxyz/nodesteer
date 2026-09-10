## Context

See `proposal.md`. The current GET `/api/nodes/enrollment` only renders metadata. Agent registration generates a fresh Agent ID when the local state is empty, so a node row created before the first connection would otherwise be orphaned. The existing protocol already carries `HelloPayload.AgentID`; the smallest compatible binding is to preassign that value and reuse the existing `RegisterOrUpdate` path.

## Goals / Non-Goals

**Goals:**

- Make the generating form submission the single persisted enrollment action.
- Keep GET metadata side-effect free for compatibility, while the Web UI uses POST.
- Reuse the existing Agent credential and HELLO registration flow.
- Make all three command values copy from the same raw response strings.

**Non-Goals:**

- No new node lifecycle state or cleanup/expiration policy for pending records.
- No change to registration-token authentication semantics.
- No Docker build, registry publish, or deployment operation.

## Decisions

1. **Use POST for generation.** GET remains read-only and compatible; POST validates the same fields, creates the pending record, and returns metadata. This avoids a side effect on a cacheable GET request.
2. **Use the existing `agent_id` field as the preassigned binding key.** The Hub generates the ID and credential, stores both, and includes only the Agent ID in generated configuration. The registration token still authorizes first connection; `RegisterOrUpdate` updates the existing row and returns its stored credential.
3. **Represent a pending node as offline.** Existing list/detail pages already understand `offline`, so no schema migration or new status enum is needed. `last_seen` remains empty until the Agent connects.
4. **Use the repository's GHCR image.** README and CI define `ghcr.io/supercxyz/cadentra-agent:latest`; both Docker output formats use this same value.
5. **Keep copy source raw and method-specific.** The button passes the selected response property directly to the existing clipboard helper. The action row uses an explicit non-submit button and remains right-aligned with responsive wrapping.

## Risks / Trade-offs

- [Pending records can remain offline if an installation is abandoned] → Keep this change limited to creation and report; cleanup/expiration is a separate product decision.
- [An enrollment command contains the registration token and preassigned Agent ID] → Preserve the existing administrator-only endpoint and do not persist command text or credentials in browser storage.
- [Repeated submissions create multiple pending records] → Each successful form submission represents a distinct enrollment request; the UI resets generated output when inputs change and the Agent ID binding prevents duplicates after connection.

## Migration Plan

No database migration is required. Deploy Hub and Agent binaries together with the updated Web bundle. Existing Agents continue using their persisted Agent ID/credential; existing GET metadata callers retain their previous behavior.
