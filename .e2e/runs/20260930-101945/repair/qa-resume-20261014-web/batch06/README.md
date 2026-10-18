# web batch06 — 节点状态操作 / 分组联动 / 离线节点行为

- **Authority**：`http://192.168.100.209:18080`（repair，唯一写端点；primary `:8080` 全程未接触）
- **Batch id / nonce / owned prefix**：`qa-resume-20261014-web-batch06` / `705d5f` / `qa-w1410b06-705d5f-`
- **用例**：P04-04、P04-05、P11-04、P06-08、P17P18-14（web owner，canonical 均为 NOT_RUN）
- **UI**：agent-browser 0.32.4，session `qa1410b06`（`--allowed-domains 192.168.100.209`）
- **结果**：`results.json`；过程：`progress.json`；夹具：`fixture-ledger.json`；收尾：`final-cleanup-guard.json`；证据：`evidence/`

## 关键约束遵循

- **4 个 protected native repair 节点全程零状态变更**（未改 maintenance / set online / 删除）；节点状态类用例全部在
  **自建 owned child**（native-2 `192.168.100.206`，修复版 agent 二进制 SHA `d55dc5cd…`）上完成。
- 需要「离线」时：停/起 owned child 进程（P04-05、P11-04 variant B），或经 harness frame proxy 切断其与 Hub 的
  链路而保持进程运行（P17P18-14 的离线执行）。
- 夹具：owned child node ×1、owned static group ×1、owned script ×1、owned task ×2（node target / group target）、
  owned schedule ×5（全部 DELETE 并 GET404）。未创建/删除 User、Application、Artifact；无真实 deploy。
- Execution 历史不可删，本批留下 5 条（见 `evidence/preserved-executions-readback.json`）。

## 文件

| 文件 | 说明 |
|---|---|
| `run_batch06.py` | 主 runner：guard → owned child → fixtures → 5 个用例 phase → cleanup → final guard |
| `reevaluate_assertions.py` | P11-04 / P06-08 断言重推导（仅基于已捕获证据，未重跑操作） |
| `complete_evidence.py` | 用真实审计字段回读节点状态审计行、执行历史回读、环境 observation |
| `account_writes.py` | 服务端写入核算（审计日志为准） |
| `runners/hubapi.py` | :18080 客户端（端口硬门禁 + 0600 凭据文件校验，凭据不落盘） |
| `runners/child_agent.py` | native-2 上的 owned child 生命周期（含 PID 身份守卫：仅 kill `exe==agent 二进制 且 cmdline 含本批 config` 的进程） |
| `runners/frame_proxy.py` | owned child 与 Repair Hub 之间的帧代理（cut/restore、丢 ACK、复制 EXECUTION_FINISHED） |
| `runners/login_fill.py` | 登录凭据经 stdin 管道注入表单（不进 argv / stdout / evidence） |
| `evidence/` | phase 证据 JSON + 12 张截图 + observations + 重推导说明 |

## 用例结论（详见 results.json）

| 用例 | 结论 | 核心证据 |
|---|---|---|
| P04-04 | PASS | UI 操作菜单 维护/恢复/设为在线 → `POST /api/nodes/{owned}` 200 → Hub 状态与徽标同步；maintenance 期 Hub 调度产出 `SKIPPED`+`node paused`，恢复后同型调度产出真实 `SUCCESS`；Agent 暂停期本地 allow_offline 调度不触发、恢复后补跑 |
| P04-05 | PASS | owned child 停机 → 断开路径置 `offline`、徽标「离线」；UI「设为在线」→ 200 → `online`；重启后收敛回 online+synced |
| P11-04 | PASS（断言重推导） | 真离线：UI 立即运行 → HTTP 400 `node … is not online (status=offline)`，无新增执行行；在线无会话：立即 400 且留一条 `FAILED`+`agent offline`；两处 toast 见截图 |
| P06-08 | PASS（断言重推导） | 分组编辑 UI 勾选/取消 → `PUT /api/groups/{id}` 200；Node Detail「任务」区随成员同步、概览「分组」字段同步；group target 真实派发 `SUCCESS`；移除成员后手动运行 400 `no target nodes` |
| P17P18-14 | PASS | 断链期间 Agent 本地执行 `offline=true, synced=false`（Hub 无行）；恢复上传后 Hub 单行 `offline=true`；丢弃首个 ACK 后重启 Agent 重传同一 Execution ID（frame 层见 2 次上传）→ Hub 仍单行、journal 收敛 `synced=true`；UI 执行详情「离线 是 / 已同步 是」 |

## 复现

```bash
cd .e2e/runs/20260930-101945/repair/qa-resume-20261014-web/batch06
python3 run_batch06.py            # 约 10 分钟；结束时自动 cleanup + final guard
```

前置：`/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json`（0600）、到 `192.168.100.206` 的 root SSH。

## 已记录的 harness 问题与观察

见 `evidence/observations.json`（OBS-B06-01..06）与 `evidence/assertion-reevaluation.json`。
