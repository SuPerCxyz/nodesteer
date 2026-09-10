## Context

The previous global page context migration correctly moved titles and actions into `CadentraHeader`, but its inner wrapper reused `Main`'s `@7xl/content` max-width. The reference screenshot shows the Header as a full-width chrome area independent of the centered body content.

## Goals / Non-Goals

**Goals:**

- Put the page context at the left edge of the AppHeader content area after the Sidebar.
- Put Hub, language, theme, user, and page actions at the right edge of the AppHeader.
- Preserve the existing 80px Header, responsive wrapping, and body max-width.

**Non-Goals:**

- Reverting the PageHeader-to-Header migration.
- Changing Main, cards, typography, colors, or page behavior.

## Decisions

- Remove only the `@7xl/content:*` classes from the Header inner context wrapper. `justify-between` and `ms-auto` remain the explicit left/right separation mechanism.

## Risks / Trade-offs

- [Header title and controls span wider than body content] → This is intentional and matches the approved layout; title remains truncated and the action cluster remains shrink-resistant.
