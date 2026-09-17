# NodeSteer UI/UX Review Report

状态：`completed_with_gaps`

## 1. 范围

- 环境：KVM2 上运行的真实 Hub Web，`http://192.168.100.249:8080`
- 浏览器：Chromium，通过持久化 `agent-browser` 会话检查
- 视口：桌面 `1440x900`、平板 `1024x768`、移动端 `390x844`
- 主路由：登录页、仪表盘、节点/`/nodes` 别名、任务、执行、文件传输、调度、脚本、分组、发布包、托管应用、审计、用户、设置
- 动态页面：6 个新建表单、添加节点弹窗、执行详情、执行日志、设置外观页

共取证 61 个页面/状态视口检查；主页面和动态页面截图、DOM 几何测量、控制台记录均保存在本目录。

## 2. 检查结论

通过项：

- 所有已检查页面均未出现 document/body 级横向溢出。
- 桌面端主布局、标题区、卡片、表单字段、分页和主要按钮整体对齐。
- 已复核的 icon-only 控件（侧栏、语言、主题、个人资料、分页、关闭）几何中心正常；初始图标偏移脚本对带文字按钮中的箭头存在误报，已以截图和语义复核排除。
- 6 个新建表单在移动端均能按纵向布局展示，未发现字段或保存/取消按钮越出视口宽度。
- 空列表页面均有统一的“暂无数据”卡片，不是空白页面。
- 已检查主路由的控制台和页面错误输出为空；记录见 `evidence/console/`。

## 3. 发现的问题

### UI-001（Medium）：移动端数据表横向滚动入口不明显

受影响页面：仪表盘、节点、任务、执行、文件传输、审计、用户；平板端部分表格也存在同样的宽度问题。

表格使用内部 `overflow-x:auto`，不会撑破页面，但在 `390px` 移动端仍保持约 `660-1152px` 的桌面列宽。用户初始只能看到前几列，操作列和重要状态需要自行横向滚动；页面没有明确的滚动提示或固定操作列。文件传输页面的源 Agent/路径列尤其拥挤。

证据：`evidence/screenshots/transfers-mobile.png`、`evidence/screenshots/audit-mobile.png`、`evidence/screenshots/users-mobile.png`，以及对应 `evidence/measurements/*-mobile.json`。

建议：移动端按优先级隐藏次要列或切换为卡片/详情布局；如果保留横向表，增加滚动渐变/提示、固定操作列，并对长路径提供完整值提示。

### UI-002（Medium）：执行详情/日志页的主身份仍是 UUID

执行详情标题使用完整 execution UUID，移动端截断为 UUID 前缀；节点链接也显示短 UUID。当前保留的执行记录关联任务已删除，因此列表进一步显示“未知任务”，用户很难快速判断业务上下文。

证据：`evidence/screenshots/execution-detail-desktop.png`、`evidence/screenshots/execution-detail-mobile.png`、`evidence/screenshots/execution-log-desktop.png`。

建议：主标题使用任务名、节点名、状态和触发方式等业务摘要；UUID 放在副标题、复制入口或可展开详情中。该问题与已有 E2E 报告中删除确认框直接显示 UUID 的产品化问题同属一类。

## 4. 未发现但已明确检查的项目

| 检查项 | 结果 |
| --- | --- |
| 页面级横向溢出 | 未发现 |
| 桌面端主要列宽与卡片布局 | 通过目视与 DOM 测量 |
| 表单字段、保存/取消按钮对齐 | 未发现可复现问题 |
| icon-only 按钮居中 | 未发现可复现问题 |
| 空状态信息与 CTA | 通过 |
| 主路由 console/page errors | 未发现 |

## 5. 限制与后续

- 本轮是 UI/UE 审查，不重复执行创建、删除、部署等业务破坏性动作；业务连续性结果沿用 `.e2e/final-report.md`。
- RBAC 不同角色、真实部署目标和部分不可达状态仍受上一轮环境条件限制，见根目录 E2E 报告。
- 本地 Vitest 未能启动 Playwright headless 浏览器（缺少 `chrome-headless-shell`）；前端 build、TypeScript、ESLint（0 error）和 KVM2 `agent-browser` 运行时复测已完成。

## 6. 修复与回归结果

本次修复构建产物已部署到 KVM2 测试 Hub `192.168.100.249`；原二进制已保留为 `/usr/local/bin/cadentra-hub.pre-ui-fix-20260917-082300`，重启后 `/healthz=ok`、`/readyz=ready`。

### UI-001：已修复

- 共享 `Table` 容器现在仅在实际存在横向溢出且尚未滚动到末尾时显示“左右滑动查看更多”。
- DataTable 的 `actions` 列固定在可视区域末端，用户无需先猜测滚动位置即可执行操作。
- KVM2 移动端复测覆盖仪表盘、节点、任务、执行、文件传输、审计、用户 7 个页面；均显示滚动提示，页面级溢出仍为 false。
- 复测时将文件传输表滚动到最右侧，提示正常消失，操作单元仍位于视口内。

### UI-002：已修复

- 执行详情标题改为任务名；任务已删除时显示“未知任务”，不再显示任务 UUID。
- 节点卡片和副标题显示节点主机名；执行 UUID 保留在带 `ID:` 标签的次要技术信息中。
- KVM2 桌面端和移动端复测均通过，页面级溢出仍为 false。

### FAIL-001：已修复

- 调度删除确认框改为“任务名 · Cron/间隔表达式”的产品化标识。
- 使用浏览器网络 fixture 展示确认流程，未创建或删除真实调度；确认标题和描述均未再显示 UUID。

本次修复范围剩余未解决问题：0。更完整的业务覆盖限制仍记录在根目录 `.e2e/final-report.md`。

详细页面状态见 `page-matrix.json`，响应式汇总见 `responsive-matrix.json`，结构化问题见 `findings.json`。
