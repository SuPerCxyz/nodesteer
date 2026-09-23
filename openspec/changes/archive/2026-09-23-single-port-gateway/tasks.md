# Tasks

## 1. OpenSpec artifact（主 Agent）

- [x] 1.1 创建 change 目录、proposal、hub/core ADDED delta、tasks

## 2. Go 单端口模式与测试（S1）

- [x] 2.1 `internal/hubserver/hub.go`：`GatewayAddr == WebAddr` 判定单端口模式；不启第二 listener；Web 最终 handler 外包分流 middleware（`Upgrade: websocket` 或 `/agent/transfers/*` → `gw.Handler()`，其余 → Web 路由）；单端口误配 Gateway TLS 输出 WARN
- [x] 2.2 `internal/hub/file_transfer.go`：`ResolveGatewayBaseURL` fallback 不再硬编码 8443（跟随 gateway 端口）
- [x] 2.3 新增单端口测试：同端口 WS 握手注册心跳、`/agent/transfers/*` 经同端口、REST/healthz 正常、纳管派生断言 `ws://…` 为共同端口
- [x] 2.4 双端口现有测试全量回归通过

## 3. 主仓部署件与文档（S2）

- [x] 3.1 `docker-compose.yml`：`NODESTEER_GATEWAY_ADDR` 与 Web 同值、ports 仅留 8080（8443 改可选注释）
- [x] 3.2 `.env.example`：移除独立 Gateway 端口项，补单端口说明
- [x] 3.3 `docs/DEPLOYMENT.md`：env 表、网络/反代节改单入口 + 明文上游 + HTTPS 全反代口径，附旧纳管命令重生成提示
- [x] 3.4 `README.md` 网络与安全段同步单口口径

## 4. polyhedron 侧（S3，含精简欠账）

- [x] 4.1 `nodesteer_hub/docker-compose.yaml`：`NODESTEER_GATEWAY_ADDR=:8080`、ports 注释仅 8080 单入口
- [x] 4.2 `nodesteer_hub/.env.example`：修复 hub.yaml 引用欠账（role_mappings 能力边界句）+ gateway 地址注释更新
- [x] 4.3 `nodesteer_agent/.env.example`：`NODESTEER_HUB_URL` 示例改单端口（`ws(s)://host:8080`）

## 5. 验收（主 Agent）

- [x] 5.1 全量回归：go vet/test、openspec validate --all、compose config（两仓库）
- [x] 5.2 跨仓库一致性：单端口触发条件、8080 唯一入口、反代 HTTPS 口径三处文档自洽
- [x] 5.3 两仓库 commit/push 分别请求用户授权
