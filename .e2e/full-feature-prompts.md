# NodeSteer 当前全功能 E2E 检查提示词

适用于当前源码与真实 KVM2 Hub/Agent。本文件组织测试，不修改产品基线、不自动修复产品。必须先确认本轮测试范围和环境；历史 PASS 不能替代本轮结果。

## 总控提示词（复制使用）

你是 NodeSteer 全功能 QA。先读取 AGENTS.md、docs/PRODUCT.md、docs/ARCHITECTURE.md（尤其 §66、§67）、docs/DEV_RULES.md、.e2e/README.md 及其规定的五份状态文件，核对当前代码和测试版本。遵守最新已确认范围；仅在授权后创建本轮数据、执行故障操作。使用 KVM2 实际 Hub、Native Agent 和可用隔离 Docker Agent；先确认地址/角色/部署模式/健康，不能把固定历史地址当作现状。使用 agent-browser 独立 named session 做页面操作；API/Agent/客户机证据用于串联业务效果，不能用 API 创建代替必须检验的 UI 创建路径。

每条 case_id 执行完整链路：用户操作 → 请求/事件 → Hub 持久状态及 Revision/Audit → Agent 同步/执行/本地 Journal → 客户机文件/进程/systemd 效果 → 页面反馈与刷新恢复。适用层不完整时记 PARTIAL。每种类型、角色、状态、反向操作、边界和错误路径独立记录；矩阵维度不得只测一个代表后整组 PASS。缺少控件须失败/记录产品缺口，不允许 isVisible() 或缺数据静默跳过。

将 REAL_E2E、REAL_UI、REAL_API、UI_MOCK、STATIC、BLOCKED 分开；Mock/静态通过不能进入真实业务通过数。状态为 NOT_RUN/RUNNING/PASS/FAIL/PARTIAL/BLOCKED，记录实际时间、步骤、预期、实际、对象ID、请求摘要、证据路径、清理状态。不得改变源码掩盖测试失败，不得扩大产品功能。完成报告列覆盖分母、执行/通过/失败/未测/阻塞、架构场景和不变量结果、所有残留和限制。

## 环境、权限和证据提示词

通过受控私密文件/环境注入 BASE_URL、NODESTEER_E2E_USERNAME、NODESTEER_E2E_PASSWORD 和 RBAC 用户；不输出秘密、环境全量、配置全文或登录响应。不把凭据作为命令参数，不开启包含秘密的 trace/video；登录、secret输入、纳管token所在页不得直接截图。已登录后的截图和请求摘要也应脱敏。先保存只读 baseline：Hub健康、节点身份/状态/Revision、Agent版本/模式/capability、各资源ID、服务和文件状态。不同执行者使用不同浏览器 session、数据前缀与独占证据目录；共享 Hub/Agent 重启/断网/升级/Settings 按主控串行调度。

测试数据前缀 qa-<run_id>-<owner>-，任何临时资源都记录创建返回ID。仅删除本轮精确捕获ID；禁止按宽名称前缀、cron表达式、目录通配符删除历史对象。保护既有VM/Agent/节点/用户/Execution/Audit；用户和执行历史若无删除API则记录残留，不靠数据库直删。临时文件/systemd只使用本轮专属路径/Unit，操作已有资源时先获指定授权且恢复baseline。故障注入不能终止非本轮无授权进程，所有恢复均验证真实状态。

## 页面与公共控件提示词

以下按当前实现校正：独立 /schedules，旧 /tasks?view=schedules 重定向；节点/任务/执行详情是单页分区；Artifacts 从应用管理入口访问。逐项执行，不能以页面文字存在当成功。


### G-01 认证与路由

| 用例 | 操作 | 预期 |
|---|---|---|
| G-01-01 | 未登录访问 `/`、`/agents`、`/tasks` 等 | 跳转 `/sign-in` |
| G-01-02 | 已登录访问 `/sign-in` | 不进入登录表单，回到业务页 |
| G-01-03 | 登录带 `redirect` 的目标页 | 成功后只回到本站合法路径 |
| G-01-04 | redirect 为外部 URL、`/sign-in` 或非法值 | 不发生开放重定向 |
| G-01-05 | 修改 localStorage Token 后刷新页面 | 401、清理 Token、跳转登录 |
| G-01-06 | `/api/me`、`/api/logout` | 返回正确用户和会话失效结果 |

### G-02 Header、Sidebar、Profile

- [ ] Header 语言菜单切换中文/English，刷新后保持。
- [ ] Theme 菜单验证 `light/dark/system`，刷新后保持，`theme-color` 正确。
- [ ] Profile 菜单进入 Settings。
- [ ] Profile 菜单退出登录，分别验证取消和确认。
- [ ] 退出后 Token、用户状态清除，刷新受限页面仍需登录。
- [ ] 桌面端 Sidebar 折叠/展开，页面布局不溢出。
- [ ] 移动端 Sidebar 打开/关闭，点击导航后自动关闭。
- [ ] 验证当前 12 个 Sidebar 导航项：Dashboard、Tasks、Schedules、Executions、Transfers、Agents、Groups、Scripts、Applications、Audit、Users、Settings；Artifacts 通过 Application 页面“管理制品”入口及直接 URL 访问。
- [ ] 导航 active 状态、浏览器前进后退和直接 URL 访问正确。

### G-03 DataTable

所有使用 DataTable 的页面都执行一次：

- [ ] 搜索命中。
- [ ] 搜索无结果显示 No Results。
- [ ] 清空搜索恢复数据。
- [ ] Reset 清除搜索/筛选。
- [ ] View 菜单隐藏列、恢复列，操作列不可误隐藏。
- [ ] 切换每页 10/20/30/40/50 条。
- [ ] 首页、上一页、页码、下一页、末页。
- [ ] 第一页/最后一页按钮禁用状态。
- [ ] API 失败显示 Error，Retry 会重新请求。
- [ ] Loading、Empty、No Results 三种空数据语义不混淆。

