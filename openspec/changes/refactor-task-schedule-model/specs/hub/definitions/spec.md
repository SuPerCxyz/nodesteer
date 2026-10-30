## MODIFIED Requirements

### Requirement: Desired State 计算
系统 SHALL 根据 Schedule Target、Group、Label、Application Assignment 计算每个 Agent 应同步的对象集合，Agent 无需下载整个 Hub 数据库。

#### Scenario: 仅同步相关对象
- **WHEN** Agent 请求同步
- **THEN** Hub 仅返回该 Agent 相关（Schedule Target 命中其 Group/Label 或 Assignment 指定）的任务、调度与脚本

## ADDED Requirements

### Requirement: Task 定义字段
系统 SHALL 将 Task 定义为可复用的执行内容定义，包含 Name、Type、Parameters、Condition、Enabled、Revision；Task SHALL NOT 包含目标节点、触发器或执行策略。

#### Scenario: 创建无目标的任务定义
- **WHEN** 管理员创建 Command/Script/Application Task
- **THEN** 只需提供执行内容与参数/条件，不需要选择节点或分组
- **AND** 任务定义可被多个 Schedule 引用

#### Scenario: 旧目标与策略字段不进入任务定义
- **WHEN** 请求在 Task 中携带 target/timeout/retry/run_user/offline_policy
- **THEN** 系统忽略这些字段，不将其写入任务定义

### Requirement: Schedule 绑定任务、目标与策略
系统 SHALL 让 Schedule 成为任务与节点绑定及执行策略的唯一来源，包含 Task ID、Name、Target（node/group/label）、触发器、Timezone、Execution Owner、Timeout、Retry、Run User、Offline Policy、Misfire Policy、Enabled。

#### Scenario: 创建调度计划
- **WHEN** 管理员选择任务定义、目标节点/分组/标签、触发器和执行策略并保存
- **THEN** 系统校验目标与策略并持久化 Schedule

#### Scenario: 同一任务绑定不同节点集合
- **WHEN** 同一 Task 被两个 Schedule 引用且目标不同
- **THEN** 两个 Schedule 独立触发，互不覆盖

### Requirement: 仅手动调度
系统 SHALL 支持 `manual` Schedule 类型：该类型不自动触发，只承载任务、目标与策略，供手动执行使用。

#### Scenario: 手动调度不自动触发
- **WHEN** 存在启用的 manual Schedule
- **THEN** Hub 与 Agent 的自动调度器均不为其创建执行
- **AND** 用户可从调度计划页手动执行
