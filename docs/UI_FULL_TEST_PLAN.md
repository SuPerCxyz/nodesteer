# NodeSteer 页面全量测试计划

版本：1.0
编制日期：2026-09-15
适用范围：当前 KVM2 环境中的 NodeSteer Hub Web UI、Native Agent 及其页面可见业务链路

本计划是面向真实浏览器操作的黑盒验收计划，补充并细化 [TEST_PLAN.md](./TEST_PLAN.md)。测试人员只能通过页面上的链接、按钮、表单、下拉框、Tab、确认框和文件选择完成业务变更；不得用 REST API、数据库或脚本替代页面创建、修改、删除、运行、部署和复制操作。

## 1. 目标与完成定义

### 1.1 测试目标

- 覆盖所有已发布页面、导航入口、列表、详情、弹窗、Tab、表单和操作菜单。
- 覆盖添加节点、添加脚本、添加服务/Managed Application、节点绑定任务、手动执行、调度、日志和清理。
- 对每个可操作按钮验证：可见性、启用/禁用状态、点击结果、重复点击行为、成功/失败反馈和最终状态。
- 对页面中出现的 Copy 按钮逐一点击，并读取浏览器剪贴板，核对复制内容与可见内容一致。
- 验证页面操作后的真实业务效果：Hub 状态、Agent 同步、Linux 进程/systemd、执行结果和页面最终反馈。
- 将不适用、环境阻塞、部分完成与失败明确区分，不以页面渲染或接口返回代替实际效果。

### 1.2 单条用例通过标准

关键业务用例必须完成以下闭环：

```text
用户点击/填写
  → 页面事件和按钮状态
  → HTTP/WebSocket 请求
  → Hub 状态、Revision、Audit
  → Agent 状态/本地状态
  → Linux 实际效果
  → 页面刷新后的最终反馈
```

只有在页面反馈和实际业务效果都正确时，才能标记 `PASS`。只验证到页面或 API 的用例标记 `PARTIAL`，无法执行的标记 `BLOCKED`。

## 2. 测试边界

### 2.1 明确纳入

1. 管理员登录、退出、导航、语言、主题、Sidebar、表格和公共按钮。
2. 节点列表、节点详情、Native 节点纳管、节点状态、标签、节点与任务联动。
3. Static Group、Label Group 的创建、编辑、成员变化和删除。
4. Script 创建、编辑、Revision、参数、环境变量、克隆、任务引用和删除。
5. Command Task、Script Task、Application Task 的创建、目标选择、运行、取消和删除。
6. Cron、Interval、One-Time Schedule 的创建、启停、执行和删除。
7. Artifact 上传、校验、下载、引用、删除。
8. Managed Application（服务）创建、节点分配、部署、启动、停止、重启、升级、健康检查和删除。
9. Execution 列表、过滤、详情、stdout/stderr、日志复制和取消。
10. 文件传输页面的表单、目标、开始、重试、取消和状态展示（环境支持时）。
11. Audit、Users、Settings 页面及管理员权限范围。
12. 重要错误路径、空态、加载态、重复点击、刷新恢复和返回路径。

### 2.2 明确不以 Native 结果代替的范围

- Docker Agent、Docker Host Integration、容器重建持久化：需要独立可用的 Docker 测试环境，当前 Native 环境结果不能代替。
- Hub 故障、断网、离线调度、通知丢失、Revision Reconciliation、文件传输恢复等系统故障场景：属于单独的系统测试批次，不能由普通页面操作推断通过。
- OIDC/SSO：只有环境启用并提供测试 IdP 时执行，否则标记 `BLOCKED`。
- 多浏览器/多版本兼容性：本轮先执行 Chromium 主路径，其他浏览器另行安排。

### 2.3 节点纳管的两种验收层级

节点页面本身必须完成 Native/docker run/docker compose 命令生成、字段校验和复制验证。

完整“添加节点”还必须完成：

```text
页面生成注册命令 → 在全新未纳管 Agent 上执行 → HELLO/Heartbeat
→ 节点从 pending 变为 online/synced → 节点详情可操作
```

