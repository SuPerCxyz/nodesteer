## 1. Baseline and shared infrastructure

- [x] 1.1 Pin and document shadcn-admin commit and preserve third-party license attribution
- [x] 1.2 Compare current layout, Table, DataTable, pagination, view options, Card, Form, Badge, Dialog, Sheet, and theme files with the pinned template
- [x] 1.3 Fix authenticated layout parent/child route rendering so task run and edit child pages render correctly
- [x] 1.4 Add shared localized labels for pagination, column visibility, technical values, and common feedback

## 2. DataTable and table presentation

- [x] 2.1 Add stable ColumnDef sizing and shared cell/header alignment support without changing the shadcn Table primitive
- [x] 2.2 Apply role-based widths and long-value disclosure to Tasks, Nodes, Transfers, Schedules, Scripts, Groups, Applications, Artifacts, Audit, and Users
- [x] 2.3 Replace or adapt the unbounded Executions list to use pagination and preserve URL filter state
- [x] 2.4 Align Overview, Task Detail, and Execution Detail compact tables with the shared table conventions
- [x] 2.5 Verify mobile table scrolling, pagination layout, row height, badges, action columns, and keyboard access

## 3. Page and form fixes

- [x] 3.1 Fix file transfer form Grid column alignment and responsive behavior
- [x] 3.2 Normalize create/edit form widths, field groups, action alignment, validation, and loading/error presentation
- [x] 3.3 Replace raw Task target JSON with structured display and correct execution/task identity fallback
- [x] 3.4 Improve settings labels/technical field presentation and localized artifact upload display
- [x] 3.5 Fix dashboard stretch/blank-space composition without changing business data

## 4. Feedback, status, and theme quality

- [x] 4.1 Replace blank loading placeholders with template Skeleton or localized loading states
- [x] 4.2 Verify StatusBadge, non-status Badge, state labels, and capsule alignment in Light/Dark
- [x] 4.3 Fix Dark Mode profile/avatar and log/code contrast while preserving the template theme tokens
- [x] 4.4 Audit Dialog, Sheet, Dropdown, Tabs, buttons, icon labels, focus states, and destructive feedback

## 5. Verification and delivery

- [x] 5.1 Run frontend format check, lint, typecheck, browser tests, and build (browser suite blocked by unsupported Ubuntu 26.04 Playwright environment)
- [x] 5.2 Run Go test and lint gates
- [x] 5.3 Build and deploy the embedded frontend to KVM2 Hub
- [x] 5.4 Verify all list/detail/editor pages at 1920, 1366, 1024, and 390 widths in Light/Dark where runtime coverage was available
- [x] 5.5 Verify task run navigation, pagination, table filters, dialogs, settings, logs, and responsive sidebar
- [ ] 5.6 Update verification evidence and archive the OpenSpec change only after all in-scope tasks are complete
