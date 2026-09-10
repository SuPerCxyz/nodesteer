## Context

Authenticated pages currently render `CadentraHeader` and a separate `PageHeader` as siblings. The Header's content spans the available SidebarInset width, while `Main` uses the template content container and padding. Page-specific actions are therefore rendered below the global toolbar.

## Goals / Non-Goals

**Goals:**

- Make the shared Header the single rendering location for page title, description, and page action content.
- Use the same container-query max-width and padding rhythm as `Main`.
- Keep the current shadcn-admin primitives, colors, typography, controls, routes, and data flow.
- Support long titles and mobile widths without horizontal overflow.

**Non-Goals:**

- Redesigning the Sidebar, cards, tables, badges, theme, or business pages.
- Introducing route metadata or a second page-context state store.
- Changing detail-page data, back navigation, Tabs, or API behavior.

## Decisions

1. **Use explicit Header props.** Existing page components already own their translated titles, descriptions, and dynamic actions. Passing those values to `CadentraHeader` keeps a single source of truth without adding a layout context or route metadata layer.
2. **Remove the shared PageHeader component.** With all callers migrated, keeping a second title renderer would preserve the old architecture and create dead code.
3. **Share Main's container rules.** The Header page-context wrapper uses the same `@7xl/content` max-width rules and applies the wide-screen inner padding required to align with `Main` content.
4. **Use an 80px desktop Header with wrapping on narrow screens.** This accommodates two-line context while retaining the existing controls and allows the current flex layout to handle mobile widths.

## Risks / Trade-offs

- [Page actions can reduce title width] → The context uses `min-w-0`, truncates long title/description text, and keeps actions in a shrink-resistant group.
- [Some detail pages retain a back button in Main] → Back navigation was not part of the old `PageHeader`; it remains unchanged by this scope.

## Migration Plan

1. Add Header props and container alignment.
2. Migrate all current `PageHeader` call sites, including loading/error states and page actions.
3. Remove the unused PageHeader component and verify all routes at desktop and mobile widths.
4. Build and deploy the embedded frontend to KVM2.

## Open Questions

None.
