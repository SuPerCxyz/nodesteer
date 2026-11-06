## Context

本 change 加固 `unified-monitoring` 的传输与补报链路。约束来自用户已确认的后端范围：不提升协议版本、不落盘指标缓冲、不删除历史、不改 raw 保留期、不动预览与生产实例。

## Goals / Non-Goals

**Goals:**

- 瞬时 SQLite BUSY 不导致 HELLO 失败、心跳掉线或节点假 offline。
- Agent 断连期间在 4MiB/60 批硬边界内保留指标，恢复后最旧优先补发并幂等入库。
- Hub 聚合能重算 1h 内的迟到样本，事务按时间分块。
- Agent 稳态 RSS ≤20MiB、峰值 ≤24MiB（以实测为准，不达标如实记录）。

**Non-Goals:**

- 不新增磁盘 spool、指标表或长期保留策略。
- 不改变 `ProtocolVersion`/`InboundProtocolVersion`，不要求旧 Agent 升级后才能连接。
- 不重做同步协议；WebSocket 读限制保持在对现有控制/同步负载可验证的范围内。
- 不调整 CPU 核数相关并发（不设 GOMAXPROCS）。

## Decisions

### 1. BUSY 重试只包裹可重试写路径

Hub Gateway 对 `markConnected`、`UpdateHeartbeat`、`markDisconnected` 使用统一的 `retryBusy`：仅在 `store.IsBusyError` 时按 100ms/250ms/500ms 退避重试。认证失败、协议错误、会话被替换仍立即失败。SQLite `busy_timeout` 默认 15s，可通过 Hub 配置覆盖。

`handleDisconnect` 先注销会话再写 offline，写失败只记日志并重试，不把连接重新放回会话表，也不因为瞬时写失败改变“会话已终止”的事实。

### 2. ACK 是能力协商，不是新协议版本

- `HELLO_ACK` 与 `HUB_HELLO` 新增可选 `capabilities` 字段，Hub 声明 `node_metrics_ack=true`。旧 Agent 忽略未知字段。
- Hub 在 `InsertNodeMetricSamples` 事务提交成功后发送 `NODE_METRICS_ACK`（含 node_id、sequence、ok）。
- 新 Agent 只有在 Hub 声明能力后才等待 ACK；否则回退“WebSocket 写成功即出队”。
- 队列按 sequence 等待 ACK；超时（默认 10s）清除在途标记并按最旧优先重发。Hub 以 `(node_id, metric, series_key, timestamp)` UPSERT，重复入库幂等。
- Agent 使用可等待写结果的发送通道，区分“已入本地发送队列”和“WebSocket 写成功”。

### 3. 补报队列按批数与字节双上限

默认 20 批、配置最大 60 批、固定 4MiB 硬上限。每批在入队时序列化为 envelope 并计费；任一上限先到即淘汰最旧，累计丢弃批次/缺口计数。保留样本原始时间戳与 sequence。进程退出即丢失，符合“不落盘”。

### 4. 1h 迟到窗口 + 分块事务

聚合每层仍从最后一个 rollup 桶向前覆盖 10 个桶，同时向前扩展到 `now-1h` 的迟到窗口。每个 series 的重算范围按 15 分钟（首次回填按 6 小时）切块，每块独立事务提交，避免一次长写锁。rollup 已存在但最后桶早于迟到窗口时，记录告警并从窗口起点截断；更老样本仍保留 raw，不删除、不回填 rollup。连续断档继续由空桶/gap 语义表达。

### 5. Agent 内存边界

- WS 发送队列 32 帧 + 1MiB 估算字节上限。控制消息优先等待预算；`NODE_METRICS` 预算不足时返回失败，由指标队列保留；`LOG_CHUNK` 在预算不足时丢弃（日志本身为尽力而为）。
- Agent 读限制 2MiB。现有 Hub 单条脚本内容上限 256KiB，任务/调度/应用定义均为小对象；Hub 在发送同步响应前检查序列化大小，超过 2MiB 时返回明确错误而不是让 Agent 被静默断开。
- 执行输出每流 512KiB、总计 1MiB；容器扫描 >16 个时降为 60s 周期、上限 32，仍输出 `container.collection.*` 汇总。
- systemd 单元默认注入 `GOGC=50`、`GOMEMLIMIT=8MiB`、`GODEBUG=disablethp=1`，通过 `agent.env` 可覆盖。
- 运行统计随心跳上报（旧 Hub 忽略），并写入 Agent 日志，便于观察队列字节/深度与 ACK 超时。

## Risks / Trade-offs

- 4MiB/60 批在 30s 采样下约 30 分钟；更长的断连会丢弃更旧批次，这是有界内存的预期代价。
- ACK 模式会增加一次往返；ACK 超时重发可能产生重复批次，由 Hub UPSERT 消化。
- 1h 迟到窗口每分钟重复计算最近 1h 的 1m 桶，CPU/IO 随 series 数增长；分块事务降低锁时长但不降低总工作量。
- 2MiB 读限制要求 Hub 同步响应受控；超过限制的配置会得到明确错误而非半开连接。

## Migration

无 schema 迁移。新增配置项均有默认值；旧 Agent/旧 Hub 不需要变更。回滚只需恢复二进制和配置。
