# 本轮后端真实 E2E

`runner.py` 使用 Python 标准库 HTTP 和已授权客户机 stdin helper；凭据仅由 `/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/` 的 0600 JSON 加载，参数、日志与仓库不保存真实认证值。

`case-inventory.json` 保存逐项用例与真实结果，包含架构 §66 的 35 场景、§67 的 18 不变量。`pending` 是尚未执行，不能作为通过。由 root/环境单元执行的场景需要关联它们的运行证据，不以此 runner 的计划、源码或纯自检代替。

```bash
python3 .e2e/runs/20260930-101945/backend/runner.py --config /tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json --suite read
python3 .e2e/runs/20260930-101945/backend/runner.py --config /tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json --suite normal --allow-mutations
```

`--case` 可重复，逐个用例可以定点复测。应用服务窗口只允许 Agent1 本轮新登记 Unit，加 `--suite coordinated --case feature.application.lifecycle --allow-services`。正常 suite 不修改共享 Settings，不重启 Hub/Agent。

`isolated_runner.py` 精确绑定原 core Node `d940ab68-9f29-434b-bfe8-5d075a519edc` 和 `qa-20260930-101945-env-agent`，不会把后来添加的 Host/TLS/Docker/Compose Agent 当作目标。Agent state/Journal 从本轮 Docker 卷只读 SQLite 查询，容器内不需要安装 Python。生命周期和 Hub 故障仅在 root 已授权的窗口执行。

```bash
python3 .e2e/runs/20260930-101945/backend/isolated_runner.py --config /tmp/nodesteer-e2e-20260930-101945-7wksl0_x/isolated.json --allow-hub-faults --case arch.hub_disconnect_during_execution
```

`ws_proxy.py` 在本轮 Hub VM 的 18084 端口透明转发 WebSocket/HTTP，HTTP 和 Agent 文件 URL仍访问真实 core Hub。代理只修改小型、完整、未压缩的指定控制帧；压缩/分片帧原样透传并记录注入限制。HELLO/凭据、大 Payload 不打印。掩码/长度纯自检仅验证测试工具，不计项目 E2E。

结果与恢复入口：

- `results.jsonl`：所有尝试的不可丢失记录，每次复测单独证据文件。
- `all-suite-summary.json`：完整primary/core独立case去重结果；`latest-summary.json`只代表最后一次CLI批次。
- `web-system-mapping.json`：134个系统case的逐维状态、真实证据与剩余；复合case不因部分PASS抹掉实测FAIL。
- `normal-summary.json`：primary 去重结果、已重测成功的 harness 失败、精确清理 ID。
- `failures.json`：已实测的产品缺陷；harness/环境干扰不混入。
- `ledger.json`、`isolated/ledger.json`：两个 Hub 各自的资源归属，不能跨 Hub 清理。
- `isolated/progress.jsonl`：每个 case/API/控制命令前后断点，含精确容器/卷/Node ID。
- `isolated/proxy-info.json`：本轮代理 PID、控制/日志路径、原 Hub URL，恢复时据此精确关闭自己的进程并恢复 Agent URL。
- `isolated/interrupted-baseline.json`：env 同 IP 身份修复期间的污染断点，不是产品失败。

清理按 `schedule → task → application → artifact → group → script` 的精确 ID 顺序；应用清理会停止自己的 Unit，需要服务窗口。Execution/Audit/Transfer 历史没有 DELETE API，保留归属记录。缺卷场景仅创建/删除替代测试卷，原 core 卷始终保留；引入的新身份单列给 root，避免误删除别的环境 Agent。

首次两项字段适配（`/api/me` 的 `Role`、成功退出码 0 被省略）、代理 upstream loopback、离线过滤误选 Host、校验周期 45 秒大于原 40 秒等待等旧尝试必须标为 harness 修正后重测，不能算未修复产品缺陷。运行中断线的在线启动 Manual Execution 保持初始 `offline=false`，应验证 `synced=false` 持久化、真实业务效果与恢复后 ACK/日志补传；不要擅自增加 `offline` 字段语义。

本轮最终primary/core定义按ledger精确清理，历史Execution/Audit保留；Transfer通过产品Cancel释放Stage、保留原终态验证证据。新增Viewer账号API无DELETE，最终为viewer并注销。`primary-cleanup-result.json`、`primary-stage-cleanup-result.json`、`isolated/cleanup-result.json`与`isolated/cleanup-reconciled-definitions.json`记录实际清理；无Name的Schedule按其task_id归属复查。原core容器/卷、精确generatedDocker目标由Root/Env统一删除。代理原地址已恢复、测试锁仅15秒BEGIN IMMEDIATE→ROLLBACK，无数据/DDL写入，进程/远端QA文件已移除。

环境同IP修复期间Exec `8b125d68-63fd-4014-8102-b1a8b21a3bc4`实际精确Cancel后仍RUNNING、原Journal无记录；相关本轮Task定义已通过API清理，历史不直接修写，见`isolated/pollution-cancel-result.json`。