### G-04 公共交互与可访问性

- [ ] 所有提交按钮 pending 时禁用，不能重复创建。
- [ ] 删除菜单必须打开确认框；取消不发送 DELETE，确认后才删除。
- [ ] 所有 Alert/Error 文案具体、无重复通用错误。
- [ ] Tab 键顺序、Enter 提交、Escape 关闭弹窗符合预期。
- [ ] 所有图标按钮具备可识别 aria-label/title。
- [ ] 1366×768、1440×900、移动宽度验证无横向溢出和遮挡。
- [ ] 长 ID、SHA256、路径、命令可复制或换行，不覆盖按钮。

## 5. 页面按钮级测试

### P01 登录 `/sign-in`

- [ ] 正确用户名/密码登录，跳 Dashboard，用户和角色正确。
- [ ] 错误密码停留登录页，显示明确错误，不写入 Token。
- [ ] 用户名为空、密码为空、同时为空，前端校验和服务端响应正确。
- [ ] 登录按钮 loading 禁用，重复点击只产生一次请求。
- [ ] Password 显示/隐藏按钮正确。
- [ ] 中文/English 切换及刷新持久化。
- [ ] OIDC disabled 时 SSO 按钮不显示。
- [ ] OIDC enabled 时 SSO 按钮跳转 IdP，state/PKCE 正常。
- [ ] `allow_local_login=false`（SSO 模式）时本地登录一律返回 403 并记审计；`allow_local_login=true`（默认本地模式）时本地登录可用、SSO 按钮与 `/api/oidc/login`（404）均关闭。
- [ ] OIDC callback 缺少 state/code、错误 state、重复 state、错误 code。

### P02 布局

- [ ] 13 个导航项逐个打开对应页面。
- [ ] Header 语言、主题、Profile、退出及确认框。
- [ ] Sidebar 桌面/移动端操作。
- [ ] 浏览器前进、后退、刷新、直接输入子路由。
- [ ] Hub unavailable 状态点和文案变化。

### P03 Dashboard `/`

- [ ] 顶部 4 个指标：节点总数、运行中执行、失败执行、成功率。
- [ ] Running Executions 的 View All 和每条执行链接。
- [ ] Agent Health 的 View All。
- [ ] Application Status 的 View All 和每个应用链接。
- [ ] Sync Status 的 View All。
- [ ] Recent Executions 的 View All、任务名/执行详情链接。
- [ ] Upcoming Schedules 的 View All、调度链接。
- [ ] 节点、执行、应用、同步统计与对应列表 API 一致。
- [ ] 无运行执行、无应用、无调度、无失败执行的空态。
- [ ] Dashboard API 任一请求失败时 Error/Retry，不显示假数据。

### P04 节点列表 `/agents`、兼容路由 `/nodes`

- [ ] 列表字段：hostname、status、IP、Agent version、arch、deployment mode、last seen、操作。
- [ ] `/nodes` 和 `/agents` 均可访问，链接统一到 canonical `/agents`。
- [ ] hostname 进入 `/agents/{id}`。
- [ ] 操作菜单：online → maintenance；maintenance → online；set online。
- [ ] offline 节点 set online。
- [ ] operator/viewer 点击受限操作的 UI 可见性、403 和状态不变。
- [ ] 添加节点按钮权限与后端 admin-only 策略一致。
- [ ] 列表搜索、无结果、刷新、分页。
- [ ] 节点 API 失败 Retry。

### P05 节点纳管弹窗与详情

#### P05-A 添加节点弹窗

- [ ] 打开/关闭弹窗。
- [ ] 节点名称、节点地址、Hub 地址必填校验。
- [ ] 节点名称非法字符、长度边界。
- [ ] 节点地址 IPv4、IPv6、DNS、非法地址。
- [ ] Hub 地址仅允许 http/https 且 Host 有效。
- [ ] 点击“生成安装命令”只发送一次 POST。
- [ ] pending/loading 状态按钮禁用。
- [ ] Native、docker run、docker compose Tab 内容正确。
- [ ] 三种命令包含 Gateway、Registration Token、节点身份和节点参数。
- [ ] Copy 成功显示 Copied；剪贴板失败显示 copy failed。
- [ ] Retry 重新使用原参数，不产生额外错误。
- [ ] 提交后校验 Hub 侧 pending 节点记录；Agent HELLO 后复用 Node ID/Agent ID 并变为 online/synced。
- [ ] 关闭后重新打开，确认表单、命令和错误状态符合产品定义。

#### P05-B 节点详情

- [ ] Node Detail 单页各分区：Overview、Inventory、Capability、Labels、Tasks、Schedules、Applications、Executions；不要求不存在的 Tab。
- [ ] Node ID、Agent ID、IP、OS、Version、Arch、Mode、Revision、Sync、Last Seen。
- [ ] Inventory：OS、Kernel、CPU、Memory、Filesystem、Network。
- [ ] Capability enabled/disabled 展示。
- [ ] 无 Inventory、无任务、无调度、无应用、无执行的空态。
- [ ] Labels 添加、覆盖同名 key、空 key 禁用、保存后刷新。
- [ ] Tasks → Task Detail。
- [ ] Schedules → Schedule Detail。
- [ ] Managed Applications → Application Detail。
- [ ] Execution 表中的 Task 链接，ID 是否可点击按当前设计验证。
- [ ] 返回按钮回 `/agents`。
- [ ] 无效 Node ID 显示 404/Error/Retry。

