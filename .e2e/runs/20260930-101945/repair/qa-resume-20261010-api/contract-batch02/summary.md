# API-CONTRACT batch 02

- `HEAD /api/agent/binary?architecture=amd64`: 200, zero body bytes, Content-Length `16805087`; SHA header matches the earlier full GET digest `d55dc5…73add`.
- `GET /api/agent/binary?architecture=invalid-qa`: 400 JSON `{error: "architecture must be amd64 or arm64"}`.
- Admin `POST /api/scripts` and `/api/tasks` with empty `name`: each 400 JSON `{error: "name required"}`.
- Scripts/Tasks counts and ID digests remained unchanged (both 0). No fixture, Run, login, or session was created. All requests used bounded timeouts on repair:18080.
- Scope covers only these API-CONTRACT dimensions; canonical API-CONTRACT remains PARTIAL. Evidence: `results.json`, `progress.json`.
