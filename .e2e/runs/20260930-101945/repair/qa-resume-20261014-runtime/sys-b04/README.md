# sys-b04 运行记录（通知重复/乱序、窗口过期 → 以 Hub Current Desired State / 完整快照收敛）

批次：`sys-b04`　用例：SYS-05、AR-11、AR-12、SYS-07、AR-14、AR-15
权威端点：`http://192.168.100.209:18080`（repair）；管理写仅走该端点。
执行时间：2026-10-15 10:24Z–10:28Z（UTC）。nonce `fe9f0eed4def`。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| SYS-05 | NOT_RUN | **PASS** | high | 重复帧、陈旧帧重放、真实乱序（旧帧扣留、新帧先到、旧帧后放行）三种 seam 下，Agent 最终状态恒等于 Hub 最新 Desired State，applied revision 单调不回退 |
| AR-11 | NOT_RUN | **PASS** | high | 同一次变更 + 重复通知：Hub/Agent 各 1 行、revision/sha 一致，Execution 历史不变，`quick_check=ok` |
| AR-12 | NOT_RUN | **PASS** | high | 旧通知（rev 2647）比新通知（rev 2648）晚 0.235s 抵达 Agent；Agent 仍停在最新态；随后真实 Agent 重启后状态持久、desired==actual、新下发 SUCCESS |
| SYS-07 | NOT_RUN | **BLOCKED** | high | Full Resync 分支在本环境**算术上不可达**（见下）；给出边界判据与两次真实边界观测，未观察到 `full_resync=true` |
| AR-14 | NOT_RUN | **PASS_WITH_NOTES** | medium | 窗口过期触发未观测；以“最大落后 Agent → 完整 Desired 快照”替代路径验证恢复语义（幂等、持久、desired==actual），并单独验证删除/剪枝路径 |
| AR-15 | NOT_RUN | **PASS_WITH_NOTES** | medium | 同替代路径下两次恢复 + 真实 Agent 重启：完整快照重放幂等、状态持久、无重复 Execution、恢复后可正常执行 |

## SYS-07 不可达判据（关键发现）

分支条件为 `since > 0 && (current - since) > changelog_window`（`internal/hub/sync.go` `BuildSyncResponse`）。

* 现场 `current ≈ 2652`，`changelog_window = 5000`（全局 settings，禁止修改）。
* 能满足条件的最大 `since` 是 `current - window - 1 = -2349`，为负 → 与 `since > 0` 矛盾；`since ≤ 0` 直接短路，不进入窗口判断。
* 结论：除非写全局 `changelog_window` 或把全局 revision 推进 > 2349 步（均被批次硬性约束禁止），本环境无法产生 `full_resync=true`。
* 两次真实边界观测（`evidence/phase7-sys07-ar14-window-boundary.json`）：`since=1`（最小正数）与 `since=0` 均返回 `full_resync=false`、`snapshot=true`，且携带该节点**完整** Desired 对象集（script/task id 均含本次 owned 对象）；Agent 均收敛到 Hub 当前 revision。这正是计划中允许的“真实 Full Desired Snapshot”替代 seam。

配套正向能力：`evidence/phase6-tombstone-prune.json` —— 真实删除自有 script+task 后，连接中的 Agent 通过正常 tombstone 路径剪枝（child 计数从 2/2 回到 1/1，Hub 侧 GET404）。

## 机制与关键读数

* 重复帧：`evidence/phase4-sys05-ar11-duplicate-notification.json`（action=duplicated，child sha 收敛到 v2，applied revision 单调）。
* 陈旧帧重放：注入 `global_revision` 远小于本地 revision 的通知 ×2 → 触发 0 次 SYNC。
* 乱序：`evidence/phase5-sys05-ar12-out-of-order.json` + 派生证据 `evidence/phase5b-out-of-order-delivery-order.json`（旧帧释放相对新帧投递晚 0.235s，亚秒级顺序来自原始帧账本）。
* 持久化：真实 Agent 进程重启后为非 full 同步，仍 online/synced、desired==actual，新下发 1 次 SUCCESS（stdout `QA:fe9f0eed4def:EXEC:after-restart`）。
* Execution 计数：全程恰 2 条（Hub 与 Agent 各 2，ID 唯一）。

## 边界与约束遵守

* 未重启 Hub（`hub_restarts: 0`）；未停/起任何受保护 Agent；未改产品源码；无 Git 操作。
* 自有 child 运行在 native-3（精确 repair Agent 二进制 `d55dc5cd…`），经操作机自有 frame proxy 连 Hub。
* 唯一“写入自有 child 本地库”的 seam：停自有 child 后把其**自有** `state.db` 的 `sync_state.global_revision` 设为 1 与 0；全局 settings 与全局 revision 序列全程未写（前后一致，已写入证据）。

## 产物

`case-result.json`、`progress.json`、`fixture-ledger.json`、`final-cleanup-oracle.json`、`run-evidence.json`、`evidence/`（含 `evidence/attempt01-cleanup-bookkeeping-bug/` 首轮失败产物原样保留）。
可复现脚本：`run_sysb04.py`、`frame_proxy.py`、`child_agent.py`、`hubdb.py`、`derive_evidence.py`、`preflight_readonly.py`。

## 首轮失败说明（harness 缺陷，非产品缺陷）

首轮全部阶段断言通过，仅在清理记录环节因 GET404 汇总的 KeyError 崩溃（剪枝探针内的 fixture 记录缺字段），导致 oracle 未生成；修复后完整重跑（EXIT=0），本报告以第二次完整运行为准。
