## 1. 现状与设计

- [x] 1.1 确认单 Agent 原生监控边界：同一 Agent 采集指标并执行任务，Hub 原生存储，Nocturne 退出运行依赖；保留 §67 不变量。
- [x] 1.2 同步 `docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、`docs/DEPLOYMENT.md`、`.env.example`、`docker-compose.yml` 和本 change 的原生监控基线。

## 2. Agent 采集与协议

- [x] 2.1 实现 CPU、内存、负载、磁盘、网络采集；处理缺失、真实零值、伪文件系统、只读挂载、网络计数器回绕和挂载点上限。
- [x] 2.2 默认 30s、运行时限制 5s–300s；单批最多 500 样本；断线内存补报默认 20 批、最大 60 批、4MiB 硬上限（见 `harden-monitoring-transport-memory`），溢出丢弃最旧批次且保留缺口可见。
- [x] 2.3 声明 `node_metrics` 能力；Native 与 Docker Host Integration 支持，Docker 基础模式不声明。
- [x] 2.4 定义 `NODE_METRICS` 批量协议（node_id、sequence、timestamp、metric、dimensions、value、quality）及质量常量。
- [x] 2.5 旧 Agent 无 `node_metrics` 时：gateway 拒绝 `NODE_METRICS` ingest；查询无样本显示 `no_data`，规则不触发，不以 0 代替。

## 3. Hub 原生时序存储、聚合与查询

- [x] 3.1 gateway 接收 `NODE_METRICS`，校验会话节点、能力、批次大小、节点身份、时间、质量和维度，按 node_id + metric + series + timestamp 幂等写入。
- [x] 3.2 schema v23 建立 raw、1m、5m、1h 存储与索引；保留 raw 7d、1m 90d、5m 1y、1h 2y；后台每分钟执行聚合/清理。保留期为代码常量，配置项未接入。
- [x] 3.3 实现 overview、series、metrics、latest 查询 API，返回 tier、step、points、gaps 和质量状态；`metrics` 强制要求 `series`，全部仅 Administrator 可用。
- [x] 3.4 P3：overview 改为 2 条批量 SQL 消除 N+1；v25 新增 `(node_id,tier,bucket_start)` 索引；节点删除清理 raw/rollup；受控 6 节点×40 series×2 样本（480 条）聚合 106ms、重开库 0.85ms、批量 latest 1.25ms。未做 100 节点真实压测，不声称任意规模。

## 4. Web 总览与历史

- [x] 4.1 总览直接遍历 NodeSteer 节点，显示在线状态、CPU/内存/负载/磁盘/网络、最近采样和告警，不依赖监控节点绑定；告警数仍为占位 0。
- [x] 4.2 点击节点进入详情页，CPU/内存/负载 1/5/15/磁盘/网络 RX/TX 多图表；支持指标/维度筛选、1h/6h/24h/7d（默认 1h）、gap 断线、零值与缺失区分；无能力或无样本显示不可用。
- [x] 4.3 节点操作跳转 `/agents/{id}`，复用现有节点详情与任务入口。
- [x] 4.4 Nocturne 绑定/自动匹配 UI 和页面入口已删除；旧 Nocturne 历史不展示，遗留 `monitoring_node_bindings` 表不参与新链路。

## 5. 规则与执行

- [x] 5.1 `node_metric` 目标改为 NodeSteer 节点、metric 和 series/dimensions，移除 Nocturne node_id/series 字段与绑定校验。
- [x] 5.2 规则从 Hub 原生样本求值，保留新鲜度/最大样本延迟、持续时间、条件恢复、gap/乱序/冲突中断、去重和冷却。
- [x] 5.3 手动/自动触发、分批、验证和显式补偿继续复用现有 Task/Schedule/Execution 链路；相关实现与用例已存在，定向后端回归包含相关包，完整矩阵未做。
- [x] 5.4 已知失败与未知结果区分、失败暂停、人工确认/放弃、未解决事件保留；相关实现与用例已存在，定向后端回归包含相关包，完整矩阵未做。
- [x] 5.5 失败/重启/补偿/权限矩阵已实测：Schedule/Task 停用、规则停用、目标不匹配、补偿成功/失败、Hub 重启 pending→unknown/abandon、节点离线、多节点部分失败/补偿、viewer/operator 403。多节点“未知结果”未单独构造，由重启和补偿失败覆盖。

## 6. Nocturne 移除

- [x] 6.1 文档移除 Nocturne 当前部署说明；`.env.example` 和 `docker-compose.yml` 不再包含 `NOCTURNE_URL` / `NOCTURNE_TOKEN`。
- [x] 6.2 Nocturne 客户端、只读代理、绑定 API、`nocturne_url` 配置和相关路由已移除；遗留表仅保留数据。
- [x] 6.3 页面绑定/自动匹配入口已移除；旧 Nocturne 历史不迁移、不回填、不展示。
- [x] 6.4 迁移 v25 `native_metrics_rollup_index_and_legacy_binding_cleanup` 已删除遗留 `monitoring_node_bindings` 表并创建 rollup 索引；v24→v25 迁移测试通过。未自动备份旧绑定元数据，升级前需备份 Hub DB。

## 7. 验证与文档

- [x] 7.1 定向后端回归通过：`go test -buildvcs=false ./internal/agent ./internal/protocol ./internal/store ./internal/monitoring ./internal/hub ./internal/hub/api ./internal/hubserver` 退出码 0（2026-11-03，agent 42.391s、store 3.751s、monitoring 1.412s、hub 1.416s、hub/api 10.103s、hubserver 53.947s；protocol 使用缓存）。
- [x] 7.2 真实隔离预览：2 个 Agent 5s 采样，overview 返回 CPU/内存/负载/磁盘/网络；1h 历史 90 点、质量“有效”；左导/首页等宽双栏已截图 `dashboard.png`；另有 `overview.png`、`history.png`。
- [x] 7.3 测试环境空密码：默认 false；`NODESTEER_ALLOW_EMPTY_PASSWORD=true` 时 `/api/auth-mode` 返回 `allow_empty_password=true`，API/浏览器登录成功；截图 `empty-login.png`。
- [x] 7.4 最终回归通过：后端 `make test`/`make lint` 通过、9 包 `-count=1` 57.5s、5 包 `-race` 162.2s 均退出码 0；前端 `npm test` 68/68 文件批次、0 retry，`npm run lint` 0 error（11 个既有 warning），`npm run build` 通过。
- [x] 7.5 真实链路已验证：Agent CPU 53.45% → 原生 `node_metric` 规则 → 任务执行 SUCCESS、标记文件出现、事件 completed，约 4.09s；ACK/静默/webhook 500→200 重试、通知记录 API 均实测；节点删除清理由 3.4 覆盖。未做任意规模压测。
- [x] 7.6 文档/OpenSpec 与当前实现状态对齐，未实现项保持未勾，不归档。

## 8. 告警与通知

- [x] 8.1 schema v24 创建 `alert_rules`、`alert_states`、`alert_events`、`notification_channels`、`alert_notifications`、`alert_notification_attempts`，含活动事件去重索引。
- [x] 8.2 告警规则支持阈值/持续时间/恢复时间/冷却/severity/渠道/启停/静默；状态机覆盖 pending/firing/resolved/silenced/unknown，ack 可用。
- [x] 8.3 评估循环默认 30s；无样本、过期、离线或查询失败时 fail-closed，不触发、不恢复；告警只通知/记录，不触发任务。
- [x] 8.4 通知渠道支持 webhook、Slack、钉钉、企业微信、Telegram、Email（SMTP）；敏感配置只保存 `url_env`/`token_env`/`password_env`/`to_env`，API 不回显。
- [x] 8.5 通知发件箱默认 3 次尝试、指数退避；支持测试发送；规则/事件/渠道 API 和 admin-only 权限已接入。
- [x] 8.6 前端 `/monitoring/alerts` 提供规则/渠道/事件页签，支持启停、静默、ack 和测试发送。
- [x] 8.7 钉钉加签已实现：`secret_env` HMAC-SHA256，timestamp+sign 追加到 webhook URL，secret 不落库/日志。
- [x] 8.8 邮件 HTML/附件已实现：`html`、`attachments_env`、`attachment_dir`；最多 5 个/单 5MB/总 10MB，拒绝路径穿越/符号链接/目录外文件。
- [x] 8.9 通知发送记录查询 API 已实现：`GET /api/monitoring/alerts/notifications`，支持 event/channel/status/分页，返回 attempt_details，脱敏。

## 9. 指标与监控 UI 扩展

- [x] 9.1 CPU 组成指标（user/nice/system/idle/iowait/irq/softirq/steal）及 per-core 限制；前端可点击分核。
- [x] 9.2 内存 used/cached/buffers/available、Swap、Inode、磁盘 read/write bps/IOPS、网络 errors/drops、温度、风扇采集。
- [x] 9.3 容器 CPU/内存/网络指标，自动探测 Docker/Podman socket；无 socket 时不产生容器样本。
- [x] 9.4 多序列双色渐变面积图，统一 `web/src/config/chart-palette.ts` 16 组明暗调色板；线宽 1.8、面积渐变、hover tooltip、图例隐藏、gap/断点不补 0；每图按宽度 ≤120 点（半宽约 60），单调三次平滑防过冲；stacked 图补画总使用率线。
- [x] 9.5 节点详情分区 CPU→内存/Swap→负载→磁盘/Inode→磁盘 I/O/IOPS→网络→温度/风扇→容器。
- [x] 9.6 节点概览改为一行式表格（仪表盘与 `/monitoring`）；`/agents` 列表移除监控卡，详情顶部一行概览。
- [x] 9.7 监控组拆分为节点监控 `/monitoring`、服务监控 `/monitoring/services`、告警 `/monitoring/alerts`；联动规则迁至 `/rules`。
- [x] 9.8 时间范围 1h/6h/24h/7d + 自定义（最大 30 天），默认 1h。
- [x] 9.9 容器 capability/UI 已实现：`container_metrics` 能力仅在 Docker/Podman socket 可用时声明；overview 返回 `container_metrics_available`；前端区分未启用/暂无数据/正常/未知。

## 当前实现与验证记录

- Agent：`internal/agent/metrics.go` 采集 CPU/内存/负载/磁盘/网络；`internal/agent/metrics_report.go` 负责 30s 默认周期、5s–300s 限制、500/批、20/60 批与 4MiB 内存补报队列和断线重传；`internal/protocol/protocol.go` 定义 `NODE_METRICS`；`internal/agent/agent.go` 声明 `node_metrics`。
- Hub：schema v23 创建 `node_metric_samples` / `node_metric_rollups`；`internal/store/native_metrics.go` 实现 raw→1m→5m→1h 聚合、保留清理、序列/最新/范围查询；`internal/hubserver/native_metrics_maintenance.go` 每分钟执行聚合/清理；`internal/hub/gateway.go` 接入 `NODE_METRICS`；`internal/hubserver/native_metric_probe.go` 为 `node_metric` 规则提供原生样本。
- API/Web：`internal/hub/api/monitoring_native.go` 提供 `/api/monitoring/nodes/overview|series|metrics|latest`，admin-only，`metrics` 强制 `series`；前端总览遍历 NodeSteer 节点，历史支持 1h/6h/24h/7d（默认 1h），节点操作 `/agents/{id}`；Nocturne 绑定/自动匹配 UI 已删除。
- Nocturne：后端、路由、配置和绑定已移除；v25 迁移已 `DROP TABLE monitoring_node_bindings` 并创建 rollup 索引；旧历史不迁移、不回填、不展示。升级前需备份 Hub DB（旧绑定元数据不自动备份）。
- 空密码：`NODESTEER_ALLOW_EMPTY_PASSWORD` 默认 false；仅隔离测试可开启，`/api/auth-mode` 返回 `allow_empty_password`，登录表单仅在开启时允许空密码。
- Hub 告警/通知：schema v24 创建 `alert_rules`、`alert_states`、`alert_events`、`notification_channels`、`alert_notifications`、`alert_notification_attempts`；`internal/monitoring/alerts.go` 实现状态机，`alert_engine.go` 默认 30s 评估，`notify.go` 实现发件箱、3 次指数退避和测试发送；告警只通知/记录，不触发任务。
- 告警 API/UI：`/api/monitoring/alerts/rules|events|ack|silence`、`/api/monitoring/notification-channels|test`，admin-only；前端 `/monitoring/alerts` 提供规则/渠道/事件页签，支持启停、静默、ack、测试发送。
- 指标/UI 扩展：CPU 组成、内存/Swap/Inode、磁盘 I/O/IOPS、网络 errors/drops、温度/风扇、容器指标；节点详情按 CPU→内存/Swap→负载→磁盘/Inode→磁盘 I/O/IOPS→网络→温度/风扇→容器分区；多序列双色渐变面积图；节点概览改为 D 紧凑机架列表；监控组拆为节点监控/服务监控/告警，联动规则迁至 `/rules`；时间范围 1h/6h/24h/7d + 自定义（最大 30 天）。
- 已验证：定向后端回归退出码 0；定向告警测试 `go test -buildvcs=false ./internal/monitoring -run 'Test.*Alert|Test.*Notification|Test.*Channel'`、`./internal/hub/api -run 'TestAlert|TestNotification'`、`./internal/store -run 'TestAlert'` 均退出码 0；隔离预览 2 Agent/5s 采样，overview 返回 CPU/内存/负载/磁盘/网络；1h 历史 90 点有效；CPU 组成/内存/Swap/Inode/磁盘 I/O/网络错误/温度/容器 CPU 均有真实序列；webhook 测试发送成功，规则 30s 评估后 firing 且 webhook 收到 payload；空密码 API/浏览器登录成功。截图位于 `/home/superc/tmp-nodesteer-native-preview/` 与 `/home/superc/tmp/nodesteer-native-preview/`。
- 最终回归通过：后端 `make test`/`make lint` 通过、9 包 `-count=1` 57.5s、5 包 `-race` 162.2s 均退出码 0；前端 `npm test` 68/68 文件批次、0 retry，lint/build 通过。
- 卡片数据行统一为 D 紧凑机架列表：新增 `web/src/features/shared/rack-list.tsx`，`DataTable` 增加 `variant='rack'`；覆盖节点概览、节点历史、服务目标/观测、告警规则/渠道/事件、联动规则/触发历史、仪表盘最近执行、任务执行历史、节点执行记录、任务/执行/传输/用户列表。无表头/列线；1600/1280、明暗主题实测 `table=0`、无横向滚动；节点行窄屏标签换到第二行。
- 红框反馈调整：仪表盘“自动化控制/节点监控”改为上下堆叠、全宽；节点指标三列展开，进度条 3px→5px，指标文字改为黑色，负载/网络/温度去掉边框容器改为行内标签；后续将“节点监控”3 个汇总指标并入“节点监控概览”卡片顶部，节点网络改为接收/发送（上下行）分开展示。
- 时间范围交互：移除“时间范围”可见文案（保留 select 的 aria-label）；自定义时间改为弹窗选择开始/结束时间并校验 30 天上限，确认后才应用，页面头部高度与布局不变。
- CPU 图默认不绘制 Idle（空闲率）曲线，避免 95%–100% 的贴顶线干扰；Idle 仍保留在“分核详情”展开的明细表中。
- 卡片样式统一：`Card` 基础间距 `gap-3 py-4`，`CardHeader` 统一 `px-6 pb-1`；页面级卡片标题 16px、节点详情图表卡 14px；实测标题上边距 17px、标题下 4px、内容前 12px，标题与内容左对齐 25px；仪表盘“节点监控概览”红框头部高度由 24px 降至 20px。
- 图表自适应/密度/线宽：`MultiSeriesChart` 使用 ResizeObserver 跟随容器宽度；1600/1280/768 视口实测 viewBox 与容器宽度一致，无左右空白/裁剪；点数按宽度 `clamp(round(width/10),40,120)`（全宽 120、半宽 60），min/max 保留尖峰；Fritsch–Carlson 单调三次平滑防过冲；线宽 1.8、深色光晕 4px/0.06；统一 16 组调色板；CPU 组成堆叠按时间容差对齐，明细表默认收进分核详情。
- 告警真实链路：webhook 测试发送成功；规则 30s 评估后 firing，webhook 收到 payload；活动事件确认按钮已修复并实测；5.5/7.5 真实 Agent 指标→原生规则→任务执行 SUCCESS（标记文件、事件 completed，约 4.09s）；ACK/静默/webhook 500→200 重试/通知记录 API 均实测。
- 左导与首页已按“自动化控制 / 节点监控同等重要”重排：左导拆为两个分组；首页顶部为左右等宽双栏（自动化控制 + 节点监控），下方保留应用/同步/最近执行/即将执行调度；截图 `dashboard.png`。
- 剩余限制：未做 100 节点真实压测（仅 6 节点×40 series 受控规模）；未单独构造“多节点未知结果”（由重启/补偿失败覆盖）；未验证 3 次重试耗尽后的最终失败；`overview.alerts` 仍为占位 0；未归档。
- 本 change 未归档；未勾项不得用组件存在、接口返回或旧测试结果替代真实验证。
