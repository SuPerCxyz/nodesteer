## Context

现有原生监控基线位于未归档 unified-monitoring-automation delta，样本、inventory JSON 与既有单 series 历史 API 已存在。详见 proposal 和 spec。

## Goals / Non-Goals

**Goals:** 保留旧数据语义并对新数据表达真实容量、设备身份、容器身份与采集完整性，保证有界且可取消的图表历史加载。

**Non-Goals:** schema 迁移、批量历史 API、依赖变更、旧历史回填、现场部署或修改其他 change。

## Decisions

- 使用新增指标而不推算旧百分比的历史字节量；Bfree 和 Bavail 的不同语义得到保留。
- 块设备按 HostRoot sysfs 实际存在及 partition 标记分类并随 inventory 机制刷新；不可读视为 unknown，拒绝设备名猜测。
- 容器名称与 ID 同为维度，旧 name-only 保持独立；枚举 limit+1 并仅以 stats 成功的唯一 ID 计数，同批同时间戳上报三元组。
- 复用现有查询及缓存，通过有界并发队列与 AbortSignal 限制请求和消除旧响应，失败单独呈现和重试。

## Risks / Trade-offs

- [旧 Agent/历史无新增字段] → 明确显示未知和无数据，不回填。
- [并行工作树变更] → 检查现有差异、保留并限定本项修改。
- [大数量序列] → 不截断队列、限制并发、取消过期工作并验证缓存生命周期。

## Migration Plan

部署需另行授权；新旧 Agent 可混合运行，旧样本保持原样。回滚软件后新增 JSON 字段与指标不要求数据库迁移，旧版本按其既有兼容语义处理。

- 读取 HostRoot 的真实 sysfs（包括相对链接），Host Integration 的分类与采集须指向同一宿主；不存在或权限不足为 unknown，热插拔触发 inventory 更新。
- 每 500 样本拆批时三项计数保持同批；Hub latest 逐 series 返回时，缺批、错位、乱序、过期、未来时间、读取失败或非法值均不能判完整。2000-series 上限导致写入失败时保留既有数据，旧值变过期后必须显示 unknown；不改变硬上限或 schema。
- 隔离验收须追踪真实 Agent 采集 → Hub ingest/storage → API → 页面操作、状态、实际数据与反馈；fixture 仅验证边界，不替代真实链路。未执行项及失败原因须保留在验证记录中。

- 可选 inventory.hostname 来自同一 HostRoot 的 `/proc/sys/kernel/hostname`，不使用可覆盖的节点名伪装；旧 Agent 或读取失败显示未知。
