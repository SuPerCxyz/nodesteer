# batch09-settings 运行记录（Settings 修改/校验/持久化/广播 与 Agent 运行时实际消费）

批次：`batch09-settings`（web）　用例：**P21-03、P21-04、P21-05、P21-06、FL-16**
同批还执行了 api 用例 **IDEMPOTENT-MANUAL**，证据落在
`../qa-resume-20261014-api/idempotent-manual/`（见本文末“跨交付物说明”）。
权威端点：`http://192.168.100.209:18080`（repair，唯一管理写端点）。
执行时间：2026-10-15 13:16:37Z–13:22:26Z（UTC）。nonce `f7549c600f26`，prefix `qa-b09-f7549c600f26-`。
自有 child Agent 主机：native-4（192.168.100.247）；自有帧代理运行在操作机 192.168.100.170。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| P21-03 | NOT_RUN | **PASS** | high | UI 改值 → 保存 → `已保存` toast；非法值 → 后端原文错误 toast 且不落库；同一 tick 连点 2 次保存 + 再点 1 次 → 均成功、无错误、状态一致 |
| P21-04 | NOT_RUN | **PASS** | high | UI 覆盖非法数字/0/负数/超大值/低于下限共 7 例，逐例断言“期望错误文案出现 + API 逐字段不变”；非法 Gateway URL 4 例经同一校验代码 400 且不落库；无部分写入 |
| P21-05 | NOT_RUN | **PASS** | high | 保存后硬刷新，UI 4 个输入框值与持久化一致；`GET /api/settings` 与 Hub DB `settings` 表一致，且等于保存前快照 |
| P21-06 | NOT_RUN | **PASS** | high | PUT 触发 Hub 向“在线自有 child”真实下发 `SETTINGS` 帧，payload 与保存值逐字段一致；4 个受保护 repair 节点变更期间保持 online/synced |
| FL-16 | NOT_RUN（旧基线 PARTIAL） | **PASS** | high | 心跳/Revision/日志上限三项设置均被 Agent **实际消费**并可用行为反向验证（详见下节） |

## 设置快照与恢复核验（本批硬性要求）

| 视图 | 手段 | 结果 |
|---|---|---|
| 管理 API | `GET /api/settings` | before == after（4 键逐字段相同，含 `changelog_window`） |
| Hub 存储 | `hub.db` 的 `settings` 表（`mode=ro` + `query_only`） | 4 个受管键值相同；非凭据键集合相同 |
| Hub 配置文件 | `/etc/nodesteer/hub-repair-20260930-101945.yaml` | sha256 `e594591f…` 前后一致；该文件不含运行期设置键 |
| Hub 单元 | `nodesteer-hub-repair-…` / `nodesteer-hub.service` MainPID | 未重启（本批 `hub_restarts=0`）；:8080 主 Hub MainPID 前后相同 |

- 快照文件：`evidence/settings-before.json`、`evidence/settings-after.json`；逐字段 diff：`evidence/phase12-settings-after-and-restore-diff.json`（`all_equal=true`）。
- 恢复路径：测试中先用 `PUT` 把 `heartbeat_interval_sec`/`revision_check_interval_sec`/`max_log_bytes` 改为 `3/5/2048`，最后**通过 UI 保存**恢复为 `30/45/1048576`；`run_b09.py` 的 `finally` 还会再比对快照并补写差异键（本次 `keys_to_fix` 为空）。
- Hub `settings` 是通用 KV 表，除运行期设置外还存 `cred:<node_id>`（每节点凭据）。自有 child 入网会新增一行、删节点后该行消失：本批独立复核（`evidence/independent-verification.json`）确认清理后全表键集合与开跑前**完全一致**。

## FL-16：Agent 实际消费证据（行为观测，不是配置回读）

自有 child 的**本地配置**故意写成 `heartbeat_sec=2 / revision_check_sec=2 / max_log_bytes=262144`，
因此任何非 2s 的观测节拍只可能来自 Hub 下发。

