# Proposal: Gateway 单端口模式

## Why

Hub 当前为双独立 listener（Web `:8080` + Agent Gateway `:8443`），部署时必须对外暴露两个端口：
Web/API/Artifact 下载走 8080、Agent WebSocket 与文件传输走 8443，防火墙、反代与纳管配置都要维护两套入口。
用户要求 **Agent Gateway 与 Web 共用一个端口、对外只暴露一个**；且 **HTTPS 一律由反代终结，Hub 侧全明文**，
进一步消除双入口与双套 TLS 配置的复杂度。

## What Changes

- **ADD**（单端口模式）：当 `GatewayAddr == WebAddr` 时进入单端口模式 —— Hub 不再启动第二
  listener，最外层按 `Upgrade: websocket` 头与 `/agent/transfers/*` 路径把请求分流到 Gateway
  handler，其余请求照常进入 Web mux（REST/SPA/healthz 不变）。默认 `GatewayAddr=:8443`
  的双端口行为**完全不变**（向后兼容，零新配置项，显式把 gateway 地址配成与 web 相同即触发）。
- **ADD**（地址派生）：纳管命令与文件传输的 Gateway 地址派生继续跟随 `GatewayAddr` 的端口
  （单端口时自然派生 Web 端口），fallback 字符串不再硬编码 8443。
- **ADD**（部署口径）：HTTPS 全部反代终结 —— Hub 侧明文运行，`WEB_TLS`/`GATEWAY_TLS` 在反代
  部署下恒空；单端口模式误配 Gateway TLS 时输出 WARN。Agent 经反代以 `wss://` 连接，
  公签证书无需自定义 CA。
- 部署件同步：主仓与 polyhedron 的 compose 改为单入口（仅 8080），`.env.example`、
  `DEPLOYMENT.md`、README 网络与安全段按单端口 + 反代 HTTPS 口径重写。

## Capabilities

- **ADD**: hub-core（Gateway 单端口监听模式、按协议特征分流、地址派生、默认双端口回归）