当前 KVM2 的 5 个 Native Agent 均已纳管。若没有额外的全新目标机，页面生成和复制可执行，但真实注册只能记为 `PARTIAL`；不得停用或重置既有 Agent 来伪造新节点。需要完整验收时，应使用一台临时 Ubuntu Native Agent VM，并在执行前记录创建、清理和磁盘范围。

## 3. 测试环境基线

| 角色 | 地址/标识 | 用途 |
|---|---|---|
| Hub Web/API | `192.168.100.249:8080` | 页面和只读状态核验 |
| Hub Agent Gateway | `192.168.100.249:8443` | Agent 连接与同步 |
| Native Agent | `192.168.100.238` | Debian 13 |
| Native Agent | `192.168.100.222` | Fedora 42 |
| Native Agent | `192.168.100.204` | Rocky 9.8 |
| Native Agent | `192.168.100.247` | openSUSE Leap 16 |
| Native Agent | `192.168.100.237` | Ubuntu 26.04 |
| KVM 主机 | `kvm2` | VM/QGA/服务状态补充核验 |

前置检查：

- [ ] `/healthz` 和 `/readyz` 可用。
- [ ] 5 个现有 Native Agent 均为 `online/synced`，不修改其现有配置。
- [ ] Hub、Agent、浏览器和 KVM 时间基本一致。
- [ ] 浏览器控制台和网络面板从干净会话开始记录。
- [ ] 使用安全凭据输入方式登录；密码、Token、Cookie 不写入本文件、截图或日志。
- [ ] 既有对象清单已记录，避免误删历史数据。

## 4. 数据、文件和清理规则

### 4.1 测试数据

统一使用唯一前缀：`qa-20260915-ui-`。

建议对象：

| 对象 | 示例名称 |
|---|---|
| Script | `qa-20260915-ui-script` |
| Script clone | `qa-20260915-ui-script-clone` |
| Group | `qa-20260915-ui-group` |
| Label Group | `qa-20260915-ui-label-group` |
| Command Task | `qa-20260915-ui-command-task` |
| Script Task | `qa-20260915-ui-script-task` |
| Application | `qa-20260915-ui-service` |
| Artifact | `qa-20260915-ui-artifact` |
| Schedule | `qa-20260915-ui-schedule` |

已有的 API smoke task 仅作为基线观测，不编辑、不删除、不作为本轮页面创建结果。

### 4.2 安全测试制品

应用测试只使用无敏感信息、无破坏性操作的临时制品。制品应：

- 只写入自身临时目录或 stdout；
- 可启动、停止、重启并被 systemd 管理；
- 能返回稳定的健康检查结果；
- 不修改系统账号、网络、磁盘分区或现有应用；
- 测试完成后通过页面删除应用、制品和任务，再用只读方式确认 Agent 上没有残留 unit、进程和文件。

### 4.3 清理要求

- 只删除本轮以 `qa-20260915-ui-` 创建的对象。
- 删除前保存对象 ID、执行 ID、截图和必要的日志证据。
- 删除操作本身也必须通过页面按钮完成，并验证取消删除分支。
- 保留最终测试报告、失败证据和执行 ID；不保留密码、Token 或完整敏感日志。
- 临时 VM 只有在明确记录目标和授权后才能删除，不能使用宽泛删除命令。

## 5. 公共页面与按钮测试

### UI-001 登录与会话

页面：`/sign-in`

操作与验收：

1. 未登录访问根路径和受保护子路径，跳转登录页。
2. 输入正确管理员凭据并点击登录，按钮进入 loading/禁用，成功跳转 Dashboard。
3. 输入错误凭据、空用户名、空密码，显示明确错误，不产生有效会话。
4. 退出登录：点击 Profile → Logout，分别验证取消和确认；确认后 Token 清除，刷新受保护页面仍需登录。
5. 直接刷新、浏览器前进/后退、带合法 redirect 登录，路径和会话状态正确。
6. 过期/无效会话只通过页面表现为重新登录；不把受限页面内容继续留在可操作状态。

### UI-002 布局、导航和主题