| 维度 | 观测 | 证据 |
|---|---|---|
| 连接期消费（Hub 值 30/45） | 132s 窗口：HEARTBEAT 4 帧、间隔 `[25,31,36]`s；REVISION_CHECK 2 帧、间隔 `[53]`s；对照本地 2s 配置应有约 66 帧 → 本地值被覆盖。含 `no_local_2s_cadence_burst` 守卫 | `evidence/phase3-baseline-settings-consumption.json` |
| 保存 3/5 后广播 | 帧代理抓到 hub→agent `SETTINGS`，payload `{heartbeat_sec:3, revision_check_sec:5, max_log_bytes:2048}` | `evidence/phase4-…json` |
| 保存后消费（稳态） | 排空前一日志 tick 后 34s：HEARTBEAT 9 帧、间隔 `[3,4,4,4,4,3,4,3]`s；REVISION_CHECK 6 帧、间隔 `[6,6,6,6,5]`s | `evidence/phase4-…json` |
| `max_log_bytes=2048` | 同一脚本真实输出 20022 字节 → Hub 行 stdout=**2048**、`stdout_truncated=true`；Agent 本地行 stdout_len=2048 | `evidence/phase6-max-log-bytes-truncation.json` |
| 恢复后反向复验 | 45s 窗口内仅 1 帧 HEARTBEAT（≈30s 节拍，若是 3s 节拍应有约 13 帧）；同脚本 stdout=**20022**、`stdout_truncated=false` | `evidence/phase10-post-restore-consumption.json` |

节拍判定按产品语义取带：Agent 周期循环基于 1s ticker，故 3s±20% 实际落点为 3–4s、5s±20% 落点为 4–6s；
另因 Agent 只在“触发后才重排下一次”，改值瞬间存在一次旧间隔（最长 36s/54s）尾巴，`phase4` 用 58s settle 将其排除，属测量方法而非放宽断言。

## 跨交付物说明（IDEMPOTENT-MANUAL）

- 同一串行批次中共用同一自有 child + 帧代理；**本批**负责创建并在 `phase13` 删除全部 fixture（`fixture-ledger.json`、`final-cleanup-oracle.json`）。
- api 交付物自带证据：`../qa-resume-20261014-api/idempotent-manual/evidence/phase3-idempotent-manual.json`
  （M1 新 UUID + 同一 Execution ID 重复投递 → Hub 2 次 ACK、仍 1 行；M2 新 UUID；扣留 ACK + 断链重连触发真实重传 → 仍 1 行、内容逐字节一致、本地 `synced` 由 false→true）。
- api 目录的 runner 说明见该目录 `README.md`。

## 自有资源清单（均已删除并复核 404 / 无残留）

| 资源 | ID | 结果 |
|---|---|---|
| child node | `f4794065-27fd-4048-8419-00bde6eb0425` | DELETE 200 → GET 404 |
| child agent_id | `agent-f30cf01a-9c79-4462-9463-541b95e6771d` | 进程已终止（PID 171953） |
| child root | `/var/lib/nodesteer-qa-b09-settings-f7549c600f26`（native-4） | `root_exists_after=false` |
| script | `68d15775-556c-4ef8-a972-4d85bc88ca95` | DELETE 200 → GET 404 |
| task | `0d782c2f-34a1-484f-808d-b1017470e873` | DELETE 200 → GET 404 |
| executions（历史留存，Hub 无 DELETE 接口） | `51c71e74-0edd-479e-a4fa-9c1c0ffea419`(2048 截断)、`cdc193fb-3d75-4b9d-bf72-9d111e287de1`(完整日志)、`3bb001ca-a447-4747-911d-f463d2ccad02`(M1)、`fddd3187-4008-4fdc-9929-9b9dc39e46ef`(M2) | 保留为历史 |

