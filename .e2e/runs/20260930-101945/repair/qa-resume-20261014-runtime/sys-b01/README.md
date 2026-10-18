# sys-b01 运行记录（Hub 重启：Agent 自动重连 / Session 与 Desired State 重建 / 权威源收敛）

批次：`sys-b01`　用例：SYS-03、SYS-17、INV-01、AR-01
权威端点：`http://192.168.100.209:18080`（repair）；管理写仅走该端点。
执行时间：2026-10-15 10:08Z–10:11Z（UTC）。nonce `a2630efc1488`。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| SYS-03 | NOT_RUN | **PASS** | high | 停/起 repair Hub：调用方视角 /healthz+/readyz 中断→恢复，MainPID 变化、二进制 SHA 不变；4 个受保护 Agent 自动重连回 online/synced，revision 与行不丢 |
| SYS-17 | NOT_RUN | **PASS** | high | 持有 SYNC_RESPONSE 拉开对账窗口：窗口内 Agent 未 READY（本地 revision 仍为旧值），真实下发得到 `BLOCKED / agent not ready (reconciling)`；放行后 SYNC_ACK 收敛、下一次下发 SUCCESS |
| INV-01 | NOT_RUN | **PASS** | high | 断开自有 child 后由 Hub 侧改 Script：child 保持旧值与旧 revision；重启后 Hub 行仍是操作者所写值，child 重连收敛到 Hub 值（单向） |
| AR-01 | NOT_RUN | **PASS** | high | 重启前后各 1 次真实 SUCCESS（目标受保护节点，stdout 带 nonce 标记），settle 后该 Task 恰 2 行（目标节点）/2 行（child）：无重复 Execution、无半状态 |

## 机制与关键读数

* 唯一被停/起的单元：`nodesteer-hub-repair-20260930-101945.service`（MainPID 5952→97295→97389，二进制 SHA 始终 `164ea4ac…`）。`nodesteer-hub.service`(:8080) 与全部 Agent 单元 MainPID 不变（`evidence/phase0-untouched-units*.json`）。
* 断线窗口：`evidence/phase5-hub-restart.json`（outage：HTTP 无应答且 :18080 TCP 拒连；recovery 0.5s；healthz=readyz=200）。
* 重连时间线（1s/0.4s 采样）：4 个受保护节点 + 自有 child 在 `offline` → `online/synced`，全部在重启后 ~2.9s 内恢复（`evidence/phase6-post-restart.json` → `protected_reconnect.restart_side_timeline`）。
* 持久化：`revision_sequence` 不降、owned script/task 行与 revision_changes 仍在、`PRAGMA quick_check=ok`。
* READY 门禁（SYS-17）：HELLO(idx 0) 早于首个 applied_rev≥target 的 SYNC_ACK(idx 26)；窗口内 child 本地 sha 仍为 v1、本地 revision 2627 < 目标 2628；真实下发 → `BLOCKED / "agent not ready (reconciling)"`（含 Agent 侧 WARN 日志行）；放行后 0.2s 收敛、再下发 → `SUCCESS`（stdout `QA:a2630efc1488:EXEC:after-ready`）。
* 真实 Execution：`9693fe3a…`（重启前，target 节点）、`6e8231ef…`（窗口内 BLOCKED）、`db35338b…`（READY 后 SUCCESS）、`2e35feee…`（重启后 target 节点 SUCCESS）。

## 边界与约束遵守

* 未触碰 `:8080` 主 Hub 与任何 `nodesteer-agent*.service`；未重启任何 VM（不使用 libvirt）；未改产品源码；无 Git 操作。
* 自有 child 运行在 native-1（精确 repair Agent 二进制 `d55dc5cd…`，0700 root + 0600 config），经操作机 192.168.100.170 上的自有 frame proxy 连 Hub；Agent 本地库仅只读查询。
* 目标 Task 的 fixture 确实落在受保护节点 native-2 上并真实执行（stdout 携带 nonce 标记），测后精确删除并 GET404。
* Hub 全局 settings 全程只读（30/45/5000/1048576，前后一致）。

## 产物

`case-result.json`、`progress.json`、`fixture-ledger.json`、`final-cleanup-oracle.json`、`run-evidence.json`、`evidence/`（含 `evidence/attempt01-harness-bug/` 首轮失败产物原样保留）。
可复现脚本：`run_sysb01.py`、`frame_proxy.py`、`child_agent.py`、`hubctl.py`、`hubdb.py`、`node_units.py`、`preflight_readonly.py`。

## 首轮失败说明（harness 缺陷，非产品缺陷）

首轮在 Hub 重启后于 `GET /api/nodes` 收到 401（Hub 管理会话在内存中，重启即失效），harness 未重认证；另有 child 清理脚本的 `%` 格式化缺陷。两者均已修复并完整重跑（EXIT=0），本报告以第二次完整运行为准。
