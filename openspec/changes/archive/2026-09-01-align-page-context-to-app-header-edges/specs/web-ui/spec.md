# web-ui Specification Delta

## MODIFIED Requirements

### Requirement: Shared page context header

Authenticated pages SHALL render their title, description, and page-level actions in the shared application Header. The Header page context SHALL use the full width available after the Sidebar, while the page body may retain its own centered content container.

#### Scenario: Page context placement

- **WHEN** a user opens any authenticated first-level or detail page
- **THEN** the page title and description appear in the shared Header and the page body contains no duplicate page title heading

#### Scenario: Page action placement

- **WHEN** a page provides a primary or contextual action
- **THEN** that action appears in the shared Header alongside the existing global actions

#### Scenario: Content alignment

- **WHEN** a user views a page at desktop or laptop width
- **THEN** the Header page context starts at the AppHeader's left content edge after the Sidebar, global actions end at the AppHeader's right content edge, and the body starts below the Header without an extra PageHeader spacing block

#### Scenario: Responsive page context

- **WHEN** a user views an authenticated page on a narrow viewport
- **THEN** title, description, page actions, and global controls remain usable without horizontal page overflow
