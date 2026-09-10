# Cadentra 完整功能真实测试计划

版本：v2.0

更新时间：2026-09-07

适用对象：Cadentra Hub、Web UI、Native Agent、Docker Agent、Docker Host Integration。

本计划以实际代码、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、REST API、Agent Gateway 协议和真实 Linux/KVM 环境为依据。测试结论必须来自真实操作及证据，不以控件存在、接口注册或静态代码代替业务效果。

## 1. 测试目标与完成定义

### 1.1 测试目标

- 覆盖所有页面、子页面、编辑器、弹窗、下拉框、Tab、链接、操作菜单和表单按钮。
- 覆盖页面之间的真实数据传递、状态刷新、回链和反向操作。
- 覆盖 REST API、鉴权、RBAC、持久化、Revision、Audit 和错误码。
- 覆盖 Agent Native、Docker、Docker Host Integration 的同步、执行、部署和恢复。
- 覆盖 Hub/Agent 重启、断网、通知丢失、Revision 落后、离线执行和恢复。
- 覆盖应用部署、升级、健康检查、回滚、文件中继和执行日志。

### 1.2 完成定义

单条用例必须同时记录以下链路：

```text
用户操作
  → 页面事件
  → HTTP/WebSocket 请求
  → Hub 响应与状态码
  → Hub 数据库/Revision/Audit
  → Agent 本地状态
  → Linux 客户机实际效果
  → 页面最终反馈
```

用例状态：

- `⬜` 未执行
- `✅` 真实验证通过且证据完整
- `❌` 真实验证失败，已记录复现步骤和影响
- `⛔` 环境阻塞，记录原因和补测条件
- `SKIP` 明确不适用，必须说明原因

不得因为按钮不存在、数据为空或 `isVisible()` 返回 false 而静默通过。

## 2. 真实环境与安全边界

### 2.1 KVM2 环境

| 角色 | 地址/域名 | 用途 |
|---|---|---|
| Hub | `192.168.100.249` | Web/API `8080`，Agent Gateway `8443` |
| Native Agent | `192.168.100.243` | Debian 测试节点 |
| Native Agent | `192.168.100.222` | Fedora 测试节点 |
| Native Agent | `192.168.100.204` | Rocky 测试节点 |
| Native Agent | `192.168.100.212` | 纳管命令和 Native Agent 专用测试节点 |
| KVM 主机 | `kvm2` | libvirt/QGA/虚拟机状态验证 |

当前 KVM2 已有 Native Agent。Docker Agent 和 Docker Host Integration 必须使用独立测试 VM 或明确的隔离 Compose 环境；没有隔离环境时标记 `⛔`，不能把 Native 结果代替 Docker 结果。

### 2.2 测试数据规则

- 所有对象使用 `qa-20260907-<case>-<name>` 前缀。
- 节点、VM、Agent Credential、文件和 systemd Unit 使用唯一名称。
- 测试前记录节点、对象、Revision、磁盘、服务状态。
- 测试后只清理本轮创建且可准确识别的对象。
- 不删除既有受保护节点、VM、用户、执行历史或非本轮数据。
- 密码、Token、Cookie、私钥不得进入代码、Git、测试报告、命令参数或普通日志。

### 2.3 前置检查

- [ ] Hub `/healthz` 返回 200。
- [ ] Hub `/readyz` 返回 200。
- [ ] Hub Web 页面可以访问。
- [ ] 目标 Agent 服务 active/enabled。
- [ ] Agent Gateway TCP/WebSocket 可连接。
- [ ] KVM 目标 VM running、QGA ready、IPv4 可达、SSH ready。
- [ ] 目标 Agent 的 machine-id、MAC、Agent ID、Node ID 不与其他测试 VM 重复。
- [ ] Hub、Agent、数据库和磁盘有可恢复备份。
- [ ] 浏览器控制台无历史错误，网络面板可保存请求证据。

## 3. 功能关系与测试依赖

### 3.1 业务关系

```text
登录
  ↓
Dashboard
  ├─ 节点列表 → 节点详情
  │               ├─ 标签 → Label Group
  │               ├─ 任务 → Task Detail
  │               ├─ 调度 → Schedule Detail
  │               ├─ 托管应用 → Application Detail
  │               └─ 执行 → Execution Detail
  ├─ Script → Revision → Script Task
  ├─ Group/Label → Task Target
  ├─ Task → Run Now / Schedule → Execution → Logs → Audit
  ├─ Artifact → Application → Assign Node → Deploy/Upgrade/Operate
  └─ Source Agent → Hub File Relay → Multiple Target Agents
```

