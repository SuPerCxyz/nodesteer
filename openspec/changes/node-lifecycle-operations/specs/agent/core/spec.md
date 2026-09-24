## ADDED Requirements

### Requirement: Agent 自助升级

系统 SHALL 支持对 native 部署的 Agent 执行自助升级（单节点或批量）：Hub 下发升级执行并记录于执行历史；Agent 下载 Hub 提供的当前构建二进制、校验 SHA256、备份当前可执行文件、替换自身并重启服务；任一失败步骤 SHALL 回滚到备份版本并回报失败。升级完成重连后 Agent SHALL 上报新版本。docker 部署形态 SHALL 不提供自升级（升级入口隐藏），由镜像更新流程处理。版本粒度为「升至 Hub 同版本」。

#### Scenario: 单节点升级

- **WHEN** 管理员对 native 部署节点发起升级
- **THEN** Hub 创建升级执行并下发，执行历史可见该记录与终态
- **AND** Agent 下载二进制并通过 SHA256 校验后替换自身、重启服务

#### Scenario: 升级失败回滚

- **WHEN** 升级过程中下载校验失败或新二进制启动异常
- **THEN** Agent 恢复备份的旧二进制并保持服务可用
- **AND** 执行记录为失败并含失败原因

#### Scenario: 升级后版本上报

- **WHEN** 升级成功且 Agent 以新版本重连
- **THEN** HELLO 上报的 `agent_version` 更新为新版本，节点详情同步显示

#### Scenario: docker 形态不提供升级

- **WHEN** 节点为 docker 部署形态
- **THEN** 升级入口不出现，不下发升级执行

#### Scenario: 批量升级

- **WHEN** 管理员多选多个 native 节点执行批量升级
- **THEN** 逐节点创建独立升级执行，各自产出成功/失败终态与原因