### P06 分组 `/groups`

- [ ] 列表、搜索、分页、空态、Error/Retry。
- [ ] 新建 Static Group：名称、描述、多个节点成员、创建结果。
- [ ] 新建 Label Group：label key/value、动态成员结果。
- [ ] 编辑名称、描述、类型、成员、label 定义。
- [ ] 类型切换后旧字段不产生错误残留。
- [ ] 删除打开确认框；取消/确认分别验证。
- [ ] 节点 Label 变化后 Label Group 成员刷新。
- [ ] Group 变化后 Task Target 和 Node Detail 任务列表同步。
- [ ] 取消新建不发送请求。

### P07/P08 脚本 `/scripts`

- [ ] 列表字段、搜索、空态、分页、View 列控制。
- [ ] 新建入口、编辑入口、返回/取消。
- [ ] Shell、Bash、Python 三种解释器。
- [ ] 名称、描述、内容、Working Directory、Run User、Timeout、Enabled。
- [ ] 参数新增、类型选择、默认值、删除、空名称不添加。
- [ ] Environment 新增、覆盖、删除、空 KEY 不添加。
- [ ] 保存成功后列表出现、Revision=1、SHA256 正确。
- [ ] 编辑保存 Revision +1，历史内容可展开查看。
- [ ] Clone 生成独立 ID、Revision=1、名称 copy、内容/参数/环境一致。
- [ ] 删除确认框取消/确认、Tombstone、Agent 本地删除但历史 Execution 保留。
- [ ] 后端错误、重复名称、超长内容、超时边界。

### P09-P11 任务 `/tasks`

- [ ] 列表、搜索、分页、View、空态、Error/Retry。
- [ ] Task 名称详情、编辑、立即运行入口。
- [ ] 启用/禁用，状态和按钮反转。
- [ ] 删除确认框取消/确认。
- [ ] 类型 command：命令必填、保存、实际执行。
- [ ] 类型 script：脚本选择、禁用脚本不可引用、保存、实际执行。
- [ ] 类型 app_deploy：应用选择、节点目标、部署执行。
- [ ] 类型 app_operation：start/stop/restart/upgrade 四种操作。
- [ ] Target node：单节点、多节点、去选节点。
- [ ] Target group：单组、多组、去选组。
- [ ] Target label：key/value、匹配节点变化。
- [ ] 类型/目标切换后旧字段不误提交。
- [ ] Timeout、Retry、Run User、Enabled、Offline Policy。
- [ ] 参数添加/删除、secret 类型不在页面泄漏值。
- [ ] Local Condition 8 种 metric、6 种 operator、设置/清除。
- [ ] Remote Condition online、last_execution、operator、节点和 task_id 校验。
- [ ] Condition AND 结构通过 API 或当前支持的编辑入口验证。
- [ ] Task Detail 单页五个分区：Overview、Definition、Targets、Executions、Schedule（无Tab）。
- [ ] Definition 显示 command 内容、script 链接和 application 名称；当前 application 是文本，无详情链接，不虚构该控件。
- [ ] Execution History 链接到 Execution Detail。
- [ ] Schedule 表链接到 Schedule Detail。
- [ ] 空名称、空命令、无脚本、无应用、无目标、非法 ID 的错误反馈。

#### P11 立即运行

- [ ] 确认弹窗取消不发请求。
- [ ] 确认后创建 PENDING，发送到 online Agent，最终 SUCCESS/FAILED。
- [ ] 运行中按钮 loading/禁用。
- [ ] offline 节点不进入无限等待，返回明确 BLOCKED/错误。
- [ ] command/script/application 三类任务实际运行结果。
- [ ] 参数值、环境变量、Working Directory、Run User 实际生效。
- [ ] 多节点执行每个 Node 一条 Execution。
- [ ] 每次Manual Run新UUID是新意图；同Execution ID/Key重复投递/上传才验证幂等。

### P12-P13 调度 `/schedules`

- [ ] 列表、搜索、分页、类型/表达式/时区/Owner/Offline/Misfire/Enabled。
- [ ] 调度任务名链接、编辑、启用/禁用、删除确认。
- [ ] Cron 表达式必填和非法表达式。
- [ ] Interval 最小值、0、负数、小数和超大值。
- [ ] One-Time 未来时间、过去时间、时区、空 run_at。
- [ ] Hub Owner 和 Agent Owner。
- [ ] `allow_offline` 与 `hub_online_required` 组合。
- [ ] `run_once` 与 `skip` Misfire。
- [ ] 保存后不向非 one_time 调度提交无效 run_at。
- [ ] 调度编辑部分字段更新不覆盖未修改字段。
- [ ] Task Detail 调度表与 Schedule List 一致。
- [ ] 取消编辑不发送请求。

### P14-P15 应用 `/applications`

- [ ] 应用列表实际只有详情/编辑、删除、新建入口；不把列表不存在的部署按钮列入测试。
- [ ] 新建名称、版本、描述、Artifact、Binary Path、Config Path、Unit、Config。
- [ ] Arguments 添加/删除、Environment 添加/覆盖/删除。
- [ ] Health Check：systemd、tcp、http、command；target/timeout/attempts/interval。
- [ ] 自动 Unit 名称补全。
- [ ] 创建后 Revision、Artifact Usage、Node Assignment。
- [ ] 编辑保存 Revision +1、版本历史。
- [ ] 勾选多个节点分配，取消勾选后取消分配。
- [ ] Node Detail Managed Applications 回链。
- [ ] Deploy 按钮无节点时禁用。
- [ ] Deploy、Start、Stop、Restart、Upgrade 五种操作逐一验证。
- [ ] Admin/operator/viewer 的部署、定义修改权限。
- [ ] state 页面显示 version、operation、health、error、updated_at。
- [ ] 无状态、UNKNOWN、healthy、unhealthy、stopped。
- [ ] systemd/TCP/HTTP/Command 健康检查实际结果。
- [ ] 应用删除确认框取消/确认，关联 Assignment/State/Revision 行为正确。
- [ ] Artifact 不存在、节点离线、Capability 缺失、Agent 不可达。
- [ ] 应用部署失败后 Binary、Config、Unit、Health、Deployment Journal 均可恢复。

