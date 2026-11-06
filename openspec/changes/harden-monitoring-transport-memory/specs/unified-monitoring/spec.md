## ADDED Requirements

### Requirement: BUSY 可重试错误不得中断已认证连接

Hub SHALL 在 HELLO 连线状态写入、心跳更新和断连 offline 写入路径对 `SQLITE_BUSY`/`SQLITE_LOCKED` 做短退避重试。认证失败、协议错误、会话被替换或节点不存在仍按既有语义处理。

#### Scenario: HELLO 遇到瞬时 BUSY

- **WHEN** Agent 完成认证但 `markConnected` 或心跳写入首次返回 BUSY
- **THEN** Hub 重试写操作并在成功后返回 accepted 的 HELLO_ACK
- **AND** 不因该瞬时错误关闭连接

#### Scenario: 心跳遇到瞬时 BUSY

- **WHEN** 已认证会话的 HEARTBEAT 更新首次返回 BUSY
- **THEN** Hub 重试并继续发送 HEARTBEAT_ACK
- **AND** 不把节点标记 offline

### Requirement: Agent 内存补报缓冲有硬上限

Agent SHALL 在内存中保留未确认的指标批次，默认 20 批、可配置最大 60 批，并始终受 4MiB 序列化字节上限约束。任一上限先到即淘汰最旧批次并记录丢弃计数；缓冲不落盘。

#### Scenario: 断连超过批数上限

- **WHEN** Agent 断连期间累计批次超过配置批数
- **THEN** 队列只保留最新的有界批次
- **AND** 记录被丢弃的批次数与缺口

#### Scenario: 断连超过字节上限

- **WHEN** 累计序列化大小超过 4MiB
- **THEN** 队列淘汰最旧批次直到满足字节上限
- **AND** 不创建磁盘文件或数据库表

### Requirement: 指标批次仅在写成功或 ACK 后出队

Agent SHALL 区分本地入队与 WebSocket 写成功。Hub 声明 `node_metrics_ack` 能力时，Agent 仅在该批次对应 ACK 成功后出队；未声明时回退为 WebSocket 写成功后出队。写失败、半开连接和 ACK 超时都不得丢弃批次。

#### Scenario: 半开连接

- **WHEN** Agent 已把批次放入发送队列但 WebSocket 写超时
- **THEN** 该批次仍保留在补报队列
- **AND** 重连后按 sequence/时间戳重发

#### Scenario: ACK 超时

- **WHEN** Hub 已入库但 ACK 在超时时间内未到达
- **THEN** Agent 重发同一批次
- **AND** Hub 以主键 UPSERT 保持幂等

#### Scenario: 旧 Hub

- **WHEN** Hub 的 HELLO_ACK 未声明 `node_metrics_ack`
- **THEN** Agent 在 WebSocket 写成功后出队，不等待 ACK

### Requirement: Hub 重算 1h 迟到窗口

Hub SHALL 在每次原生指标聚合时额外重算最近 1h 的 1m/5m/1h 桶，并按时间分块提交事务。重复或乱序批次必须幂等；超过迟到窗口的样本保留 raw 但不扩展 rollup 重算范围，并记录截断。

#### Scenario: 补报 15 分钟前样本

- **WHEN** 先写入并聚合当前样本，再补报 15 分钟前的样本
- **THEN** 对应 1m、5m、1h 桶重算并包含补报值

#### Scenario: 补报 1 小时前样本

- **WHEN** 补报样本位于 1h 迟到窗口内
- **THEN** 聚合重算该时间桶
- **AND** 重复补报不改变结果

#### Scenario: 超过迟到窗口

- **WHEN** rollup 最后桶早于 1h 窗口
- **THEN** 聚合从窗口起点截断并记录
- **AND** 不删除更老的 raw 数据

### Requirement: Agent WebSocket 与执行资源有界

Agent SHALL 将 WebSocket 发送队列限制为 32 帧和 1MiB 估算字节、读限制为 2MiB，执行输出限制为每流 512KiB/总计 1MiB，SQLite 连接池限制为 2，容器扫描在超过 16 个容器时降为 60s 且上限 32。

#### Scenario: 发送队列满

- **WHEN** WebSocket 写端变慢且队列达到帧数或字节上限
- **THEN** 指标批次留在补报队列而不是增长 WS 队列
- **AND** 控制消息优先于指标/日志获得发送预算

#### Scenario: 高容器节点

- **WHEN** 节点运行超过 16 个容器
- **THEN** 完整容器扫描周期降为 60s、最多采集 32 个容器
- **AND** 仍上报 `container.collection.discovered_count/sampled_count/limit`

## MODIFIED Requirements

### Requirement: 原生指标聚合窗口

Hub SHALL 将既有“每层向前 10 桶”语义扩展为“10 桶与 1h 迟到窗口取更早者”，并保持 `node_id + metric + dimensions + timestamp` 幂等与 raw 保留期不变。

#### Scenario: 常规聚合

- **WHEN** 已有 rollup 且新样本按 30s 周期到达
- **THEN** Hub 重算最后 10 个桶与最近 1h 窗口的并集
- **AND** raw 保留期和重复样本幂等语义不变

#### Scenario: 超过窗口

- **WHEN** 样本早于 1h 迟到窗口
- **THEN** raw 继续保留
- **AND** rollup 重算范围在窗口起点截断
