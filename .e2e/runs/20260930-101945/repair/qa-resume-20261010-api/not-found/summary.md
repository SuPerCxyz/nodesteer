# API-NOT-FOUND batch — 2026-10-10

- Existing private bearer was re-read from the 0600 private file and `/api/me` confirmed `administrator`; no token was printed or saved. Every target was a fresh UUID. Each supported route first returned GET 404, then DELETE, then GET 404.
- **Nodes:** GET 404; DELETE 404 (handler short-circuits before Node.Delete); GET 404.
- **Groups, Scripts, Tasks, Schedules, Applications, Artifacts:** each GET 404 → DELETE 200 `{"ok":"true"}` → GET 404. Script revisions `[]`; Application state/executions/revisions `[]`; Artifact download 404.
- **Transfers:** GET 404; DELETE 403 `{"error":"permission denied"}`; GET 404. Source exposes only GET plus POST retry/cancel, so DELETE is unsupported and produced no audit event.
- **State:** all 8 collection counts and ID digests were unchanged (Nodes 6, Apps 1, Artifacts 1, Transfers 5; Groups/Scripts/Tasks/Schedules 0). New audit events: 6, one per successful manager-backed delete. Max Node `GlobalRevision` changed 1401→1406 (five revisions: Group/Script/Task/Schedule/Application). Script/Task/Schedule/Application tombstones and all six exact-ID audit entries remain as expected history; Group has changelog/audit but no tombstone; Artifact has audit only. No live resource metadata remained.
- **Mapping:** API-NOT-FOUND is evidenced for the named resource routes. User item GET/DELETE remain excluded because implementation returns 405; no unrequested resource methods were tested. API-CONTRACT receives only these route/method/status/body-shape dimensions.
- Evidence: `source-preflight.json`, `collections-before.json`, `results.json`. Static refs: `internal/hub/api/api.go:841-875,1009-1053,1089-1128,1168-1235,1270-1318,1428-1466,1513-1542,1602-1732`; manager/SQL refs are in `source-preflight.json`.
