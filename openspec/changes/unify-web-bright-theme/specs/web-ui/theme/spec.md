## Purpose

定义 Cadentra Web UI 的 Bright Theme 视觉规格。本规格只约束前端呈现方式（颜色、布局、组件、状态、排版、图标与一致性），不改变任何产品功能、业务流程、数据与权限。

## ADDED Requirements

### Requirement: 视觉基准与主题
系统 SHALL 以 Light Mode 为唯一主题呈现 Web UI，整体明亮、干净、信息密度紧凑，参考现代 shadcn Admin 布局骨架，不呈现深色主界面或大面积深色背景；仅 Terminal / Log Viewer 允许使用深色背景。

#### Scenario: 页面默认明亮
- **WHEN** 用户访问任意已登录页面或登录页
- **THEN** 主体表面为 White / Near White，页面背景为明亮的浅灰蓝（如 `#F8FAFC`），主区域不使用 `#0F172A` 等深色背景

#### Scenario: Terminal 深色例外
- **WHEN** 用户查看 Execution stdout/stderr 日志视图
- **THEN** 日志视图使用深色背景与浅色等宽字体，与周围浅色界面形成对比

### Requirement: 设计 Token 统一
系统 SHALL 通过集中定义的设计 Token（CSS Variables 与 Tailwind Theme）控制全部视觉样式，不得在页面与组件中散落硬编码颜色。Token 至少覆盖背景、前景、表面、Primary、Accent、语义色（Success/Warning/Danger）、边框、输入、焦点环、圆角与阴影。

#### Scenario: 全局 Token 生效
- **WHEN** 检查任一页面的主色、边框、圆角与焦点样式
- **THEN** 其取值来自统一 Token（Primary 蓝色、中性边框、统一 radius），而非页面内联 hex

### Requirement: Primary 与 Accent 配色
系统 SHALL 使用高饱和蓝色作为主品牌色（Primary 如 `#3B82F6`，hover 如 `#2563EB`），Cyan（如 `#06B6D4`）仅作克制的次级强调，不在同一组件中与 Primary 同时竞争。

#### Scenario: Primary 按钮高饱和蓝色
- **WHEN** 页面出现主操作按钮
- **THEN** 按钮背景为高饱和蓝色、白色文字，hover 加深，且同一视觉区域只突出一个主操作

### Requirement: 语义状态色
系统 SHALL 统一使用 soft-tint 语义色体系表达状态（Success `#22C55E`/`#16A34A`、Warning `#F59E0B`/`#D97706`、Danger `#EF4444`/`#DC2626`），状态 Badge 采用浅色底 + 高饱和前景色 + 可选状态点的形式，不使用大面积实心红/绿/橙 Badge。

#### Scenario: 状态 Badge 样式
- **WHEN** 页面展示节点、任务、执行等状态
- **THEN** 每种状态 Badge 呈现为 Soft Background + Saturated Foreground，不同状态可辨识，且不以颜色作为唯一区分手段（结合文字/图标/状态点）

### Requirement: 布局骨架
系统 SHALL 提供统一的 App Shell：固定 Sidebar（明亮底色、Active 项蓝色 accent 指示）、紧凑 Header/Topbar、Main Content 与 Page Header。Sidebar 不使用深色/黑色背景。

#### Scenario: Sidebar 明亮与 Active 指示
- **WHEN** 用户浏览任一主页面
- **THEN** Sidebar 为白/浅色底，当前导航项有蓝色文字与浅蓝背景，并显示清晰的 active 指示

### Requirement: 基础组件一致
系统 SHALL 统一以下组件视觉：Button、Input/Select、Table、Badge、Card、Tabs、Dialog/Drawer/Popover、Dropdown、Empty/Loading/Error 状态、Skeleton。组件需覆盖 Default、Hover、Active、Focus、Selected、Disabled 状态，且不同页面使用一致的组件样式。

#### Scenario: 表单输入聚焦
- **WHEN** 用户聚焦任意 Input/Select
- **THEN** 显示统一的蓝色焦点环与边框，且 Input 在非聚焦状态不呈现灰底全局默认样式

#### Scenario: 表格行交互
- **WHEN** 用户在数据表格上悬停或选择行
- **THEN** 行有统一 hover/selected 视觉，表头统一高度与密度，长文本不导致行异常增高

### Requirement: 长文本与溢出
系统 SHALL 对 ID、UUID、Hash、路径、URL、命令等长技术内容采用 nowrap/truncate/ellipsis、复制提示与必要时的横向滚动，不得将长字符串拆成多行破坏行高。

#### Scenario: 长 ID 显示
- **WHEN** 表格展示 SHA256、节点 ID 等长文本
- **THEN** 内容单行截断或以等宽字体缩略显示，不撑开行高、不产生异常折行

### Requirement: 图标统一
系统 SHALL 全站使用单一图标集（lucide），不得混用多套图标库或 emoji 作为 UI 图标。图标尺寸按场景统一（导航 18px、按钮/表格操作 16px、状态 14-16px），与文字垂直居中。

#### Scenario: 导航图标
- **WHEN** 用户查看 Sidebar 导航
- **THEN** 所有导航项使用统一的 lucide 图标，尺寸一致且与文字垂直居中对齐

### Requirement: 响应式
系统 SHALL 保证 1366×768、1440×900、1920×1080 常见分辨率下布局正常：Sidebar 折叠/移动端、Header、表格、表单、弹层均不出现明显错位或内容截断。

#### Scenario: 常见分辨率布局正常
- **WHEN** 用户以 1366×768、1440×900 或 1920×1080 分辨率访问各页面
- **THEN** Sidebar、Header、表格与表单布局正常，无横向溢出、无明显错位或内容截断

### Requirement: 完成一致性
系统 SHALL 在全部可访问页面（Dashboard、Nodes、Groups、Scripts、Tasks、Schedules、Applications、Artifacts、Executions、Audit、Users、Settings、Login 及详情/编辑页）保持相同的 Shell、排版、组件、颜色与状态语言，不出现不同页面风格分裂或旧主题残留（包括 Dropdown/Dialog/Popover 等 Overlay）。

#### Scenario: 全站巡检无旧主题残留
- **WHEN** 打开任一页面及其下拉、弹窗、抽屉、提示等浮层
- **THEN** 所有层级的主题、颜色、边框、圆角与 hover/active/focus 状态均符合 Bright Theme，无旧深色残留、无异常换行与 Overflow

### Requirement: 非功能边界
系统 SHALL NOT 因视觉统一而改变产品功能、业务字段、数据含义、业务流程、权限、API、路由或新增/删除产品模块。本规格仅约束呈现层。

#### Scenario: 业务行为不变
- **WHEN** 完成视觉统一后执行任一既有业务流程（如创建任务、触发执行、上传制品）
- **THEN** 行为、数据与 API 交互与统一前一致
