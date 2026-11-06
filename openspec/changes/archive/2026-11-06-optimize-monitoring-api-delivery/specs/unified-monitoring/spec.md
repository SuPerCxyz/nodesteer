## ADDED Requirements

### Requirement: API JSON 压缩

Hub API SHALL 在客户端声明 `Accept-Encoding: gzip` 时压缩 JSON 响应，并设置 `Content-Encoding: gzip` 与 `Vary: Accept-Encoding`。二进制、SSE、无 body 状态码和已编码响应 MUST 保持原样。

#### Scenario: 大 JSON 响应

- **WHEN** 客户端请求监控历史 JSON 并接受 gzip
- **THEN** 响应使用 gzip 编码
- **AND** 解压后 JSON 与未压缩响应语义一致

#### Scenario: 二进制下载

- **WHEN** 客户端下载 Agent 二进制或 artifact 并接受 gzip
- **THEN** 响应不设置 `Content-Encoding: gzip`
- **AND** 文件字节不变

### Requirement: 静态资源压缩与缓存

Hub 静态文件服务 SHALL 对文本资源提供 gzip（可选预压缩 brotli），设置 HTML `no-cache`、哈希资源 `immutable`、其他静态资源短缓存，并支持 ETag/Last-Modified 304。

#### Scenario: 哈希 JS 首次与再次加载

- **WHEN** 浏览器请求 `/assets/index-<hash>.js`
- **THEN** 首次返回 gzip 内容与一年 immutable 缓存头
- **AND** 再次携带 `If-None-Match` 返回 304 且无 body

#### Scenario: HTML 入口

- **WHEN** 浏览器请求 `/` 或 SPA 回退
- **THEN** 返回 `Cache-Control: no-cache`

### Requirement: Overview 查询裁剪与缓存

Hub overview SHALL 只查询页面需要的 metric，latest 时间窗口不超过 15 分钟，采样周期推导窗口不超过 15 分钟；SHALL 使用进程内缓存并在指标写入 generation 变化时失效。

#### Scenario: 大库 overview

- **WHEN** 数据库包含百万级 raw 样本且 overview 被请求
- **THEN** 查询只扫描所需 metric 的最近窗口
- **AND** 15s 内重复请求命中缓存

### Requirement: 单节点摘要与批量接口

Hub SHALL 提供单节点 overview 摘要、批量 series 和批量 history 接口，同时保持旧接口兼容。

#### Scenario: 节点详情批量加载

- **WHEN** 客户端请求 `series/batch` 与 `history/batch`
- **THEN** 单次请求返回最多 64 个 metric/series 的结果
- **AND** 旧 `/series` 与 `/metrics` 接口行为不变

### Requirement: 查询索引与慢查询观测

Hub SHALL 通过 schema v28 增加 execution/file transfer/audit 的 created_at 索引，并记录超过阈值的 API/SQL 耗时。

#### Scenario: 执行列表排序

- **WHEN** 执行列表按 `created_at DESC` 查询
- **THEN** 查询计划使用 created_at 索引

#### Scenario: 慢请求

- **WHEN** API 或原生指标 SQL 超过阈值
- **THEN** 记录 method/path 或操作名与耗时
- **AND** 不记录查询值、token 或私钥