### 3.2 测试执行依赖

1. 登录和公共布局。
2. 节点清单、节点纳管和节点标签。
3. 分组。
4. 脚本及脚本 Revision。
5. Command/Script/Application Task。
6. 调度、立即运行和执行记录。
7. 制品和应用部署。
8. 文件中继。
9. RBAC、Audit、Settings。
10. Hub/Agent 故障、离线、恢复和持久化。

## 4. 公共页面与公共控件

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
- [ ] 验证 13 个导航项：Dashboard、Tasks、Executions、Agents、Transfers、Schedules、Scripts、Groups、Applications、Artifacts、Audit、Users、Settings。
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
- [ ] OIDC enabled 时本地登录按产品版本要求返回 403 或按明确兼容策略处理。
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

- [ ] Overview、Executions、Tasks 三个 Tab。
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
- [ ] Task Detail 五个 Tab：Overview、Definition、Targets、Executions、Schedule。
- [ ] Definition 对 command/script/application 的链接和内容正确。
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
- [ ] 同一 Manual Run 重试/重复请求按 Execution ID 幂等。

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
- [ ] Overview/Logs Tab。
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
- [ ] Runtime 字段：heartbeat、revision check、changelog、gateway、max log。
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

## 6. 页面联动测试

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

## 8. REST API 与 RBAC 测试

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

### 8.2 通用 API 断言

每个端点至少验证：

- 无 Token：401。
- 非法/过期 Token：401。
- Viewer/Operator 越权：403。
- 不存在资源：404。
- 错误 JSON：400。
- 缺少必填字段：400。
- 非法方法：405。
- 成功响应结构、Content-Type、状态码。
- 成功后的数据库、Revision、Audit、Agent 状态。
- 重复请求的幂等性和并发安全。

### 8.3 三角色矩阵

| 操作 | Administrator | Operator | Viewer |
|---|---:|---:|---:|
| 查看所有列表/详情 | ✅ | ✅ | ✅ |
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

## 9. Agent、同步与一致性测试

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

## 13. 测试证据与缺陷记录

每条失败必须记录：

```text
Case ID
环境/VM/Node/Agent
前置数据与对象 ID
复现步骤
实际 HTTP/WebSocket 请求
实际响应/状态码
Hub DB/Revision/Audit
Agent 本地 DB/Journal
客户机服务/文件/进程结果
页面截图、控制台、Hub/Agent 日志
影响范围
是否可恢复
建议修复方向
```

缺陷优先级：

- P0：数据丢失、越权、重复执行、错误部署生产文件、无法恢复。
- P1：核心链路不可用、状态错误、离线/重连不一致、回滚失败。
- P2：单页面操作失败、错误提示错误、数据展示不一致。
- P3：文案、布局、非核心可访问性问题。

## 14. 测试执行顺序与清理

1. 记录 Git 工作区、Hub/Agent/VM 基线。
2. 执行公共认证、布局、DataTable 和权限可见性。
3. 创建节点、标签、分组。
4. 创建 Script、Revision、Task。
5. 执行 Manual Run、Schedule、Condition、Logs、Audit。
6. 上传 Artifact，创建 Application，分配节点，执行部署和回滚。
7. 执行 File Relay 多目标、重试、取消和恢复。
8. 执行 Native/Docker/Host Integration 系统测试。
9. 汇总 API、数据库、Agent、客户机和 UI 证据。
10. 清理本轮临时对象；无法通过产品入口删除的对象必须列入剩余环境项。
11. 最后重新执行健康检查、节点状态、Agent 连接和 Web Smoke。

## 15. 通过标准

- 所有纳入当前版本的页面用例均有真实结果。
- 所有核心联动和 E2E 用例均有客户机实际效果证据。
- P0/P1 缺陷为 0。
- 关键不变量全部满足：Desired State 唯一权威、三层同步、Revision 原子性、Execution 幂等、Journal 先写、Fail Closed、Artifact 校验、可恢复部署、systemd 边界、Docker 持久化。
- 任何 `⛔/SKIP` 都有明确原因、影响和补测条件。
- 测试报告不得把计划项或自动化脚本通过当作真实功能通过。
- 代码、文档、测试结果和当前部署版本一致。
