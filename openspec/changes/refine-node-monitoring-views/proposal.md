## Why

现有节点监控视图缺少真实容量、设备类别和容器采集完整性，且多序列历史不能可靠表达实际身份与缺失。已确认的功能完整性审查和 Explore 要求补齐采集、协议、查询、展示及端到端验证。

## What Changes

- 新增文件系统总量/已用字节、空闲内存、块设备分类和容器稳定 ID 与采集完整性三元组；保留旧指标及历史含义。
- 改进节点详情、时间范围、节点表导航、CPU/内存/磁盘/容器图表和并发受控的独立历史查询。
- 保持旧 Agent、无数据、错误和采集上限可辨识；使用现有存储和查询 API。

## Capabilities

### New Capabilities

- `node-monitoring-views`: 节点监控采集真实性、身份、完整性及可交互的历史展示。现有 `unified-monitoring` 仅存在于未归档 change 的 delta，本 change 不假称已存在主 spec。

### Modified Capabilities

无。

## Impact

Agent、inventory JSON、Hub 指标单位与查询、Web、测试及直接相关文档。无数据库迁移、依赖、后台批量接口、现场部署或其他 change 归档。

容量图按挂载点命名并在 Y 轴、图例和提示中使用真实字节容量；IOPS 仅含 sysfs 确认的整块设备（包括 dm/md），不含分区。旧 Agent 缺少 `memory.free_bytes` 或可选 `inventory.block_devices` 时显示未知或无数据。详情标题显示主机名、运行时长、发行版、内核、CPU、内存；桌面同一视觉行、移动端换行，不显示 ready 或最近采样技术行，异常仍须反馈。四张容器图默认显示全部可用 series。

三个无维度 gauge 为 `container.collection.discovered_count`、`container.collection.sampled_count`、`container.collection.limit`，同批同 timestamp，limit 为 100；101 表示至少 101。仅有效三元组可判，sampled 小于 discovered 为 partial；缺失、过期、错位或非法为 unknown，不补零。不新增 protobuf 生成、SQL 迁移、依赖或 batch endpoint。

- 可选 inventory.hostname 来自同一 HostRoot 的 `/proc/sys/kernel/hostname`，不使用可覆盖的节点名伪装；旧 Agent 或读取失败显示未知。
