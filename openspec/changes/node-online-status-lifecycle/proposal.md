## Why

Agent 未成功登录的节点可能显示在线：Hub 重启后内存会话消失但数据库中的 `online` 无人回收（孤儿 online），HELLO 被拒绝的路径完全不写 status 且被拒连接断开时无法定位节点，旧 online 永不回落。纳管创建的新记录初始 status 为 `offline`，列表显示「离线」而非「等待」。附带两个命令生成缺口：Native 纳管命令以 `enable --now` 结束，对已运行服务不重启导致换配置不生效；`docker run` 缺少同名容器清理，冲突时直接失败。90s 心跳超时置离线为字面量写入，会覆盖 `maintenance`/`disabled`。

## What Changes

- 新增节点状态 `pending`，节点生命周期收敛为三段：纳管创建 = pending（等待接入）→ HELLO 被接受 = online → 断开/心跳超时 = offline。
- accepted HELLO 后显式置 online（不再依赖心跳写入的副作用），maintenance/disabled 不被自动转换覆盖。
- 拒绝的 HELLO（credential 无效、未注册、token 不匹配）若可定位到节点、其当前 online 且无活跃会话 → 置 offline；有活跃会话不改状态；从未成功会话的 pending 节点保持 pending。
- Hub 启动时将无活跃会话的 online 复位为 offline，根治孤儿 online。
- Native 纳管命令以 `enable && restart` 结束；`docker run` 前置 `docker rm -f`。
- Web 节点列表对 `pending` 显示「等待中」badge。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `hub/nodes`: 节点在线状态机（等待/在线/离线三段生命周期，含拒绝、断开、超时、启动复位转换）。
- `hub/core`: 拒绝的 HELLO 不得使节点滞留在线（Secure Agent transport 补拒绝场景）。

## Impact

- Hub Gateway HELLO 处理、断开/超时回调、启动初始化、enrollment 命令生成、Web 节点状态渲染。
- 无数据库迁移（`status` 为字符串列，新增取值）；远端条件的**观测值比较集**纳入 `pending`（任务条件可比较 `node == pending`），Hub→Agent 状态**指令集**（`MsgNodeStatus`）不下发 `pending`（判定与依据见任务 1.5）。
- 不改 credential 轮换；保留手工「设为在线」API；不触碰 `node-enrollment` spec（避让未归档 `download-agent-binary-enrollment` 对同名需求的 MODIFIED 占用，`enable && restart`/前置 `docker rm -f` 作为命令补强由代码与测试承载，不违反现行 enrollment 需求）。
- 运维：存量孤儿 online 由启动复位自动收敛；lstable 节点列表经复核已为单节点（`dfa2f6d6` 在线、`56023d92` 不存在），无需清理（详见任务 4.3）。