### P16 制品 `/artifacts`

- [ ] 列表、搜索、分页、View、空态、Error/Retry。
- [ ] 上传名称、版本、架构 amd64/arm64、文件。
- [ ] HTML required 阻止空字段提交。
- [ ] 重复对象、空文件、超大文件、上传中按钮禁用。
- [ ] Hub 计算 Size/SHA256，和本地校验一致。
- [ ] 下载得到正确文件、Content-Length、`X-Artifact-SHA256`。
- [ ] Artifact Usage 显示引用应用并可跳转。
- [ ] 删除确认框取消/确认；被应用引用时行为明确。
- [ ] Agent Cache 下载 `.tmp`、校验失败不安装、不污染正式缓存。
- [ ] 下载中断后临时文件清理和重试。

### P17-P18 执行 `/executions`

- [ ] 列表字段、分页、空态、无匹配结果。
- [ ] q 搜索：Execution ID、Task ID/名称、Node ID。
- [ ] status：PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELED/TIMED_OUT/BLOCKED。
- [ ] trigger：manual/schedule/system。
- [ ] status/trigger/q 清空后 URL 参数和结果正确。
- [ ] Task、Execution、Agent 三类回链。
- [ ] Detail 元数据：Task/Revision/Script/Node/Trigger/Offline/Synced/Exit/Duration。
- [ ] Execution Detail单页元数据/Logs分区（无Tab）。
- [ ] stdout、stderr、all stream；日志搜索。
- [ ] Wrap、Follow、Copy Logs。
- [ ] 空日志、截断日志、实时分片日志。
- [ ] RUNNING 取消确认、取消后 CANCELED；非 RUNNING 不显示取消按钮。
- [ ] BLOCKED 显示 block_reason。
- [ ] Agent Offline 执行 synced=false，恢复后幂等上传为 synced=true。
- [ ] Task/Script Revision 在 Execution 中固定记录。

### P19 审计 `/audit`

- [ ] 列表、搜索、分页、空态、Error/Retry。
- [ ] 登录、创建/修改/删除 Script、Task、Schedule、Artifact、Application。
- [ ] 执行、取消、部署、升级、节点状态、用户角色/密码修改。
- [ ] `action/resource/resource_id/username/time/detail` 正确。
- [ ] 审计记录不可被普通用户修改或删除。

### P20 用户 `/users`

- [ ] Admin 列表、搜索、分页、角色下拉。
- [ ] 新建用户按钮打开/关闭表单，取消恢复原状态。
- [ ] 用户名/密码必填，角色 administrator/operator/viewer。
- [ ] 创建成功、重复用户名、错误输入。
- [ ] 修改角色即时保存，刷新后保持。
- [ ] 修改 admin 自身角色的锁定/降权行为。
- [ ] Operator/Viewer 无用户管理控件，访问页面显示管理员限制。
- [ ] 后端 `/api/users/{id}` 密码修改、新旧密码生效范围。
- [ ] 当前无删除用户能力，必须明确记录为产品边界，不伪造删除通过。

### P21 设置 `/settings`

- [ ] Runtime/Appearance 两个切换按钮。
- [ ] Runtime 显示当前 API 的 settings 键和值；现部署原4键为 heartbeat、revision check、changelog、max log。gateway 当前 absent，不虚构控件或新增该配置。
- [ ] 修改、保存、成功提示、失败提示、重复保存。
- [ ] 非法数字、0、负数、超大值、非法 Gateway URL。
- [ ] 保存后刷新持久化。
- [ ] 设置变更是否广播到在线 Agent。
- [ ] Appearance 的 light/dark/system 和刷新持久化。
- [ ] Operator/Viewer 读取、保存按钮显示策略和 API 403。

### P22 文件传输 `/transfers`

- [ ] 源 Agent 下拉、源路径输入。
- [ ] 目标 Agent 下拉、目标路径输入。
- [ ] 源节点不能作为目标。
- [ ] 添加目标按钮的禁用条件。
- [ ] 添加多个目标、重复目标不允许。
- [ ] 删除已添加目标，不发送请求。
- [ ] 开始传输按钮的禁用条件和 loading。
- [ ] 成功后源路径、目标列表、输入状态重置。
- [ ] 每目标独立状态，整体状态正确聚合。
- [ ] 目标失败隔离，其他目标仍成功。
- [ ] FAILED → retry，验证整体重试和 `target_id` 单目标重试。
- [ ] PENDING/UPLOADING/DELIVERING → cancel，取消确认按当前产品设计验证。
- [ ] 源/目标相对路径、路径逃逸、错误 Agent Credential、文件超限。
- [ ] 目标文件内容、Mode、SHA256、原子替换和半文件清理。
- [ ] Hub 重启、Agent 重连、目标离线后的传输恢复。


## 跨页连续工作流提示词


