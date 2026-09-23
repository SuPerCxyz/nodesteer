# Tasks — Web 详情页布局与内容宽度一致性

## 1. 共享布局基础

- [x] 1.1 `components/layout/main.tsx`：统一宽度策略（全宽 + 2400px 上限，≥2560 视口居中），移除 `fluid` 分支
- [x] 1.2 更新 `fluid` 调用点（overview / executions / catalog / tasks / transfers），保持行为一致
- [x] 1.3 `features/shared/ui.tsx`：新增 `SectionCard`（分区卡片）与 `DetailField` / `DetailGrid`（响应式字段栅格）
- [x] 1.4 `components/ui/sidebar.tsx`：紧凑密度（菜单项 36→32px、分组标签 32→24px、组间距与内边距收紧）

## 2. 详情页单页化

- [x] 2.1 任务详情（`features/tasks/index.tsx`）：取消 Tabs，概览/定义/目标/执行/调度改为分区，字段用响应式栅格
- [x] 2.2 节点详情（`features/catalog/index.tsx`）：取消 Tabs，概览/执行/任务改为分区，移除 `max-w-[1600px]`
- [x] 2.3 执行详情（`features/executions/index.tsx`）：取消 Tabs，概览与日志分区，日志保持全宽与现有功能
- [x] 2.4 编辑页（`features/editors/index.tsx`）：外层与列表同宽，表单内容 1024px 可读宽度

## 3. 前端测试可执行性

- [x] 3.1 定位 vitest browser 模式多文件卡死根因并修复（配置或用例隔离）
- [x] 3.2 `npm run test` 全量通过

## 4. 验证与交付

- [x] 4.1 本地构建通过（`npm run build`）
- [x] 4.2 Playwright 实测 1366/1920/2560/3840：列表与详情宽度一致、分区可见、字段 2/3 列、日志功能正常
- [x] 4.3 侧边栏在 768/900 高度实测无裁切，菜单项全部可见
- [x] 4.4 部署 kvm2 Hub 并线上复测（宽度、分区、日志、侧边栏）

## 5. 追加：任务/调度合并（用户确认方案 A）

- [x] 5.1 新增共享切换器 `features/shared/task-schedule-tabs.tsx`（视图写入 `?view=` 查询参数）
- [x] 5.2 `/tasks` 路由支持 `view` 参数，`TaskSchedules` 按视图渲染任务或调度列表
- [x] 5.3 旧路由 `/schedules` 重定向到 `/tasks?view=schedules`；仪表盘、编辑页返回、保存后跳转同步更新
- [x] 5.4 侧边栏移除「调度」项（11 项）

## 6. 遗留项修复（用户确认）

- [x] 6.1 kvm2 Hub 执行 `systemctl daemon-reload`，消除 unit 过期告警（未重启服务）
- [x] 6.2 节点详情 Inventory 增加「文件系统」字段（展示挂载点/类型/可用/总量）
- [x] 6.3 短视口（<=700px 高）隐藏 Sidebar 分组标签，640/700px 高度下 11 项全部可见且无需滚动
- [x] 6.4 kvm2 根分区空间：用户确认充足，不做清理
