## Purpose

修复 Agent 端一期缺口：Remote Condition Fail Open、RunUser 未接线、Agent-owned App 部署链路、Prefetch、stderr 流标记、Inventory、Rollback 完整性。

## ADDED Requirements

### Requirement: 实时日志流标记
系统 SHALL 正确标记 Realtime Log 分片的 stream（stdout/stderr）。

#### Scenario: stderr 分片
- **WHEN** 进程向 stderr 输出且触发分片上报
- **THEN** LOG_CHUNK 的 stream 字段为 "stderr"
- **AND** stdout 输出 stream 为 "stdout"

### Requirement: Inventory 完整性
系统 SHALL 采集 OS Version 与 CPU MHz。

#### Scenario: Inventory 上报
- **WHEN** Agent 采集并上报 Inventory
- **THEN** os_version 非空、cpu[].mhz 反映真实频率（无法获取时返回 0）
- **AND** 无法可靠获取 OS 版本时标记 UNAVAILABLE

### Requirement: 回滚完整性
系统 SHALL 在升级失败回滚时恢复 config/unit 并执行回滚后 Health Check。

#### Scenario: 健康检查失败回滚
- **WHEN** 新版本 Health Check 失败
- **THEN** 恢复 Previous 二进制、config、unit，daemon-reload 后启动 Previous
- **AND** 对 Previous 执行 Health Check