- 逐个点击 Sidebar 一级入口，确认 URL、标题、active 状态和返回路径。
- 验证 Dashboard、Agents、Tasks、Executions、Transfers、Schedules、Scripts、Groups、Applications、Artifacts、Audit、Users、Settings。
- 点击语言切换并刷新，页面文本和偏好保持。
- 点击 light/dark/system 主题并刷新，布局、对比度和主题保持。
- 折叠/展开 Sidebar，移动宽度打开/关闭菜单，点击导航后菜单按预期关闭。
- 点击所有 View All、详情链接、面包屑、返回按钮，确认没有死链和错误 redirect。

### UI-003 公共表格和按钮

在每个列表页至少执行一次：

- 搜索命中、无结果、清空搜索、Reset；
- 列隐藏/恢复、排序（若页面提供）、分页、每页数量；
- Loading、Empty、No Results、Error/Retry；
- 首页/末页按钮禁用状态；
- 操作按钮的 hover/tooltip、可见性、loading 禁用和重复点击保护；
- 删除按钮的确认框取消/确认；取消不得产生业务请求。

## 6. 节点、分组和标签

### UI-010 节点列表和详情

1. 点击 Agents/Nodes，验证 5 个已纳管节点、状态、IP、OS、架构、版本、模式、Revision、Last Seen。
2. 搜索 hostname、Node ID、IP，验证命中与无结果。
3. 点击每个节点详情，验证 Overview、Inventory、Tasks、Schedules、Applications、Executions 等 Tab。
4. 点击任务、调度、应用和执行回链，确认目标 Node ID 一致。
5. 在节点详情添加、编辑、覆盖和清除 Label；刷新后验证持久化以及 Group 成员变化。
6. 对 maintenance/online/offline 状态执行页面提供的状态按钮，确认确认框、状态变化和列表刷新。
7. 访问无效 Node ID，验证 404/Error/Retry，不显示另一节点数据。

### UI-011 添加节点与 Copy 按钮

1. 点击 Add Node，验证弹窗打开、关闭、取消和重新打开后的表单重置。
2. 分别提交空名称、非法名称、非法地址、合法 IPv4/DNS、非法 Hub 地址，验证前端校验和错误反馈。
3. 填写合法信息，分别点击 Native、docker run、docker compose Tab。
4. 点击生成命令，验证一次点击只产生一次结果，loading 期间按钮禁用。
5. 点击每个命令区域的 Copy，立即读取浏览器剪贴板，核对剪贴板与页面完整命令（含换行、引号、Token 占位和值）一致。
6. 验证复制成功提示、重复复制、剪贴板权限失败时的错误提示。
7. 有临时全新 Agent 时，在该 Agent 上执行页面生成的 Native 命令，回到页面轮询 pending → online → synced，并验证节点详情和心跳。
8. 无临时 Agent 时，只将命令生成/复制标记通过，实际纳管标记 `PARTIAL`，不声称节点添加完成。

### UI-012 Groups

- 创建 Static Group：填写名称、描述、多个节点，保存后列表和成员正确。
- 创建 Label Group：填写 label key/value，保存后动态成员数量正确。
- 编辑名称、描述、成员和 Label 条件，刷新后保持。
- 修改节点 Label，回到 Group 验证动态成员变化，并验证 Task 目标随之变化。
- 点击删除，验证取消不删除、确认后只删除本轮对象；引用关系按产品定义处理。

## 7. Script 页面

页面：`/scripts`

### UI-020 创建和编辑 Script

1. 点击 Add Script，验证取消、返回和表单重置。
2. 填写名称、描述、Shell/Bash/Python、内容、Working Directory、Run User、Timeout、Enabled。
3. 添加、修改、删除参数；验证参数名称、类型、默认值和空名称校验。
4. 添加、覆盖、删除环境变量；验证空 KEY 禁止保存，secret 值不在列表/日志泄漏。
5. 点击 Save，验证列表、Revision 1、SHA256、详情内容和 Audit。
6. 编辑内容并保存，验证 Revision +1，历史版本内容可查看且执行引用固定版本。
7. 点击 Clone，验证新 ID、Revision 1、名称、内容、参数和环境变量复制正确且相互独立。
8. 点击 Delete，分别验证取消和确认；确认后列表消失、Agent 最终删除本地副本、历史 Execution 保留。
9. 验证重复名称、超长内容、非法超时和服务端错误提示。

