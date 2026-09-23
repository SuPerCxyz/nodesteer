# NodeSteer 开发与交付规则

本文件是项目 `AGENTS.md` 的分片，承载实现方式与完成判定规则。
与全局 `AGENTS.md` 重复的通用要求由全局承担，此处只保留项目特有内容。

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

说明：`docs/ARCHITECTURE.md` §4 已声明“一期不拆微服务，不强制引入 Kafka/RabbitMQ”，本节黑名单是项目执行口径的超集。

## 完成门禁

通用验证强度与“宣布完成前”的检查清单由全局 `AGENTS.md` 第 6 节承担，此处不重复。
声明 NodeSteer 功能完成前，必须在全局清单之外额外确认：

- 持久化链路完整。
- Hub/Agent 或前后端链路已打通。
- 用户可见行为符合 `docs/PRODUCT.md`。
- 只完成部分内容时必须明确标记 `PARTIAL`（全局“只声明该范围”的项目内标记约定），不得声称 DONE。
