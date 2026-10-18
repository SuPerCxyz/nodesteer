# sys-b09 运行记录（Agent 本地调度与条件引擎）

批：`sys-b09`（Misfire / 重复触发 / 心跳退避 / 条件结构与 Remote online）
用例：SYS-23、SYS-22、AR-17、SYS-18、SYS-26、SYS-27
权威端点：`http://192.168.100.209:18080`（repair），管理写仅走该端点。
执行时间：2026-10-14 08:33Z–08:49Z（另含 08:27Z 起的 SYS-18 自有 child Agent 探针）。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| SYS-23 | PASS | **PASS** | high | `skip` 不补跑；`run_once` 精确补跑 1 次（补的是最近一次错过的 slot） |
| SYS-22 | PASS | **PASS** | high | Hub 与 Agent 两条路径上「同 Task+Node+Scheduled Time 重复触发」均只产生 1 条 Execution |
| AR-17 | PASS | **PASS_WITH_NOTES** | medium | 幂等/持久化/Desired State 一致已核验；「恢复后」（重启）未覆盖（本批禁止重启） |
| SYS-18 | PARTIAL | **PASS** | high | 心跳/Revision Check/重连退避的配置、实测节拍与 Jitter 已量化 |
| SYS-26 | PASS | **PASS** | high | AND 单层/嵌套多层 true/false、fail-closed BLOCKED、16 组错误结构被拒 |
| SYS-27 | PASS | **FAIL** | high/medium-high | Remote online 恒 Fail Closed，4 个 native 节点全部复现（根因见下） |

## 发现缺陷（1 条）

`QA-SYSB09-REMOTE-CONDITION-CLOCK-SKEW`：Remote online 条件在修复版环境下**永远**返回
`BLOCKED / "condition unknown: <nil>"`，满足与不满足不可区分，无法产生真实执行效果。

* 复现：`evidence/phase2-conditions.json`（4 个 remote 用例）、
  `evidence/phase10-remote-condition-multinode.json`（4 个 native 节点全部 BLOCKED，29–132ms）。
* 受控隔离（同一 Agent 二进制 + loopback fake Hub，同一时钟）：
  `evidence/phase2d-remote-condition-mechanism.raw.json`
  `observed_at` 在 1ms/1s 之前 → SUCCESS；**在 0.5s 之后 → BLOCKED**；值为 `UNKNOWN` → BLOCKED。
  ⇒ Agent 侧 remote 实现本身正确，失败点是「时间戳新鲜度」判定。
* 环境测量：`evidence/phase2c-clock-offset.json` —— repair Hub 的时钟领先 4 个 native Agent
  约 **0.14s**（同源测量，并序偏差可抵消）。
* 代码位置：`internal/agent/sync_validation.go:31-42`（`observed.After(now)` / `age<TTL`）、
  `internal/agent/agent.go:1685-1725`、`internal/hub/gateway.go:594-617`（`observedAt=Hub time.Now()`）、
  `internal/agent/agent.go:1023-1027`（UNKNOWN → BLOCKED）。
* 未主张修复方案；未修改任何产品源码。

## 边界与约束遵守

* 未重启 repair Hub；未停止/重启 4 个 protected repair Agent；未触碰 `nodesteer-agent.service`；
  未对 `:8080` 主部署做任何管理写。
* Hub 全局设置保持只读观测（30/45/1048576，前后一致）。
* SYS-23 的「Agent 不可用」以**等价不可调度窗口**构造（调度 disabled 期间 slot 到期后 enable），
  不停任何 Agent；未覆盖 Hub 长时间不可达的 misfire。
* SYS-27/SYS-18 使用**自有 child Agent**（同一 repair 二进制 SHA `d55dc5cd…`，独立临时 data_dir，
  指向本机 loopback fake Hub，不接触 repair Hub、不建 systemd 单元）；进程已停止、沙箱已删除。
* 未在重启窗口复核 AR-17 的「恢复后」维度。

## 其他 harness 说明

* 并发：同 Hub 上另有 sys-b05 与 web batch03 并行；所有断言只针对本批 nonce 前缀对象，
  不做全局计数结论（观测到节点集合/全局 revision 被其它批次推进属预期）。
* 已修正的 harness 错误：
  1. 首版 Agent 本地库只读复核脚本 SQL 引号被破坏（`incomplete input`）→ 用
     `evidence/phase11-agent-db-readback-retry.json` 重跑，结论只取修正版；
  2. `remote_condition_probe.py` v1 阻塞式帧读取在网络静默期卡住，造成 2 个用例假超时 →
     v2（非阻塞可续读）重做，4 个受控用例全部符合预期。
* 客户端（探测脚本）未接触产品状态：fake Hub 只监听 127.0.0.1。

## 产物

* `case-result.json`（6 用例逐维度判定、缺陷、安全边界、harness 错误）
* `progress.json`、`fixture-ledger.json`（22 个 owned fixture 全部 DELETE→GET404）、
  `final-cleanup-oracle.json`（残留、节点、Agent 单元、Hub 设置、沙箱复核）
* `evidence/`：分阶段原始证据（含 SYS-18 探针原始 JSON、时钟偏移、缺陷受控实验）
* 可复现脚本：`sysb09_run.py`（主流程）、`sysb09_followup.py`、`finalize_results.py`、
  `fakehub_probe.py`、`remote_condition_probe2.py`、`clock_offset_probe.py`
