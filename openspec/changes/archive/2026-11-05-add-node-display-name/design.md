## Context

节点的用户可见名称目前直接使用 `hostname`。`hostname` 是 Agent 上报的运行时元数据，重新注册路径会覆盖它，因此不能承载管理员或 Operator 设置的稳定名称。另一方面，节点 ID / Agent ID 是执行、审计、告警、文件传输的稳定标识，显示名只用于展示，不参与身份匹配。

本 change 新增 `nodes.display_name`，与 `hostname` 解耦；空值回退 hostname。它与 `add-agent-inbound-connection` 的节点 endpoint/连接字段独立，迁移版本预留 v26，入站 change 使用 v27。

## Goals / Non-Goals

**Goals**

- Operator 可以为节点设置/清除显示名，Viewer 只读。
- 重新注册、重新纳管、Agent HELLO 元数据刷新都不覆盖显示名。
- 所有用户可见的节点名称统一走一个 helper；空值回退 hostname，再回退 node ID。
- 审计可追溯重命名操作，且不记录敏感信息。

**Non-Goals**

- 不要求显示名唯一，不用显示名做路由、权限、匹配或执行目标。
- 不修改 Agent 的 hostname 上报，也不把显示名同步给 Agent。
- 不改变节点排序/分组/标签语义；不做批量重命名。
- 不把显示名写入历史审计的节点标题快照（历史记录仍按 node ID 解析）。

## 1. 数据模型与迁移

**版本规划**

- `v26`：`add-node-display-name` 的 `nodes.display_name`。
- `v27`：`add-agent-inbound-connection` 的入站连接列。

两个迁移独立、可乱序应用；实现顺序建议先合入本 change。若入站 change 先合入，则入站使用当时下一个空闲版本，本 change 使用再下一个；不得复用版本，也不得把两个 change 的列合并成互相依赖的迁移。

```sql
ALTER TABLE nodes ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
```

- 旧行默认空，不做回填；UI 立即回退 hostname。
- 不新增唯一索引；允许重名。
- `display_name` 不参与 `agent_id`/`hostname` 的查找和重新纳管匹配。

## 2. 存储与 Upsert 语义

- `createNode` 保持 `display_name` 为空；`PrepareEnrollment` 不清空、不写入。
- `RegisterOrUpdate`（credential 重连）只更新 hostname/ip/os/arch/version/mode/capabilities；`display_name` 不在更新列表。
- `UpsertNode` 的 `INSERT ... ON CONFLICT(id) DO UPDATE` 必须从更新列表排除 `display_name`；首次 INSERT 使用默认空值或显式空值。
- 新增专用方法 `UpdateNodeDisplayName(ctx, id, value)`，使用 `UPDATE ... WHERE id=?` 并校验行数；不存在返回 `sql.ErrNoRows`，API 映射 404。
- 读取节点时 `scanNode` 增加 `display_name`；列表/详情返回空字符串而不是 `null`。
- 重新纳管复用既有节点时保留显示名；如果管理员希望清空，走重命名接口显式传空。

## 3. API 契约

`GET /api/nodes`、`GET /api/nodes/{id}` 增加：

```json
{ "display_name": "生产数据库 01" }
```

更新接口（沿用 `POST/PUT /api/nodes/{id}`）：

```json
{ "display_name": "生产数据库 01" }
```

- 字段级权限：`display_name` 修改要求 `canWrite`（Operator/Administrator）；Viewer 返回 403。`endpoint` 等字段的 admin-only 规则不受影响；同一请求同时包含 `display_name` 和 `endpoint` 时，先校验所有字段权限，再原子更新，禁止部分成功。
- 校验：`TrimSpace` 后按 Unicode 码点计数 `<= 64`；拒绝 C0/C1 控制字符、DEL、换行、制表符；允许内部空格、中文、emoji、常见标点；空字符串表示清除。
- 不校验唯一性；重复显示名允许。
- 不存在的节点返回 404；JSON 类型错误/超长/控制字符返回 400，且不写库。
- `display_name` 不出现在 Agent 协议、同步载荷或执行参数中。
- 响应中的 `hostname` 保持原值，前端 helper 负责回退；`display_name` 使用非 `omitempty` 字段，空值返回 `""` 而不是 `null`。
- `POST /api/nodes`（批量状态/标签入口）不接受 `display_name`，避免在没有明确单节点上下文的请求中批量重命名；重命名只走 `/api/nodes/{id}`。

## 4. 审计