| 编号 | 联动路径 | 必须验证 |
|---|---|---|
| FL-01 | Login → Dashboard → Agents | 登录、统计、节点 View All、节点状态一致 |
| FL-02 | Agents → Agent Detail → Task | 节点任务过滤、Task 回链、目标一致 |
| FL-03 | Agent Label → Label Group → Task Target | Label 改变后 Group/Task/Node Detail 同步 |
| FL-04 | Script Create/Edit/Clone → Task Script | 副本独立、Revision/SHA、任务引用正确 |
| FL-05 | Task → Run Now → Execution → Logs | 参数、环境、stdout/stderr、状态和回链 |
| FL-06 | Task → Schedule → Task Detail/Schedule List | 调度两处一致，删除后两处消失 |
| FL-07 | Execution Cancel → Audit | 取消状态和审计记录一致 |
| FL-08 | Artifact Upload → Usage → Application | Artifact Usage 回链，应用引用正确 |
| FL-09 | Application Assign → Agent Detail | Node Detail 托管应用与 Application 节点列表一致 |
| FL-10 | Application Deploy → State/Execution | 部署执行、健康、版本、错误一致 |
| FL-11 | Application Upgrade → Rollback | 版本、备份、Unit、Config、Health、Journal 一致 |
| FL-12 | Node Maintenance → Dashboard/Task Run | Dashboard 统计变化，任务运行按状态阻断 |
| FL-13 | File Transfer Create → Target File | Hub staging、目标文件、状态和 SHA 一致 |
| FL-14 | Token Expire → Login Redirect | 回跳目标安全且路径正确 |
| FL-15 | Admin Role Change → New Login | 新会话权限生效，旧会话行为明确 |
| FL-16 | Settings Save → Agent Runtime | Agent 心跳/Revision/日志设置实际消费或明确不消费 |

## 7. 端到端业务流程

| 编号 | 流程 | 通过标准 |
|---|---|---|
| E2E-01 | Script → Script Task → Node → Run → Logs → Audit | SUCCESS、输出和审计完整 |
| E2E-02 | Command Task 多节点运行 | 每节点一条执行，输出/状态正确 |
| E2E-03 | Script Task 参数/环境/用户/目录 | 客户机实际读取到正确值 |
| E2E-04 | Agent-owned Interval Offline | Agent 离线继续执行，恢复后无重复 |
| E2E-05 | Agent-owned Cron | 跨分钟准点执行且 Hub 不双触发 |
| E2E-06 | One-Time + Timezone + Misfire | 未来、过去、补跑/跳过符合策略 |
| E2E-07 | Schedule Disable | 禁用后不再产生新执行 |
| E2E-08 | Retry/Timeout/Cancel | 次数、最终状态、进程组清理正确 |
| E2E-09 | Local Condition | 满足执行，不满足 SKIPPED，未知不放行 |
| E2E-10 | Remote Condition | online/last_execution、TTL 过期 Fail Closed |
| E2E-11 | Artifact → Application → Deploy → Stop | 文件、Unit、状态和健康检查正确 |
| E2E-12 | Application Upgrade/Rollback | 成功升级；失败恢复旧版本 |
| E2E-13 | Application Crash Recovery | 中途崩溃后由 Deployment Journal 恢复明确状态 |
| E2E-14 | File Relay 多目标 | 成功、失败、重试、取消、Hub 重启均正确 |
| E2E-15 | User Lifecycle | 创建、登录、改角色、新会话权限正确 |
| E2E-16 | Native Enrollment | pending → HELLO → online/synced |
| E2E-17 | Docker Enrollment | docker run/Compose Agent 真实接入并持久化 |
| E2E-18 | Docker Recreate | 容器重建后 Identity/Revision/Journal/Cache 不丢 |


## REST API 与 RBAC 提示词


### 8.1 API 端点清单

- Auth：`/api/login`、`/api/me`、`/api/logout`、`/api/oidc/*`。
- Node：`/api/nodes`、`/api/nodes/{id}`、`enrollment`、`revoke-credential`。
- Group：`/api/groups`、`/api/groups/{id}`。
- Script：`/api/scripts`、`/api/scripts/{id}`、`revisions`。
- Task：`/api/tasks`、`/api/tasks/{id}`、`run`。
- Schedule：`/api/schedules`、`/api/schedules/{id}`。
- Artifact：`/api/artifacts`、`/{id}`、`download`。
- Application：`/api/applications`、`/{id}`、`state`、`executions`、`revisions`、`nodes`、`assign`、`deploy`。
- Execution：`/api/executions`、`/{id}`、`logs`、`cancel`。
- Transfer：`/api/transfers`、`/{id}`、`retry`、`cancel`。
- Audit/Settings/Observability：`/api/audit`、`/api/settings`、`/metrics`、`/healthz`、`/readyz`。

### 8.2 按端点实际认证与契约断言

- 公开 healthz/readyz、OIDC登录/回调/state 和已实现 Agent binary 下载不统一要求401，验证实际公开只读契约。
- 受保护管理API分别验证无凭据、非法/过期会话和角色越权；Artifact download分别验证user、registration token、Agent credential允许的路径；File Relay按归属Agent身份检查，不能用用户会话冒充Agent。
- 不存在资源按具体端点检查：读取通常404；DELETE不存在对象按当前200幂等（具体端点以实现契约为准），不统一404。
- 错误JSON、必填、方法及成功状态码按该端点实际实现断言，不机械套用全局400/405。
- 对成功写操作检查实际持久化、Revision、Audit、Agent效果；无此副作用的只读/执行端点不机械要求Revision增加。
- 每次Manual Run生成新Execution UUID代表新执行意图；同Execution ID/Key重复投递/上传才检验幂等。普通重复创建/请求按具体语义验证，不新增并发锁契约。

### 8.3 三角色矩阵

