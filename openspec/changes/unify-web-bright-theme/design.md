# Design — Cadentra Bright Theme Web UI 视觉统一

## Context

Cadentra Web UI（`web/`）当前为 React 19 + Vite + react-router + i18next，纯手写全局 CSS（`src/index.css`，深色主题）。无 Tailwind / shadcn / 图标库。所有页面使用统一 class 体系（`.layout/.sidebar/.card/.table/.btn/.badge/.form-grid/.dl/.log-view/.empty` 等），基础结构一致，适合整体换肤与组件层升级。

需求与验收见 proposal.md 与 `specs/web-ui/theme/spec.md`。本设计只描述实现路径与关键技术决策。

## Goals / Non-Goals

**Goals**
- 在现有 React 页面结构与业务逻辑不变的前提下，将呈现层升级为 Tailwind + shadcn/ui 组件体系。
- 建立 Cadentra Bright Theme 全局 Design Tokens，单一来源控制颜色/圆角/阴影/焦点。
- 统一 lucide 图标，替换全部 emoji/Unicode 图标。
- 保持信息密度与现有页面信息架构不变。

**Non-Goals**
- 不实现 Dark Mode 切换（仅 Light Mode，Log Viewer 深色例外）。
- 不重构路由、状态管理、API 层、i18n 文案与业务逻辑。
- 不新增页面或产品模块，不改变导航业务结构。

## Decisions

### 1. 引入 Tailwind CSS v4（`@tailwindcss/vite` 插件）
选 v4：与 Vite 原生集成、CSS-first 配置、减少传统 postcss/tailwind.config 样板，且对现有 `.card` 等 class 命名无冲突风险。在 `vite.config.ts` 加插件，`src/index.css` 改为 `@import "tailwindcss"` + `@theme` + CSS Variables 层。

### 2. shadcn/ui 组件层（`src/components/ui/*`）
shadcn 是"复制到项目"的组件源码，非运行时依赖，最贴合"保留轻量、按需取用"的一期原则。仅生成本项目实际需要的组件：Button、Badge、Card、Input、Select、Table、Tabs、Dialog、DropdownMenu、Label、Skeleton、Tooltip、ScrollArea、Switch。使用 CSS Variables（`--primary`、`--background` 等）驱动主题，与 Tailwind `@theme` 联动。

### 3. 单图标集 lucide-react
替换 `Layout.tsx` 导航与品牌中的 emoji/Unicode 字符（`▦/🖥/⚙/◆` 等）。全部页面图标统一走 lucide 组件，尺寸按场景（nav 18、btn 16、status 14-16）。

### 4. 保留现有语义 class 作为主题锚点
为最小化页面改动，保留业务相关 class（`.dl/.chip/.log-view/.chip-list/.lang-switch/.btn-sm/.inner-card/.form-grid` 等）并在 CSS 层将其映射/补充到 Bright 风格；页面内联的 `.btn/.btn-primary/.badge-*/.table/.card/.stat-card/.nav-item/.page-header` 逐步替换为 shadcn 组件或统一 Tailwind class。**表格类 `.table` 先统一为 DataTable 风格**，降低长文本/选中态维护成本。

### 5. 组件状态完整性
以 shadcn 组件默认实现覆盖 Hover/Active/Focus/Selected/Disabled/Keyboard Focus；自定义处（如表格行 hover/selected、Sidebar active）在 CSS 层补齐。Focus Ring 统一 `--ring`（`#3B82F6` 20~25% 透明度）。

### 6. 深色 Terminal 例外
`.log-view` 保留深色（`#0F172A` 底 + 浅色等宽字），作为 Light UI + Dark Terminal 的刻意对比，不参与主题切换。

## 依赖

- `tailwindcss`、`@tailwindcss/vite`
- `lucide-react`
- shadcn 基础：`class-variance-authority`、`clsx`、`tailwind-merge`、`@radix-ui/react-*`（dialog、dropdown-menu、select、tabs、tooltip、label、switch、scroll-area、slot）、`tailwindcss-animate`（如 shadcn 生成使用）

## 风险与缓解

- **Tailwind Preflight 影响全局**：Preflight 会重置元素默认样式，可能影响 `.dl`/`details/summary` 等既有元素。缓解：在 `@layer base` 中对业务 class 补充必要默认样式，逐页巡检。
- **shadcn Select 基于 Radix 且样式较重**：原页面大量使用原生 `<select>`。缓解：优先保留原生 `<select>` 仅统一外观（用 CSS），Dialog/Dropdown/Tabs/Tooltip/Switch 才用 shadcn 组件，避免每处都迁移。
- **构建产物**：Hub 通过 `web/dist` 静态托管，改造后 `npm run build` 必须成功且页面功能回归（登录、列表、增删改、执行详情）不受影响。
- **无 git 仓库**：目录不在 git 控制下，改造前在 `/tmp/opencode` 备份原 `index.css`、`Layout.tsx` 与 `vite.config.ts` 以便回退。