新增动作 `node.rename`：

- 目标：`node` + node ID。
- 详情：`old`、`new`（均为去空白后的值；空值用空字符串）、操作者、时间。
- 仅在值实际变化时写入；重复提交相同值不产生审计。
- 不记录 Agent credential、令牌或其他秘密。
- 与既有 `node.update` 区分：仅 status/labels 变化仍记 `node.update`；同时包含重命名与其他字段时，分别记录或合并为一条带动作明细的记录，但必须能识别 `node.rename`。

## 5. 前端统一显示

新增 helper：

```ts
nodeDisplayName(node?: { display_name?: string; hostname?: string; id?: string }): string
```

规则：`display_name.trim()` 非空 → 使用；否则 `hostname.trim()` 非空 → 使用；否则 `id`；全部为空 → 通用“未知节点”文案。

必须替换的用户可见位置（以现有代码为准，不限于）：

- 节点列表/卡片、节点详情标题与面包屑、节点选择器。
- Dashboard 概览、监控总览、监控节点详情。
- 执行列表/详情（node 列、目标、标题）。
- 文件传输列表/详情（源/目标节点）。
- 告警规则、告警事件、通知中的节点名称（事件带 `node_id` 时按节点表解析；节点已删除时回退 `node_hostname` 或 ID）。
- Task/Schedule 目标节点展示、应用分配节点展示。
- 确认对话框、toast、错误信息中出现的节点名称。

不要求修改：

- 后端审计/日志中的 hostname 字段。
- Agent 上报、协议、执行参数、文件路径。
- 监控序列的 `node_id`、API 查询参数。
- 历史审计的自由文本（无法可靠反解显示名）。

节点详情增加“重命名”入口：

- Operator/Administrator 可见；Viewer 不可见或禁用。
- 对话框预填当前 `display_name`，显示 64 字计数和“留空则使用 hostname”说明。
- 提交成功后 invalidate `['nodes']` 及相关查询；失败展示后端错误，不乐观改缓存。
- 空值提交表示清除显示名，列表立即回退 hostname。

## 6. 兼容与边界

- 旧 Hub DB 升级后 `display_name=''`；旧前端忽略新字段，行为不变。
- 旧 API 客户端只发送 status/labels 时不受影响；新字段可选。
- `hostname` 仍按 Agent 上报更新；显示名不随 hostname 变化。
- 显示名可重复；所有匹配、权限、执行、审计关联继续使用 node ID。
- 节点删除后，历史告警/执行中的名称按既有 `node_hostname` 或 ID 回退，不保留显示名快照。
- 长度按码点而非字节；64 个 emoji 也允许，但 UI 需避免布局溢出（截断 + title）。

## 7. 测试

- 迁移：全新库、v25 旧库、v26/v27 乱序应用、重复启动幂等、默认空值。
- 存储：Upsert 不覆盖、重新注册不覆盖、重新纳管保留、专用更新不存在返回 404、读取空值。
- API：Operator 200、Viewer 403、Anonymous 401；64/65 字符、控制字符、空值清除、仅空白、重复名、非法 JSON、节点不存在。
- 审计：值变化写 `node.rename`，相同值不写，详情含 old/new，不含秘密。
- 前端 helper：显示名优先、空白回退 hostname、两者空回退 ID、全空回退文案。
- 页面：节点列表/详情、执行、传输、告警、概览、目标选择器均使用 helper；重命名对话框权限、计数、空值清除、错误反馈。
- E2E：创建节点 → 重命名 → 列表/详情/执行/告警/传输/概览一致显示 → 清空回退 hostname → 重新注册后显示名保留。

## 8. 文档

- `docs/PRODUCT.md` §17 节点管理：说明显示名与 hostname 的区别、权限和回退。
- `docs/ARCHITECTURE.md` §15 Agent Identity / 节点模型：说明显示名是展示字段，不参与身份。
- `README.md`（如涉及节点命名）补充一句。
- 不需要修改部署文档；该字段不影响端口、证书或升级顺序。

## 9. 未决/注意

- 与 `add-agent-inbound-connection` 的迁移顺序需在实现时确认；本 change 固定 v26，入站 change 固定 v27。
- 如果现有前端表格使用 hostname 作为排序/搜索 accessor，改为 displayName + hostname 的组合，保证重命名后仍可按原名搜索。
- 告警事件已有 `node_hostname` 快照；本次不新增 `node_display_name` 快照，前端按 `node_id` 解析，避免历史语义漂移。