| 操作 | Administrator | Operator | Viewer |
|---|---:|---:|---:|
| 一般资源列表/详情（不含Users） | ✅ | ✅ | ✅ |
| Users GET/管理 | ✅ | 403 | 403 |
| 节点状态/标签修改 | ✅ | 403 | 403 |
| 纳管节点 | ✅ | 403 | 403 |
| Group/Script/Task/Schedule 增删改 | ✅ | 403 | 403 |
| Task Run | ✅ | ✅ | 403 |
| Execution Cancel | ✅ | ✅ | 403 |
| Application Deploy/Operate | ✅ | ✅ | 403 |
| Artifact Upload/Delete | ✅ | 403 | 403 |
| Artifact Download | ✅ | ✅ | ✅ |
| File Transfer 创建/重试/取消 | ✅ | 403 | 403 |
| User 管理 | ✅ | 403 | 403 |
| Settings 修改 | ✅ | 403 | 403 |


## Agent、调度、部署和文件中继提示词


### SYS-01 至 SYS-10：连接与同步

- [ ] SYS-01 Agent 首次注册：Registration Token、Stable Agent ID、Credential、Node 记录。
- [ ] SYS-02 已注册 Agent 重启：HELLO、Heartbeat、online/synced、ID 不变。
- [ ] SYS-03 Hub 重启：Agent 自动重连、Session 重建、Desired State 不丢。
- [ ] SYS-04 实时通知丢失：Revision Check 最终收敛。
- [ ] SYS-05 通知重复/乱序：最终以 Hub Current Desired State 为准。
- [ ] SYS-06 Agent Revision 落后：增量 Sync 正确。
- [ ] SYS-07 Change Log 窗口过期：触发 Full Resync。
- [ ] SYS-08 Script/Task 依赖同步失败：事务回滚，Revision 不前进，无半同步。
- [ ] SYS-09 删除 Script/Task/Schedule/Application：Tombstone 被 Agent 消费，未来调度停止，历史 Execution 保留。
- [ ] SYS-10 Commit 前不发送通知，Commit 后通知一次或可安全重复。

### SYS-11 至 SYS-18：Agent 本地与离线

- [ ] SYS-11 Native Agent 本地 SQLite/WAL、Identity、Revision、定义持久化。
- [ ] SYS-12 Docker Agent restart/recreate 后持久化 Identity、Revision、Schedule、Journal、Cache。
- [ ] SYS-13 Agent 断网时 Allow Offline 调度继续执行。
- [ ] SYS-14 Hub Online Required 在 Hub 不可用时不执行。
- [ ] SYS-15 离线 Execution 上传和 ACK 幂等，无重复。
- [ ] SYS-16 Agent 发现旧 RUNNING 但进程不存在，按 `agent_restarted` 正确收尾。
- [ ] SYS-17 Agent 重连前不宣告 READY，完成 Reconciliation 后再 READY。
- [ ] SYS-18 心跳、Revision Check、重连退避包含配置和 Jitter 行为。

## 10. Scheduler、Condition、Execution 测试

### SYS-19 至 SYS-30：调度与条件

- [ ] SYS-19 Cron、Interval、One-Time 均由正确 owner 触发。
- [ ] SYS-20 Agent-owned Schedule 不被 Hub 重复触发。
- [ ] SYS-21 Hub-owned Schedule 不由 Agent 本地触发。
- [ ] SYS-22 同一 Task+Node+Scheduled Time 重复触发只产生一条 Execution。
- [ ] SYS-23 Misfire `skip` 不补跑，`run_once` 只补跑一次。
- [ ] SYS-24 Timezone/DST 边界。
- [ ] SYS-25 Local Condition 的全部 metric/operator。
- [ ] SYS-26 Condition AND、多层结构和错误结构。
- [ ] SYS-27 Remote online 满足/不满足。
- [ ] SYS-28 Remote last_execution 成功/失败/不存在。
- [ ] SYS-29 Remote State TTL 未过期、过期、未观察：UNKNOWN → BLOCKED。
- [ ] SYS-30 Condition 不满足记录 SKIPPED，不能误执行。

### SYS-31 至 SYS-40：执行与日志

- [ ] SYS-31 Manual Execution UUID 幂等。
- [ ] SYS-32 Scheduled Execution Key 幂等。
- [ ] SYS-33 Execution 开始前先写 Journal。
- [ ] SYS-34 stdout/stderr 分片、顺序、stream 标记。
- [ ] SYS-35 Hub 断开时本地日志仍完整。
- [ ] SYS-36 Hub 恢复后日志和 Execution 幂等上传。
- [ ] SYS-37 最大 stdout/stderr/total log 截断并标记。
- [ ] SYS-38 Timeout 发送 SIGTERM，再按 Grace Period SIGKILL。
- [ ] SYS-39 Cancel 清理整个进程组，不残留子进程。
- [ ] SYS-40 Retry 次数、间隔、最终状态、每次日志和审计。

## 11. Artifact、Application、systemd 测试

### SYS-41 至 SYS-50：制品与应用

- [ ] SYS-41 Artifact 下载成功后 SHA256 校验和原子落盘。
- [ ] SYS-42 SHA256 错误禁止安装，正式缓存和目标文件不被污染。
- [ ] SYS-43 Artifact 下载中断、超时、HTTP 错误后 `.tmp` 清理。
- [ ] SYS-44 Prefetch 成功、失败、重复 Prefetch。
- [ ] SYS-45 Application 初次部署：Resolve → Download → Verify → Install → Unit → Start → Health。
- [ ] SYS-46 Application 启动/停止/重启和状态反馈。
- [ ] SYS-47 systemd Unit Registry：未登记 Unit 拒绝操作，已登记 Unit 可操作。
- [ ] SYS-48 Health Check systemd/TCP/HTTP/COMMAND。
- [ ] SYS-49 Upgrade：Backup → Stop → Atomic Replace → Config/Unit → Start → Health。
- [ ] SYS-50 Health 失败：停止新版本、恢复 Binary/Config/Unit、Health 后记录 ROLLBACK_SUCCESS。

