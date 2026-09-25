# Proposal — Web 列表表格对齐与列宽自适应

## Why

全站列表表格在常见笔记本视口（1280/1440）下出现三类可见问题：可排序表头文字比单元格内容右移约 10px，未真正左对齐；表头与内容因列宽偏小被省略号截断（如 `状态→状…`、`类型→类…`、`架构→架…`、`修订号→修…`、节点「系统」列 `Debian GNU/Li…`）；表格总宽略超卡片容器（任务表 1280 下溢出约 40px），最右列被裁、操作列遮挡相邻内容。此外节点列表「系统」列位于分组/地址之后，与主机名信息不相邻，阅读动线不佳。

## What Changes

- 排序表头与单元格使用同一左基线（`ps-2` 对齐 `td px-2`），去掉排序按钮多余水平内边距、收窄图标间距，降低表头占宽。
- 表格最小宽度由「各列舒适宽之和」改为「各列最小宽之和」：容器装得下时列宽按比例收缩适配容器，消除「比容器宽一点点」的裁切；确实放不下时才横向滚动，操作列保持固定可见。
- 逐页校准列宽 `size/minSize`，保证表头与典型内容完整可读；超长值保留省略号并通过 `title` 暴露完整值。
- 统一行操作对齐（任务列表由右对齐改为左对齐），概览/任务详情/节点详情内嵌执行历史表同步对齐与截断样式。
- 节点列表列序调整为：状态、主机名、系统、分组、节点地址、架构、Agent 版本、部署模式、最后在线、操作。
- 增补前端表格列宽/表头对齐用例，并重新构建部署 kvm2 测试 Hub 做多视口线上验收。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `web-ui`: 「Usable data tables」要求收紧为可验证的表头/内容同左基线、表头不截断、列宽随容器自适应且仅在必要时横向滚动；新增节点列表列序要求。

## Impact

- 前端：`web/src/components/data-table/column-header.tsx`、`web/src/features/shared/data-table.tsx`、`web/src/components/ui/table.tsx`、`web/src/features/{catalog,tasks,executions,transfers}/index.tsx`、`web/src/features/overview/index.tsx`、相关 vitest 用例。
- 不改变后端、API、字段、权限、路由、i18n 文案与业务流程；不引入新依赖。
- 验证：`npm run test`、`tsc/lint/format/build`；构建含内嵌前端的 Hub，备份后部署 kvm2 测试 Hub（192.168.100.209）并重启服务，在 1024/1280/1440/1920 逐页截图测量；异常回滚备份。
