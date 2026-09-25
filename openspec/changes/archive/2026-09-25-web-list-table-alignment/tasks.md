## 1. 共享表格基座

- [x] 1.1 `web/src/components/data-table/column-header.tsx`：排序按钮左基线对齐 `td`（`-ms-2 ps-2`），收窄图标间距与右侧内边距，标签保持 `truncate`
- [x] 1.2 `web/src/features/shared/data-table.tsx`：表格 `minWidth` 改为各可见列 `minSize` 之和；列宽基准取 `minSize`，容器可容纳时表格拉伸并把余量分配给各列
- [x] 1.3 `web/src/components/ui/table.tsx`：核对表头/单元格 `px-2`、单行省略号与 sticky 操作列样式；无需改动

## 2. 列宽与列序校准

- [x] 2.1 `web/src/features/catalog/index.tsx`：节点列序改为 状态、主机名、系统、分组、节点地址、架构、Agent 版本、部署模式、最后在线、操作
- [x] 2.2 `web/src/features/catalog/index.tsx`：节点列 `size/minSize` 校准（1440 表头与典型内容完整，1280 保留横向滚动）
- [x] 2.3 `web/src/features/catalog/index.tsx`：调度/脚本/分组/应用/发布包/用户列 `size/minSize` 校准
- [x] 2.4 `web/src/features/tasks/index.tsx`：任务列 `size/minSize` 校准，行操作改为左对齐
- [x] 2.5 `web/src/features/executions/index.tsx` 与 `web/src/features/transfers/index.tsx`：列 `size/minSize` 校准

## 3. 截断信息披露

- [x] 3.1 为会被截断的单元格补 `title` 完整值（节点系统/IP、脚本名称与描述、分组名称、用户名称、应用名称、发布包 SHA256 等）
- [x] 3.2 概览、任务详情、节点详情内嵌执行历史表统一对齐与截断样式

## 4. 测试与构建

- [x] 4.1 增补 vitest：表格 `minWidth=ΣminSize`、排序按钮与单元格同左基线（`data-table.test.tsx`，5 用例通过）；节点列序在 5.3 线上断言
- [x] 4.2 执行 `cd web && npm run test` 全量通过（25 文件 / 150 用例）
- [x] 4.3 执行 `cd web && npm run lint && npx tsc -b && npm run build` 与 `format:check` 通过

## 5. kvm2 部署与线上验收

- [x] 5.1 构建含内嵌前端的 Hub（`npm run build` + `make build-hub`，最终 sha256 `ba94dee2…`）
- [x] 5.2 备份并替换 `192.168.100.209:/usr/local/bin/nodesteer-hub`（原始回滚备份 `nodesteer-hub.bak-20260925-0825`），重启 `nodesteer-hub.service`，healthz/readyz=200
- [x] 5.3 agent-browser 在 1024/1280/1440/1920 逐页测量：表头无截断、表头与内容左基线一致、表格不超容器；1280 仅节点表保留横向滚动（10 列），1440+ 全部适配；节点列序正确；无数据页面（调度/分组/应用/发布包）用 `network route` 注入真实结构数据在本地预览复测通过
- [x] 5.4 排序（升/降序）、分页（第 2/102 页）、列显隐（系统列隐藏/恢复并持久化）、行多选与批量工具条、行操作菜单回归通过，控制台 0 错误
- [x] 5.5 验收通过，无需回滚

## 6. 收尾

- [x] 6.1 `openspec validate web-list-table-alignment` 通过（4/4 artifacts）
- [x] 6.2 线上证据：`/tmp/opencode/online-{nodes,tasks,executions,scripts,transfers,audit,users}-1440.png`、`online-nodes-1280.png`、`online-nodes-dark-1440.png`、测量输出 `online-measure-final2.txt`；无数据页面证据：`/tmp/opencode/local-{schedules,groups,applications,artifacts}-mock-1440.png`