### SYS-51 至 SYS-58：部署恢复与 Host Adapter

- [ ] SYS-51 升级每个阶段崩溃后的 Deployment Journal 恢复。
- [ ] SYS-52 Backup 缺失、权限不足、磁盘不足的明确失败。
- [ ] SYS-53 NativeHostAdapter 路径、权限、文件模式、用户组。
- [ ] SYS-54 ContainerHostAdapter `/host` 映射和 Host Inventory。
- [ ] SYS-55 Docker Host Mount 缺失/只读/错误路径。
- [ ] SYS-56 `../`、绝对路径、Symlink Escape、Allowlist。
- [ ] SYS-57 跨文件系统临时文件和 Atomic Replace。
- [ ] SYS-58 Capability 缺失时在下发前 BLOCKED，不到客户机执行。

## 12. File Relay 测试

### SYS-59 至 SYS-66

- [ ] SYS-59 源 Agent 身份认证、源节点只能上传。
- [ ] SYS-60 目标 Agent 身份认证、目标只能下载自己的目标。
- [ ] SYS-61 大小校验、SHA256 校验、Hub staged blob 原子提升。
- [ ] SYS-62 目标文件 `.tmp` 下载、校验、原子替换。
- [ ] SYS-63 多目标同时成功，整体 SUCCESS。
- [ ] SYS-64 多目标并发回执一成功一失败，状态不可互相覆盖。
- [ ] SYS-65 单目标失败重试和整体取消。
- [ ] SYS-66 Hub 重启、Agent 重连、目标离线后状态和文件不产生半成品。


## 两轮差异审查补测提示词

- **X01 / P0**：节点批量 Native Agent 自升级：混选 Docker、暂停/离线、重复在途升级、部分失败、取消确认、版本更新和重连。（证据入口 `web/src/features/catalog/node-bulk-actions.tsx:21`）
- **X02 / P0**：节点 Credential 吊销确认/取消；本轮测试节点拒绝旧凭据，重新纳管后恢复；不得吊销既有节点。（证据入口 `web/src/features/catalog/index.tsx:270`）
- **X03 / P0**：on_start 每次Agent进程启动触发一次；网络重连不再次触发；禁用和删除停止触发。（证据入口 `web/src/features/editors/index.tsx:224`）
- **X04 / P1**：Cron 图形五字段/预设/原始表达式双向同步、非法表达式、下次执行预览与浏览器/IANA时区。（证据入口 `web/src/components/cron/cron-editor.tsx:29`）
- **X05 / P1**：Task Detail 新建调度传 task_id 并预选锁定；无效 task_id 明确反馈；取消无写请求。（证据入口 `web/src/features/tasks/index.tsx:443`）
- **X06 / P1**：Artifact 列表新建 Application 预选 artifact_id/version；应用内嵌上传成功自动选中，失败/取消不覆盖已有定义。（证据入口 `web/src/features/editors/index.tsx:917`）
- **X07 / P0**：所有详情/编辑/Run 深链刷新和前进后退；不存在 ID、并发删除、404/Error/Retry/返回；旧 tasks?view=schedules 重定向 /schedules。（证据入口 `web/src/routes/__root.tsx:17`）
- **X08 / P0**：删除/Run/Stop/批量升级/吊销取消零写请求，确认单请求；慢响应重复点击、失败后重试；取消编辑不改 Revision/Audit。（证据入口 `web/src/components/confirm-dialog.tsx:56`）
- **X09 / P0**：Task 四类型往返保存后 command/script_id/application_id/app_operation 隐藏字段不改变当前业务；target Node/Group/Label 往返及 Group Static/Label 往返。（证据入口 `web/src/features/tasks/index.tsx:764`）
- **X10 / P0**：Schedule 四类型往返裁剪 run_at/expression/interval；非法时区切 on_start；One-Time 时区转换，观察DST边界并与项目解析实现核对，不新增DST拒绝/补偿契约；Owner/Offline 组合。（证据入口 `web/src/components/cron/schedule-payload.ts:9`）
- **X11 / P0**：Local/Remote/AND 条件编辑回显、AND 勾选未设置的语义、覆盖/清除一个和最后一个条件后实际执行/阻断。（证据入口 `web/src/features/tasks/index.tsx:602`）
- **X12 / P0**：Label/Group/Application Assignment 改变后立即及缓存收敛后检查跨页与下次真实执行目标；空目标、重叠组、离线/禁用/维护/Capability 不匹配。（证据入口 `web/src/features/catalog/index.tsx:580`）
- **X13 / P0**：跨页缓存和两会话编辑、旧会话改角色后 API 权限、另一标签退出或替换 Token 后保护页面；记录实际收敛时间。（证据入口 `web/src/main.tsx:46`）
- **X14 / P0**：选中 Node 后过滤/排序/翻页/列表增删/刷新，批量请求 ID 不漂移；删除最后页末项、pageSize/筛选/总数；Execution 跨页搜索/Trigger。（证据入口 `web/src/features/shared/data-table.tsx:153`）
- **X15 / P0**：PENDING→RUNNING→终态无需刷新；新日志分片、终态 stdout/stderr 完整且无旧 chunks 覆盖；断线恢复后的 Follow/搜索/流过滤/Copy。（证据入口 `web/src/features/executions/index.tsx:356`）
- **X16 / P0**：使用非敏感标记值检查 secret 默认值/输入/Revision/Execution/Audit/错误遮蔽；凭据、Registration Token、Authorization 和 Cookie 不进入交付证据。（证据入口 `web/src/features/tasks/index.tsx:1116`）
- **X17 / P0**：真实 OIDC 按当前互斥模式验证：allow_local_login=true为本地模式（enabled=false/local_fallback=true），false为SSO-only；缺字段/慢响应/失败、SSO成功/拒绝/取消；外站/协议相对/sign-in/query/hash 回跳；停留页轮询/写请求401退登。（证据入口 `web/src/features/auth/sign-in/index.tsx:34`）
- **X18 / P0**：批量一成功一失败不互相覆盖；Application 定义成功 Assign 失败可定位重试；Transfer 添加目标后换源不变成自传，重试不破坏已成功目标。（证据入口 `web/src/features/editors/index.tsx:1024`）
- **X19 / P1**：键盘 Tab/Enter/Escape 确认/取消与焦点返回；批量工具栏 Arrow/Home/End/Escape 不误清选择或误执行。（证据入口 `web/src/components/data-table/bulk-actions.tsx:64`）
- **X20 / P1**：长中文名称/路径/ID/SHA/命令/大量参数/日志；列全部隐藏再恢复、空搜索恢复、窄屏表单/菜单/确认框。（证据入口 `web/src/features/shared/data-table.tsx:197`）

