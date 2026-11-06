## 1. 连接稳定性

- [x] 1.1 `internal/store/sqlite.go` busy_timeout 默认 15s，并支持 Hub 配置覆盖。
- [x] 1.2 Gateway HELLO、心跳、断连 offline 路径对 BUSY 短退避重试；认证/协议/会话失效语义不变。
- [x] 1.3 Hub 启动后延迟首轮原生聚合；补测试覆盖延迟与取消。
- [x] 1.4 Agent WebSocket 增加 ping/pong 或读超时，约 60s 内发现无 FIN 断链；不误伤 30s 心跳。

## 2. 内存补报队列与 ACK

- [x] 2.1 队列默认 20 批、最大 60 批、4MiB 硬上限；按序列化大小计费并淘汰最旧。
- [x] 2.2 修复“Send 入队即删除”：WebSocket 写成功或 ACK 后才出队。
- [x] 2.3 `NODE_METRICS_ACK` 能力协商、Hub 提交后 ACK、Agent ACK 超时重发；旧 Hub 回退写成功出队。
- [x] 2.4 队列统计：字节、批次数、最旧待发时间、丢弃数、ACK 超时；随心跳/日志暴露。

## 3. Hub 迟到重算

- [x] 3.1 聚合增加 1h 迟到窗口，按 15 分钟/首次回填分块事务。
- [x] 3.2 超窗口记录截断，不删除 raw、不改保留期。
- [x] 3.3 测试 15min/1h 迟到样本、重复幂等、超窗口截断。

## 4. Agent 内存优化

- [x] 4.1 WS `sendCh` 32 + 1MiB 字节上限与背压/丢弃策略；读限制 2MiB。
- [x] 4.2 执行输出 512KiB/流、总计 1MiB。
- [x] 4.3 SQLite 连接 4→2、cache_size 约 -512KiB。
- [x] 4.4 容器扫描高容器节点 60s、上限 32，保留汇总。
- [x] 4.5 systemd/Docker 默认 GOGC/GOMEMLIMIT/GODEBUG 可配置；不设 GOMAXPROCS；代码默认补齐 GOGC/GOMEMLIMIT。

## 5. 验证

- [x] 5.1 单测：BUSY 重试、ACK、写失败不丢批次、20/60 批与 4MiB 淘汰、迟到重算、WS 队列上限、容器扫描上限、执行输出上限。
- [x] 5.2 `make test`、`make lint`；相关包 `-count=1`，高风险 `-race`。
- [x] 5.3 隔离 E2E：临时 Hub/Agent、断连 15 分钟、重启丢未发送数据、重复幂等、1h/6h/24h 补报、新旧互通。
- [x] 5.4 实测 Agent RSS/VmHWM，记录是否达到 ≤20MiB/≤24MiB。

## 6. 文档

- [x] 6.1 同步 `docs/ARCHITECTURE.md` 的 10 桶/迟到窗口描述与队列/内存边界。

## 验证记录

- 单测/静态检查：`make test`、`make lint` 退出码 0；`go test -race` 覆盖 `internal/agent`（Metrics/Container/Reconnect 子集）、`internal/agent/connection`、`internal/hub`、`internal/store`、`internal/agent/execution`。
- 隔离 E2E（临时目录 `/tmp/opencode/nodesteer-e2e`，端口 18832/18833，未触碰 18810 预览与生产进程）：
  - 新 Hub + 新 Agent：5s 采样；SIGSTOP Hub 15 分钟。Agent ping 约 10s 发现半开连接，补报队列稳定在 60 批并持续淘汰最旧；恢复后按最旧优先补入最后约 5 分钟数据（17:18:33Z 起，丢弃窗口 17:08:20–17:18:20 为 0 行）。
  - 重复补发：Python WebSocket 客户端以同 sequence 发送同一批次两次，收到 2 个 `NODE_METRICS_ACK`，数据库仅 1 行。
  - 迟到重算：补写 15 分钟前与 59 分钟前样本后，1m/5m/1h 桶均包含补报值；1h/6h/24h 查询 API 可见补报点。
  - Agent 重启：断连中杀死 Agent 再启动，重启前约 29 秒缓冲未回填（该区间 raw 行数为 0），确认不落盘。
  - 兼容：旧 Hub + 新 Agent、新 Hub + 旧 Agent 均正常在线并持续入库，协议版本未提升。
- 内存实测（5s 采样 + 60 批上限的最不利场景）：断连前 RSS 17.3–18.4MiB；队列满时 RSS/VmHWM 峰值 22.13MiB；恢复后 RSS 18.16MiB。满足稳态 ≤20MiB、峰值 ≤24MiB。
