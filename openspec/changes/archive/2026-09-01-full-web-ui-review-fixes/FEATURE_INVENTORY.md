# Feature Inventory

## Pinned reference

- Repository: `https://github.com/satnaing/shadcn-admin`
- Local reference: `/tmp/cadentra-shadcn-admin`
- Commit: `e16c87f213a5ba5e45964e9b67c792105ec74d26`
- Target: `/home/superc/code/cadentra/web`

## In-scope UI surfaces

- Shared shell: Sidebar, Header, Page Context, Main, theme, profile, language.
- Shared data presentation: Table, DataTable, column sizing, pagination, column visibility, technical values.
- Shared states: loading, empty, error, success toast, destructive confirmation.
- Cadentra pages: Overview, Tasks, Executions, Agents/Nodes, Transfers, Schedules, Scripts, Groups, Applications, Artifacts, Audit, Users, Settings, Sign In.
- Detail/editor surfaces: task, execution, agent, schedule, script, group, application, artifact, task run, node enrollment, logs.
- Responsive targets: 1920, 1366, 1024, 390; Light and Dark themes.

## Explicit exclusions

- Backend contracts, database, Hub/Agent protocol and business data semantics.
- New template demo business modules.
- Unsupported log controls or fields not exposed by the Cadentra API.
