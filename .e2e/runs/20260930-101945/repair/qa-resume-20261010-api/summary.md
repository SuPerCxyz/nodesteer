# API batch 2026-10-10

- **AUTH-PUBLIC:** unauthenticated `GET /healthz`, `/readyz` → 200 (`ok`, `ready`); `/api/oidc/state` → 200, `{enabled:false, local_fallback:true}`; disabled `/api/oidc/login`, callback with no query and `state` only → 404 JSON `{error:"oidc not enabled"}`. Redirects were not followed; no IdP used.
- **AgentBinary:** public amd64 GET → 200, 16,805,087 bytes; SHA-256 matched response header (`d55dc5…73add`).
- **AUTH-USERS/AUTH-MANAGEMENT:** anonymous `/api/users` GET → 401. Existing private admin bearer `/api/me` confirmed administrator; `/api/users` GET → 200, 8 entries. Invalid bearer GET/POST/PUT/DELETE → 401. Admin invalid POST fields/role and invalid/empty PUT → 400; item GET/DELETE → 405. User count and ID/username/role digest were unchanged before/after.
- **AUTH-USERS remains PARTIAL:** no owned user created. Operator/Viewer real 403 and successful Admin create/update remain untested.
- **API-CONTRACT remains PARTIAL:** evidence covers only the endpoints/methods above.
- Source: [api.go](../../../../../internal/hub/api/api.go) lines 219, 125-137, 179-195, 462-500, 701-718, 2001-2116. User DELETE is unsupported by this implementation; treated as contract, not defect.
- Evidence: `auth-public-live.json`, `auth-users-admin-read.json`, `auth-users-negative-matrix.json`, `user-route-static-review.json`. Secrets were not recorded. Host fixture remains stopped and untouched.
