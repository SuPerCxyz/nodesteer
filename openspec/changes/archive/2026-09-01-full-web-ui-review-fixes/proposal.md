## Why

全面 UI/UX 审查发现 Cadentra 虽已采用 shadcn-admin 的基础组件，但仍存在路由呈现错误、表格与分页不可扩展、表单网格错位、详情数据不清晰、国际化残留、响应式分页断裂和 Dark Mode 对比度问题。现在需要以已锁定的 shadcn-admin 源码为视觉基线，集中修复这些问题，形成可实际使用的工程控制台。

## What Changes

- 修复任务运行子路由，使任务行的“立即运行”进入真实运行确认页面。
- 修复执行详情的任务名称、退出码和技术字段展示。
- 为公共 DataTable 增加可验证的列宽、长文本、对齐和移动端处理策略。
- 将执行列表接入统一分页/筛选呈现，避免一次渲染全部执行记录。
- 修复 DataTable 分页和列视图菜单的中文国际化。
- 修复文件传输及所有编辑表单的 Grid 对齐、宽度和响应式布局。
- 统一列表、详情、Dashboard、日志、设置和认证页面的视觉层级与信息密度。
- 修复 Loading、Empty、Error、Dialog、Sheet、Dropdown、Badge、Dark Mode 和无障碍显示问题。
- 保留现有 API、权限、实时状态、业务数据和路由能力；不修改后端 contract。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `web-ui`: 完善全站页面的真实数据呈现、任务运行入口、分页/筛选、状态反馈、响应式和可访问 UI 行为。

## Impact

- 参考源码：`/tmp/cadentra-shadcn-admin`，commit `e16c87f213a5ba5e45964e9b67c792105ec74d26`。
- 前端共享组件：layout、DataTable、Table、分页、列视图、StatusBadge、Loading/Empty/Error、日志查看器。
- 前端业务页面：Overview、Tasks、Executions、Agents、Transfers、Schedules、Scripts、Groups、Applications、Artifacts、Audit、Users、Settings、Auth。
- 不新增依赖、不改变后端 API；Hub 仍通过嵌入的 `web/dist` 提供前端。
