## Why

统一监控上线后，Hub/Agent 连接与指标补报仍有两个可靠性缺口：瞬时 SQLite BUSY 会被当成会话失效，导致节点假离线或 HELLO 被关闭；Agent 侧“入队即删除”会让半开连接丢失指标，10 批/无字节上限也无法覆盖 30 分钟内的短时中断。同时 Agent 进程内存缺少硬边界，1–2 核小节点上容器扫描、WebSocket 队列和执行输出会放大峰值。

本 change 只加固既有 `unified-monitoring` 链路，不引入磁盘 spool、指标数据库或新协议版本。

## What Changes

- Hub：HELLO、心跳与连线状态写入遇到 `SQLITE_BUSY/LOCKED` 时短退避重试；只有认证/协议/会话失效才关闭或置 offline。SQLite `busy_timeout` 默认 15s 且可配置；首轮原生聚合延迟启动，先让 HELLO 完成。
- Agent：WebSocket 增加 ping/pong 存活探测，Hub 无 FIN 消失时约 60s 内感知；指标补报缓冲改为默认 20 批、最大 60 批、4MiB 硬上限，按序列化大小计费，超限淘汰最旧并记录丢弃。
- 指标发送改为“WebSocket 写成功或收到 ACK 才出队”；新增 `NODE_METRICS_ACK` 能力协商，旧 Hub 未声明时回退写成功即出队；ACK 超时保留并重试，Hub 主键 UPSERT 保证重复批次幂等。
- Hub 聚合增加默认 1h 迟到窗口，按时间分块提交事务；覆盖 Agent 内存补发范围内的迟到/乱序样本，超窗口记录并截断，不删除历史 raw。
- Agent 内存优化：WS `sendCh` 128→32 + 1MiB 字节上限；读限制 8MiB→2MiB；执行输出默认 512KiB/流、1MiB 总计；SQLite 连接 4→2、cache_size 约 -512KiB；容器扫描 >16 时降为 60s、上限 32，保留汇总；systemd 默认 `GOGC=50`、`GOMEMLIMIT=8MiB`、`GODEBUG=disablethp=1`（可覆盖，不设 GOMAXPROCS）。
- 新增内存/队列观测：缓冲字节、批次数、最旧待发时间、丢弃数、WS 队列深度/字节、ACK 超时。

## Capabilities

### Modified Capabilities

- `unified-monitoring`: 增加连接稳定性、内存补报与迟到重算的可靠性要求；数据模型、协议版本和保留期不变。

## Impact

Hub/Agent 连接管理、SQLite 打开参数、原生指标聚合、Agent 指标队列/连接队列/容器采集/执行输出、systemd 打包、测试与 `docs/ARCHITECTURE.md`。旧 Hub 与旧 Agent 继续互通；不新增数据库表，不迁移历史数据。
