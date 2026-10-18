# Native-4 retained child: read-only result and fault plan

Status: read-only inventory complete; no fault or database lock has been run. Current canonical statuses are read only from `.e2e/runs/20260930-101945/repair/feature-inventory.json`: SYS-01/02/09, AR-08 and INV-02 are `NOT_RUN`; INV-05 is `PARTIAL`.

The retained child is Node `15961ef7-81b4-43f3-80c8-1b6977ca2aea`, Agent `agent-ab15a133-73cd-4025-8c97-f2ff37e000f5`, under original Native-4 parent Node `03cb50cc-eab6-4afd-bbe0-f73c4e326773`. Its root is `/tmp/qa-resume-20261008-child-ciivjupc`, device/inode `33:135`, owner `65534`, mode `0700`. The current child process is PID `47015`, startticks `130084225`, UID `65534`, executable SHA `d55dc5cd…`; its original supervisor is PID `44932`, startticks `123677922`, UID `65534`. The existing loopback proxy `127.69.212.143:35095` remains owned by that supervisor and forwards only to repair `:18080`. No process was stopped and no proxy was started or changed.

The child was online/synced in the repair API. The parent was also online/synced. A read-only Command Task on the exact parent ran successfully and was deleted with GET 404. Its inventory found SQLite quick-check `ok`, DB device/inode `33:155`, UID `65534`, mode `0644` under the `0700` owner directory, and an empty Script, Task, Schedule, Application, deployment-journal and Unit Registry baseline. The task’s DB sample read local revision `1336`; the following Hub API read showed `1337` and synced. Treat this as a time-of-read discrepancy and require a fresh same-window local/Hub revision match before any fixture mutation.

Evidence: [api-preflight.json](api-preflight.json) and [child-runtime-readonly-evidence.json](child-runtime-readonly-evidence.json). No credentials, config contents, script contents or token fingerprints were emitted.

## Proposed minimal INV-05 write-failure test

This tests a SQLite write-stage `SQLITE_BUSY` and rollback. It does not force `sql.Tx.Commit()` itself to fail.

1. Recheck exact owner manifest/root inode, child and supervisor PID/startticks/UID/argv/executable SHA/cgroup, proxy ownership, child Hub status, and local DB revision equality. Abort if any fingerprint changes.
2. Create one benign QA Script and one type=`script` Task through repair `:18080`, targeted only to this child; retry `0`, timeout at most `60`, no Schedule. Wait until the child’s local DB shows exact baseline Script ID/revision/SHA, Task ID/revision and global revision. Create no App or Artifact.
3. Start one short-lived Command Task on the original Native-4 parent (timeout at most `30`, retry `0`, no Schedule). Its script opens only the child’s `state.db`, uses a second SQLite connection with a short timeout, runs `BEGIN IMMEDIATE` without any SQL writes, emits a flushed `LOCK_ACQUIRED` marker, holds for `12s`, reads the local revision and Script SHA, and always `ROLLBACK`s in `finally`.
4. Poll `/api/executions/{id}/logs` for `LOCK_ACQUIRED`. Only after seeing it, update the exact QA Script once to benign v2 through repair `:18080`. This is a real Hub mutation; do not change global settings or unrelated objects.
5. During the lock, verify the child’s real `agent.log` contains an `apply sync failed`/`database is locked` match without emitting raw log lines. The lock holder also records the unchanged local Script SHA/revision before rollback. The expected path is the Agent’s configured `busy_timeout=5000ms`: Script upsert fails, `applySync` rolls the transaction back, and `OnSyncResponse` returns `OK=false, AppliedRev=0`.
6. After the holder’s guaranteed rollback, wait up to `60s` for the Agent’s normal RevisionCheck (source fallback is `45s`, plus jitter) to request and apply the still-current Hub state. Verify local Script v2 revision/SHA and global revision match Hub; then run the benign Task once and verify one marker and one successful Execution.
7. Delete only the exact QA Script and Task and wait for the connected child to consume their tombstones; verify Hub GET 404 and local rows gone. Preserve the retained child Node, root, supervisor, proxy, and all Execution/Audit history. Delete the lock Task and verify GET 404.

The normal Hub path returns a full desired-state payload with `Snapshot=true`, `FullResync=false` while the changelog gap remains inside the existing `5000` window. The proposed fixture has no Apps, so `cleanupPrunedApplications` has no side effects. The existing Agent source sets WAL plus `busy_timeout(5000)` in `internal/agent/store.go:50`; `BeginSync`, rollback and commit ordering are in `internal/agent/store.go:222-260` and `internal/agent/agent.go:788-920`; negative ACK handling and periodic revision recovery are in `internal/agent/agent.go:329-339, 647-667, 735-760`; Hub desired sync construction is in `internal/hub/sync.go:232-277`.

This is not a true `CommitSync()` failure injection: `BEGIN IMMEDIATE` makes the Script upsert fail before the final `CommitSync()` call. No safe mechanism for forcing the final SQLite commit syscall to fail has been identified on the live WAL database. Do not report this lock scenario as a COMMIT failure or as a full INV-05 PASS. A true commit-call fault needs a Root-approved storage/VFS fault mechanism or another bounded isolated method.

## AR-08 repeated valid apply

The child and existing relay are retained, but the read-only evidence proves only that the relay is owned and connected. It does not prove a deterministic duplicate-frame control; the previously observed control file was empty. Sequential Script updates or multiple GETs do not prove that the Agent applied the exact same valid response twice.

A valid repeated-apply test needs one exact unmodified `SYNC_RESPONSE` payload/revision to be delivered twice through this existing owned relay, then must verify stable local revision, one row per object and no duplicate Task side effect. Root must first confirm an existing relay replay/hold capability (or approve a precise test-control addition to this existing owner). Do not start another proxy. If no replay control exists, record the duplicate-apply dimension as not executable with the current harness; do not infer it from normal reconnection or repeated ACKs.

## Inputs needed before any fault execution

- Confirm the `12s BEGIN IMMEDIATE` scenario is the intended minimal INV-05 write-failure test, with the stated limit that it does not force `CommitSync()` itself to fail.
- If a true commit-call failure is mandatory, provide or approve a safe exact fault mechanism.
- Confirm whether the existing Native-4 relay has a deterministic same-frame replay control for AR-08. No new proxy is proposed.
- Approve the one benign Script/Task pair and exact cleanup on this retained child. No fault, object creation, DB lock, process stop/restart or proxy modification has occurred in this phase.
