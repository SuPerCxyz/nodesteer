## Context

Cadentra 使用 React 19、Vite、TypeScript、Tailwind v4、Radix/shadcn primitives 和 TanStack Table。当前布局、Card、Table、Form 大部分来自 shadcn-admin，但业务页同时存在 DataTable 与手写 Table，且页面级数据和表单结构没有统一的布局约束。

## Goals / Non-Goals

**Goals:**

- 以锁定的 shadcn-admin 源码作为组件结构、token、响应式和交互基线。
- 修复审查报告中的 P0/P1/P2 UI、显示和页面交互问题。
- 保持真实 API 数据、权限、实时更新和既有业务语义。
- 让列表、详情、编辑、日志和异常状态在 Light/Dark/Desktop/Mobile 下可用。

**Non-Goals:**

- 不重新设计产品信息架构。
- 不更换 React、Router、状态管理或 UI 组件库。
- 不修改后端 contract、数据库和 Agent/Hub 行为。
- 不添加模板 Demo 业务或不存在的字段。

## Decisions

### 1. 模板源码作为视觉基线

保留当前 Cadentra 对模板业务内容的替换，但对通用组件优先与 pinned shadcn-admin 源码保持一致。业务特有的 StatusBadge、ExecutionProgress、AgentSelector 和 LogViewer 只组合模板 primitives，不覆盖模板基础样式。

### 2. 共享表格先统一行为，再配置业务列

扩展现有 `features/shared/data-table.tsx`，支持 ColumnDef 的 `size/minSize/maxSize`、表格容器横向滚动、Cell 内容约束和可访问的完整值查看。业务列只声明数据角色和宽度策略，不在页面内重复实现表格基础样式。

### 3. 执行列表使用统一分页

保留现有 API 查询参数和真实筛选能力；在前端统一分页呈现，避免 Execution 页面直接渲染全部结果。若后端未提供某项筛选，不虚构过滤语义，而是在现有数据范围内实现明确的本地筛选或隐藏该控件。

### 4. 路由父子关系显式渲染

任务详情父路由需要渲染 Outlet，使 `/tasks/:taskId/run` 和 `/tasks/:taskId/edit` 能够显示自己的子页面。任务详情本身仍保留为默认子内容，业务 API 不变。

### 5. Form Grid 使用稳定列模板

表单按照“主字段、双列字段、重复行字段、操作列”划分稳定 Grid；文件路径等长字段使用弹性列，操作按钮使用固定窄列。移动端降为单列，不通过负 margin、absolute 或固定高度修复。

### 6. 显示值与原始值分离

列表使用本地化、人类可读的状态/枚举；ID、Revision、Path、Cron、Hash 等技术值使用 monospace，并通过截断、Tooltip、Copy 或详情页访问完整值。API 原始值不直接作为普通用户文案。

## Risks / Trade-offs

- [执行 API 仅返回 task_id] → 前端补充任务名称查询或稳定 fallback；无法取得名称时仍显示完整可复制 ID。
- [TanStack Table 与手写 Table 并存] → 先统一共享 DataTable 和业务列配置，详情页小型结构化表格继续使用模板 Table。
- [模板分页文案为英文] → 通过 i18n 提供中文/英文文案，不修改模板组件的布局结构。
- [长日志与长表格] → 保持容器级滚动和文本截断，避免对大量日志引入无必要依赖；验证大数据时记录性能边界。

## Migration Plan

1. 以 pinned 模板源码为只读基线，修复共享组件和路由。
2. 修复列表、详情、表单、日志和状态展示。
3. 运行前端 format/lint/typecheck/test/build 与 Go 回归检查。
4. 构建 Hub 并部署到 KVM2，使用真实页面做 Light/Dark、Desktop/Mobile 和关键交互验证。
5. 如需回退，仅替换新构建的 Hub 二进制；不修改后端数据。
