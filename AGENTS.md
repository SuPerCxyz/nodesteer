# NodeSteer Project Rules

NodeSteer 是一个轻量级 Linux Hub-Agent 自动化控制平台。

本文件仅保留主干：项目定位、必读文档、指令优先级对齐和分片索引。
通用开发、测试、Git、代码质量和 Agent 工作流遵循全局 `AGENTS.md`；具体主题规则按下方分片索引读取对应文件。

## 必读文档

涉及功能、架构或行为修改前，按任务范围阅读：

- `docs/PRODUCT.md`：产品功能基线和一期范围。
- `docs/ARCHITECTURE.md`：系统架构、一致性约束和核心架构不变量（§67）。

两份文档均为强约束。

## 指令优先级

优先级遵循全局 `AGENTS.md` 第 1 节的单一列表，本文件不另设平行编号列表：

- `docs/PRODUCT.md`、`docs/ARCHITECTURE.md` 构成该节第 3 项“当前仓库不可违反的强制约束”，优先于 OpenSpec artifact、通用规则与现有实现。

## 分片索引

| 任务范围 | 必读分片 |
|---|---|
| 进行、继续或复测 Web E2E 测试 | `.e2e/README.md`（记录索引、状态文件读取顺序和复测规则） |
| 实现方式、技术选型、完成判定 | `docs/DEV_RULES.md`（实现原则和项目完成门禁） |
| 核对架构一致性、不变量 | `docs/ARCHITECTURE.md` §67（核心架构不变量唯一出处，本文件不复述） |

## 一期范围

`PRODUCT.md` 中标记为“一期”或“一期核心”的功能必须真实实现。

明确标记为后续规划的功能一期不要提前复杂实现。

不得通过修改文档、TODO、Stub、Mock 或前端假数据掩盖未完成的一期需求。
