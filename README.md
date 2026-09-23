<p align="center">
  <img src="web/public/images/cadentra-logo-original.png" alt="NodeSteer" width="280">
</p>

<h1 align="center">NodeSteer</h1>

<p align="center">轻量级 Linux Hub-Agent 自动化控制平台</p>

<p align="center">
  Hub 集中定义自动化意图，Agent 主动连接、同步并可靠执行任务。
</p>

## 目录

- [项目简介](#项目简介)
- [核心能力](#核心能力)
- [架构](#架构)
- [快速开始](#快速开始)
- [部署](#部署)
- [网络与安全](#网络与安全)
- [开发与测试](#开发与测试)
- [项目结构](#项目结构)
- [文档](#文档)
- [第三方声明](#第三方声明)

## 项目简介

NodeSteer 面向 Linux 服务器提供 Hub-Agent 自动化管理、任务调度、远程执行和二进制应用部署能力。

Hub 是 Desired State 的唯一权威源；Agent 主动连接 Hub，缓存已同步配置并负责本地调度与执行。Hub 暂时不可用时，已同步且明确允许离线执行的任务仍可安全运行，恢复连接后由 Agent 完成状态收敛。

> 项目品牌已统一为 NodeSteer。当前代码、二进制、服务、路径和镜像仍沿用 `cadentra-*` 等技术标识，后续将单独迁移。

## 核心能力

- **Hub 控制平面**：Web 管理、REST API、节点/分组/标签、脚本、任务、调度、发布包、托管应用、执行记录、日志、RBAC 和审计。
- **Agent 执行平面**：主动连接、注册认证、Heartbeat、Inventory、脚本/命令执行、Local Scheduler、离线执行、Execution Journal 和 Health Check。
- **可靠同步**：长连接实时通知、Revision 周期校验、Reconnect Reconciliation 和 Tombstone 删除同步。
- **可靠执行**：Execution 幂等、执行前 Journal 持久化、实时/离线日志和断线后的结果回传。
- **应用交付**：Artifact SHA256 校验、缓存、Prefetch、Managed Application、systemd 生命周期和基础回滚。
- **多种部署方式**：Native Agent、Docker Agent、Docker Host Integration、Docker Compose。
- **文件中继**：将源 Agent 文件经 Hub 校验后转发到一个或多个目标节点。

## 架构

```text
                         Users
                           │
                           ▼
                    ┌─────────────┐
                    │   Web UI    │
                    └──────┬──────┘
                           │ REST API
                           ▼
                ┌────────────────────┐
                │    NodeSteer Hub    │
                │ API / Sync / Tasks │
                │ Gateway / Artifact │
                └─────────┬──────────┘
                          │ Agent Gateway
             ┌────────────┴────────────┐
             │                         │
             ▼                         ▼
       Native Agent               Docker Agent
             │                         │
             └────────────┬────────────┘
                          ▼
                     Linux Host
```

默认端口：

- Web UI / REST API：8080
- Agent Gateway：8443

## 快速开始

### 1. 获取源码并构建

```bash
git clone https://github.com/SuPerCxyz/cadentra.git
cd cadentra

make web         # 安装前端依赖
make web-build   # 构建 Web 前端
make build       # 构建 Hub 与 Agent 二进制
```

前端也可以单独执行：

```bash
cd web
pnpm install --frozen-lockfile
pnpm run build
```

### 2. 启动 Hub

```bash
cp packaging/systemd/hub.yaml.example /etc/cadentra/hub.yaml
# 编辑 registration_token、admin_password 和 base_url
./bin/cadentra-hub --config /etc/cadentra/hub.yaml
```

访问 http://localhost:8080 打开 Web UI。Agent Gateway 默认监听 :8443。

### 3. 部署 Native Agent

```bash
cp packaging/systemd/agent.yaml.example /etc/cadentra/agent.yaml
# 编辑 hub_url 和 registration_token
./bin/cadentra-agent --config /etc/cadentra/agent.yaml
```

Agent 首次连接使用 Registration Token 注册，随后获取并持久化唯一 Agent Credential。

## 部署

### Docker Compose

```bash
cp .env.example .env
# 按部署网络修改 REGISTRATION_TOKEN、HUB_BASE_URL 等配置
docker compose up -d --build
```

Compose 默认同时提供 Hub 和一个 Docker Agent。Agent 数据保存在 agent-data 卷，Hub 数据和发布包保存在 hub-data 卷。**环境变量全表、OIDC 配置与故障恢复、升级备份见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。**

如需从其他网络接入 Agent，将 HUB_GATEWAY_BASE_URL 设置为 Agent 可访问的 Gateway 地址。节点页面可以生成 Native、docker run 和 Docker Compose 纳管命令，并携带节点身份和 Registration Token；节点地址支持 IPv4、IPv6 或 DNS 主机名。

### CI 构建产物

推送到 `master` 或版本标签后，GitHub Actions 会构建 Linux amd64/arm64 的 Hub/Agent 二进制并上传为构建制品，同时发布包含 `linux/amd64` 和 `linux/arm64` 的 GHCR 多架构镜像：

```text
ghcr.io/supercxyz/cadentra-hub:latest
ghcr.io/supercxyz/cadentra-agent:latest
```

### systemd

```bash
install bin/cadentra-agent /usr/local/bin/cadentra-agent
install packaging/systemd/cadentra-hub.service /etc/systemd/system/
install packaging/systemd/cadentra-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cadentra-hub
systemctl enable --now cadentra-agent
```

Hub 的节点纳管页面不要求手动选择架构。Native 安装命令会在目标机通过 `uname -m` 判断 x86/amd64 或 ARM/arm64，从 Hub 公开二进制端点下载并校验 SHA256。标准构建会把 Agent payload 直接打包进 Hub 可执行文件；`agent_binary_amd64_path` 和 `agent_binary_arm64_path` 仅作为未打包本地开发构建的回退路径。Docker Hub 镜像和 CI 发布镜像使用内置 payload 提供 amd64/arm64 Agent。

### Agent 部署模式

- native：直接管理宿主机，适合生产部署。
- docker：在容器内运行 Agent Core，状态通过 /var/lib/cadentra 持久化。
- docker_host_integration：通过 Host Adapter 访问允许的宿主机路径和能力。

## 网络与安全

Agent 采用主动连接模型，默认不需要开放 Agent 入站管理端口。Hub 内网、Agent 公网的场景只需将 Agent Gateway（默认 8443）通过 NAT 或反向代理暴露给 Agent。

生产环境建议为 Web/API 和 Agent Gateway 配置 TLS，并让 Agent 使用 wss:// 和 tls_ca_file 验证 Hub 证书。Artifact 下载使用独立的 HTTPS 数据通道，不占用控制长连接。

## 开发与测试

前端开发：

```bash
cd web
pnpm run dev
pnpm run format:check
pnpm run lint
pnpm run test
pnpm run build
```

后端和全仓库检查：

```bash
make test       # Go 测试
make lint       # go vet
make build      # Hub / Agent 构建
```

前端浏览器测试需要 Playwright 浏览器运行时；Ubuntu 26.04 使用项目锁定版本安装依赖前，请确保 Playwright 版本支持该系统。

## 项目结构

```text
cmd/hub/            Hub 入口
cmd/agent/          Agent 入口
internal/hub/       Hub 领域服务
internal/hub/api/   REST API
internal/hub/auth/  认证与 RBAC
internal/hubserver/ Hub 组装与内嵌前端服务
internal/agent/     Agent Core
internal/agent/host Native / Container HostAdapter
internal/protocol/  Hub-Agent 控制协议
internal/store/     持久化层
internal/models/    共享模型
migrations/         数据库迁移目录（当前为空）
web/                React + Vite + TypeScript 前端
packaging/          Docker 与 systemd 部署文件
docs/               产品、架构和测试文档
openspec/            变更管理与规格文件
```

## 文档

- [docs/PRODUCT.md](docs/PRODUCT.md)：产品功能基线和一期范围
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：系统架构和一致性约束
- [docs/TEST_PLAN.md](docs/TEST_PLAN.md)：测试计划
- [docs/TRACEABILITY_MATRIX.md](docs/TRACEABILITY_MATRIX.md)：需求追踪矩阵
- [docs/REAL_TEST_REPORT.md](docs/REAL_TEST_REPORT.md)：真实环境功能测试与失败项复验报告
- [docs/UI_FULL_TEST_PLAN.md](docs/UI_FULL_TEST_PLAN.md)：Web 页面全量黑盒测试计划
- [docs/UI_FULL_TEST_REPORT_20260915.md](docs/UI_FULL_TEST_REPORT_20260915.md)：页面全量测试执行报告
- [docs/IMPLEMENTATION_PROMPT.md](docs/IMPLEMENTATION_PROMPT.md)：研发实施总控提示词
- [docs/DEV_RULES.md](docs/DEV_RULES.md)：实现原则和项目完成门禁（AGENTS.md 分片）
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)：Docker Compose 部署、环境变量与 OIDC 配置

## 第三方声明

前端使用的 shadcn-admin 模板及其他第三方组件声明见 [web/THIRD_PARTY_LICENSES.md](web/THIRD_PARTY_LICENSES.md)。
