## 1. 基线确认

- [x] 1.1 复核节点模型、注册/重注册/Upsert 路径，确认 `hostname` 会在哪些路径被覆盖，`display_name` 必须完全独立。
- [x] 1.2 确认迁移版本：本 change 使用 v26，`add-agent-inbound-connection` 使用 v27；两迁移独立、可乱序应用。
- [x] 1.3 盘点前端所有用户可见的节点名称位置，形成替换清单（列表/详情/执行/告警/传输/概览/目标选择）。
- [x] 1.4 确认非目标：不做唯一性、不做批量重命名、不把显示名同步给 Agent、不改变身份/匹配/执行语义。

## 2. 数据库与存储

- [x] 2.1 新增迁移 `v26_node_display_name`：`ALTER TABLE nodes ADD COLUMN display_name TEXT NOT NULL DEFAULT ''`；不删除、不回填。
- [x] 2.2 `UpsertNode` 的 `ON CONFLICT` 更新列表排除 `display_name`；首次 INSERT 使用空值默认。
- [x] 2.3 `createNode`/`PrepareEnrollment`/`RegisterOrUpdate` 均不写 `display_name`；重新纳管保留既有值。
- [x] 2.4 新增 `UpdateNodeDisplayName(ctx, id, value)`，校验行数，不存在返回 `sql.ErrNoRows`。
- [x] 2.5 `scanNode`、`GetNode`、`GetNodeByAgentID`、`ListNodes` 读取并返回 `display_name`（空字符串而非 null）。
- [x] 2.6 迁移/存储测试：全新库、v25 旧库、v26/v27 乱序、重复启动、Upsert/重新注册/重新纳管保留、专用更新 404。

## 3. API 与校验

- [x] 3.1 `internal/models.Node` 增加 `display_name` JSON 字段；确认不进入 Agent 协议/同步载荷。
- [x] 3.2 `GET /api/nodes`、`GET /api/nodes/{id}` 返回 `display_name`。
- [x] 3.3 `POST/PUT /api/nodes/{id}` 接受 `display_name`；与 status/labels/endpoint 同请求时先完成全部权限与格式校验，再原子更新。
- [x] 3.4 校验：去首尾空白、按 Unicode 码点 ≤64、拒绝 C0/C1/DEL/换行/制表符；空值清除；重复名允许。
- [x] 3.5 权限：Operator/Administrator 可写，Viewer 403，匿名 401；错误不产生部分更新。
- [x] 3.6 API 测试：64/65 码点、emoji、控制字符、空白、清除、重复名、节点不存在、非法 JSON。
- [x] 3.7 审计 `node.rename`：仅值变化时记录 old/new/操作者/时间；相同值不重复；与 `node.update` 可区分；审计不含秘密。

## 4. 前端 helper 与页面替换

- [x] 4.1 新增 `nodeDisplayName` helper 及单元测试：显示名优先、空白回退 hostname、再回退 ID、全空未知文案。
- [x] 4.2 更新 `web/src/lib/api.ts` 的 `Node` 类型和测试夹具，增加可选 `display_name`。
- [x] 4.3 节点列表/卡片：显示名作为主文本，hostname 作为 title/辅助；搜索 accessor 同时包含显示名、hostname、ID。
- [x] 4.4 节点详情标题、面包屑、身份区使用 helper；增加重命名入口和弹窗（预填、64 字计数、空值说明、错误反馈、成功后 invalidate）。
- [x] 4.5 执行列表/详情（目标、node 列、标题）使用 helper。
- [x] 4.6 文件传输列表/详情（源/目标节点）使用 helper。
- [x] 4.7 告警规则/事件/通知使用 helper：优先按 `node_id` 查节点；节点已删除时回退 `node_hostname` 或 ID。
- [x] 4.8 Dashboard 概览、监控总览、监控节点详情、目标选择器、Task/Schedule/Application 节点展示使用 helper。
- [x] 4.9 确认确认框、toast、错误信息中的节点名称也使用 helper，避免残留 hostname。
- [x] 4.10 重命名成功后刷新相关查询；清除显示名后所有页面立即回退 hostname。

## 5. 权限与交互测试

- [x] 5.1 Viewer 不显示重命名入口，直接调用接口返回 403。
- [x] 5.2 Operator/Administrator 可重命名；弹窗校验 64 字和控制字符；空值清除。
- [x] 5.3 重命名失败展示后端错误，不乐观改缓存；成功后列表/详情/执行/告警/传输/概览一致。
- [x] 5.4 节点重名不导致匹配、执行、权限或路由异常。
- [x] 5.5 重新注册/重新纳管后显示名仍保留，hostname 更新不影响显示名。

## 6. 文档与收尾

- [x] 6.1 更新 `docs/PRODUCT.md` §17：显示名与 hostname 的区别、权限、回退和重名规则。
- [x] 6.2 更新 `docs/ARCHITECTURE.md` §15：显示名是展示字段，不参与身份/匹配。
- [x] 6.3 如 README 涉及节点命名，补充一句说明；部署文档无需变更。
- [x] 6.4 最终回归：后端相关包 `go test`、`make lint`；前端 `npm test`、`npm run lint`、`npm run build`；记录命令与退出码。
- [x] 6.5 E2E：创建 → 重命名 → 六个页面一致 → 清空回退 → 重新注册保留。
  - 证据（2026-11-05，预览 Hub v27 干净库）：`native-node-1` 重命名为 `E2E显示名-20261105`；节点列表、详情、执行记录（按节点过滤条）、监控总览、告警规则节点选择器、文件传输源节点选择器均显示新名称；清空后详情/列表/执行页回退 `native-node-1`；重启预览 Agent 后 `display_name` 保留且 `hostname` 未被覆盖。截图：`/home/superc/tmp-nodesteer-native-preview/e2e-display-name-20261105`；浏览器 console error 与 pageerror 均为空。告警无事件/规则数据、传输无该节点记录，已用对应节点选择器覆盖。
- [x] 6.6 复核 tasks/spec/design 与实现一致；未实现/未验证项保持未勾，不归档。
