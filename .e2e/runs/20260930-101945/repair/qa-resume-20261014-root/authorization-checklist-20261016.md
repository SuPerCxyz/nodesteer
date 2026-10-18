# NodeSteer E2E 待授权清单（一次性授权用，2026-10-16）

> 性质：可勾选的授权清单。本文件为本次任务**唯一写入**；未修改源码/配置/台账，未访问远程环境，未运行测试。
> 主源：`partial-closeout-plan-20261016.md` 的 **C2 段（40 条）**；用例 `title`/剩余缺口取自台账 `.e2e/runs/20260930-101945/repair/feature-inventory.json`；`pending-decisions-20261016.md`（A 节 12 条、G 节 CR-1/CR-3）仅作交叉引用，不重复列项。
> 用法：每组一个授权项（一个复选框）。勾选即表示同意该组"要做什么"的范围；如需部分批准，可按组内"批次划分"逐批勾。

---

## 0. 现状与口径更新（读清单前必看）

| # | 事项 | 说明 |
|---|---|---|
| 0.1 | C2 40 条中 **1 条已不需要** | `P09P11-10` 台账已 **PASS**（旧方案将其列为 C2 第 29 条，已过期）。 |
| 0.2 | C2 40 条中 **1 条已 FAIL** | `G04-01` 台账已转 **FAIL**；它仍需裁决（修复/豁免），故保留在 G7。 |
| 0.3 | 两条**归因过期**（方案依据已被独立核验推翻） | `X08`：实际对应新观测 `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED`（Group/Script 创建表单），`WEB-TASK-PENDING-SAVE` 已 regression PASS；`X14`：`WEB-EXEC-SEARCH-PAGE` 已 regression PASS，跨页搜索维度已被 SAFE-02/GAP-07b 覆盖，实际只缺"批量选择漂移/跨页保持"。来源：`status-upgrade-verification-20261016.md`。 |
| 0.4 | `X16` 的缺陷已是 regression PASS，但**遮蔽矩阵从未按用例逐项执行** | 本清单把 X16 列为"安全复测"（补测），不是"复现缺陷"。 |
| 0.5 | 本清单实际可执行条数 | **39 条**（40 − `P09P11-10`）+ `G04-01` 已 FAIL（同一授权项）。 |

## 0.1 红线（授权通过后仍不放松，逐条约束每一批）

1. 不得改 4 个 native repair 节点（native-1 `.204`／native-2 `.206`／native-3 `.222`／native-4 `.247`）的**节点状态**；节点状态类一律用 owned child（自有 `qa-*` Agent 进程）。
2. 不得 `pull`/`rebuild`/`retag` docker 镜像；镜像只能按 digest 导入/删除。
3. 不得用 privileged/unconfined 绕过 AppArmor；Host 集成族维持 BLOCKED（`FAIL-ENV-HOST-001`）。
4. **不得在共享 repair 权威（`:18080`）创建持久化用户/账号**（产品无删除用户 API）。账号动作全部走隔离实例。
5. 不触碰 primary hub `:8080`、`nodesteer-hub.service`、`nodesteer-agent.service`（含 `…-repair-…-native-N.service`）；不重启 VM（repair 单元 `enabled=disabled`，D2）。
6. 不改产品源码（除 G7 裁决为"修复"后**另行授权**）；不做 Git／Archive；不出真实凭据入证据。
7. 浏览器会话只用 `--session <自己的>`，禁用 `agent-browser close --all`（D6）；tmpfs/内存类注入先升气球（D3）。

## 0.2 环境坐标（各批通用）

| 对象 | 坐标 |
|---|---|
| repair 权威 | `http://192.168.100.209:18080`，单元 `nodesteer-hub-repair-20260930-101945.service` |
| 当前修复版 Hub 二进制 | `/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard`，sha256 `cd8b7f9d…`（见 `qa-resume-20261016-verify/deploy-evidence.json`） |
| owned child 先例 | `qa-resume-20261014-runtime/p-sched/`（`child_agent.py`、`frame_proxy.py`、`hubapi.py`、`verify_cleanup.py`），运行于 native-2，`qa-*` 前缀 |
| 帧注入 harness 先例 | `qa-resume-20261010-runtime/sha-prune/ws-frame-proxy/`（sys-b02/03/04/05/07 同类） |
| 隔离实例模板 | `qa-resume-20261014-web/p-table/instance-ledger.json`（注：模板用旧二进制 `164ea4ac…`；本次应改用当前修复版 `cd8b7f9d…`） |
| Docker pinned 镜像 | `sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44`（`nodesteer-qa/agent-host:f174890c3846`），native-4 daemon |

