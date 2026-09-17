# NodeSteer 页面全量测试执行报告

测试批次：`qa-20260915-ui`
测试日期：2026-09-15
测试方式：Chromium + agent-browser 真实页面点击、填写、上传、运行、复制；SSH/API 仅用于只读结果核验
测试入口：Hub `192.168.100.249:8080`（浏览器通过本地 SSH 转发访问以验证 Clipboard API）

关联文档：

- [UI_FULL_TEST_PLAN.md](./UI_FULL_TEST_PLAN.md)
- [UI_FULL_TEST_REPORT_TEMPLATE.md](./UI_FULL_TEST_REPORT_TEMPLATE.md)

## 1. 总体结论

状态：`PARTIAL`

Native Hub-Agent 页面主链路已真实验证通过：节点纳管、脚本、分组/标签、命令任务、脚本任务、托管应用任务、调度、发布包、服务部署/启停/升级、执行日志、文件传输、审计和设置均完成了页面操作与实际效果核验。

不能标记 `PASS` 的原因：

1. Chromium 环境拒绝真实 Clipboard read；Copy handler 已核对写入文本与页面内容一致，但无法完成系统剪贴板回读闭环。
2. One-Time Schedule 的原生 datetime-local 控件未能通过当前浏览器自动化稳定填写，本轮以 Interval Schedule 完成实际调度验收。
3. Docker Agent、Docker Host Integration、OIDC 和多角色重新登录未在本批次执行。

## 2. 环境基线

| 项目 | 结果 |
|---|---|
| Hub health/ready | ✅ HTTP 200 / 200 |
| Native Agent 数量 | ✅ 6 个在线（5 个既有节点 + 1 个临时纳管节点） |
| 临时纳管 VM | ✅ `cadentra-agent-ui-enroll-test` / `192.168.100.246` / Ubuntu 26.04 / 2 vCPU / 1 GiB |
| Agent Gateway | ✅ 临时 Agent 已建立 WebSocket 连接 |
| Hub 页面 | ✅ 登录、导航和页面加载正常 |
| 浏览器错误 | ✅ 最终 Dashboard 未发现新增 errors/console 输出 |
| 源模板和既有 VM | ✅ 未修改 |

## 3. 用例结果

| 用例/模块 | 状态 | 实际结果 |
|---|---|---|
| UI-001 登录与会话 | ✅ PASS | 管理员登录成功；未登录重定向；退出/会话页面路径验证 |
| UI-002 布局、导航、语言、主题 | ✅ PASS | 13 个导航入口可达；中文/English、深色/浅色/系统主题可切换并恢复 |
| UI-003 Dashboard/DataTable | ✅ PASS | 指标、执行列表、搜索/空态/分页和最终 6 节点在线显示正常 |
| UI-010 节点列表/详情 | ✅ PASS | 5 个既有节点与临时节点的状态、Inventory、Revision、同步、标签入口正常 |
| UI-011 添加节点/Native 纳管 | ✅ PASS | 页面生成 Native 命令；在临时 VM 执行后 pending → online/synced；详情正确 |
| UI-011 docker run/Compose Copy handler | ✅ PASS（handler） | 三种命令复制处理器写入内容与页面 `<pre>` 完全一致 |
| UI-011 真实 Clipboard read | ⛔ BLOCKED | HTTP IP 源无 Clipboard API；localhost 源虽为 secure context，仍被 Chromium 拒绝 read permission |
| UI-012 Static/Label Group | ✅ PASS | 静态组 2 成员；Label Group 从 0 变为 1；节点 Label 联动正确 |
| UI-020 Script CRUD/Revision/Clone | ✅ PASS | 页面创建、参数、环境变量、r1 → r2、克隆、查看历史和删除 |
| UI-030 Command Task | ✅ PASS | 页面创建、绑定临时节点、确认运行、Execution SUCCESS 和输出 |
| UI-030 Script Task | ✅ PASS | 页面创建、绑定临时节点、Revision 2、Execution SUCCESS、stdout/stderr |
| UI-030 Label Group Task | ✅ PASS | Label 目标 `qa_env=ui` 创建并成功执行 |
| UI-030 Application Operation Task | ✅ PASS | 页面创建停止任务并执行成功，Agent systemd 实际 inactive；应用页面启动恢复成功 |
| UI-031 Execution/Logs/Copy Logs | ✅ PASS（handler） | 状态、详情、stdout、流筛选、换行、跟随和复制处理器正常 |
| UI-040 Interval Schedule | ✅ PASS | 每 15 秒、Asia/Shanghai、Agent、允许离线；实际产生多条成功调度执行，页面禁用后停止 |
| UI-040 One-Time Schedule | ⛔ BLOCKED | 原生 datetime-local 分段控件在当前 agent-browser 会话无法稳定填写 |
| UI-041 Artifact | ✅ PASS | 1.0.0、2.0.0、2.0.1 通过页面上传；SHA256/版本/引用展示正确；未引用旧版本已删除 |
| UI-041 Application/服务创建部署 | ✅ PASS | 页面创建、节点分配、Deploy、Start、Stop、Restart、systemd health 均成功 |
| UI-041 Application Upgrade | ✅ PASS | 页面切换到 2.0.1 并 Upgrade；Agent 实际文件 SHA256 为 v2，unit active/running |
| UI-041 Application Delete | ✅ PASS | 有引用任务时返回 HTTP 409 且页面显示明确处理提示；删除引用任务后，应用和最新 Artifact 均可通过页面删除 |
| UI-050 File Transfer | ✅ PASS | 页面源/目标选择、添加目标、开始传输成功；源/目标 SHA256 一致，目标权限 755 |
| UI-051 Audit | ✅ PASS | 页面操作产生 create/update/upload/assign/execute/deploy/start/stop/upgrade 审计事件 |
| UI-052 Users | ✅ PARTIAL | 管理员用户列表和新建表单/取消验证；未创建无法通过页面删除的测试账号 |
| UI-053 Settings | ✅ PASS | Runtime 心跳 30 → 31 → 30 页面保存/恢复；Appearance 主题切换恢复 |
| Docker/OIDC/多角色 | ⛔ BLOCKED | 无隔离 Docker 环境、OIDC IdP 和可清理的测试用户凭据 |

