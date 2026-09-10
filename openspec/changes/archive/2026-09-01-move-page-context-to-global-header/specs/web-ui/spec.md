# web-ui Specification Delta

## ADDED Requirements

### Requirement: Shared page context header

Authenticated pages SHALL render their title, description, and page-level actions in the shared application Header, aligned to the same content container used by the page body.

#### Scenario: Page context placement

- **WHEN** a user opens any authenticated first-level or detail page
- **THEN** the page title and description appear in the shared Header and the page body contains no duplicate page title heading

#### Scenario: Page action placement

- **WHEN** a page provides a primary or contextual action
- **THEN** that action appears in the shared Header alongside the existing global actions

#### Scenario: Content alignment

- **WHEN** a user views a page at desktop or laptop width
- **THEN** the Header page context aligns with the page body content container and the body starts below the Header without an extra PageHeader spacing block

#### Scenario: Responsive page context

- **WHEN** a user views an authenticated page on a narrow viewport
- **THEN** title, description, page actions, and global controls remain usable without horizontal page overflow
