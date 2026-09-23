# Proposal — Web 详情页布局与内容宽度一致性

## Why

详情页与列表页宽度不一致，且详情页在宽屏下明显偏窄、信息稀疏：

- 列表页使用 `Main fluid` 占满可用宽度（1920 下 1656px，4K 下 3576px）；任务详情、执行详情使用非 fluid `Main`，被 `@7xl/content:max-w-7xl` 收窄为 1280px 居中；节点详情被内层 `max-w-[1600px]` 限制。
- 详情页 tab 内容再叠加 `max-w-4xl`（896px），字段为单列 `dl`，宽屏下大片留白。
- 详情页信息被拆到多个 Tab，需要来回切换才能看到完整信息。
- 超宽屏（2K/4K）缺少统一上限，列表被拉伸到 3500px 以上，可读性下降。

## What Changes

- 统一内容宽度策略：`Main` 全宽（fluid），内容区上限 2400px，≥2560px 视口时居中；移除 `fluid` 分支与 `@7xl/content:max-w-7xl` 收窄逻辑。
- 详情页（任务 / 节点 / 执行）取消 Tabs，改为单页分区卡片；字段使用响应式栅格（1 列 / lg 2 列 / 2xl 3 列）。
- 执行日志区保持全宽并保留现有筛选、搜索、跟随、换行能力。
- 编辑页外层与列表同宽，表单内容保留 1024px 可读宽度。
- Sidebar 紧凑密度：768px 视口高度下 11 个菜单项与 5 个分组全部可见，不再被底部账号块裁切。
- 修复 `npm run test`（vitest browser 模式）多文件执行卡死问题，使全量用例可通过。

## Capabilities

### New Capabilities

- `web-ui/layout`: NodeSteer Web 内容宽度策略、详情页信息结构（单页分区 + 响应式字段栅格）、Sidebar 密度与前端测试可执行性要求。

### Modified Capabilities

无。

## Impact

- 前端：`components/layout/main.tsx`、`components/layout/authenticated-layout.tsx`（如需）、`components/ui/sidebar.tsx`、`features/shared/ui.tsx`、`features/tasks/index.tsx`、`features/catalog/index.tsx`、`features/executions/index.tsx`、`features/editors/index.tsx`、`vite.config.ts`。
- 不改变后端、API、字段、权限、路由、i18n 文案与业务流程。
- 验证：本地构建 + 多视口实测（1366/1920/2560/3840）与 `npm run test` 全量执行；部署 kvm2 Hub 后线上复测。