## 4. 关键实际证据

### 4.1 节点纳管

- 临时 VM：`192.168.100.246`。
- 页面生成的 Native 命令在该 VM 执行成功。
- Agent unit：active/enabled。
- WebSocket：`192.168.100.246 → 192.168.100.249:8443` ESTABLISHED。
- 页面最终显示临时节点 online、native、amd64、Agent 0.1.0、synced。

### 4.2 任务和执行

- Command Task、Script Task、Label Group Task、Application Operation Task 均通过页面创建。
- 页面 Run Now → 确认页 → Execution；最终均为 SUCCESS。
- Script 日志实际包含参数默认值、环境变量、`/tmp` 工作目录和 Revision 2。
- Script Execution `b3c519c0-6c0c-4cc7-9e89-3e9761106a87` 的 stdout 为 82 字符、stderr 为 0。

### 4.3 服务部署

- Application：`qa-20260915-ui-service`。
- Unit：`cadentra-ui-test.service`。
- 页面 Deploy/Start/Stop/Restart/Upgrade 均有成功执行记录。
- 升级后 Agent 文件 SHA256：`2ce7c54f99c45e860317804d674c567ee3ecf12df9dd2c29b9c7a4ed2d5f772d`。
- 清理阶段通过页面 Command Task 移除了临时 unit、脚本、配置和 Debian 传输文件；临时 Agent service 保持 active/enabled。

## 5. 缺陷与阻塞

### UI-DEF-001：托管应用删除引用保护提示（已修复）

状态：✅ 已修复并通过页面回归

修复内容：

- 保留后端对任务引用的 HTTP 409 保护，避免删除后任务失效。
- 409 错误文本补充“先删除或修改任务”的下一步操作。
- 托管应用列表显示持久的页面告警，明确说明删除被阻止的原因和处理方式。
- 删除确认文案提前提示任务引用约束。

回归：

1. 页面创建托管应用并通过页面创建引用该应用的托管应用任务。
2. 页面删除应用，网络请求返回 `409`，应用仍保留。
3. 页面显示“删除被阻止：该托管应用仍被任务引用。请先删除或修改相关任务，然后重试。”。
4. 页面删除引用任务后再次删除应用，应用从列表消失。
5. 页面删除最新 Artifact，Artifact 从列表消失。

实际结果：引用保护和用户提示均符合预期，应用与最新发布包可完成页面清理。

### UI-BLOCK-001：真实系统剪贴板回读被浏览器拒绝

页面 Copy handler 已捕获并核对 Native、docker run、Compose、Execution Logs 内容；真实 `clipboard read` 返回 `Read permission denied`。这是当前浏览器权限/运行上下文阻塞，不能将真实剪贴板回读标记为通过。

### UI-BLOCK-002：One-Time Schedule 日期控件自动化阻塞

原生 `datetime-local` 的分段控件在当前会话中无法稳定接收页面键盘输入，日期选择器引用在交互后失效；未使用脚本注入绕过，One-Time 用例保留为 BLOCKED。

### UI-OBS-001：快速无效地址操作产生 pending 记录

快速填写非法地址并点击生成后观察到 `not-an-ip` 离线节点记录；按钮随后显示 disabled。尚未以人工节奏稳定复现，暂记为观察项，建议后续补充带等待的手工复验。

## 6. 清理结果

| 对象 | 结果 |
|---|---|
| 测试 Script、Clone | ✅ 页面删除完成 |
| 测试 Group、Label Group | ✅ 页面删除完成 |
| 测试 Command/Script/Application/Label/Cleanup Task | ✅ 页面删除完成 |
| Interval Schedule | ✅ 页面删除完成 |
| 未引用 Artifact 1.0.0/2.0.0 | ✅ 页面删除完成 |
| Application `qa-20260915-ui-service` | ✅ 页面删除完成 |
| Application `qa-20260915-delete-fix` | ✅ 页面回归后删除完成 |
| 引用 Artifact 2.0.1 | ✅ 页面删除完成 |
| 临时纳管节点 `qa-20260915-ui-enroll-real` | ✅ 保留在线，用于临时 VM 环境验证 |
| 临时 KVM VM `cadentra-agent-ui-enroll-test` | ⛔ 保留；本轮未执行删除 |
| 临时 Agent 上的 unit/文件/进程 | ✅ 通过页面清理任务移除 |
| Debian 传输目标文件 | ✅ 通过页面清理任务移除 |

未执行直接 API 删除、数据库删除、源模板修改或既有节点重置。

## 7. 后续补测条件

1. 如需将本报告整体标记为 PASS，仍需补测真实系统剪贴板回读、One-Time Schedule、Docker Agent、OIDC 和多角色登录。
2. 提供允许 Clipboard read 的浏览器权限配置，重新完成所有 Copy 按钮真实回读。
3. 使用可稳定操作的 datetime-local 控件或专用浏览器配置，补测 One-Time Schedule。
4. 提供隔离 Docker Agent/Host Integration 环境，补测 Docker run、Compose、重建持久化。
5. 提供可清理的 Operator/Viewer 测试凭据，补测多角色页面可见性和实际 403。
6. 对临时 KVM VM 的保留/删除作出明确决定；删除前需重新确认精确域名和磁盘路径。
