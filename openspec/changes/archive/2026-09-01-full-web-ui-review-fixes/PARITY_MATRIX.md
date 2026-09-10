# Parity Matrix

Scope is UI composition and observable presentation parity with the pinned shadcn-admin primitives. Cadentra business-specific colors, labels and data are intentional divergences required by the product.

| ID | Feature / observable behavior | Scope | Reference evidence | Target evidence | Status | Verification | Notes / divergence |
|---|---|---|---|---|---|---|---|
| F-001 | Sidebar/Header/Main use template layout primitives | UI | pinned template layout files | `web/src/components/layout/`, KVM2 pages | Implemented | Pass: 1920/1366/390 browser QA | Cadentra menu and Page Context are business content |
| F-002 | Light/Dark theme and profile control remain readable | UI | template theme/profile files | `web/src/components/profile-dropdown.tsx`, theme files | Implemented | Pass: KVM2 Light/Dark QA | Avatar uses inline Logo to avoid external currentColor failure |
| F-003 | Shared DataTable preserves semantic table structure | UI | template Table/DataTable files | `web/src/features/shared/data-table.tsx` | Implemented | Pass: all list routes rendered tables | Business columns are Cadentra-specific |
| F-004 | DataTable columns have stable role-based widths and alignment | UI | template DataTable composition | ColumnDef size/meta in tasks/catalog/transfers/executions | Implemented | Pass: table/header widths and no page overflow | Technical values use truncation disclosure |
| F-005 | DataTable pagination and column visibility are localized | UI | template pagination/view options | `web/src/components/data-table/{pagination,view-options}.tsx`, locales | Implemented | Pass: Chinese KVM2 snapshots | English remains for English locale |
| F-006 | Execution history is paginated | UI/behavior | template pagination behavior | `web/src/features/executions/index.tsx` | Implemented | Pass: KVM2 shows 10 rows/page, 10 pages | Existing API limit remains 100 |
| F-007 | Task run action opens dedicated run page | UI/navigation | nested route behavior | task index route + parent Outlet | Implemented | Pass: KVM2 `/tasks/:id/run` | No run mutation was submitted during QA |
| F-008 | Execution detail shows task identity and explicit exit fallback | UI/data display | target domain requirement | `web/src/features/executions/index.tsx` | Implemented | Pass: KVM2 title shows task name | Falls back to real task ID if lookup fails |
| F-009 | Transfer form rows share stable Grid boundaries | UI | template Grid/Form primitives | `web/src/features/transfers/index.tsx` | Implemented | Pass: KVM2 source/destination path x=854 | Button reserves the template-compatible action column |
| F-010 | Long technical values do not break layout | UI/accessibility | template long-text patterns | table cells, titles, truncation | Implemented | Pass: Desktop/Mobile body overflow false | Complete value uses title/detail/copy where available |
| F-011 | Editor and detail pages use bounded content widths | UI | template Main/Card composition | `EditorShell`, detail Card classes | Implemented | Pass: all editor/detail routes rendered | No API changes |
| F-012 | Loading/empty/error states are visible and actionable | UI/behavior | template Skeleton/feedback primitives | shared states and loading pages | Implemented | Pass: route/state inspection | API failure branches not forced during QA |
| F-013 | Artifact upload file selection is localized and bounded | UI/behavior | template Input/Form pattern | `ArtifactEditor`, locales | Implemented | Pass: KVM2 upload form no overflow | Upload was not submitted |
| F-014 | Overview maintains hierarchy without stretched schedule blank space | UI | template Card/Grid composition | `web/src/features/overview/index.tsx` | Implemented | Pass: KVM2 Dark screenshot | Business data unchanged |
| F-015 | Responsive pages avoid page-level horizontal overflow | UI | template responsive layout | KVM2 browser QA | Implemented | Pass: 1920/1366/2560/390 body overflow false | Table containers may scroll internally |
| F-016 | Frontend automated browser suite runs | Verification | project Vitest/Playwright config | `pnpm run test` | Blocked | Not run: Playwright 1.59.1 has no Chromium for Ubuntu 26.04 | Use supported OS/browser image to verify |
