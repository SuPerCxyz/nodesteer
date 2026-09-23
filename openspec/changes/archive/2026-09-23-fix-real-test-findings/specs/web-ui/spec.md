# web-ui Specification Delta

## ADDED Requirements

### Requirement: Role-aware actions

The Web UI SHALL hide administrator-only create, edit, delete, enrollment, save, node-maintenance, and file-transfer mutation actions from Operator and Viewer users. Operator users SHALL retain permitted task execution, application operation/deployment, execution cancellation, and log viewing actions. Viewer users SHALL retain read-only pages and Artifact download.

#### Scenario: Operator actions

- **WHEN** an Operator opens the resource, task, application, and execution pages
- **THEN** only the actions allowed by the role are displayed

#### Scenario: File transfer mutation actions

- **WHEN** an Operator or Viewer opens the File Transfer page
- **THEN** creation, target management, retry, cancel, and start actions are absent
- **AND** the transfer history remains readable

#### Scenario: Viewer actions

- **WHEN** a Viewer opens any resource or task page
- **THEN** write and run actions are absent while read-only content remains available

### Requirement: Reset enrollment dialog

The Web UI SHALL clear enrollment inputs, errors, generated commands, selected method, and copy status when the Add Node dialog closes.

#### Scenario: Reopen enrollment dialog

- **WHEN** a user closes Add Node after a validation error and opens it again
- **THEN** the form is blank with the default Hub address and no previous error or generated command

### Requirement: Accurate label group count

The Groups page SHALL calculate a Label Group member count from the current Node labels and display the current matching count without requiring a write operation.

#### Scenario: Label match count

- **WHEN** current nodes contain a label matching a Label Group
- **THEN** the list displays the number of matching nodes
