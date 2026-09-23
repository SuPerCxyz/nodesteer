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

### Requirement: Schedule 创建与编辑
系统 SHALL 提供 Schedule 创建与编辑界面。

#### Scenario: 创建 Schedule
- **WHEN** 用户填写类型（cron/interval/one_time）、表达式、时区、owner、offline/misfire 策略并保存
- **THEN** 调用真实 API 创建 Schedule 并显示在列表

#### Scenario: 编辑 Schedule
- **WHEN** 用户修改现有 Schedule 并保存
- **THEN** 调用真实 API 更新

### Requirement: Application 操作
系统 SHALL 在 Application 页面提供 Deploy、Upgrade、Start、Stop、Restart 操作按钮。

#### Scenario: 触发部署
- **WHEN** 用户点击 Deploy 并选择目标节点
- **THEN** 调用真实 API 触发部署并跳转执行结果

### Requirement: Script Clone 与 Revision History
系统 SHALL 支持脚本克隆，并展示修订历史列表。

#### Scenario: 克隆脚本
- **WHEN** 用户点击 Clone
- **THEN** 创建副本并进入编辑

#### Scenario: 查看历史
- **WHEN** 用户打开脚本修订历史
- **THEN** 展示全部历史版本（编号/时间/SHA），可查看内容

### Requirement: Task 参数与条件编辑
系统 SHALL 在 Task 编辑中提供 Parameters 与 Condition 编辑入口。

#### Scenario: 编辑参数
- **WHEN** 用户编辑任务参数定义与默认值
- **THEN** 保存到真实 API

### Requirement: Node Detail 分区
系统 SHALL 在 Node Detail 展示 Tasks、Schedules、Managed Applications、Sync 分区。

#### Scenario: 查看节点任务
- **WHEN** 打开节点详情
- **THEN** 分区展示该节点相关任务、调度、托管应用与同步状态

### Requirement: Dashboard 统计
系统 SHALL 展示 Nodes Online/Offline、Execution Running/Success/Failed、Sync Synced/Outdated/Error、Application Healthy/Unhealthy 统计与 Recent Failures。

#### Scenario: 加载统计
- **WHEN** Dashboard 加载
- **THEN** 各统计来自真实后端聚合数据

### Requirement: Execution Duration 与 Artifact Usage
系统 SHALL 展示 Execution 耗时，以及 Artifact 被哪些 Application 引用。

#### Scenario: 执行耗时
- **WHEN** 查看 Execution 详情或列表
- **THEN** 显示 Start-End 计算出的 Duration

#### Scenario: Artifact 引用
- **WHEN** 查看 Artifact 详情
- **THEN** 展示引用该 Artifact 的 Application 列表

### Requirement: OIDC 登录页
系统 SHALL 在登录页根据 `/api/oidc/state` 决定展示 SSO 入口或本地登录表单。

#### Scenario: OIDC 启用时展示 SSO 入口
- **WHEN** `/api/oidc/state` 返回 `{"enabled": true}`
- **AND** 用户打开登录页
- **THEN** 页面显示"使用 SSO 登录"按钮，不显示本地用户名/密码表单
- **WHEN** 用户点击按钮
- **THEN** 浏览器跳转到 `/api/oidc/login`

#### Scenario: OIDC 未启用时维持本地登录
- **WHEN** `/api/oidc/state` 返回 `{"enabled": false}`
- **AND** 用户打开登录页
- **THEN** 页面显示本地用户名/密码表单，无 SSO 按钮

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

### Requirement: Node Detail Managed Applications
系统 SHALL 使用真实分配数据展示节点的 Managed Applications。

#### Scenario: 展示已分配应用
- **WHEN** 打开 Node Detail
- **THEN** Managed Applications 分区调用 `/applications/{id}/nodes` 反向关联展示真实分配的应用
- **AND** 不再使用执行记录启发式推断

### Requirement: Application 增强
系统 SHALL 支持 Version 历史查看、Environment 输入、真实 Health 展示。

#### Scenario: 应用详情
- **WHEN** 打开 Application 编辑页
- **THEN** 可查看版本历史、编辑 environment、展示已分配节点

### Requirement: Task 增强
系统 SHALL 在 Task 编辑页提供 Schedule 区块与 Execution History。

#### Scenario: 任务编辑
- **WHEN** 打开 Task 编辑页
- **THEN** 可查看关联 Schedules、按 task_id 查看执行历史

