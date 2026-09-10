## Why

The shared page context is currently constrained to the same centered max-width as the body. The approved layout requires page context to occupy the full AppHeader width: title/description immediately after the Sidebar and global controls at the far right.

## What Changes

- Remove the Main content max-width and wide-screen padding constraints from the Header page-context wrapper.
- Keep the page context on the left and page/global actions on the right.
- Keep Main content width, spacing, business data, and design tokens unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `web-ui`: The shared page context header uses the full AppHeader width rather than the centered body container.

## Impact

- `web/src/components/layout/cadentra-header.tsx` only.
- No route, API, business logic, or dependency changes.
