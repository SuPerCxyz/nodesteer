# web-ui Specification

## Purpose
定义一期 Web UI 必须展示真实后端状态并完成关键操作闭环。
## Requirements
### Requirement: Real application and node state

The Web UI SHALL display real application health, deployment results, execution history, current assignments, and tasks/schedules matching a node by node, group, or label target.

#### Scenario: Node detail

- **WHEN** a user opens a node detail page
- **THEN** the page lists tasks and schedules whose node, group, or label target matches the node
- **AND** displays current application assignments and health from the API

### Requirement: Action feedback

The Web UI SHALL surface API failures and successful state changes, and SHALL not report configured health-check presence as current health. Loading, empty, error, and successful states SHALL use the shared template-based feedback components without blank placeholders that conceal the current state.

#### Scenario: Failed action

- **WHEN** an operation fails at the API or Agent
- **THEN** the UI displays the returned failure reason
- **AND** does not display the operation as successful

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

### Requirement: Complete page presentation

The Web UI SHALL present every registered NodeSteer page and state with consistent shadcn-admin layout primitives, localized user-facing labels, readable technical values, and usable Light/Dark responsive behavior.

#### Scenario: Authenticated page presentation

- **WHEN** a user opens any list, detail, editor, settings, authentication, loading, empty, or error page
- **THEN** the page uses the shared layout, typography, spacing, controls, feedback states, and responsive behavior without duplicate or conflicting visual systems
- **AND** the page does not display untranslated i18n keys or backend enum identifiers as ordinary user-facing labels when a localized label is available

#### Scenario: Technical value presentation

- **WHEN** a page displays an ID, UUID, Revision, Path, Cron, Hash, IP, hostname, or command
- **THEN** the value is visually distinguishable as technical data and remains fully accessible through wrapping, truncation disclosure, copying, or a detail view

### Requirement: Usable data tables

The Web UI SHALL use stable, role-appropriate column sizing and pagination for large or dense tables while preserving semantic table structure and mobile usability.

#### Scenario: Dense table sizing

- **WHEN** a user views Tasks, Executions, Nodes, Transfers, Schedules, Scripts, Groups, Applications, Artifacts, Audit, Users, or dashboard tables
- **THEN** primary text and long technical content receive flexible readable space, status/time/numeric/action columns remain stable, and header/cell boundaries and alignment match

#### Scenario: Large execution history

- **WHEN** execution history contains many records
- **THEN** the page renders a bounded page of records with usable pagination or the server-supported equivalent instead of rendering the entire history as one unbounded table

#### Scenario: Long and narrow content

- **WHEN** a cell contains a long name, path, ID, hash, target list, or badge
- **THEN** the table does not break the page layout, compact labels remain on one line, and the complete value remains available to keyboard, pointer, and touch users

### Requirement: Complete task run navigation

The Web UI SHALL render the dedicated task run page when a user chooses the task run action.

#### Scenario: Open task run page

- **WHEN** a user selects “Run Now” from a task list or task detail page
- **THEN** `/tasks/:taskId/run` renders the run confirmation view with the selected task, target summary, loading/error state, confirmation action, and return action

### Requirement: Correct execution identity and state values

The Web UI SHALL show execution identity and state values using the most meaningful real data available from the API.

#### Scenario: Execution detail identity

- **WHEN** a user opens an execution detail page
- **THEN** the page shows the task name when it can be resolved, retains a copyable execution ID and revision, and shows an explicit placeholder for absent exit code rather than blank content

### Requirement: Aligned forms and controls

The Web UI SHALL keep logically corresponding fields and actions aligned across rows and responsive breakpoints.

#### Scenario: File transfer form alignment

- **WHEN** a user views the file transfer form on desktop, tablet, or mobile
- **THEN** source/target Agent controls, source/destination path controls, and the add-target action use stable grid boundaries and do not cause page-level overflow or baseline drift

#### Scenario: Editor form states

- **WHEN** a user opens any create or edit page
- **THEN** labels, controls, validation messages, actions, loading state, and errors remain aligned and usable without relying on positional hacks

### Requirement: Localized shared controls

The Web UI SHALL localize shared DataTable pagination, column visibility, actions, and form feedback according to the active locale.

#### Scenario: Chinese shared table controls

- **WHEN** the active locale is Chinese
- **THEN** pagination and column visibility controls do not show English template labels or internal column IDs
