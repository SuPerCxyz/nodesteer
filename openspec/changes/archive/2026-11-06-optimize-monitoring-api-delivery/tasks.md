## 1. 传输压缩

- [x] 1.1 API JSON gzip 中间件（Accept-Encoding、Vary、二进制/SSE/304/HEAD 跳过）。
- [x] 1.2 静态资源 gzip/可选 brotli、ETag/Last-Modified、HTML no-cache、哈希资源 immutable。

## 2. Overview 与缓存

- [x] 2.1 overview 仅查询所需 metric，latest 与 interval 窗口缩到 15min。
- [x] 2.2 15s overview 缓存，按 node 集合与 metric generation 失效。
- [x] 2.3 单节点 overview 摘要接口。

## 3. 批量接口与目录缓存

- [x] 3.1 批量 series 接口 + 60s 目录缓存。
- [x] 3.2 批量 history 接口 + 30s 缓存。
- [x] 3.3 旧接口契约测试不变。

## 4. 索引与观测

- [x] 4.1 schema v28 查询索引 migration + 测试。
- [x] 4.2 API 慢请求日志/计数。
- [x] 4.3 原生指标慢 SQL 日志。
- [x] 4.4 EXPLAIN QUERY PLAN 复核关键查询无全表扫描。

## 5. 验证

- [x] 5.1 `make test`、`make lint`、相关包 `-count=1`、`-race`。
- [x] 5.2 同 DB 快照实测 overview/history 优化前后耗时。
- [x] 5.3 gzip JSON/JS/CSS、二进制不压缩、缓存头、ETag 304 验证。
- [x] 5.4 静态资源传输体积实测。
- [x] 5.5 批量接口契约测试与旧 API 兼容验证。
- [x] 5.6 文档同步 `docs/ARCHITECTURE.md`。

## 验证记录

DB 快照：`/tmp/opencode/nodesteer-perf/hub-snapshot.db`（preview DB 只读备份，约 218 万 raw、88 万 rollup、8 节点）。旧二进制来自 `git archive HEAD`，新二进制为本工作树；两边使用同一快照副本，未触碰 18810 进程。

### Overview（8 节点）

| 场景 | 旧 | 新 |
|---|---:|---:|
| HTTP identity | 1.38–1.65s / 3895B | 0.13s 冷 / 1.2–2.2ms 热 / 3895B |
| HTTP gzip | 未压缩 1.5s / 3895B | 0.13s 冷 / 1.1ms 热 / 1075B |
| latest SQL | 0.422s | 0.047s |
| interval SQL | 0.206s | 0.006s |

查询计划：overview latest 使用 `sqlite_autoindex_node_metric_samples_1` 覆盖索引，无全表扫描。

### JSON gzip

| 响应 | identity | gzip | 比例 |
|---|---:|---:|---:|
| e59 `/latest` | 41531B | 2749B | 15.1x |
| tna `/latest` | 44694B | 2494B | 17.9x |
| e59 `disk.usage` history | 67115B | 7784B | 8.6x |

### 静态资源

| 资源 | raw/gzip | 旧 | 新 |
|---|---:|---:|---:|
| `assets/index-BzHK3YOh.js` | 254622 / 94831 | 254622 | 94831 |
| `assets/index-B6mrWD0o.css` | 103598 / 22223 | 103598 | 22223 |
| `fonts/maple-mono...woff2` | 1002824 | 1002824 | 1002824（已压缩，不重压） |
| `images/nodesteer-logo-original.png` | 573059 | 573059 | 573059（已压缩，不重压） |

- 首页 index 引用的文本资源合计 764241B → 301584B（gzip）。
- 叠加字体/logo 后首次加载约 2.6MB → 约 2.15MB；字体与 PNG 已压缩，服务端无法进一步显著缩小，需前端后续替换/裁剪。热加载：哈希 JS/CSS `immutable`，字体/图片 ETag 304 或 1 天缓存，重复加载为 0 传输。
- ETag 304、`Cache-Control`、`Vary`、`Content-Encoding` 已实测。

### 索引

- schema v28 在 1.4GB 快照副本上创建 6 个索引耗时约 2.2s；只建索引，无数据回填。

### 测试

- `make test`、`make lint` 退出码 0。
- `go test -race -buildvcs=false ./internal/hub/api ./internal/hubserver ./internal/store -count=1` 通过。
- 新增契约测试：series/history batch、单节点 overview、overview 缓存失效、gzip JSON/二进制、静态 gzip/304/缓存头、v28 索引。
