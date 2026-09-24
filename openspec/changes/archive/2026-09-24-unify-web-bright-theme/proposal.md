## Why

NodeSteer 前端当前为传统深色运维后台（`#0f1419` 背景、`#4f9cf9` accent、emoji 图标、手写 CSS），视觉偏暗、色彩平淡、组件状态不完整，与产品的轻量现代定位不符。需要一个统一的 Bright Theme，在保留 satnaing/shadcn-admin 的布局骨架与信息密度基础上，建立更明亮、干净、有活力的视觉体系。

## What Changes

- 引入 Tailwind CSS + shadcn/ui 组件体系，替换手写 CSS 全局样式。
- 引入 lucide-react 统一图标集，替换导航/按钮中的 emoji/Unicode 字符图标。
- 建立 NodeSteer Bright Theme Design Tokens（CSS Variables + Tailwind Theme）：Primary Blue `#3B82F6`、Accent Cyan `#06B6D4`、语义色 soft-tint 体系、明亮中性色、radius/shadow/focus-ring 统一。
- 以 Light Mode 为唯一主题（不实现 Dark Mode），仅保留 Terminal/Log Viewer 深色例外。
- 统一 App Shell（Sidebar + Header/Topbar + Main + Page Header）、Card、Data Table、Button、Input/Select、Badge、Tabs、Dialog、Dropdown、Empty/Loading/Error 等基础组件与全部状态。
- 巡检全部页面与 Overlay 组件，清理旧主题残留，修复对齐、长文本换行、Overflow 与响应式问题。
- 不改变任何产品功能、业务流程、API、字段、权限、路由与 i18n 文案。

## Capabilities

### New Capabilities

- `web-ui/theme`: NodeSteer Bright Theme 视觉规格——设计 Token、颜色策略、Sidebar/Header/Card/Table/Form 等组件视觉、组件状态、Overlay 一致性、图标与排版要求，以及"不得修改产品功能"的边界。

### Modified Capabilities

无（现有 `web-ui` specs 描述功能行为，本次不改动任何行为要求）。

## Impact

- 依赖：新增 `tailwindcss`、`@tailwindcss/vite`、`lucide-react`、shadcn/ui 运行依赖（`class-variance-authority`、`clsx`、`tailwind-merge` 及所需 Radix 包）。
- 代码：`web/vite.config.ts`、`web/src/index.css`（重写）、`web/src/main.tsx`（引入样式）、`web/src/components/Layout.tsx`、`web/src/pages/*`（类名/图标替换）、新增 `web/src/components/ui/*`（shadcn 组件）。
- 构建：`npm run build`/`typecheck`/`lint` 需保持通过；Hub 静态托管 `web/dist` 产物。
- 不影响后端、API、数据模型、权限与 Agent 行为。
