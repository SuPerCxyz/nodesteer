## Why

Cadentra currently renders each page title and description inside `Main`, leaving the shared Header as a separate toolbar row. This creates duplicated layout structure and prevents page context and page actions from aligning with the global controls.

## What Changes

- Move page title and description rendering into the shared `CadentraHeader`.
- Render page-level actions beside the global Hub, language, theme, and user controls.
- Remove page-local `PageHeader` DOM and its spacing from all authenticated pages.
- Align the Header page context container with the existing `Main` content container across desktop widths.
- Preserve existing page routes, data, actions, design tokens, and responsive behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `web-ui`: Authenticated pages now expose their page context and actions through the shared Header while retaining real page content and action feedback.

## Impact

- Shared Header and Main layout components.
- Dashboard, catalog, task, execution, transfer, editor, detail, and settings pages.
- No API, backend, dependency, or business-data changes.
