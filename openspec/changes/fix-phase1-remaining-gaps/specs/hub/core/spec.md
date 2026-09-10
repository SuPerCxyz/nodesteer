## Purpose

修复一期剩余缺口：Remote Condition Fail Open、RunUser 未接线、Agent-owned App 部署链路、Prefetch 覆盖、Settings 运行期消费、日志流标记、Inventory、Capability 校验、Rollback 完整性。

## ADDED Requirements

### Requirement: Run User 接线
系统 SHALL 支持以指定用户运行任务。

#### Scenario: 指定运行用户
- **WHEN** Task 或 Script 配置了 run_user 且 Agent 以 root 运行
- **THEN** 执行进程以该用户身份启动
- **AND** 未配置时保持现状（当前用户）

### Requirement: Agent-owned 调度的应用部署
系统 SHALL 支持 execution_owner=agent 的 Application Deploy/Upgrade Task。

#### Scenario: Agent 本地触发部署
- **WHEN** Agent Local Scheduler 触发 application_deploy Task
- **THEN** Agent 从本地应用定义解析 binary_path/unit/health 并执行部署
- **AND** 结果经 DeployResult 上报 Hub

### Requirement: Prefetch 覆盖调度部署
系统 SHALL 在 Scheduled/Offline Application Deployment 前向 Agent 下发 PREFETCH。

#### Scenario: 调度部署前预取
- **WHEN** Agent-owned Schedule 触发 Application Deploy/Upgrade
- **THEN** Agent 先下载并校验 Artifact 到缓存
- **AND** 部署时直接使用缓存

### Requirement: Settings 运行期生效
系统 SHALL 使 heartbeat_interval_sec / changelog_window 等设置运行期生效。

#### Scenario: 修改设置生效
- **WHEN** 管理员通过 /api/settings 修改 changelog_window
- **THEN** Hub 运行期读取新值，无需重启

## MODIFIED Requirements

### Requirement: Remote Node Condition
系统 SHALL 在远程状态解析结果为 UNKNOWN 时按 Fail Closed 处理，不得将未知值作为普通值参与比较。

#### Scenario: 远程节点不存在
- **WHEN** Remote Condition 引用的节点不存在或属性不可解析
- **THEN** 返回不可评估标记（evaluated=false）
- **AND** 执行被 BLOCKED，不得放行

#### Scenario: 未知值参与比较
- **WHEN** 远程状态值恰为字符串 "UNKNOWN"
- **THEN** 该值不得参与 ==/!= 等比较，一律视为不可评估
- **AND** 执行结果 BLOCKED
