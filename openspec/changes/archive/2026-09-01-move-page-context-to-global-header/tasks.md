## 1. Shared layout

- [x] 1.1 Extend the shared Cadentra Header to render page title, description, page actions, and global actions.
- [x] 1.2 Align the Header context container with Main and adjust Header/Main vertical flow.

## 2. Page migration

- [x] 2.1 Migrate dashboard, catalog, transfer, editor, task, and execution pages.
- [x] 2.2 Remove all page-local PageHeader renderers and the unused shared component.

## 3. Verification

- [x] 3.1 Verify all authenticated routes have Header context and no Main-level duplicate heading.
- [x] 3.2 Verify 1920px, 1366px, and mobile widths with no horizontal overflow.
- [x] 3.3 Run format, lint, typecheck, test, and build; deploy the embedded frontend to KVM2.
