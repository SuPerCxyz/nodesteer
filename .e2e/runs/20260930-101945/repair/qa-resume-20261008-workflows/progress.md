# Workflow E2E continuation (2026-10-08)

- Scope: repair Hub `http://192.168.100.209:18080` only; native-3 target; owned prefix `qa-resume-20261008-workflow-`.
- Safety: private connection JSON remains under `/tmp` with mode 0600; no credential/token output; no localStorage inspection; no primary `:8080` requests; no source edits; exact-ID cleanup only.
- Current stage: preflight and custom UI runner preparation.
- Canonical cases: E2E-01 and E2E-03 remain PARTIAL for continuous UI Logs/Audit and same-Task parameter override; E2E-04/05/06/07 are not complete on repair build. This run focuses E2E-01/03, Task/Group/Schedule cross-page coverage.
- Execution evidence and exact resource IDs will be appended by the runner and summarized after cleanup.
- Navigation diagnosis: no-fixture Playwright authenticated session reached `/tasks` by sidebar SPA click and direct hard navigation. Both rendered the Task heading, `<main>`, and table; `/api/me` and `/api/tasks` returned 200; no failed network request or browser console error. Prior navigation timeouts are kept as harness observations, not product failures.
- Historical fixture cleanup: exact-ID GET verification checked 16 Group/Script/Task/Schedule IDs from retained attempts; all returned 404 (`cleanup-get404-verification.json`).
- Canonical E2E-01 and E2E-03 real UI run/log/audit is now independently evidenced by attempts 02/03/05: same Script + Task, target Group containing only native-3, run parameter override and environment marker observed in native-3 SUCCESS stdout, Execution detail Logs UI, Audit UI correlated by same Task ID with `execute` action. Attempts 01/02/03/04/05 preserve harness/navigation issues separately.
- Next: isolated Task-only UI fixture for Task edit/toggle/delete cancel/confirm and linked bounded one-time Schedule create/update/enable/trigger/disable/delete. Persist exact IDs and phase transitions as they happen.
- Additional cross-page fixture `5bebfff5`: exact singleton target assertion passed; Task detail showed its exact Schedule link; Schedule detail showed its Task name; future one-time Schedule updated and enabled/disabled through UI. Schedule delete Cancel gate passed with zero DELETE + GET 200; confirm gate passed with one DELETE + GET 404. Afterward Task detail navigation timed out before verifying link removal or Task cancel counters; captured in `crosspage-partial-assertions.json`. No further retry planned.
- Latest exact-ID cleanup audit queried all 25 Group/Script/Task/Schedule IDs across this batch; all GETs returned 404 (`cleanup-get404-verification.json`).

## Handoff checkpoint

- E2E-01 requested path is PASS for this run: UI Script → Script Task → Group containing only native-3 → Run parameter override → native-3 SUCCESS → Execution Logs UI → exact Task Audit UI. The exact Task Audit API read after completion returned task actions `create`, `update`, `execute`, and `delete`; screenshot at run time showed the Task `create` and `execute` rows.
- E2E-03 is PARTIAL: the real Agent consumed the same-Task parameter override and Script environment marker. `working_dir=/tmp` was stored but no cwd oracle was emitted; `run_user` was not set or tested.
- Task-only CRUD and Schedule lifecycle are PASS for the focused run: UI edit, disable→enable; linked future one-time Agent Schedule created disabled, updated/enabled, triggered exactly once with SUCCESS/marker on native-3, then disabled; cancel preserved exact IDs and confirmed deletes ended in GET404.
- Cross-page fixture separately confirmed Task detail exact Schedule href and Schedule detail Task name. Its instrumented Schedule cancel path passed 0 DELETE + GET200, and confirmed deletion path passed exactly 1 DELETE + GET404. It then timed out loading Task detail before checking the removed link or instrumenting Task deletion cancel; this path remains unverified and is documented in `crosspage-partial-assertions.json`.
- Final cleanup verified 25 exact Group/Script/Task/Schedule IDs with GET404. No orphan remains from this batch.
- Read `workflow-summary.json` first when resuming. It lists canonical case actual/covered/remaining dimensions, request behavior, evidence, harness issues, and follow-up batches. WF-SCRIPT/WF-SCHEDULE and the overall 504-case matrix are not complete.