## 8. Task 页面

页面：`/tasks`

### UI-030 创建 Task

分别创建以下三类本轮对象：

- Command Task：执行安全命令并输出固定标记；
- Script Task：选择已创建 Script 和 Revision，传入参数/环境；
- Application Task：选择本轮 Application，并选择部署或生命周期操作。

每类均验证：

1. 名称、描述、Enabled、Timeout、Retry、Run User、Offline Policy。
2. 单节点、多节点、Static Group、Label Group 目标；取消目标后不得残留旧 ID。
3. 参数新增/删除、类型、默认值和 secret 显示策略。
4. Local Condition、Remote Condition 的合法值、空值、未知值和 Fail Closed 表现。
5. 保存后列表、详情 Overview/Definition/Targets/Executions/Schedule 五个 Tab。
6. 节点详情、Group 详情、Script 详情和 Application 详情之间的反向联动。
7. 无名称、无命令、无 Script、无 Application、无目标、非法 ID 的错误反馈。

### UI-031 Task 运行和取消

1. 在 Task 列表或详情点击 Run Now，验证确认框的取消分支不产生执行。
2. 确认运行后，验证按钮 loading/禁用、PENDING → RUNNING → SUCCESS/FAILED 的页面刷新。
3. 多节点目标必须为每个节点显示独立 Execution；目标和输出不得串线。
4. 验证 Script 参数、环境变量、目录、Run User 在 Agent 实际生效。
5. 对长时间任务点击 Cancel，验证确认/取消分支，最终 CANCELED 且 Agent 无残留进程。
6. 对 offline 节点验证明确 BLOCKED/失败反馈，不无限 loading。
7. 重复点击 Run、刷新页面和重新打开详情，验证不会重复创建非幂等执行。

## 9. Schedule 页面

页面：`/schedules`

- 创建 Cron：合法表达式、非法表达式、Timezone、Owner、Enabled、Allow Offline、Misfire。
- 创建 Interval：最小值、0、负数、小数、超大值和单位切换。
- 创建 One-Time：未来时间、过去时间、时区、空时间、Run Once。
- 验证编辑只改变目标字段，不覆盖未修改字段。
- 点击启用/禁用，确认状态、按钮反转和实际执行停止/恢复。
- 点击删除，验证取消/确认、Task Detail 和 Schedule List 同步消失。
- 对允许离线的调度验证由 Agent Local Scheduler 触发；不允许离线的调度不得被 Agent 双触发。
- 对一次性调度验证成功后不重复执行，Misfire 按配置处理。

## 10. Artifact 与服务/应用

### UI-040 Artifact

1. 点击 Add/Upload Artifact，验证文件选择、名称、版本、架构和取消。
2. 上传本轮安全临时制品，验证上传按钮 loading/禁用。
3. 验证页面展示 Size、SHA256、版本、架构、下载入口和时间。
4. 点击 Download，读取文件内容和页面显示的 SHA256，二者一致。
5. 验证空文件、重复对象、非法架构、超限和上传失败反馈。
6. 点击 Delete，验证确认框取消/确认、引用中的制品行为、列表和 Usage 刷新。

### UI-041 添加服务/Managed Application

1. 点击 Add Application，填写名称、版本、描述、Artifact、Binary Path、Config Path、Unit 名称和配置。
2. 添加/覆盖/删除 Arguments 和 Environment，验证空值校验和敏感值展示。
3. 分别验证 systemd、TCP、HTTP、Command Health Check（页面支持哪种就执行哪种，未支持的明确记录）。
4. 保存后验证 Revision、Artifact Usage、Application Detail 和 Audit。
5. 选择一个测试节点进行 Assignment；确认 Node Detail 的 Managed Applications 与 Application 页一致。
6. 在 Application 页面依次点击 Deploy、Start、Stop、Restart，验证确认框、按钮 loading、Execution、State、Health 和 Linux systemd 实际状态。
7. 页面刷新或重新登录后，版本、状态、节点分配和错误信息保持。
8. 使用第二版安全制品执行 Upgrade，验证版本变化、旧版本备份和成功健康检查。
9. 使用会导致健康检查失败的安全测试版本执行失败升级，验证 FAILED、旧版本恢复、systemd active/running 和回滚原因。
10. 点击删除，验证取消/确认、Assignment/State/Revision 的产品定义行为，并确认 Agent 无残留进程/unit/file。

