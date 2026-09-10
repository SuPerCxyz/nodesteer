# Cadentra Project Rules

Cadentra 是一个轻量级 Linux Hub-Agent 自动化控制平台。

本文件仅补充 Cadentra 项目特有规则。
通用开发、测试、Git、代码质量和 Agent 工作流遵循全局 `AGENTS.md`。

## 必读文档

涉及功能、架构或行为修改前，按任务范围阅读：

- `docs/PRODUCT.md`
- `docs/ARCHITECTURE.md`

两份文档均为强约束。

优先级：

1. 用户最新明确要求
2. `docs/PRODUCT.md`
3. `docs/ARCHITECTURE.md`
4. 本文件
5. 现有实现

## 一期范围

`PRODUCT.md` 中标记为“一期”或“一期核心”的功能必须真实实现。

明确标记为后续规划的功能一期不要提前复杂实现。

不得通过修改文档、TODO、Stub、Mock 或前端假数据掩盖未完成的一期需求。

## 核心架构不变量

必须保持：

- Hub 是 Desired State 唯一权威源。
- Agent 主动连接 Hub。
- 同步采用：长连接实时通知 + Revision 周期校验 + Reconnect Reconciliation。
- 实时通知只负责快速通知，Revision/Reconciliation 负责最终一致性。
- Agent Revision 只有配置成功持久化后才能前进。
- Allow Offline 的 Schedule 由 Agent Local Scheduler 负责，禁止 Hub/Agent 双触发。
- Execution ID / Execution Key 必须幂等。
- Execution 开始前必须先写本地 Journal。
- Remote State 过期或未知时默认 Fail Closed。
- Artifact 校验失败禁止安装。
- Managed Application 更新必须支持明确的失败恢复/回滚。
- systemd 只管理 Cadentra Managed Application。
- Native Agent 与 Docker Agent 共用 Agent Core。
- Native/Docker 宿主机差异通过 Host Adapter 隔离。
- Docker Agent 的 Revision、Schedule、Journal 等状态必须持久化。

## 实现原则

一期保持轻量，Hub 使用 Modular Monolith。

不要无必要引入：

- 微服务
- Kafka/RabbitMQ
- Redis
- Elasticsearch
- Kubernetes 强依赖
- 复杂 DSL
- Plugin Framework

简单不能以牺牲一致性、幂等、持久化和故障恢复为代价。

## 完成门禁

声明功能完成前必须确认：

- 真实代码已实现
- 持久化链路完整
- Hub/Agent 或前后端链路已打通
- 相关测试通过
- 用户可见行为符合文档

如果只完成部分内容，必须明确标记为 PARTIAL，不得声称 DONE。
