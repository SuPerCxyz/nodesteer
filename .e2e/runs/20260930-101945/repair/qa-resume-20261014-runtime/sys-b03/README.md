# sys-b03 运行记录（通知丢失 / Revision 落后 → Revision Check 收敛）

批：`sys-b03`
用例：SYS-04、AR-10、SYS-06、AR-13
权威端点：`http://192.168.100.209:18080`（repair），管理写仅走该端点。
执行时间：2026-10-14 09:35Z–09:38Z（UTC）。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| SYS-04 | NOT_RUN | **PASS** | high | 通知帧被丢弃后 Agent 本地 revision 落后，未由通知触发同步；仅由周期性 Revision Check 在 37.2s 内收敛 |
| AR-10 | NOT_RUN | **PASS** | high | 丢失通知 + 离线期间 Desired State 变更；重启后重连对账收敛，幂等、持久化、Desired==实际一致，且无重复 Execution |
| SYS-06 | NOT_RUN | **PASS** | high | 3 个 Desired State 变更通知全部丢弃，Agent 以 1 次 `since=本地 revision` 的同步收敛，`full_resync=false`，对象与 Hub 一致 |
| AR-13 | NOT_RUN | **PASS** | high | 2 变更 Revision 落后经 Revision Check 收敛；再次真实重启后仍一致，无重复 Execution |

## 机制与证据链（真实链路，未改产品源码）

* 通知通道：Hub `NotifyChange` → `CHANGE_NOTIFICATION`（Hub→Agent）；Agent `OnChangeNotification` 在 `global_revision > local` 时 `SYNC_REQUEST`。
* Revision 通道：Agent 周期发 `REVISION_CHECK`（`revision_check_sec`，本环境 Hub 下发 45）→ Hub `REVISION_CHECK_ACK(global_rev=当前)` → 落后则 `SYNC_REQUEST` → `SYNC_RESPONSE` → `SYNC_ACK(applied_rev)`。
* harness：`native-3`(192.168.100.222) 上 1 个**自有** child Agent（修复版二进制 SHA `d55dc5cd…`，独立 0600 config/0700 data_dir），经由操作机 192.168.100.170 上的**自有** frame proxy 连 repair Hub；proxy 可**按 object_id 丢弃/复制 `CHANGE_NOTIFICATION`**，并在测量窗口**抑制该 child 的全部通知**（只影响自有 child，绝不干扰其它批次），同时完整保留 Revision Check / SYNC 通道。
* 关键读数：`evidence/phase11-notification-revision-timeline.json`（通知 / Revision Check / Sync 帧时序），各阶段 `evidence/phase{4,6,7,8}-*.json`；Agent 侧 `state.db` 只读回读（`sync_state.global_revision`、`scripts`/`tasks` 行）。

### 关键量化（本次运行）

* SYS-04：丢弃通知 `global_revision=1849`；3s 后 child 本地仍 1848；收敛用 37.2s；`SYNC_REQUEST.since=1848`、`full_resync=false`、`applied_rev=1849`；收敛前最后一帧为 `REVISION_CHECK_ACK`。
* SYS-06：3 个对象通知全丢弃；lag 1859→1862；**1 次** `SYNC_REQUEST(since=1859)`；`SYNC_RESPONSE(global_rev=1868, snapshot=true, full_resync=false)`；child 回读 script sha / task1,task2 revision 与 Hub 一致。
* AR-10：丢弃 task 通知(1869)；停 child（Hub 侧立即 `offline`）→ 离线改 script(rev4)；重启后重连 `SYNC_REQUEST(since=1868)`→收敛 1870；`desired==actual=true`、store 跨重启持久；复制通知后仍单一对象行；二次重启后 Task Execution 数 =1、Hub Execution 数 =1。
* AR-13：2 变更通知丢弃，lag 1875→1877，Revision Check 收敛（`full_resync=false`）；重启后 `online/synced`、一致且 Execution 仍 =1。

### 「同一逻辑变更 → 一条 Execution」

`phase3-single-execution.json`：1 次 `POST /api/tasks/<id>/run` → 1 条 Execution（SUCCESS，stdout 含 nonce 标记），Hub/child 各 1 行；经通知丢失、lag、两次重启后仍为 1 行（`phase7` `idempotency_second_restart`）。

## 边界与约束遵守

* 只使用 native-3 起自有 child；未触碰其它批次进程/目录/端口；proxy 使用系统分配临时端口并已释放。
* 未 stop/restart repair Hub；未停止/重启 4 个 protected repair Agent；未触碰 `nodesteer-agent.service`；未对 `:8080` 主部署做任何管理写；未改产品源码。
* Hub 全局设置保持只读（30/45/1048576，前后一致）。
* 通知丢弃按 object_id / 对该 child 抑制实现，仅影响自有 child；全局 revision 由多批次共享，结论均基于自有对象与相对读数，不作全局计数断言。

## 其他 harness 说明

* 首版曾用「未被任何 Task 引用」的第二 script 作为变更对象，实测不入该节点 Desired State（`ComputeDesiredState` 为节点域，script 仅经命中 Task 收集）——已改为新增指向该 child 的 task2，属 harness 设计修正，非产品缺陷。
* 同步响应为节点域**完整快照**（`snapshot=true`，`since` 仅用于 tombstone 过滤），非逐对象 delta；「增量」在此实现中等价于 `since` 起点请求 + 幂等全量 upsert/剪枝，结论按此表述。
* 未在重启窗口复核 Hub 长时间不可达（> heartbeat 超时）场景。

## 产物

* `case-result.json`、`progress.json`、`fixture-ledger.json`、`final-cleanup-oracle.json`（含独立 GET404 复核）
* `evidence/`：`phase0-preflight`/`phase0-guard`/`phase1-owned-child`/`phase2-fixtures`/`phase3-single-execution`/`phase4-sys04`/`phase6-sys06`/`phase7-ar10`/`phase8-ar13`/`phase9-cleanup`/`phase11-notification-revision-timeline`
* 可复现脚本：`run_sysb03.py`、`frame_proxy.py`、`child_agent.py`、`preflight_readonly.py`、`hubapi.py`
