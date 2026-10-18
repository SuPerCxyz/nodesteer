# sys-b06 运行记录（Journal 先行、Hub 断开时本地日志完整、恢复后幂等上传）

批次：`sys-b06`　用例：SYS-33、INV-08、SYS-35、SYS-36、AR-19、AR-07
权威端点：`http://192.168.100.209:18080`（repair）；管理写仅走该端点。
执行时间：2026-10-15 11:05:27Z–11:07:01Z（UTC）。nonce `2200f26673a4`。
自有 child 主机：native-1（192.168.100.204）；自有帧代理运行在操作机 192.168.100.170。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| SYS-33 | NOT_RUN | **PASS** | high | 真实手动下发；节点本地 ~1ms 采样器同时观测 Agent 库与脚本首个语句，抓到 4 次“journal 已存在(RUNNING) / 进程尚未启动”半状态，journal 时间戳比进程启动早 11.0ms |
| INV-08 | NOT_RUN | **PASS** | high | 同一执行：Agent 先落 journal（RUNNING、synced=0）再 spawn 进程；journal 独立于进程生命周期存在，离线路径（Hub 停机执行、重传）同样先有 journal |
| SYS-35 | NOT_RUN | **PASS** | high | 真实 repair Hub 停机期间（23.4s）真实执行跑完：脚本效果文件出现 end 标记，Agent 本地 journal 终态 SUCCESS、stdout/stderr 6+6 分片全在、synced=0；Hub 期间不可达 |
| SYS-36 | NOT_RUN | **PASS** | high | Hub 恢复后 child 自行重连并重传：Hub 行终态 stdout 6/6、stderr 6/6 分片标记，stdout 内容 SHA256 与 Agent 本地完全一致；每 Execution 恰 1 行；实时分片保持“仅部分”，最后一行只在最终上传中出现 |
| AR-19 | NOT_RUN | **PASS** | high | 在自有代理上扣留 EXECUTION_ACK → 本地行停在 synced=0（Hub 已是终态）；真实断链+重连触发产品重传路径，Hub 仍恰 1 行、内容逐字节一致；帧账本可见同 ID 两次 EXECUTION_FINISHED（seq 49/54）+ 两次 ACK，harness 对本地库 0 写入 |
| AR-07 | NOT_RUN | **PASS** | high | 全链路一次跑通：下发 → 执行中停 repair Hub（MainPID 100088→100309、二进制 SHA 不变、healthz/readyz 中断→恢复）→ 进程离线跑完 → Hub 起 → 4 个 repair 节点 1.6s 内 online/synced → 结果上传；无重复 Execution、revision 不降、行不丢、desired==actual |

## Hub 重启前后核验（批次硬性要求）

| 项 | 停机前 | 起后 |
|---|---|---|
| `nodesteer-hub-repair-20260930-101945.service` MainPID | 100088（active，19:01:24 CST 启动） | 100309（active） |
| 二进制 SHA256 | `164ea4aca898…` | `164ea4aca898…`（一致） |
| `/healthz` + `/readyz` | 200 / 200 | 中断（首探即无 HTTP 应答且 :18080 TCP 拒连）→ 恢复 0.5s 后 200 / 200 |
| `nodesteer-hub.service`（:8080）MainPID | 1251 | 1251（一致） |
| 4 台 VM 的 `nodesteer-agent.service` MainPID | 673 / 731 / 16919 / 1002 | 逐一相同 |
| 4 台 VM 的 repair agent 单元 MainPID | 1494 / 4311 / 16918 / 1481 | 逐一相同 |
| 4 个 repair 节点收敛 | online/synced | 停机后全部 online/synced，用时 **1.6s**（观察窗口 90s，≥45s Revision Check 窗口） |

停机总时长 23.4s（11:05:50→11:06:13）。起后 3s 内捕获到 child 的 HELLO→HELLO_ACK→SYNC_REQUEST→EXECUTION_FINISHED→EXECUTION_ACK→SYNC_ACK 完整重连与对账序列（`evidence/phase-frames.json` seq 34/35/36/37/40/41）。

## 机制与关键读数

* **SYS-33/INV-08（journal 先行）**：`evidence/phase3-sys33-inv08-journal-first.json`
  * 采样器（`JOURNAL_SAMPLER`）在节点上以 ~1ms 同时读 Agent 库与脚本首个语句写入的效果文件，同一内核时钟。
  * `row_seen.status=RUNNING`、`journal_visible_before_script_first_statement_samples=4`（直接观测到半状态）、`gap_row_to_marker_sec=0.0076`、`timestamp_margin_sec=0.0110`。
  * 未注入任何人为延迟：脚本首个语句本身就是“进程已启动”的可观测事件。