## 11. Execution 与日志

页面：`/executions` 和 `/executions/{id}`

- 按 Execution ID、Task、Node、状态、触发类型搜索/过滤，验证 URL 参数和清空。
- 打开每种状态的详情，核对 Task、Revision、Script、Node、Trigger、Exit Code、Duration、Sync 状态。
- 在 Logs Tab 验证 stdout、stderr、all、搜索、Wrap、Follow、空日志和截断标记。
- 点击 Copy Logs，读取剪贴板并核对当前选定 stream/搜索结果/换行内容。
- 对 RUNNING 执行点击 Cancel；非 RUNNING 执行不展示或禁用取消按钮。
- 验证 Task/Script Revision 在 Execution 中固定记录，不因后续编辑而改变历史。
- 验证失败、超时、取消、阻断和成功的状态文案、错误原因、日志和 Audit。

## 12. Transfers、Audit、Users、Settings

### UI-050 文件传输

在环境支持时执行：选择 Source Agent、源路径、一个或多个 Target Agent、目标路径，逐个点击 Add Target、Remove Target、Start、Retry、Cancel。验证源节点不能作为目标、重复目标校验、每目标状态隔离、最终文件内容/SHA256/Mode 和失败重试。

### UI-051 Audit

用本轮页面操作产生并筛选登录、节点、Script、Task、Schedule、Artifact、Application、Execution、用户和设置事件。核对 action、resource、resource_id、username、时间和详情；确认普通角色不能修改/删除 Audit。

### UI-052 Users/RBAC

管理员页面验证创建 Administrator、Operator、Viewer 测试用户、重复用户名、空字段、角色修改和登录权限。每个角色重新登录后通过页面验证：

| 能力 | Administrator | Operator | Viewer |
|---|---:|---:|---:|
| 查看列表和详情 | 允许 | 允许 | 允许 |
| 添加/编辑/删除节点与资源 | 允许 | 禁止 | 禁止 |
| 立即运行任务 | 允许 | 按产品权限 | 禁止 |
| 取消自己的运行 | 允许 | 按产品权限 | 禁止 |
| Artifact 上传/删除 | 允许 | 禁止 | 禁止 |
| Application 部署/运维 | 允许 | 按产品权限 | 禁止 |
| 用户管理/设置修改 | 允许 | 禁止 | 禁止 |

页面隐藏必须与实际点击后的 403/错误反馈一致，不能只验证按钮隐藏。

### UI-053 Settings

- 点击 Runtime/Appearance Tab，验证字段加载、编辑、保存、取消和刷新持久化。
- 输入空值、0、负数、超大值、非法 Gateway URL，验证错误反馈。
- 验证 Theme、Heartbeat、Revision Check、Changelog、Gateway、Max Log 等设置是否影响页面或 Agent；不确定时标记待补充。

## 13. 页面操作与 Copy 验收规范

所有复制按钮建立清单并逐项记录，至少覆盖：

| 位置 | 复制内容 | 核对方式 |
|---|---|---|
| Add Node / Native | Native 安装命令 | 页面命令与剪贴板逐字符核对 |
| Add Node / docker run | docker run 命令 | 页面命令与剪贴板逐字符核对 |
| Add Node / Compose | Compose 内容/命令 | 页面内容与剪贴板逐字符核对 |
| Execution Logs | 当前日志内容 | 过滤/stream 后复制并核对 |
| Node/Artifact/Application 详情 | ID、路径、SHA256（若提供） | 页面可见值与剪贴板核对 |

每次复制至少记录：

