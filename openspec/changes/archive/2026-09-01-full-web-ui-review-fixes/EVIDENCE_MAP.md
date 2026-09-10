# Evidence Map

| Surface | Reference evidence | Target evidence |
|---|---|---|
| App shell | `/tmp/cadentra-shadcn-admin/src/components/layout/{header,main,app-sidebar,app-title,nav-user}.tsx` | `web/src/components/layout/` and KVM2 browser snapshots |
| Table primitives | `/tmp/cadentra-shadcn-admin/src/components/ui/{table,card,badge}.tsx` | `web/src/components/ui/` |
| DataTable | `/tmp/cadentra-shadcn-admin/src/components/data-table/` | `web/src/features/shared/data-table.tsx`, `web/src/components/data-table/` |
| Task run | Template routing conventions; Cadentra route registration | `web/src/routes/_authenticated/tasks/$taskId/index.tsx`, `run.tsx`; KVM2 `/tasks/:id/run` showed run confirmation |
| Execution history | Template DataTable pagination pattern | `web/src/features/executions/index.tsx`; KVM2 showed 10 rows and localized pagination |
| Form alignment | Template Form/Card composition | `web/src/features/transfers/index.tsx`, `web/src/features/editors/index.tsx`; KVM2 source/destination paths share the same x-coordinate |
| Theme/accessibility | Template theme and control primitives | KVM2 Light/Dark browser screenshots, no page errors, localized accessible control names |
