# Design — Web 列表表格对齐与列宽自适应

## Context

见 `proposal.md`。当前实现位于：

- `web/src/components/data-table/column-header.tsx`：可排序表头用 `Button size='sm'`（`px-2.5`）承载标签与排序图标。
- `web/src/features/shared/data-table.tsx`：`table-fixed` + 每列 `width=(size/totalSize)%`，表格 `minWidth=Σsize`。
- `web/src/components/ui/table.tsx`：`th/td` 统一 `px-2`、单行省略号截断。
- 各列表页（`features/catalog|tasks|executions|transfers/index.tsx`）声明 `size/minSize/maxSize`。

问题根因：表头按钮内边距使标签右移；`minWidth=Σsize`（舒适宽之和）在容器略窄时直接触发滚动与裁切；部分列 `size/minSize` 偏小不足以容纳表头/典型内容。

## Goals / Non-Goals

- Goals：表头与内容同左基线；表头不截断；列宽随容器自适应、仅在最小可读宽之和超出容器时滚动；节点列序调整；所有列表页与内嵌执行历史表一致。
- Non-Goals：不做列宽拖拽/记忆；不改变数据来源、分页/排序/多选/列显隐交互；不引入依赖；不重构主题。

## Decisions

1. **表头左对齐**：保留 `th px-2`，排序按钮改用 `-ms-2` 抵消左侧内边距并以 `ps-2` 复位，标签左边缘与 `td` 文本一致；图标间距从 `ms-2`/`gap-1.5` 收窄为 `gap-1`，右侧内边距减到 `pe-1`，降低表头占宽。非排序列（纯文本/flexRender）保持现有 `px-2`。
   - 备选：`th p-0` + 内容自带内边距——会波及内嵌表格的普通表头，改动面更大，弃用。
2. **容器自适应**：表格 `minWidth` 由 `Σsize` 改为 `ΣminSize`。容器 ≥ `ΣminSize` 时表格占满容器、列按 `size` 比例收缩（`table-fixed` + 百分比宽），不再出现「略超容器」的裁切；容器 < `ΣminSize` 时才横向滚动，操作列 `sticky end-0` 保持可见。
   - 备选：改为 `table-auto` 按内容自适应——长字段会无限撑宽且列宽抖动，弃用。
3. **列宽校准**：以「表头标签 + 排序图标 + 内边距」为 `minSize` 下限、以典型内容宽度为 `size` 目标，逐页测量校准；超长值保留省略号截断并补 `title` 完整值。
4. **节点列序**：在 `catalog/index.tsx` 节点列定义中把 `os` 列移动到 `hostname` 之后。
5. **一致性**：任务列表行操作去掉 `justify-end`；内嵌表格（overview/tasks/catalog）沿用统一表头与截断样式。
6. **验证**：vitest 断言表格 `minWidth=ΣminSize` 与排序按钮对齐类；构建后部署 kvm2 测试 Hub，用 agent-browser 在多个视口测量 表头截断、左基线差、表格/容器宽度、列序。

## Risks / Trade-offs

- [列宽收缩使长内容截断增多] → 为 `minSize` 保底表头宽，并为截断单元格提供 `title`；极长值仍以省略号呈现。
- [kvm2 重启测试 Hub 短暂中断] → 替换前备份原二进制（`nodesteer-hub.bak-<时间>`），异常立即回滚并重启。
- [`table-fixed` 百分比 + `min-width` 在极端列组合下的浏览器行为差异] → 线上多视口实测（1024/1280/1440/1920）并以截图为准。

## Migration Plan

1. 本地实现 + `npm run test` / `tsc` / `lint` / `build`。
2. `make web-build && make build-hub`（内嵌前端）。
3. `scp` 到 `192.168.100.209`，备份并替换 `/usr/local/bin/nodesteer-hub`，`systemctl restart nodesteer-hub`。
4. agent-browser 线上逐页验收；不通过则回滚备份并重启。

## Open Questions

无。
