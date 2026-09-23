# agent/applications Specification

## Purpose
定义 Managed Application 部署、健康检查、回滚和 Host Integration 的完整闭环。
## Requirements
### Requirement: Recoverable deployment

The Agent SHALL treat a systemd application health check as successful only after two consecutive `active` samples separated by a short settling interval. A transient `active` state followed by `activating`, `auto-restart`, `failed`, or a non-zero exit result SHALL fail the deployment and trigger the existing rollback path.

#### Scenario: Stable systemd health

- **WHEN** a managed systemd unit reports `active` for both health samples
- **THEN** the deployment may complete successfully

#### Scenario: Default config rollback

- **WHEN** an upgrade using the default config path fails its health check
- **THEN** the previous config and unit are restored along with the previous binary
- **AND** the deployment result reports rollback success or failure explicitly

#### Scenario: Transient systemd health

- **WHEN** a managed systemd unit reports `active` and then leaves the active state during the settling interval
- **THEN** the deployment is marked failed
- **AND** the previous binary, config, and unit are restored and checked

### Requirement: Application state visibility

The Hub SHALL persist the latest per-node application health/deployment result and expose it to the Web UI together with application execution history.

#### Scenario: Health result visible

- **WHEN** an application deployment or health check completes on a node
- **THEN** the latest health and deployment result is persisted for that node
- **AND** the application page displays that result and its execution history

### Requirement: Artifact Cache
系统 SHALL 使用内容寻址缓存（artifacts/<sha256>）保存 Artifact；下载先写 .tmp，SHA256 校验成功后才 Atomic Rename；校验失败禁止安装。

#### Scenario: Artifact 下载成功
- **WHEN** Agent 下载 Artifact 且 SHA256 校验通过
- **THEN** Agent 原子重命名缓存文件，可被部署使用

#### Scenario: Artifact 校验失败
- **WHEN** Agent 下载 Artifact 但 SHA256 不匹配
- **THEN** Agent 丢弃 .tmp 文件，拒绝安装并报告错误

#### Scenario: 缓存命中
- **WHEN** 所需 Artifact 已在缓存中
- **THEN** Agent 跳过下载直接使用缓存

### Requirement: Host Adapter
系统 SHALL 提供统一 HostAdapter 接口（WriteFile、AtomicReplace、Chmod、Chown、Mkdir、Remove、InstallUnit、UpdateUnit、DaemonReload、Enable/Disable/Start/Stop/Restart Service、ServiceStatus），由 NativeHostAdapter 与 ContainerHostAdapter 实现；Application Manager 不得维护 Native/Docker 两套逻辑。

#### Scenario: Native 部署
- **WHEN** Agent 为 Native 模式且部署路径为 /usr/local/bin/foo
- **THEN** NativeHostAdapter 以 HOST_ROOT=/ 操作该路径

#### Scenario: Docker Host Integration 部署
- **WHEN** Agent 为 Docker Host Integration 模式且部署路径为 /usr/local/bin/foo
- **THEN** ContainerHostAdapter 以 HOST_ROOT=/host 映射为 /host/usr/local/bin/foo 操作

### Requirement: Managed systemd
系统 SHALL 通过 Managed Unit Registry（application_id→unit_name）约束，只允许操作由 NodeSteer 创建并登记的 Unit；支持 Install/Update Unit、daemon-reload、enable/disable/start/stop/restart/status。

#### Scenario: 操作已登记 Unit
- **WHEN** Application API 操作已登记的 Unit
- **THEN** 操作成功执行

#### Scenario: 操作未登记 Unit
- **WHEN** Application API 尝试操作未登记 Unit
- **THEN** 系统拒绝并返回错误

### Requirement: Application Deployment
系统 SHALL 支持部署流程：Resolve Artifact→Download/Cache→SHA256 Verify→Prepare→Install Binary→Install Config→Install Unit→daemon-reload→enable→start→Health Check。

#### Scenario: 部署成功
- **WHEN** 部署流程全部成功且 Health Check 通过
- **THEN** 应用正常运行并标记部署成功

#### Scenario: 部署中 Health Check 失败
- **WHEN** 部署后 Health Check 失败
- **THEN** 系统执行失败恢复流程

### Requirement: Application Upgrade 与 Rollback
系统 SHALL 支持升级流程（Prefetch→Verify→Backup→Stop→Atomic Replace→Update Config/Unit→Start→Health Check），升级前至少保留一个 Previous；Health Check 失败时执行 Rollback（Stop New→Restore Previous Binary/Config/Unit→daemon-reload→Start Previous→Health Check）。

#### Scenario: 升级成功
- **WHEN** 升级流程完成且 Health Check 通过
- **THEN** 新版本运行，Previous 备份保留

#### Scenario: 升级失败回滚
- **WHEN** 新版本 Health Check 失败
- **THEN** 系统恢复 Previous 版本并再次执行 Health Check，Execution 可 FAILED 同时 Deployment Result 为 ROLLBACK_SUCCESS

### Requirement: Health Check
系统 SHALL 提供统一 HealthChecker，支持 SYSTEMD、TCP、HTTP、COMMAND 四种类型，支持 timeout、attempts、interval 配置。

#### Scenario: HTTP 健康检查
- **WHEN** 应用配置 HTTP Health Check 且返回成功
- **THEN** 应用标记为 Healthy

#### Scenario: TCP 健康检查失败
- **WHEN** 应用 TCP Health Check 失败
- **THEN** 应用标记为 Unhealthy

### Requirement: Deployment Journal 与 Crash Recovery
系统 SHALL 为每次部署持久化 Deployment Journal（deployment_id、application_id、from_version、to_version、phase、backup_path、started_at），Phase 包含 PREPARING/STOPPED/REPLACED/STARTED/VERIFYING/DONE/ROLLING_BACK；Agent Crash/Restart 后识别半完成部署并安全恢复 Previous。

#### Scenario: 崩溃后恢复
- **WHEN** Agent 在部署中途崩溃并重启
- **THEN** Agent 读取 Deployment Journal，默认优先安全恢复 Previous 版本

### Requirement: systemd Unit Registry
系统 SHALL 将 Managed Application 与 systemd Unit 建立 Registry，仅允许操作已登记 Unit。

#### Scenario: 操作已登记 Unit
- **WHEN** 对已部署应用的 Unit 执行 start/stop/restart
- **THEN** 操作成功执行

#### Scenario: 操作未登记 Unit
- **WHEN** 收到针对未登记 Unit 的操作请求
- **THEN** 系统拒绝并返回错误

### Requirement: Docker Host Integration Inventory
系统 SHALL 在 Docker Host Integration 模式下报告宿主机 Inventory，而非容器自身状态。

#### Scenario: Host Integration 上报宿主机信息
- **WHEN** Agent 以 Docker Host Integration 模式运行
- **THEN** Inventory 读取 `/host` 挂载下的宿主机 OS/Kernel/CPU/Memory/Filesystem/Network

#### Scenario: 无法可靠获得
- **WHEN** 宿主机信息无法可靠读取
- **THEN** 返回 UNAVAILABLE，不伪装容器状态为宿主机状态

