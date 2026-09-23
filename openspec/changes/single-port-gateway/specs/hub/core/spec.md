# hub-core Specification Delta

## ADDED Requirements

### Requirement: Gateway 单端口模式
系统 SHALL 支持 Agent Gateway 与 Web API 共用同一监听地址：当配置的 Gateway 地址与 Web 地址相同时，Hub SHALL 只启动一个 listener，并按请求特征分流 —— 携带 `Upgrade: websocket` 头的请求与 `/agent/transfers/*` 路径 SHALL 转入 Gateway 处理，其余请求 SHALL 照常进入 Web 路由。配置不同时（默认 `:8443`）SHALL 保持既有双独立 listener 行为不变。

#### Scenario: 单端口按协议特征分流
- **WHEN** Hub 配置 `gateway_addr` 与 `web_addr` 相同（如均为 `:8080`）
- **THEN** Hub 仅启动一个 listener
- **AND** 同一端口上：Agent WebSocket 握手（子协议 `nodesteer`）成功建立并完成注册与心跳
- **AND** `/agent/transfers/*` 上传/下载经同一端口完成
- **AND** REST API、静态前端与 `/healthz` 在同一端口正常响应
- **AND** 若此时误配 Gateway TLS 证书，Hub 输出 WARN 且不影响启动（反代部署下 TLS 恒空）

#### Scenario: 纳管与传输地址跟随单端口派生
- **WHEN** 未显式配置 `gateway_base_url` 且 Gateway 地址与 Web 同端口
- **THEN** 生成的纳管命令 WebSocket 地址与文件传输地址使用该共同端口（如 `ws://host:8080`）
- **AND** 地址派生不再依赖硬编码的 8443 回退值

#### Scenario: 默认双端口行为回归
- **WHEN** `gateway_addr` 保持默认 `:8443` 且与 `web_addr` 不同
- **THEN** Web 与 Gateway 维持两个独立 listener，既有连接、传输与纳管行为不发生任何变化
