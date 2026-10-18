# sys-b08 运行记录（离线自治与在线门禁：Allow Offline 继续执行、Hub Online Required 不执行、Agent-owned 调度离线触发）

批次：`sys-b08`　用例：SYS-13、SYS-14、AR-18、E2E-04
权威端点：`http://192.168.100.209:18080`（repair）；管理写仅走该端点。
执行时间：2026-10-15 11:22:01Z–11:23:50Z（UTC）。nonce `fc0350484850`。
自有 child 主机：native-3（192.168.100.222）；自有帧代理运行在操作机 192.168.100.170。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| SYS-13 | NOT_RUN | **PASS** | high | 链路被切断（Agent 进程存活）期间，`allow_offline` 的 Agent-owned interval 调度继续触发：第一窗口新增 6 条 `offline=1`/`trigger_type=schedule` 执行，节点效果文件 1→7 行，Agent 日志 `schedule trigger` 增量 +6 |
| SYS-14 | NOT_RUN | **PASS** | high | 同一窗口内 `hub_online_required` 调度：执行数 1→1（0 新增）、效果文件 1→1 行不变、日志 `schedule trigger` 增量 0；在线时两者都触发（同一对象阳性对照），恢复后又触发，证明是离线门禁而非调度失效 |
| AR-18 | NOT_RUN | **PASS** | high | 两个独立离线窗口后：每个 slot 本地恰好 1 行、Hub 恰好 1 行，本地/Hub ID 集合相等、每条离线执行 stdout 内容 SHA256 与状态一致；Hub revision 不降、script/task/schedule revision 两侧一致、两侧 `quick_check=ok` |
| E2E-04 | NOT_RUN | **PASS** | high | Agent-owned interval 离线继续执行（首窗 +6 条真实业务效果），恢复后无重复：slot 级（task+node+scheduled_time）唯一、ID 集合相等，第二次离线/在线循环后仍无重复、无旧 slot 补跑 |

## 机制与关键读数

* **Hub 不可用窗口的 seam**：自有帧代理 `mode=cut`（`proxy_cut_events=2`），**本批 0 次 Hub 停/起**（`hub_stop_events=0`）。Agent 进程刻意保持存活，因此被验证的是真实的 Agent 本地调度自治，而非 Hub 侧补发。
* **阳性对照与门禁差异**：`evidence/phase2-fixtures.json` → `online_positive_control.both_fired_while_online=true`（在线时两条调度各触发 ≥1 次）；`evidence/phase3-sys13-sys14-offline-autonomy.json` → `derived`。
  * 第一窗口 32.2s：`offline_executions_allow=6`、`offline_executions_required=0`；`allow_effect_file_grew_during_offline=true`、`required_effect_file_unchanged_during_offline=true`；日志增量 allow=+6 / required=0 / `connection lost`=+1。
  * `hub_blind_during_offline_allow/required=true`：窗口内 Hub 侧执行行数未变（Hub 完全不知情），离线行的 `offline=1`。
* **帧级证据（`evidence/phase-frames.json`）**：整批 `RUN_EXECUTION` 帧 = **0**（`manual_run_posts=0`），6 条离线执行 ID 只出现在 `agent_to_hub EXECUTION_FINISHED`（各 1 次，共 6）与 `hub_to_agent EXECUTION_ACK`（各 1 次，共 6）——即“Agent 本地触发、恢复后上传”，Hub 从未下发过它们。
* **恢复后幂等（slot 级）**：`evidence/phase4-e2e04-ar18-recovery-idempotency.json`
  * `allow`：本地 13 行 / Hub 13 行、`local_slot_unique`/`hub_slot_unique`=true、`id_sets_equal=true`、`duplicate_ids=0`、离线内容 SHA 全部匹配。
  * `required`：2/2、同样唯一且集合相等（其 `offline_content_matches` 为空 → 该窗口无离线执行）。
  * `no_duplicate_after_two_cycles=true`。
* **settle 与一致性**：`evidence/phase5-settle-consistency.json` → script sha256 两侧一致、task/schedule revision 两侧一致、子节点本地 `global_revision=2691`、`quick_check=ok`；对两个快照中都存在的执行，实时分片计数未变（`stable=true`）。
* **如实记录的产品行为**：Agent-owned 调度产生的执行在 Hub 侧**没有实时 LOG_CHUNK**（`max_realtime_chunks_per_execution=0`）。原因是 Hub 的 `AppendLog` 要求该 execution 行已存在，而 Agent-owned 执行的行是随最终上传才在 Hub 创建的；日志权威来源是最终上传的 stdout/stderr。

## 边界与约束遵守

* 未停/起任何 Hub 单元（`hub_stop_events=hub_start_events=0`），未触碰受保护 Agent，未改产品源码，无 Git 操作，未重启任何 VM。
* 自有 child 运行在 native-3（精确 repair Agent 二进制 `d55dc5cd…`，0700 root + 0600 config），经操作机自有帧代理连 Hub；Agent 本地库仅只读查询（`mode=ro` + `query_only`），本批对本地库 0 写入。
* 清理顺序：先 DELETE 两个 interval Schedule（停止继续触发），再 DELETE 两个 Task、Script，最后 DELETE 自有节点；逐条 GET404（见 `progress.json.final_cleanup_get_http`）。离线执行历史保留为幂等证据：settle 时两个 Task 在 Hub 侧合计 17（allow）/ 6（required）条执行行，`fixture-ledger.json.preserved_executions` 记录了第一批离线窗口恢复后仍可见的执行 ID（allow 8 条 / required 1 条）。
* 独立复核：`verify_final.py`（不依赖本批记账）→ `evidence/independent-verification.json`，`passed=true`：4 节点 online/synced、6 个 fixture 全 404、native-3 无残留、Hub 健康、受保护 + 主 Hub + repair Hub MainPID 全部与基线相同、settings 未变。
* 未触及已知缺陷 `E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW`（本批不使用 Remote 条件，未放宽任何断言）。

## 产物

`case-result.json`、`progress.json`、`fixture-ledger.json`、`final-cleanup-oracle.json`、`run-evidence.json`、`evidence/`（含 `attempt01-harness-bug/`、`attempt02-assertion-fixes/`、`attempt03-strictness-fix/`、`attempt04-frames-dump/` 四轮失败/加固产物原样保留）。
可复现脚本：`run_sysb08.py`、`frame_proxy.py`、`child_agent.py`、`hubctl.py`、`hubdb.py`、`node_units.py`、`preflight_readonly.py`、`verify_final.py`。

## 首轮至第四轮失败说明（均为 harness 缺陷，非产品缺陷）

1. **attempt01**：queries 假设 Agent 本地表存在 `task_id`/`name` 列，实际 `schedules` 表为 `(id, definition_json, revision, enabled)`；改为读取 `definition_json` 解析 `task_id` 后修复。
2. **attempt02**：断言用了不存在的键（`pre['required_local_rows']`，实际为 `required_rows` 计数），导致 SYS-14 误判 PARTIAL；另 `log_chunks_stable` 直接比较两次快照全表，而活的 interval 调度在两次采样之间本就会产生新执行（数值全为 0，无任何重复分片），改为只比较两次快照交集的执行后修复。
3. **attempt03**：AR-18 的内容校验因键名写错（`offline_content_matches` → 实际 `offline_content_matches_hub`）被静默跳过，属于断言被削弱；改正后重新完整执行。
4. **attempt04**：`evidence/phase-frames.json` 因帧账本中存在非 JSON 原生值被 `except: pass` 静默吞掉；改为 `json.dumps(..., default=str)` 兜底并把错误写入 `progress.json` 后修复。