---

# 一、授权项（按授权类型分组，共 9 组 / 39 用例）

## G1 隔离可销毁实例类（7 条）

- [ ] **授权项**：按 2026-10-15 p-table 先例，在 `192.168.100.209` 上临时启动 **1 个可整体销毁的隔离 Hub 实例**（当前修复版同版本二进制 + 独立 config/data/log/端口），并在**该实例内**创建 owned 账号（admin/operator/viewer）与注册 1 个 owned child Agent；用完整体销毁并核对零残留。
- **覆盖用例（7）**：`P04-06`、`P14P15-12`、`P21-08`、`P20-04`、`X13`、`P03-09`、`P19-03`

| 用例 | 台账缺口（摘） |
|---|---|
| P04-06 | operator/viewer 点击受限操作的 UI 可见性/403/状态不变 —— 无受限角色凭据 |
| P14P15-12 | Admin/operator/viewer 的部署、定义修改权限 —— 无可清理受限账号 |
| P21-08 | Operator/Viewer 读取、保存按钮策略与 API 403 |
| P20-04 | 创建成功/重复用户名/错误输入（并复核 `MAIN-USER-EMPTY-PASSWORD`） |
| X13 | 跨页缓存、两会话编辑、Token 替换后保护页面与实际收敛时间 |
| P03-09 | no-app／no-failed-execution 空态（需真空权威） |
| P19-03 | 执行/取消/部署/升级/节点状态/**角色密码修改**六类动作审计（角色段与本组同源） |

- **要做什么**：
  1. 启动（transient，不写 systemd 单元）：`runuser -u nodesteer -- setsid nohup <当前修复版二进制> --config /etc/nodesteer/qa-<nonce>.yaml`；数据目录 `/var/lib/nodesteer-qa-<nonce>`（0750 nodesteer）、日志 `/var/log/nodesteer-qa-<nonce>.log`、端口 `18083`（本地隧道 `38283`）、凭据文件 `/tmp/opencode/<nonce>-creds.json`（0600）。
  2. 实例内建 admin 之外的两个受限账号（operator/viewer）+ 1 个 owned 应用/部署单元 + 把 owned child 注册到该实例（`P14P15-12`、`P19-03` 需要）。
  3. `P03-09`：以新实例初始数据目录直接取"零应用/零执行/零失败执行"空态。
  4. `P20-04`：创建/重复用户名/错误输入；并复核 `MAIN-USER-EMPTY-PASSWORD`（P1，`main/failures.json`）在修复版是否已修（空密码应被拒）。若仍可空密码登录 → 新缺陷候选，如实记录。
  5. `X13`：两个浏览器会话 + 另一标签退出/Token 替换，记录保护页面与实际收敛时间（长窗观察）。
  6. `P19-03`：在本实例内改角色/改密码并抓审计行；执行/取消/部署/升级/节点状态段用自有任务与 owned app/child（消费 G5 授权）。
  7. 收尾：销毁（`pkill -TERM/-KILL -f qa-<nonce>` → `rm -rf` data → `rm -f` config/log → 终止本地隧道与浏览器 auth → 删凭据文件），逐项复核 `PGREP=0 / PORT_LISTEN=0 / DATA_DIR-CONFIG-LOG 不存在`，并断言 repair `:18080`、primary `:8080` 未受影响。
- **风险与回滚**：风险 = 误绑 repair 端口、误写 repair 数据目录、残留进程/端口/凭据。规避 = 启动前 `ss -ltnp` 选空闲端口；所有路径带唯一 nonce；先记录 repair/primary 的 PID 与端口基线。回滚 = **整体销毁即回滚**（实例本身可丢弃），销毁后按 `instance-ledger.json` 的 `remote_checks` 口径逐项复核并留档。
- **我的推荐**：**批准** —— 与 2026-10-15 已成功执行、已验证 `ISOLATED_INSTANCE_DESTROYED_AND_VERIFIED` 的同一模板，零持久化残留，是这 6–7 条（含 P0 的 `P14P15-12`）唯一可行路径；且共用权威上"不建账号"的红线因此得以维持。
- **批次划分（2 批，共用同一实例、串行，全部完成后一次性销毁；该实例独占）**：
  - A 批（4 条）：`P04-06`、`P21-08`、`P20-04`、`P03-09`（三个账号一次建成）
  - B 批（3 条）：`P14P15-12`、`X13`、`P19-03`（含长窗与角色/密码审计；需 G5 的 owned app/child 配合）
  - 可选并入：`X14`（见 G9）在同一实例内完成 → 本组共收口 8 条。

## G2 协议帧注入／断连注入类（3 条）

- [ ] **授权项**：沿用既有 `ws-frame-proxy` harness，对 **owned child 自有 websocket 信道**做帧级伪造/延迟/切断（重复、丢失、乱序、延迟注入），不改产品代码、不影响 protected 节点与既有 Agent。
- **覆盖用例（3）**：`P11-08`、`AR-08`、`P17P18-03`

| 用例 | 台账缺口（摘） |
|---|---|
| P11-08 | 同 Execution ID/Key 的重复投递/上传幂等 —— 无 UI/HTTP 触发路径 |
| AR-08 | duplicate/lost/reordered 通知帧与调度幂等未注入验证 |
| P17P18-03 | PENDING（等待中）无真实样本 |

- **要做什么**：用 `qa-resume-20261010-runtime/sha-prune/ws-frame-proxy`（或 `p-sched/frame_proxy.py`）代理 owned child 连接：`P11-08` 重放同 Execution ID/Key 的投递帧，断言无第二条 Execution、无双写、幂等（负向：乱序后仍收敛）；`AR-08` 注入 duplicate/lost/reordered 通知帧，断言恢复后幂等、Desired State 与实际一致；`P17P18-03` 在投递前制造可控延迟窗口采样 UI "PENDING" 行（**以 2 次尝试为限**）。帧账目必须在同一 proxy 实例内串行。
- **风险与回滚**：proxy 挂掉只切断 owned child（不影响产品 Agent/节点）；注入仅作用于自有信道。回滚 = 恢复直连 → 核对 4 个 native 节点 `online/synced`、无残留进程/目录、无新增 Execution → 关闭 proxy。
- **我的推荐**：**批准** —— 是 2026-10-14 已授权 sys-b02/03/04/05/07 同类操作的直接延续，新增风险低。附加建议：`P17P18-03` 若 2 次尝试仍无稳定样本，转 C3 口径接受（不强求）。
- **批次划分（1 个独占窗口，3 条）**：`P11-08`、`AR-08` 串行（同 proxy 帧账目）；`P17P18-03` 可用另一 owned child 并行。**不得与 G3 的窗口批同时开**（同用 owned child/信道）。

## G3 调度停机／离线／补偿窗口类（3 条）

- [ ] **授权项**：允许对 owned child（自有 Agent 进程）做**停用/断连/恢复**与 **≥1 个 misfire 窗口的补偿复现**；不得改 protected 节点状态、不得停 repair/生产服务。
- **覆盖用例（3）**：`P12P13-06`、`P12P13-07`、`P12P13-08`

| 用例 | 台账缺口（摘） |
|---|---|
| P12P13-06 | Hub-Owner vs Agent-Owner 的真实调度执行差异未验证 |
| P12P13-07 | `allow_offline` × `hub_online_required` 组合的实际投递行为未验证 |
| P12P13-08 | `run_once`/`skip` 漏触发真实场景（停机后补偿）未验证 |

- **要做什么**：owned child 上建自有 schedule（hub-owner 与 agent-owner 各 1，非双触发已由 SYS-19/20/21/INV-06 证明，本批只补"执行差异"）；`P12P13-07` 在 owned child 断连/停机期间投递，观察 allow_offline×hub_online_required 四象限行为；`P12P13-08` 停机跨过 ≥1 个 slot 后恢复，断言 run_once 补偿/skip 语义与 UI 反馈。**若确需 Hub 侧停机观测，改在 G1 隔离实例内做，不动 repair Hub。**
- **风险与回滚**：停机仅限 `qa-*` 自有进程（不碰 `nodesteer-agent-repair-*.service`）。回滚 = 恢复进程 → 核对 `online/synced`、无重复 Execution、无残留 → 留档。执行前建议只读复核：`P12P13-08` 与已 PASS 的 `E2E-06`（"Agent 错过 slot 的 SKIP/RUN_ONCE 与恢复"）存在口径重叠，若证据可直接覆盖则该项移出本批（省一个窗口）。
- **我的推荐**：**批准** —— 全部动作在自有进程范围，无 protected 影响。
- **批次划分（1 个独占窗口，3 条）**：三条串行（同一 owned child 的 schedule 状态会互相干扰）；与 G2 串行。

## G4 Docker／Compose 独占窗口类（2 条）

- [ ] **授权项**：允许在 **native-4（`192.168.100.247`）docker daemon** 上用**按 digest 导入的 pinned 镜像**重建 `qa-*` 容器/卷/网络，做 Compose 重建与恢复验证；用完删除容器/卷/网络与导入镜像、还原 daemon baseline；不得 pull/rebuild/retag。
- **覆盖用例（2）**：`E2E-18`、`AR-04`

| 用例 | 台账缺口（摘） |
|---|---|
| E2E-18 | Compose 形态重建与 B 恢复后 UI 读回仍缺 |
| AR-04 | 持久化子维度可复用；**幂等与 Desired State 一致性**维度未覆盖（含 attempt02 desired 1573 vs 本地 1575 的未解释差异） |

- **要做什么**：镜像 `sha256:ecd4dd9f…`（`nodesteer-qa/agent-host:f174890c3846`）；若 native-4 daemon 缺该镜像，按 sys-b13 先例**只读**从 native-2 `docker save | docker load` 导入（核对镜像内 agent 二进制 sha）；建 1 个 `qa-*` Docker Node 接入 repair 权威；`E2E-18` 做 Compose 重建 + B 恢复后 UI 读回（Identity/Revision/Journal/Cache）；`AR-04` 做重建后幂等 + Desired State 对账并解释 1573/1575 差异。收尾 `docker rmi` 还原 baseline。
- **风险与回滚**：独占 native-4 docker（窗口内不并行任何 native-4/docker 操作）；容器/卷/网全部 `qa-*` 前缀，删除后复核不存在。回滚 = 删除自有容器/卷/网 + `docker rmi`（先例已验证）。
- **我的推荐**：**批准**（2 条 P0，独占小窗口，先例完整）。**附带提示**：当前镜像内 Agent 为旧构建 `d55dc5cd…`；若要求 Docker 形态也跑修复版 Agent，属"跨构建版本号/替换容器内二进制"议题，**需另行单列授权**，本项不含。若不愿动 native-4 daemon，可换隔离实例 + 本地 docker 复现，但 Compose/Desired State 语义会与 repair 环境有偏差（不推荐）。
- **批次划分（1 个独占窗口，2 条）**：整批串行；窗口内不并行其他 native-4 操作。

## G5 自有夹具批类（任务/脚本/分组/条件/标签/应用）（14 条）

- [ ] **授权项**：允许在 repair 权威（`:18080`）创建 **owned `qa-*` 前缀夹具**：任务（四类型）/脚本/分组/条件/标签 + 1 个 owned 应用及其分配，并**在 owned child 上写节点标签**；测完按前缀清理并出 cleanup oracle；不得改 protected 节点状态、不得建持久化用户。
- **覆盖用例（14）**：`P09P11-07`、`P09P11-08`、`P09P11-09`、`P09P11-11`、`P09P11-12`、`P09P11-15`、`P09P11-16`、`P09P11-17`、`X09`、`X11`、`FL-02`、`FL-03`、`FL-04`、`P14P15-09`

| 用例 | 台账缺口（摘） |
|---|---|
| P09P11-07 | app_deploy 应用选择控件逐项 + 真实部署执行 |
| P09P11-08 | app_operation start/stop/restart/upgrade 四操作逐项与执行 |
| P09P11-09 | 单节点 / 去选节点的单独断言 |
| P09P11-11 | 标签匹配节点变化（**需 owned child 节点标签写**） |
| P09P11-12 | 类型/目标切换后 payload 无旧字段的写侧证据 |
| P09P11-15 | 8 metric × 6 operator 枚举与条件设置/清除实际效果 |
| P09P11-16 | Remote 条件 last_execution/task_id 与节点校验 |
| P09P11-17 | AND 结构的实际结构与执行效果 |
| X09 | 四类型往返隐藏字段（script_id/application_id/app_operation）与 Node/Group/Label 往返 |
| X11 | 条件回显、AND 未设置语义、清除最后一个条件后的真实执行/阻断 |
| FL-02 | Agents→Agent Detail→Task 过滤/回链/目标一致 |
| FL-03 | Label→Label Group→Task Target→标签改变后的跨页同步 |
| FL-04 | Script CRUD→Task→副本独立/Revision/SHA/引用 |
| P14P15-09 | Node Detail Managed Applications → 应用详情回链点击 |

- **要做什么**：四小批（见批次划分），统一 `qa-<nonce>-` 前缀；真实执行会在权威留下**不可删除的 Execution/Audit 行**，需在报告中登记条数；对 protected 节点**只读/只选择**，节点标签写只针对 owned child；结束跑 `verify_cleanup.py` 式 oracle：自有对象 0 残留 + 4 节点 `online/synced` + 无 `qa-*` 进程/目录。
- **风险与回滚**：风险 = 共享权威持久对象残留、误对 protected 节点执行状态类动作、误点破坏性批量。规避 = 前缀 + 逐条 DELETE 回读 404（p-sched 先例口径）；批量/状态类动作前先取消并核对 0 写。回滚 = 按前缀删除 + oracle 复核。
- **我的推荐**：**批准** —— 单笔收益最大（14 条），且 p-app/p-cond/p-sched/sys-b06 已有同型先例与清理口径。
- **批次划分（4 小批，3–4 条/批；E5-1/E5-2 可并行，E5-3/E5-4 视 owned child 归属错开）**：
  - **E5-1 条件批（4 条）**：`P09P11-15`、`P09P11-16`、`P09P11-17`、`X11`
  - **E5-2 应用批（3 条）**：`P09P11-07`、`P09P11-08`、`P14P15-09`
  - **E5-3 任务目标批（3 条）**：`P09P11-09`、`P09P11-12`、`X09`
  - **E5-4 标签跨页批（4 条）**：`P09P11-11`、`FL-02`、`FL-03`、`FL-04`（独占 owned child 的标签写）
  - 约束：并发 ≤5；E5-4 与 G3 调度窗口错开（同 owned child 资源）。

## G6 长窗／多次执行独占 + 不可删除 Transfer 历史类（3 条）

- [ ] **授权项**：允许 `X18`/`X12`/`X15` 的**独占长窗**：批量部分成功与 Assign 失败注入+重试、分配变化后多次真实执行对照、日志分片与断线恢复观察；并**接受其中新增的不可删除 Transfer/Execution 历史记录**（登记计数变化）。
- **覆盖用例（3）**：`X12`、`X15`、`X18`

| 用例 | 台账缺口（摘） |
|---|---|
| X18 | 批量部分成功/失败、定义成功 Assign 失败可定位重试、重试不破坏已成功目标 |
| X12 | 分配变化→跨页与下次执行、空目标/重叠组/离线/禁用/维护/**Capability 失配** |
| X15 | PENDING→RUNNING→终态免刷新、日志分片、断线恢复后 Follow/搜索/流过滤/Copy |

- **要做什么**：`X18` 用可控失败目标（ENOSPC/非白名单，沿用 P22-11/P22-14 方法）注入一成功一失败 + Assign 失败重试；`X12` 用标签/分组/应用分配 fixture 做多次真实执行对照（Capability 失配可复用 G4 的 docker 节点）；`X15` 长时运行 + 断线注入观察日志分片与恢复后 Follow/搜索/过滤/Copy。Transfer/Execution 历史不可删 → 报告中登记新增条数。
- **风险与回滚**：长窗独占（期间不并行同节点批次）；断线注入仅限 owned child；tmpfs/内存类注入先升气球（D3）。回滚 = 恢复 owned child 直连、清理自有夹具、登记历史计数。
- **我的推荐**：**批准，但建议排在 G1/G5/G2-3 之后**（3 条收口、占用窗口最长，性价比最低）。
- **批次划分（2 批）**：① `X18`（1 条，独占，含历史写入）；② `X12` + `X15`（2 条，长窗独占，可复用 G4 窗口的 docker 节点）。

## G7 缺陷与产品裁决类（5 条）

- [ ] **授权项**：对 5 项待裁事项给出裁决（**接受为产品语义** / **立项修复** / **豁免并收窄口径**）；裁决为"修复"时，另行授权对应的前端/服务端改动与复测范围。
- **覆盖用例（5）**：`G04-01`、`X08`、`G04-04`、`X19`、`P12P13-05`

| 用例 | 裁决点（含最新口径） |
|---|---|
| G04-01（已 FAIL） | 归因应为 `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED`（Group/Script **创建表单** pending 未禁用 → 2 次 POST）；`WEB-TASK-PENDING-SAVE` 已 regression PASS。裁决：立项修复（建议同查 Application/Task/Schedule 表单是否共用组件）还是豁免？ |
| X08 | "慢响应重复点击"口径是否含创建表单（含则随 G04-01 处理）；另可顺带补 1 条只读断言"取消编辑不改 Audit"（`/api/audit` 查询） |
| G04-04 / X19 | Enter 不提交（0 POST）与 Escape 关闭后焦点落 BODY：接受为设计还是立项修复（弹窗组件）？ |
| P12P13-05 | 过去时间 One-Time 保存可用（`OBS-20261014-PAST-ONETIME-SAVE-ENABLED`）与空 `run_at` 服务端拒绝口径 |

- **要做什么**：逐项给出裁决结论并写入台账口径；"接受"类直接收口（改 `remaining_dimensions` 措辞），"修复"类走 G7-修复子项（前端 `web/src/features/*` 或服务端校验）+ 针对性复测（1 批）。
- **风险与回滚**：零环境风险（纯裁决）。若裁决为"修复"，新增源码改动需单独确认，并复核不影响 `qa-resume-20261016-verify` 的现有回归口径。
- **我的推荐**：**逐项裁决** —— `G04-01` 建议"立项修复 + 复测"（前端小改，影响 2 条用例）；`X08` 建议"收窄口径至创建表单类"并顺带补 1 条断言；`G04-04`/`X19` 建议"接受为设计 + 写入口径"（若追求可访问性达标则改为修复）；`P12P13-05` 建议"接受 + 文档化"（并明确空 `run_at` 服务端拒绝要求）。
- **批次划分**：无环境批次；裁决后 3 条（`G04-04`、`X19`、`P12P13-05`）直接收口；`G04-01` 走"修复 1 批 + 复测 1 批"。

## G8 安全复测类（1 条）

- [ ] **授权项**：允许用**非敏感标记值**对 secret 的默认值/输入/Revision/Execution/Audit/错误遮蔽，以及 Registration Token/Authorization/Cookie 泄漏面做一次**逐项复测**（仅标记值；证据不留真实凭据）。
- **覆盖用例（1）**：`X16`

- **要做什么**：在 repair 权威（或 G1 隔离实例，二选一）建 `qa-*` 自有 Script/Task，写入 marker secret，逐项核对：默认值摘要、编辑回显、Revision 历史、Execution 与运行日志、Audit、错误信息；并扫描交付证据目录（`repair/**/evidence`）不含 Registration Token/Authorization/Cookie 原文（先例：证据目录扫描 CLEAN）。注：`WEB-SECRET-DEFAULT` 已 regression PASS，本项是补齐用例要求的遮蔽矩阵，不是复现缺陷。
- **风险与回滚**：只写自有 `qa-*` 对象 + 读日志；无真实凭据入证据。回滚 = 删除自有对象 + 证据目录 CLEAN 复核。
- **我的推荐**：**批准** —— `X16` 是 P0，且成本低；**建议在 G5 夹具批内并行执行**（可复用同一 Script/Task fixture）。
- **批次划分**：1 批（1 条），与 G5 并行（以只读为主）。

## G9 数据形态类（大数据集／跨页漂移）（1 条）

- [ ] **授权项**：允许构造**多页节点数据集**（≥11 条自有/`pending` 记录）做批量选择漂移与跨页保持断言；**推荐在 G1 隔离实例内做**（若如此，本项并入 G1，不需面向共享权威的额外写入授权）。
- **覆盖用例（1）**：`X14`

- **要做什么**：按 p-table 的 13 节点数据集与 FL-12/GAP-23 的"自有 pending 纳管记录"法构造多页节点集；选中若干行后执行过滤/排序/翻页/删除/刷新，抓取批量请求 payload，断言 ID 集合与 UI 勾选一致（含跨页保持）。对 protected 节点只做选择/打开批量栏并取消（0 写），不实际执行破坏性批量动作。注：跨页搜索维度已由 SAFE-02/GAP-07b 覆盖且 `WEB-EXEC-SEARCH-PAGE` 已 regression PASS，本项只补"漂移/跨页保持"。
- **风险与回滚**：自建节点按前缀清理；共享权威上需防误点执行（批量动作一律先取消并核对 0 写）；在隔离实例上则无此风险（可真实执行一次破坏性批量）。
- **我的推荐**：**换成隔离实例做**（并入 G1）—— 零 protected 风险，且批量选择语义可在自有节点上真实执行。
- **批次划分**：随 G1 的 A/B 批内插入（1 条），不单独占窗口。

---

# 二、建议分组中"无对应 C2 用例"的类别（避免误批）

| 建议类别 | C2 中是否有 | 说明与相邻决策 |
|---|---|---|
| ① 改全局设置类 | **无** | C2 无此需求。相邻需单独决策的是待决事项 **A9**（只有改全局 `changelog_window` 才能真测 `SYS-07`，属 BLOCKED 桶）；隔离实例内可自由改设置，不需要全局授权。 |
| ② 重启 Hub／服务类 | **无** | 修复版部署时已完成 repair Hub 与 Agent 重启（`deploy-evidence.json`，primary `:8080` 全程未动）。如需 Hub 停机观测（如调度 owner 差异的 Hub 侧），**用 G1 隔离实例替代**，不重启 repair Hub。 |
| ③ 替换／升级二进制类 | **无** | C2 无此需求。相邻两项：G4 若要 Docker 形态跑修复版 Agent 需"容器内二进制替换/新镜像按 digest 导入"单列授权；`CR-1` 收窄 + `CR-2` 文档同步属代码交付，不在本清单（见 `pending-decisions-20261016.md` G 节）。 |
| ④ 创建持久化账号／角色类 | **已并入 G1** | 所有账号动作仅在隔离实例内（零持久化）。**在共享权威创建账号的建议一律不批准**（无删除 API）。 |
| ⑤ 放开 Host 安全策略类 | **无** | 相邻为 BLOCKED 的 Host 集成族 4 例（`SYS-54/55`、`AR-31`、`INV-14`，待决 **A8**）与 OIDC 4 例（`P01-08/09/10`、`X17`，待决 **A7**）——要解 BLOCKED 需另立授权，与本清单无关。 |
| ⑥ 特定数据形态类 | **有** | 即 G9（`X14`）。 |
| ⑦ 其它 | —— | `G6`（长窗独占）、`G7`（裁决）、`G8`（安全复测）已在正文单列。 |

---

# 三、最小授权集（若只想批最少的一组）

| 优先 | 授权项 | 覆盖用例数 | 预计收口 | 一句话理由 |
|---|---|---:|---:|---|
| 1 | **G5 自有夹具批** | 14 | **14** | 单笔收益最大；夹具全部自有前缀 + cleanup oracle + 同型先例齐备，无新环境风险。 |
| 2 | **G1 隔离可销毁实例** | 7（+`X14` 并入则 8） | **7–8** | 全部账号/空态/多会话类唯一可行路径；可整体销毁、零持久化残留，安全性最高。 |
| 3 | **G2+G3 合并为一个"owned child 注入/停机窗口"授权** | 6 | **6** | 3+3 条共用同一 owned child 与帧代理，合成一个独占窗口即全部收口。 |

- **三项合计：27 条**（`X14` 并入 G1 则为 **28 条**），占 C2 可执行 39 条的 ~70%。
- 只能批 1 项 → 批 G5（14 条）；只能批 2 项 → G5 + G1（21–22 条）。
- **零环境成本附加项**：`G7` 裁决（5 条）不需要任何环境授权；裁决后 `G04-04`/`X19`/`P12P13-05` 可直接收口，`G04-01` 需"修复+复测"各 1 批，`X08` 需 1 条断言。若一并处理，最小集实际可覆盖 **32 条**。
- 未纳入最小集的剩余项：`G4`（2 条，Docker 独占）、`G6`（3 条，长窗）、`G8`（1 条，可与 G5 并跑 → 实际可顺手 +1 条）、`G9`（1 条，已并入 G1 建议中）。

> 附：本清单所有批次通过后，仍需按项目惯例由 Root/独立核验复核证据，再改 `feature-inventory.json` 状态；授权通过 ≠ 状态变更。
