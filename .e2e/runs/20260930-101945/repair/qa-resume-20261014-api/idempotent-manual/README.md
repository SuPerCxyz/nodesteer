# idempotent-manual 运行记录（IDEMPOTENT-MANUAL / api）

用例：**IDEMPOTENT-MANUAL**（owner=api）
“每次 Manual Run 新 UUID 为新意图；相同 Execution ID/Key 重复投递/上传保持幂等”
权威端点：`http://192.168.100.209:18080`（repair，唯一管理写端点）。
执行时间：2026-10-15 13:16:37Z–13:22:26Z（UTC）。nonce `f7549c600f26`。

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| IDEMPOTENT-MANUAL | NOT_RUN | **PASS** | high | 手动 Run 每次拿到新的 Hub 生成 Execution UUID（M1 `3bb001ca…`、M2 `fddd3187…`，互不相同且各自在 Hub 恰 1 行）；对 M1 真实重复投递同一 `EXECUTION_FINISHED` 帧 → Hub 回 2 次 `EXECUTION_ACK`、行数仍为 1；对 M2 扣留 1 个 `EXECUTION_ACK` 后断链重连 → 产品真实重传同一 Execution ID，Hub 仍恰 1 行、stdout 逐字节一致，Agent 本地 `synced` 由 false → true |

## 证据与判定维度

`evidence/phase3-idempotent-manual.json`（8/8 维度 PASS）：

| 维度 | 观测 |
|---|---|
| `m1_success_and_marker` | M1 `SUCCESS`、stdout 含 own marker `QA:f7549c600f26:EXEC` |
| `duplicate_delivery_kept_one_hub_row` | 重复投递后 `GET /api/executions?task_id=…` 命中该 UUID 的行数 = 1 |
| `duplicate_delivery_acked_twice` | 自建帧代理观测到 2 个针对 M1 的 Hub→Agent `EXECUTION_ACK` |
| `m2_new_distinct_uuid` | M2 UUID ≠ M1 UUID（同一 task、同一 node、同一 params 的第二次 Manual Run） |
| `m2_success` | M2 `SUCCESS` |
| `ack_withheld_left_local_row_unsynced` | 扣 ACK 后读 Agent 本地 `state.db`（`mode=ro`）：`status=SUCCESS`、`synced=false` |
| `reupload_same_execution_id_observed` | 断链+恢复后同一 ID 的第 2 个 `EXECUTION_FINISHED` 帧 + Hub ACK |
| `reupload_kept_one_hub_row_and_identical_content` | Hub 行数仍 1，重传前后 stdout 完全相同 |

关键读数：M2 本地行 stdout_len=20022（与脚本真实输出一致）、`hub_rows_after_reupload=1`、`content_identical=true`。

## 共享上下文（重要）

本用例与 web 批次 `batch09-settings` 在**同一次串行运行**中执行，共用：

- 同一个自有 child Agent（native-4 192.168.100.247，repair 精确二进制 `d55dc5cd…`，0700 root + 0600 config）；
- 同一个运行在操作机的帧代理（同时提供 web 设置观测与 api 的重复投递/扣 ACK seam）；
- 同一套 Hub fixture（script `68d15775-556c-4ef8-a972-4d85bc88ca95`、task `0d782c2f-34a1-484f-808d-b1017470e873`、child node `f4794065-27fd-4048-8419-00bde6eb0425`）。

因此 **fixture 的创建/删除与 ledger 由 web 批次 `batch09-settings` 声明**：
`../qa-resume-20261014-web/batch09-settings/fixture-ledger.json`、`final-cleanup-oracle.json`
（`status=CLEANED`、`clean=true`，script/task/node 均 DELETE 200 → GET 404，child 进程与 root 已删除）。
本交付物不额外创建 Hub 资源（见 `fixture-ledger.json` 的 `no_additional_hub_resources_created_by_this_deliverable`）。

## 复现方式

编排入口在 web 批次：`../qa-resume-20261014-web/batch09-settings/runners/run_b09.py`
（`phase11_idempotent_manual` 即本用例，证据写入本目录 `evidence/`）。
共享模块：`childlib.py`（自有 child 生命周期）、`frame_proxy.py`（重复投递/扣 ACK/断链 seam）、
`hubapi.py`（repair :18080 客户端）。将这三个模块与 `run_b09.py` 放入同一 `PYTHONPATH` 目录即可运行。

## 边界与约束遵守

- 只使用授权 seam：重复转发 `EXECUTION_FINISHED`、扣留 1 个 `EXECUTION_ACK`、`cut`/`pass` 自有代理连接；
  对 Agent 本地库仅只读查询（`mode=ro` + `PRAGMA query_only=ON`），harness 对产品库 0 写入。
- 未停/起任何单元（`hub_restarts=0`），未重启 VM，未触碰 `:8080` 与任何 `nodesteer-agent*.service`。
- 未改产品源码，无 Git 操作，无 Archive，未输出凭据明文。

## 未验证项 / 限制

1. 仅覆盖 **manual** 触发的 Execution ID 幂等；Hub 侧定时（schedule）执行键的幂等由其他批次用例负责。
2. 断链时长为秒级（`cut` 4s 后 `pass`），未覆盖“超过心跳超时后节点被判 offline 再恢复”的长断链路径。
3. Hub 无 Execution DELETE 接口，`3bb001ca…`/`fddd3187…` 作为历史行保留在已删除的 owned task 名下。

## 独立复核

见 `../qa-resume-20261014-web/batch09-settings/evidence/independent-verification.json`
（`passed=true`，18 项检查，含设置复原、无 fixture 残留、受保护单元 MainPID 未变、Hub 健康）。
