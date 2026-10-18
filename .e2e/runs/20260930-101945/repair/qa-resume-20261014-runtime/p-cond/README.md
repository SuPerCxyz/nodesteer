# p-cond 运行记录（条件引擎：Local metric/operator 矩阵、AND 组合、Remote/TTL Fail Closed）

批：`p-cond`（P-COND：条件 / 指标 / Remote / Local 维度收口）
用例：SYS-25、SYS-28、SYS-29、SYS-30、E2E-09、E2E-10、INV-09
权威端点：`http://192.168.100.209:18080`（repair；管理写仅走该端点，`:8080` 零写入）
调度目标：仅 `native-1`（`91f44866-…`，192.168.100.204）
nonce：`0c3335fd`，fixture 前缀 `qa-pcond-0c3335fd-*`
执行时间：2026-10-15 07:13Z–07:18Z（另含 07:22Z 只读二进制取证）

## 结论

| 用例 | 原状态 | 建议状态 | 置信度 | 摘要 |
|---|---|---|---|---|
| SYS-25 | PARTIAL | **PASS_WITH_NOTES** | high | 8 metric × 6 合法 operator 全矩阵 + false/unknown + AND 单层/嵌套（66 用例全 PASS）；仅 AND+Remote 组合受 SYS-27 阻塞 |
| SYS-30 | PARTIAL | **PASS_WITH_NOTES** | high | 34 条不满足路径全部 SKIPPED、无副作用、UI 显示阻塞原因；Remote「不满足」受 SYS-27 阻塞（观测为 BLOCKED） |
| E2E-09 | PARTIAL | **PASS** | high | 满足→真实执行、不满足→SKIPPED、未知→BLOCKED（3 类独立成因）+ 2 层嵌套 + UI 完整反馈全部实测 |
| E2E-10 | PARTIAL | **PARTIAL** | high | 13/13 Remote 全 BLOCKED（无副作用）；正向路径受 SYS-27 缺陷阻塞；TTL 过期不可构造；恢复未运行 |
| SYS-28 | PARTIAL | **PARTIAL** | high | 真实 SUCCESS/FAILED 历史控制组已建立；「不存在」fail-closed 通过；成功/失败/false 比较受 SYS-27 阻塞 |
| SYS-29 | PARTIAL | **PARTIAL** | high | 未观察/真实断连 fail-closed 通过；未过期不可用、过期不可构造（缓存路径无写入方） |
| INV-09 | PARTIAL | **PASS_WITH_NOTES** | medium-high | 未知远程状态→BLOCKED 且无业务效果（2 条独立证据）；但区分度受 SYS-27 削弱（确定性状态也被判未知） |

## 关键实测数据

- **时钟偏移（独立复测）**：repair Hub 主机时钟领先 native-1 约 **+0.1464 s**（中位数，12 组交叉采样，
  范围 0.086–0.150，方法：两端 `date -u +%s.%N` 折算到同一瞬间）。
  与 `sys-b09` 的 +0.14 s 一致，佐证 SYS-27「observed_at 恒在未来 → 恒 Fail Closed」。
- **执行统计**：116 条真实执行（66 local 矩阵 / 7 AND / 13 Remote / 30 重复性对照）
  → SUCCESS 51、SKIPPED 34、BLOCKED 31。
- **副作用 oracle**：51 次 SUCCESS 的 marker 文件时间戳全部落在各自执行窗口内；
  65 次非 SUCCESS 全部无 marker、无 stdout。
- **block_reason 分布**：`condition not satisfied`（SKIPPED 28）、
  `condition unknown: <nil>`（14，含 13 Remote + AND+Remote）、
  `condition unknown: exit status 3 / 127`（4）、`condition unknown: no such file or directory`（1）。
- **重复性**：5 个代表用例 × 6 次 → 终态完全一致。
- **UI 完整反馈**：4 个 Execution 详情页（成功/已跳过/本地已阻断/远程已阻断）
  + offline 节点页 + 远程条件任务页，状态标签、阻塞原因、空输出、执行 ID、节点名均匹配。
- **二进制取证（只读）**：`:18080` 监听进程 `/proc/5952/exe` →
  `nodesteer-hub-static-login-guard`，SHA `164ea4ac…`（同目录 `nodesteer-hub` `e0b120e6…`
  并非服务进程）；native-1 Agent `/proc/1494/exe` SHA `d55dc5cd…`。

## 缺陷与观察

- **SYS-27 缺陷复现（已知 P0，非本批新发现）**：13 条 Remote 条件全部 BLOCKED。
  其中 11 条属「正向/负向本应可判定」的维度（含对**确定性状态**的判定，如 offline 节点
  `online == offline` 本应 SUCCESS），因此可确认失败点是时间戳新鲜度门禁而非数据缺失。
  本批未主张修复方案、未改产品源码、未通过放宽断言或人为调时钟绕过。
- 观察 1（medium，非缺陷）：Hub 远程状态缓存分支不可达 —— `SetRemoteState` 唯一调用点是
  `gateway.handleRemoteState`（处理 Agent 上报），而仓库内无任何 Agent 侧发送 `MsgRemoteState`
  的代码路径，故 `resolveRemoteState` 恒走实时分支，「TTL 过期」无法经真实产品路径构造。
- 观察 2（low，非缺陷）：`file_exists`/`dir_exists`/`process_exists`/`port_listening` 的
  `>`/`<`/`>=`/`<=` 走非数值字典序回退（如 `file_exists > false` 判为满足），
  Hub 校验器按 metric 无关的白名单放行全部 6 个 operator。

## 安全边界遵守

- 未重启 repair Hub；未停/启任何 repair/primary Agent；未触碰 `nodesteer-agent.service`；
  未重启任何 VM（未使用 `virsh reboot`）；`:8080` 零管理写。
- 未启动任何 owned child 进程 / proxy / 注入服务；测试结束无残留进程。
- 未改产品源码；未执行任何 Git / Archive 操作。
- 仅只读引用环境中既有的 offline 节点作为 Remote 目标（未修改、未重启其 VM）。

## 产物

- `case-result.json`：7 用例逐维度判定、缺陷复现、观察项、安全边界、harness 错误、未验证项。
- `progress.json`、`fixture-ledger.json`（13 个 owned task 全部 DELETE→GET404 + 1 个 host 目录已删）、
  `final-cleanup-oracle.json`（残留、节点、单元、Hub 设置、健康）。
- `evidence/`：`cases.jsonl`（逐用例原始记录）+ 分阶段 JSON + 7 张 UI 截图。
- 可复现脚本：`pcond_run.py`（主流程，分阶段可重跑）、`finalize_pcond.py`、
  `live_binaries.py`、`recon.py`。