* **SYS-35（Hub 断开时本地日志完整）**：`evidence/phase4-…-hub-outage.json` → `outage.during_outage`
  * 停机时本地行：`status=SUCCESS, synced=false, stdout_len=291, stderr_len=258`；效果文件 `…|start|outage|…|end|outage|`。
  * 观察到的产品行为（如实记录）：该行的 `offline=false`，因为 Journal 在**在线时**创建；`offline` 反映创建时刻的连接态而非完成时刻。
* **SYS-36（恢复后幂等上传）**：同文件 `execution_after_recovery`
  * `stdout_complete_on_hub=true`、`stdout_chunk_markers_on_hub=6`、`stderr_chunk_markers_on_hub=6`、`hub_stdout_sha256 == local_stdout_sha256`、本地 `synced=true`。
  * 实时分片为 best-effort：`terminal_line_in_realtime_chunks=false`（最后一行在 Hub 不存在时产生）、`terminal_line_in_final_stdout=true`、`no_chunk_equals_final_stdout=true`。
* **AR-19（Execution Re-upload）**：`evidence/phase5-ar19-execution-reupload.json`
  * seam：仅扣留 1 个 Hub→Agent `EXECUTION_ACK`（seq 50），本地零写入；`ack_withheld_kept_row_unsynced=true`。
  * 断链+重连后同 ID 再次 EXECUTION_FINISHED（seq 54）→ 新 ACK（seq 57）→ `reupload_acked=true`、`content_identical_after_reupload=true`、`no_duplicate_row=true`。
* **AR-07 + 持久化**：`hub_persistence_after_restart`（revision 2664 不降、owned script/task 行仍在、`quick_check=ok`）、`phase6-settle-consistency.json`（Hub/本地 ID 集合相等、共 3 条、无重复、desired==actual、settle 后 log_chunks 不变）。

## 边界与约束遵守

* 唯一被停/起的单元：`nodesteer-hub-repair-20260930-101945.service`（本批 `hub_stop_events=1`、`hub_start_events=1`）。未触碰 `:8080` 主 Hub 与任何 `nodesteer-agent*.service`；未重启任何 VM（不使用 libvirt）；未改产品源码；无 Git 操作。
* 自有 child 运行在 native-1（精确 repair Agent 二进制 `d55dc5cd…`，0700 root + 0600 config），经操作机自有帧代理连 Hub；Agent 本地库仅只读查询（`mode=ro` + `query_only`）。
* Hub 全局 settings 全程只读（30/45/5000/1048576，前后一致）；所有 fixture 精确 DELETE 200 → GET404。
* 独立复核：`verify_final.py`（不依赖本批记账，重新读取环境）→ `evidence/independent-verification.json`，`passed=true`（4 节点 online/synced、fixture 全 404、native-1 无残留、Hub 健康、受保护/主服务 MainPID 逐一相同、settings 未变）。

## 产物

`case-result.json`、`progress.json`、`fixture-ledger.json`、`final-cleanup-oracle.json`、`run-evidence.json`、`evidence/`（含 `evidence/attempt01-harness-bug/`、`evidence/attempt02-oracle-repairhub-mainpid/` 两轮失败产物原样保留）。
可复现脚本：`run_sysb06.py`、`frame_proxy.py`、`child_agent.py`、`hubctl.py`、`hubdb.py`、`node_units.py`、`preflight_readonly.py`、`verify_final.py`。

## 首轮/次轮失败说明（harness 缺陷，均非产品缺陷）

1. **attempt01**：`child_agent.py` 的通用远端辅助函数把参数放进 SSH 命令行（`ssh host python3 - <args>`），ssh 会拼接成远端 shell 命令导致语法错误；改为把参数以 `%r` 嵌入程序源码、仅用 stdin 传输后修复。本批当次未停 Hub（`hub_stop_events=0`），环境已复位。
2. **attempt02**：oracle 把 repair Hub 自身也纳入“MainPID 与基线逐一相同”的比较，而该单元正是本批唯一授权重启单元，导致误判 `clean=false`；改为“受保护 + 主 Hub 逐一相同”且 repair Hub 单独记为 `changed_by_design=true` 后修复。业务断言全部已通过。