`fixture-ledger.json.status = CLEANED`；`final-cleanup-oracle.json.clean = true`。

## 边界与约束遵守

- 未停/起任何单元（`hub_restarts=0`），未使用 `virsh`，未重启任何 VM，未触碰 `:8080` 主 Hub 与任何 `nodesteer-agent*.service`（4 台节点的主 agent 与 repair agent 单元 MainPID 前后逐一相同）。
- 未改产品源码（`product_source_changes=0`），无 Git 操作（`git_operations=0`），无 Archive。
- 凭据仅经 0600 私有文件与管道读入，未落盘明文、未进 argv/stdout/证据；child 配置文件为 0700 root + 0600 config。
- `agent-browser` 仅使用本批自有 session（`qab09set1015`），收工前已 close。

## 未验证项 / 限制

1. 只对**自有** child 做了消费观测；4 个受保护 repair 节点只核验了“广播后仍 online/synced 且单元未重启”，未对其做节拍级插桩。
2. `agent_gateway_base_url` 在 Hub 中原本不存在，UI 因此不渲染该输入框；本批只验证了**非法** URL 走同一校验代码被 400 拒绝且不落库。**合法** Gateway URL 未写入，原因是 `settings` 表无删除接口、原始状态无该键可回滚，写入后无法恢复原状。
3. 设置变更的观测窗口为秒级（3/5/2048），未覆盖超长窗口（如跨日）下的漂移。
4. UI 侧未做多用户/无写权限角色的设置页只读验证（不在本用例维度内）。

## 失败轮次说明（均为 harness 缺陷，非产品缺陷）

| 轮次 | 结果 | 证据目录 |
|---|---|---|
| attempt01 | 前台调用被 harness 工具超时截断，`finally` 未执行 | `evidence/attempt01-tool-timeout/` |
| attempt02 | 自建帧代理 `Lock` 非可重入，收到首个 `SETTINGS` 帧时死锁（relay 卡死→child 掉线） | `evidence/attempt02-harness-proxy-deadlock/` |
| attempt03 | 心跳节拍窗口从改值瞬间起算（旧 tick 尾巴）+ 恢复判定把 `cred:` 行计入键集合 + api 目录层级少一级 | `evidence/attempt03-harness-assertion-calibration/` |
| attempt04 | 连接期 Revision 窗口短于两个最大间隔；oracle 受管键比较两侧键数不同 | `evidence/attempt04-harness-assertion-calibration-2/` |
| attempt05（报告） | 全部用例 PASS，`oracle_clean=true` | `evidence/` |

每轮均留有 `attempt-note.json`，含根因、修复与环境复位记录。attempt01/02 中断后立即用 `runners/abort_cleanup.py` 复位（设置回写 + child 清理 + 节点删除）；被截断期间全局设置曾短暂处于 `3/5/2048`，已即时恢复并复核。

## 产物

`case-result.json`、`progress.json`、`fixture-ledger.json`、`final-cleanup-oracle.json`、`run-evidence.json`、`evidence/`（含 before/after 快照与 `independent-verification.json`）。
可复现脚本：`runners/run_b09.py`（唯一编排入口，含 api 用例阶段）、`runners/frame_proxy.py`、`runners/childlib.py`、`runners/settings_snapshot.py`、`runners/ui.py`、`runners/hubapi.py`、`runners/node_units.py`、`runners/preflight_readonly.py`、`runners/verify_final.py`、`runners/abort_cleanup.py`。

## 独立复核

`runners/verify_final.py` 不依赖本批内存记账，重新读取环境与磁盘快照，产出
`evidence/independent-verification.json`：18 项检查全部为 true（`passed=true`），
含 API/Hub DB/配置文件三方设置一致、全表键集合复原、无 fixture 残留、child root 与进程消失、
4 节点 online/synced、受保护单元 MainPID 与 preflight 基线逐一相同、`/healthz`+`/readyz`=200。