## 架构 §66：35 个故障与恢复场景

每项先记录稳定baseline，注入获授权的单一故障，观测失败/恢复状态，再验证文件、Journal、Revision、幂等和UI收敛。高风险共享操作交主控串行；没有客观注入条件记 BLOCKED，不以静态分析冒充。

- **AR-01**：Hub Restart；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-02**：Agent Restart；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-03**：Docker Agent Restart；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-04**：Docker Agent Recreate；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-05**：Persistent Volume Missing；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-06**：Agent Disconnect；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-07**：Hub Disconnect During Execution；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-08**：Agent Offline During Hub Script Update；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-09**：Agent Offline During Task Update；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-10**：Lost Notification；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-11**：Duplicate Notification；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-12**：Out-of-order Notification；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-13**：Revision Lag；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-14**：Change Log Window Expiry；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-15**：Full Resync；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-16**：Sync Dependency Failure；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-17**：Duplicate Schedule Trigger；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-18**：Offline Scheduled Execution；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-19**：Execution Re-upload；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-20**：Duplicate Execution Upload；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-21**：Timeout；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-22**：Cancel；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-23**：Process Group Cleanup；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-24**：Artifact Download Interruption；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-25**：Artifact SHA Failure；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-26**：Application Install；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-27**：Upgrade；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-28**：Health Failure；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-29**：Rollback；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-30**：Crash During Upgrade；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-31**：Docker Host Mount Missing；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-32**：Host Adapter Permission Denied；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-33**：Capability Mismatch；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-34**：Path Escape；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。
- **AR-35**：Cross-filesystem Rename；对照实际故障链验证恢复，无丢失、重复、半状态或受保护资源破坏。

## 架构 §67：18 个不变量

不能只读源码勾选。为每项关联实际操作/错误/故障 case，保存满足不变量的证据；无法运行时 STATIC/BLOCKED。

- **INV-01**：Hub 是 Desired State 唯一权威源。
- **INV-02**：Agent 主动连接 Hub。
- **INV-03**：同步必须是 Notify + Revision Check + Reconciliation。
- **INV-04**：Notification 不承担最终一致性。
- **INV-05**：Agent Revision 成功落盘后才能前进。
- **INV-06**：Agent-owned Schedule 只能由 Agent 触发。
- **INV-07**：Execution ID / Key 必须幂等。
- **INV-08**：Execution Start 前必须先写 Journal。
- **INV-09**：Unknown Remote State 默认 Fail Closed。
- **INV-10**：Artifact 校验失败禁止安装。
- **INV-11**：Managed Application 必须存在恢复路径。
- **INV-12**：systemd 只管理 NodeSteer Managed Application。
- **INV-13**：Native 与 Docker 共享 Agent Core。
- **INV-14**：Native/Docker 差异必须进入 Host Adapter。
- **INV-15**：Docker Agent state 必须 Persistent。
- **INV-16**：Capability 不匹配不得下发 Host Operation。
- **INV-17**：Commit 前不得发送 Desired State Notification。
- **INV-18**：Artifact/File Payload 不走控制长连接。

## 验收与清理提示词

逐项读取本轮 case-inventory、证据、失败、数据清理表。按资源依赖逆序停用调度、取消本轮仍在运行操作、解除分配、删除本轮Schedule/Task/Application/Script/Group/Artifact/未注册测试Node；已运行的Agent与托管服务/文件按主控恢复顺序处理。每个删除响应之后重新GET并核对Agent Tombstone/不再执行；只按本轮精确ID清理。没有删除能力的本轮用户/Execution/Audit保留并报告。运行状态与报告一致；未执行/失败/无法注入条件明确保留，不声称全部通过。

## 当前自动化边界

web/e2e 页面渲染冒烟、payload Mock 与真实业务分别统计。登录/OIDC/节点删除/传输取消/应用任务Mock只算UI_MOCK；API辅助创建仅准备数据。旧Suite E2E-01此前未运行任务且仅检查body，不能算Script→Agent全流程。真正闭环须验证脚本任务引用、Run、每目标Execution及客户机输出。共享设置保存、既有节点状态变更、升级/吊销必须交主控，不能无授权随suite执行。

当前契约补充：Persistent Volume Missing仅验证现有错误/恢复和持久性风险，不预设卷缺失必须拒绝注册；并发一致性按已有事务/幂等机制检验，不要求新增并发锁。
