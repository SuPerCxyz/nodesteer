# Autonomous Web E2E Test Report

## 1. Scope

- Base URL: `http://192.168.100.249:8080`
- Environment: KVM2 real Hub deployment, administrator browser session
- Browser/tool: agent-browser 0.32.4, Chrome for Testing 151
- Run ID: `20260916-214000`
- End: `2026-09-16T23:04:28+08:00`
- Scope: source/runtime discovery, primary routes, authenticated UI, API smoke, CRUD/state continuity, negative confirmations, execution/logs, settings persistence, artifact download, and transfer retry
- Business code changes: none made during this test run

## 2. System Model

Static discovery found 14 primary pages, 32 route entries including create/edit/detail/run routes, 12 resource types, 13 main API areas, and administrator/operator/viewer role definitions. Runtime exploration reached the dashboard plus the 13 primary authenticated navigation pages and the main create/edit/detail routes used below.

Primary resources modeled:

`node`, `group`, `script`, `task`, `schedule`, `artifact`, `application`, `execution`, `transfer`, `audit_log`, `user`, and `settings`.

Important state dependencies:

```text
authenticated
  → node/script/task/schedule/group/artifact existence
  → task run
  → execution pending/terminal
  → execution detail/logs
```

## 3. Coverage Summary

Values are calculated from `feature-inventory.json`, `state-graph.json`, and `workflows.json`:

```text
Features discovered: 56
Features tested: 40
Features passed: 38
Features failed: 0
Features blocked: 10

States discovered: 17
Transitions discovered: 12
Transitions tested: 9

Workflows generated: 8
Workflows executed: 6

CRUD lifecycles expected: 5
CRUD lifecycles tested: 3

Negative paths expected: 8
Negative paths tested: 5
```

The machine-readable source of truth is `.e2e/coverage.json`; no 100% claim is made.

## 4. Critical Workflows Tested

- Authentication: invalid credentials remain on `/sign-in`; controlled login reaches dashboard; logout confirmation redirects to `/sign-in?redirect=%2Fusers`; Chinese/English switching works.
- Script lifecycle: create `e2e-script-20260916-a81c`, update content/description, revision advanced `r1 → r2`, search and refresh verified persistence, delete confirmation cancel/confirm completed.
- Task execution: create `e2e-task-20260916-a81c` targeting `ubuntu2604-agent`, open run confirmation, execute successfully, verify `SUCCESS`, execution detail and `E2E_TASK_SUCCESS` logs, then delete task.
- Schedule lifecycle: create Cron `0 0 * * *`, edit to `5 0 * * *`, disable, delete; both confirmation branches exercised.
- Group lifecycle: create static group, change description and membership, delete with confirmation.
- Artifact lifecycle: upload `e2e-artifact-20260916-a81c` version `1.0.0`, download to evidence, delete with confirmation.
- Settings: change heartbeat `30 → 31`, save, refresh persistence, restore to `30` and verify after refresh.
- Transfer negative path: create transfer from the known source path to a test destination, observe retained `FAILED`, inspect source-path error through authenticated API, invoke retry, and observe failure retained with retry available.

## 5. Failures

### FAIL-001 — Medium（已修复）

Delete confirmation for a schedule displayed the raw schedule UUID instead of a human-readable task/expression label. Reproduced twice. Original evidence: `.e2e/evidence/screenshots/schedule-delete-confirm-uuid.png`.

This is separate from the earlier list UUID cleanup: general list rows hide UUIDs, but this confirmation title still rendered the identifier. Fixed in the post-run UI pass; the confirmation now uses the task name plus Cron/interval expression. Retest evidence: `.e2e/ui-review/evidence/screenshots/schedule-delete-confirm-fixed.png`.

## 6. Coverage Gaps and Blocks

- Managed application create/assign/deploy/health/rollback was not executed because it would mutate an existing enrolled Agent and no isolated enrolled target is available; the requested manual VM intentionally remains unmanaged.
- Operator/viewer RBAC and denied-action testing is blocked because the corresponding credentials are not present in the environment.
- Node enrollment/revoke/status mutations were not executed; creating a pending node would change Hub state and is outside the current unmanaged-node scope.
- Positive file relay did not complete because the selected source path was absent on the source Agent. The failure and retry paths were exercised and retained as test evidence; the failed transfer has no delete action in the UI.
- Execution cancellation while running and live streaming/follow behavior were not exercised.
- Script revision increment was verified, but a dedicated revision-history UI was not opened.
- Node label add works; no visible label removal/edit control was found. The test label was removed through the authenticated API to restore the existing node.

## 7. Source vs Runtime Differences

- Source route discovery and runtime navigation matched for the primary pages and dynamic create/edit/detail routes used.
- `/nodes` renders the node catalog but does not canonicalize the browser URL to `/agents`.
- Schedule delete confirmation was fixed to use product-facing task/expression fields; UUID is retained only as technical data.
- Runtime application form is reachable, but the artifact selector is empty after artifact cleanup, so deployment could not safely proceed.

## 8. Test Data and Cleanup

- Deleted: test script, task, schedule, group, artifact, and temporary node label.
- Restored: runtime heartbeat setting to `30` seconds.
- Retained intentionally: successful execution history, as required for audit/execution continuity.
- Retained as evidence: one failed transfer record because the UI exposes retry but no delete action; it is marked `safe_to_delete: false` in `.e2e/test-data.json`.
- No Hub node was created for `cadentra-agent-manual-test`.

## 9. Final Status

`completed_with_gaps`

The primary Web UI and authenticated API surface was explored with real browser actions and evidence. Remaining gaps are explicitly recorded and are primarily constrained by missing RBAC credentials, the lack of an isolated enrolled deployment target, and the unavailable positive source file for relay testing.

## 10. Post-fix UI Regression

- Shared responsive table hint and sticky action-column behavior were deployed to the KVM2 test Hub and verified at `390x844` and `1440x900`; evidence is under `.e2e/ui-review/`.
- Execution detail now shows localized friendly task/node labels and keeps the execution UUID in secondary technical metadata.
- The scoped UI findings are resolved; broader business coverage remains `completed_with_gaps` as described above.
