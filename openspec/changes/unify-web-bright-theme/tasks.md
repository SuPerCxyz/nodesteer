# Tasks — NodeSteer Bright Theme Web UI 视觉统一

## 1. 依赖与基础设施

- [x]- [ ] 1.1 备份现状：将 `web/src/index.css`、`web/src/components/Layout.tsx`、`web/vite.config.ts`、`web/src/main.tsx` 复制到 `/tmp/opencode/cadentra-web-backup/`
- [x]- [ ] 1.2 安装依赖：`npm install tailwindcss @tailwindcss/vite lucide-react class-variance-authority clsx tailwind-merge tailwindcss-animate @radix-ui/react-slot @radix-ui/react-dialog @radix-ui/react-dropdown-menu @radix-ui/react-select @radix-ui/react-tabs @radix-ui/react-tooltip @radix-ui/react-label @radix-ui/react-switch @radix-ui/react-scroll-area`
- [x]- [ ] 1.3 `vite.config.ts` 添加 `@tailwindcss/vite` 插件
- [x]- [ ] 1.4 `src/index.css` 重写：`@import "tailwindcss"`、`@theme`、Bright Theme CSS Variables（--background/--foreground/--primary/--accent/--success/--warning/--danger/--muted/--border/--input/--ring/--radius/--shadow）、`.log-view` 深色例外、业务 class 补充层
- [x]- [ ] 1.5 `src/main.tsx` 确认引入新的 `index.css`

## 2. shadcn/ui 组件层

- [x]- [ ] 2.1 生成/创建 `src/lib/utils.ts`（cn()：clsx + tailwind-merge）
- [x]- [ ] 2.2 创建 `src/components/ui/button.tsx`（variants: primary/secondary/ghost/destructive/link/outline + sizes）
- [x]- [ ] 2.3 创建 `src/components/ui/badge.tsx`（variants: success/warning/danger/info/neutral + dot 支持）
- [x]- [ ] 2.4 创建 `src/components/ui/card.tsx`、`skeleton.tsx`、`label.tsx`、`input.tsx`、`select.tsx`
- [x]- [ ] 2.5 创建 `src/components/ui/table.tsx`（DataTable 风格：表头浅灰蓝、行 hover/selected、紧凑 padding）
- [x]- [ ] 2.6 创建 `src/components/ui/tabs.tsx`、`dialog.tsx`、`dropdown-menu.tsx`、`tooltip.tsx`、`switch.tsx`、`scroll-area.tsx`

## 3. App Shell 与基础组件

- [x]- [ ] 3.1 重写 `src/components/Layout.tsx`：Sidebar（明亮、nav lucide 图标、active 蓝色指示 + accent bar）、Topbar/Header、Main、PageHeader；替换 emoji 图标
- [x]- [ ] 3.2 更新 `Layout.tsx` 导出组件：Badge→ui/badge、Loading/Empty/ErrorMsg 统一为 Bright 风格（含 Skeleton），StatusChip 调整
- [x]- [ ] 3.3 登录页 `Login.tsx`：卡片化明亮样式、brand 图标 lucide、Primary 按钮

## 4. 页面迁移（保持业务逻辑不变）

- [x]- [ ] 4.1 Dashboard.tsx：stat-card→Card 组件、.table→ui/table、Badge 统一
- [x]- [ ] 4.2 Nodes.tsx + NodeDetail：列表/详情表格、.dl 样式、Badge
- [x]- [ ] 4.3 Groups.tsx、Scripts.tsx（+Editor）、Tasks.tsx（+Editor/RunNow）：列表、表单、参数编辑器
- [x]- [ ] 4.4 Schedules.tsx（+Editor）、Applications.tsx（+Editor）、Artifacts.tsx、Executions.tsx（+Detail）、Misc.tsx（Audit/Users/Settings）
- [x]- [ ] 4.5 全站 lucide 图标替换（导航、按钮、brand、状态点）

## 5. 视觉巡检与验证

- [x]- [ ] 5.1 组件状态巡检：Button/Input/Select/Badge/Table 的 Default/Hover/Active/Focus/Selected/Disabled/Loading/Error 统一
- [x]- [ ] 5.2 Overlay 巡检：Tabs/Dialog/Dropdown/Tooltip 无旧主题残留、无内容截断/异常换行
- [x]- [ ] 5.3 长文本与对齐巡检：长 ID/SHA/路径 nowrap/truncate；Icon 与文字垂直居中；表格行高稳定
- [x]- [ ] 5.4 响应式：1366×768 / 1440×900 / 1920×1080 无错位；Sidebar 折叠可用
- [x]- [ ] 5.5 `npm run typecheck`、`npm run lint`、`npm run build` 通过
- [x]- [ ] 5.6 部署到 Hub 后浏览器验证全部页面与关键交互（登录/列表/增删改/执行详情）视觉与功能正常
