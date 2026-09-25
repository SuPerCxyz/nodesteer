## MODIFIED Requirements

### Requirement: Usable data tables

The Web UI SHALL use stable, role-appropriate column sizing and pagination for large or dense tables while preserving semantic table structure and mobile usability. Within every list table, each column header SHALL share the same left baseline as that column's content, and header labels SHALL render without truncation at supported desktop viewports. The table SHALL adapt column widths to the available container width and SHALL introduce horizontal scrolling only when the columns' minimum readable widths cannot fit; the row-action column SHALL remain reachable. Row-action content SHALL be left-aligned consistently across list pages.

#### Scenario: Dense table sizing

- **WHEN** a user views Tasks, Executions, Nodes, Transfers, Schedules, Scripts, Groups, Applications, Artifacts, Audit, Users, or dashboard tables
- **THEN** primary text and long technical content receive flexible readable space, status/time/numeric/action columns remain stable, each column header and its cell content share the same left baseline, and header labels are fully visible

#### Scenario: Adaptive table width

- **WHEN** the available container is narrower than the sum of the columns' comfortable widths but still fits their minimum readable widths
- **THEN** the table shrinks its columns proportionally to fit the container without horizontal scrolling, without a cut-off right-most column, and without sticky action controls covering adjacent cell content

#### Scenario: Table wider than the container

- **WHEN** the available container is narrower than the sum of the columns' minimum readable widths
- **THEN** the table scrolls horizontally with a visible scroll affordance, and the row-action column remains fixed and usable

#### Scenario: Large execution history

- **WHEN** execution history contains many records
- **THEN** the page renders a bounded page of records with usable pagination or the server-supported equivalent instead of rendering the entire history as one unbounded table

#### Scenario: Long and narrow content

- **WHEN** a cell contains a long name, path, ID, hash, target list, or badge
- **THEN** the table does not break the page layout, compact labels remain on one line, and the complete value remains available to keyboard, pointer, and touch users through truncation disclosure such as a hover/focus title

## ADDED Requirements

### Requirement: Node list column order

The Node list SHALL present its columns in the order: status, hostname, OS, groups, node address, architecture, agent version, deployment mode, last seen, and actions, keeping the OS value adjacent to the hostname, while all Node fields required by the product baseline remain present in the table or in the column visibility controls.

#### Scenario: Node list column order

- **WHEN** an administrator opens the Node list
- **THEN** the columns appear in the order status, hostname, OS, groups, node address, architecture, agent version, deployment mode, last seen, actions
- **AND** the OS column is directly adjacent to the hostname column

#### Scenario: Node list required fields

- **WHEN** an administrator opens the Node list
- **THEN** hostname, address, OS, architecture, agent version, deployment mode, status, and last-seen values remain available either as visible columns or through the column visibility controls
