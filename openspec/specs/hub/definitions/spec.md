# hub/definitions Specification

## Purpose
Hub 管理 Script、Task、Schedule 定义及其 Revision、Assignment 与删除同步，作为 Agent Desired State 的内容来源。
## Requirements
### Requirement: Desired State 计算
系统 SHALL 根据 Task Target、Group、Label、Application Assignment 计算每个 Agent 应同步的对象集合，Agent 无需下载整个 Hub 数据库。

#### Scenario: 仅同步相关对象
- **WHEN** Agent 请求同步
- **THEN** Hub 仅返回该 Agent 相关（Target 命中其 Group/Label 或 Assignment 指定）的对象

