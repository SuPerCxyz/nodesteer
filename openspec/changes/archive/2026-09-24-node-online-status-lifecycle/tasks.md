## 1. Hub 状态机（Go）

- [x] 1.1 新增 `pending` 状态枚举，纳管创建初始置 `pending`，合法值校验放开。
- [x] 1.2 accepted HELLO（credential 与注册两条路径）显式置 `online`，maintenance/disabled 不被覆盖。
- [x] 1.3 拒绝分支防滞留：credential 无效与 token 不匹配两类拒绝在可定位节点、无活跃会话且当前 `online` → `offline`；有活跃会话不改状态；`pending` 保持。
- [x] 1.4 Hub 启动将无活跃会话的 `online` 复位 `offline`；心跳超时回调带 maintenance/disabled 保护。
- [x] 1.5 波及面确认并测试锁定：远端条件观测值比较集纳入 `pending`（指令集 `MsgNodeStatus` 不下发）、仅 `online` 可执行（任务/应用）对 `pending` 不可执行。

## 2. 纳管命令修复

- [x] 2.1 Native 命令收尾改为 `daemon-reload && enable && restart`（替代 `enable --now`）。
- [x] 2.2 `docker run` 前置 `docker rm -f nodesteer-agent 2>/dev/null || true;`。
- [x] 2.3 增加命令文本断言测试（含 `systemctl restart`、`docker rm -f`、禁 `enable --now`）。

## 3. Web UI

- [x] 3.1 节点 status 类型与分支补齐 `pending`，列表/详情经 `StatusBadge` 显示「等待中」badge（文案走 `ui.tsx` 既有内联 map；zh/en locale 新增的无消费方 `nodes.pending` 死 key 已在整合验收阶段移除）。

## 4. 验证与清理

- [x] 4.1 Go：gofmt 空 / vet / `go test ./...` / race（hub+hubserver）全绿；Web：tsc / lint（0 errors）/ format / build / vitest 15 files·87 tests 全绿。
- [x] 4.2 `openspec validate --all` 通过（20/20）。
- [x] 4.3 lstable Hub 节点列表复核：**现状仅剩单节点** `agent-dfa2f6d6…`（`status=online`、`sync_status=synced`、`last_seen` 持续刷新、带 inventory）。复核发现原计划与现实相反 —— 在线的正是 `dfa2f6d6`（`first_seen` 23:15:06，当晚 rejected 后 3 秒即注册成功），原计划保留的 `56023d92` 已不存在；「单节点」验收目标天然达成，**未执行任何删除**（只读确认步避免了误删唯一在线节点）。
