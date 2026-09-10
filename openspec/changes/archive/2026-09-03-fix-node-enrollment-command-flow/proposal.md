## Why

节点纳管弹窗当前只生成命令，不会留下待接入节点记录；生成的 Docker 命令还使用了本地镜像名，且三种方式的复制体验不一致。管理员执行生成动作后应能立即看到节点占位记录，并可直接复制完整的安装内容。

## What Changes

- 将纳管命令生成改为管理员授权的写操作，成功后持久化一条待接入、初始离线的节点记录。
- 为待接入记录预分配 Agent 身份，并写入 Native、`docker run`、`docker compose` 安装内容，使 Agent 首次连接时复用该记录而不是创建重复节点。
- 将 Docker 镜像改为 `ghcr.io/supercxyz/cadentra-agent:latest`。
- 将“生成安装命令”放在左侧、“复制”放在右侧，三种安装方式均复制完整原文。
- 保留现有输入校验、管理员权限、Agent 持久化目录和既有注册令牌流程。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `node-enrollment`: 修改生成动作、待接入节点记录、预分配 Agent 身份、Docker 镜像地址和完整复制行为。
- `agent/core`: 支持从生成的 Native 配置或 Docker 环境变量加载预分配 Agent 身份，并在首次 HELLO 时绑定已有待接入记录。

## Impact

- Hub API：新增 POST `/api/nodes/enrollment` 纳管生成接口；保留 GET 元数据接口兼容已有调用方。
- Hub NodeManager/SQLite：新增待接入节点创建与凭证持久化路径。
- Agent 配置、环境变量和 HELLO 身份加载链路。
- Web Nodes 纳管弹窗、节点列表缓存刷新与复制交互。
- 补充 Hub/Agent/前端构建及相关行为测试；无新增依赖、无数据库迁移。
