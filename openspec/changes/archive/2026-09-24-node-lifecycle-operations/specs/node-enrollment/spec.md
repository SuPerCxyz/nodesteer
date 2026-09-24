## ADDED Requirements

### Requirement: Agent 自动检测并上报运行参数

Agent SHALL 在启动时自动生成运行参数而非依赖配置硬编码：`agent_version` 来自编译期注入的真实构建版本；`deployment_mode` 在未显式配置时自动探测（容器环境为 docker，否则 native，显式配置优先）；`host_integration` 由部署模式推导。纳管命令与生成的配置模板 SHALL NOT 硬编码这些参数（既有配置文件保持向后兼容）。Agent SHALL 通过 HELLO 上报真实值；Hub SHALL 对上报的 `deployment_mode` 做枚举白名单校验，非法值拒绝或归一化。`data_dir` 为本地路径，SHALL NOT 上报。

#### Scenario: 版本来自编译注入

- **WHEN** CI 或本地以 ldflags 构建 Agent 二进制并启动
- **THEN** `agent_version` 为编译注入的真实版本值，HELLO 上报该值
- **AND** 未注入时回退到明确的未知/开发默认值而非伪造版本

#### Scenario: 部署模式自动探测

- **WHEN** Agent 启动且未显式配置 `deployment_mode`
- **THEN** 运行于容器环境时探测为 docker，裸机运行时为 native
- **AND** 显式配置或环境变量始终优先于探测结果

#### Scenario: 纳管模板去硬编码

- **WHEN** 管理员生成纳管命令
- **THEN** 生成的配置不再写入 `deployment_mode`/`host_integration`/`agent_version` 硬编码键，由 Agent 启动时自动生成
- **AND** 既有旧配置文件仍可正常解析

#### Scenario: Hub 校验上报模式

- **WHEN** HELLO 上报的 `deployment_mode` 不在合法枚举内
- **THEN** Hub 拒绝或归一化该值，不将非法值写入节点记录