- 点击前按钮是否 enabled；
- 点击后是否显示 Copied/Success；
- 浏览器剪贴板实际文本；
- 是否包含额外前缀、缺失换行或多余空格；
- 重复点击、切换 Tab 后复制和剪贴板权限失败结果。

## 14. 端到端验收流程

### E2E-01 节点 → 任务 → 执行

添加/选择节点 → 创建 Command Task → 绑定单节点 → 页面 Run Now → 查看 Execution → 查看日志 → 刷新节点详情。要求节点、目标、输出、状态、Audit 一致。

### E2E-02 Script → 多节点任务

创建 Script r1 → 修改为 r2 → 创建 Script Task 引用 r2 → 绑定 5 个 Native Agent → 页面运行 → 验证每节点独立 Execution、参数/环境实际生效和 Revision 固定。

### E2E-03 Group/Label → 动态目标

给节点添加 Label → 创建 Label Group → 创建以 Group 为目标的 Task → 修改 Label → 刷新 Group/Task/Node 页面 → 运行并核对实际目标集合。

### E2E-04 Artifact → 服务

上传安全制品 → 创建 Managed Application → 分配节点 → Deploy → Start/Stop/Restart → 查看 Health/Execution → Upgrade → 失败升级回滚 → 页面删除测试对象。

### E2E-05 Schedule → Local Scheduler

创建 One-Time 或短间隔 Schedule → 页面启用 → 等待 Execution → 禁用 → 验证不再产生新执行；若设置 Allow Offline，补充 Agent 离线/恢复专项测试。

### E2E-06 Copy 完整链路

逐页发现 Copy 按钮 → 点击 → 读取剪贴板 → 与可见内容核对 → 切换页面/Tab 后再次复制 → 记录成功或失败证据。

## 15. 证据、判定和缺陷

每条用例至少保存：用例 ID、页面 URL、操作步骤、结果、截图路径、关键对象 ID/Execution ID、必要的只读 API/SSH/QGA 证据和时间。

缺陷必须包含：

```text
缺陷编号
用例编号
环境/节点/浏览器
前置数据
逐步复现操作
预期结果
实际结果
页面截图/控制台/网络证据
Hub/Agent/Linux 补充证据
严重性
是否可复现
当前状态
```

严重性：

- P0：数据破坏、权限绕过、任意命令/文件越界或无法登录。
- P1：核心创建、执行、部署、回滚或状态一致性失败。
- P2：重要边界、错误反馈、日志或联动错误。
- P3：文案、布局、非核心可用性问题。

## 16. 执行顺序

1. 基线、登录、公共布局和表格。
2. 节点详情、节点纳管命令、Copy 和实际注册。
3. Group/Label。
4. Script 和 Revision。
5. Command/Script Task、节点目标和执行日志。
6. Schedule 和 Local Scheduler。
7. Artifact、Managed Application、systemd 和回滚。
8. Transfers。
9. Audit、Users、RBAC、Settings。
10. 端到端回归、失败复验和清理。

每个阶段结束后重新刷新相关列表并检查状态一致性，再进入下一阶段。创建对象失败时先记录证据，修复或重试后不能覆盖原始失败记录。

## 17. 总体验收标准

本轮页面全量测试只有在以下条件同时满足时才能标记 `PASS`：

- 必测页面和核心按钮均已执行，有明确结果。
- 添加节点、脚本、服务、任务绑定节点和执行链路真实通过；节点无临时 VM 时明确标记 `PARTIAL`。
- 每个发现的 Copy 按钮均完成剪贴板核验。
- 核心状态、Revision、Execution、Audit、Agent 同步和 Linux 实际效果一致。
- P0/P1 缺陷为 0；P2/P3 缺陷均有结论和风险接受记录。
- 未执行项、环境阻塞项、Docker/故障/OIDC 等边界明确列出。
- 本轮数据已通过页面清理，且没有误删既有对象或遗留服务/进程/文件。

执行记录使用 [UI_FULL_TEST_REPORT_TEMPLATE.md](./UI_FULL_TEST_REPORT_TEMPLATE.md)。
