## Purpose

补齐 Web UI 一期缺口：Schedule 创建/编辑、Application 操作、Script Clone/Revision History、Task 参数与条件编辑、Node Detail 分区、Dashboard 统计、Execution Duration / Artifact Usage。

## ADDED Requirements

### Requirement: Schedule 创建与编辑
系统 SHALL 提供 Schedule 创建与编辑界面。

#### Scenario: 创建 Schedule
- **WHEN** 用户填写类型（cron/interval/one_time）、表达式、时区、owner、offline/misfire 策略并保存
- **THEN** 调用真实 API 创建 Schedule 并显示在列表

#### Scenario: 编辑 Schedule
- **WHEN** 用户修改现有 Schedule 并保存
- **THEN** 调用真实 API 更新

### Requirement: Application 操作
系统 SHALL 在 Application 页面提供 Deploy、Upgrade、Start、Stop、Restart 操作按钮。

#### Scenario: 触发部署
- **WHEN** 用户点击 Deploy 并选择目标节点
- **THEN** 调用真实 API 触发部署并跳转执行结果

### Requirement: Script Clone 与 Revision History
系统 SHALL 支持脚本克隆，并展示修订历史列表。

#### Scenario: 克隆脚本
- **WHEN** 用户点击 Clone
- **THEN** 创建副本并进入编辑

#### Scenario: 查看历史
- **WHEN** 用户打开脚本修订历史
- **THEN** 展示全部历史版本（编号/时间/SHA），可查看内容

### Requirement: Task 参数与条件编辑
系统 SHALL 在 Task 编辑中提供 Parameters 与 Condition 编辑入口。

#### Scenario: 编辑参数
- **WHEN** 用户编辑任务参数定义与默认值
- **THEN** 保存到真实 API

### Requirement: Node Detail 分区
系统 SHALL 在 Node Detail 展示 Tasks、Schedules、Managed Applications、Sync 分区。

#### Scenario: 查看节点任务
- **WHEN** 打开节点详情
- **THEN** 分区展示该节点相关任务、调度、托管应用与同步状态

### Requirement: Dashboard 统计
系统 SHALL 展示 Nodes Online/Offline、Execution Running/Success/Failed、Sync Synced/Outdated/Error、Application Healthy/Unhealthy 统计与 Recent Failures。

#### Scenario: 加载统计
- **WHEN** Dashboard 加载
- **THEN** 各统计来自真实后端聚合数据

### Requirement: Execution Duration 与 Artifact Usage
系统 SHALL 展示 Execution 耗时，以及 Artifact 被哪些 Application 引用。

#### Scenario: 执行耗时
- **WHEN** 查看 Execution 详情或列表
- **THEN** 显示 Start-End 计算出的 Duration

#### Scenario: Artifact 引用
- **WHEN** 查看 Artifact 详情
- **THEN** 展示引用该 Artifact 的 Application 列表
