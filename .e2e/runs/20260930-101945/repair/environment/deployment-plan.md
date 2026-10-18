# Repair E2E 环境准备

## 当前只读预检

详见同目录 `preflight.json`。kvm2 的五个 VM 全部 `running`，QGA ping 正常；kvm2 和五台 VM 的 SSH 只读检查均成功。原 `.209` Hub 和四台原 Native Agent 服务仍 active。`.209` 的 `18080`、`18443` 当时没有 TCP listener，也没有 Docker 端口映射；修复前缀下没有 systemd unit、容器、volume 或 network。以上均为快照，启动前须再核对端口和资源名。

Node3 `.204` 仅约 904 MiB 可用，留作轻量 Native 检查；镜像和大 Artifact 放 Hub VM 或空间较充足的 `.207`、`.222`、`.247`。五台均为 x86_64；五台均有 Docker 与 Compose CLI/daemon。

现有私有 primary connection 只可提供 SSH 基础设施入口；其 Hub token、admin 密码、registration token、旧 Node/Agent ID 与 Agent credential 均不得复制到 repair authority。

## 构建产物门禁

Root 冻结修复源码并给出构建 revision、Agent 上报版本、SHA256 后才部署。Hub 需包含最终 Web `dist` 和 amd64 Agent payload；Agent 需为相同源码 revision 的 Linux amd64 构建。仓库入口为先 `make web-build`，再 `make build`，输出 `bin/nodesteer-hub` 与 `bin/nodesteer-agent`。已存在的 `make docker` 会写 `nodesteer/*:latest`，不得用于本轮；Docker/Compose/Host Integration 如需镜像，使用唯一 repair tag 并记录镜像 digest，内容应对应 Root 冻结的 Agent 构建。

部署前必须收到 Root 提供的确切 revision、二进制 SHA256 和 Agent 版本；上传后在 VM 复算 SHA256 一致。当前没有部署任何新二进制。

## 隔离拓扑

唯一 repair 前缀：`qa-repair-20260930-101945`。

- 主 repair Hub 在 `.209` 使用 Web+Gateway 单端口 `:18080`，对四台测试 VM 可达；Web URL `http://192.168.100.209:18080`，Agent Gateway `ws://192.168.100.209:18080`。独立二进制放 `/opt/nodesteer-repair-20260930-101945/`，独立 config 放 `/etc/nodesteer/hub-repair-20260930-101945.yaml`，DB/artifact 放 `/var/lib/nodesteer-hub-repair-20260930-101945/`，独立 unit `nodesteer-hub-repair-20260930-101945.service`。Hub 从空数据目录启动，使用新建的 repair-only admin 与 registration secret；不复制 primary DB、用户或凭据。
- 只有需要 TLS 的 case 才在 `.209:18443` 建第二个独立 authority；它必须有独立 Hub config、DB、admin/registration credentials、TLS material 和 Agent identities，不能与主 repair Hub 共用状态。当前 `18443` 仅记为预检可用候选端口。
- 四台 Native repair Agent 与原服务并行运行，每台使用相同构建版本但独立二进制副本、唯一 config、全新 `data_dir` 和 unit；`/usr/local/bin/nodesteer-agent`、原 config/state/service 保持原样。每台新 Agent 以空 state 注册，生成新 AgentID/credential；不得填入原 AgentID 或读取复用旧 Agent credential。
- `PrepareEnrollment` 会按 NodeIP 复用身份。同一 repair authority 内不同 fixture（Native、Docker、Compose、Host Integration）必须分别使用 QA 标记的唯一 `.invalid` NodeIP/DNS alias、唯一 `node_name`、独立 `data_dir` 与生成的 AgentID。私有 connection 将 `fixture/node alias` 和真实 `ssh_target` 分开保存；alias 是测试身份标签，绝不作为 VM 真实 SSH IP。重复部署到同一 VM 时另用新 alias，不重复注册真实 VM IP。
- Docker/Compose/Host Integration 只为 Bug→case 确需的模式创建额外 fixture；container、Compose project/network/volume、config、data directory、managed Unit 全部用 `qa-repair-20260930-101945` 前缀。Host Integration 参考既有 `environment/isolation-plan.json` 的宿主隔离做法，但只允许修改本轮专属 `/opt/qa-repair-...` 路径和本轮准确 Unit；原服务与任意系统文件不可触及。Agent3 不放镜像、大文件或大 Artifact。

## 执行入口与验收门

部署控制可用现有 `.e2e/runs/20260930-101945/environment/guest-exec.py --connection <repair-private>/repair-connection.json --node <唯一fixture name>`；每个 config row 按唯一 fixture name 选 VM，真实 SSH 地址只作为 transport 字段。现有 `backend/isolated_runner.py` 固定引用旧 core NodeID/container/volume，不适用于 repair authority；不要复用或更改旧 runner。需要新的业务 API runner 时使用独立 repair 文件及唯一前缀，不改既有 runner。

先复核端口、目标路径/unit 与 repair Docker 资源不存在，再按 SHA 部署并启动唯一 repair Hub。只有 `/healthz`、`/readyz` 返回 200，repair Hub 登录/me 正常，且四个新 Native Node ID 全部不同于 primary ID、状态 `online` + `synced`、Agent version 与冻结构建一致，才开始 Bug→case 绑定的 E2E。每个 TLS/Docker/Compose/Host Integration fixture 另确认唯一 alias/ID/data_dir 和本机资源清单后才执行对应 case。

每个变更资源先记 exact ID/path 到 repair ledger。只清理 repair 前缀拥有的 unit/container/volume/network/目录与 Hub 数据；测试历史证据保留。完成后复核 primary Hub/四 Agent 的原服务状态与 IDs 和预检一致。

私密目录已准备为 `/tmp/nodesteer-e2e-20260930-101945-repair`，mode `0700`；后续 `repair-connection.json`、密码、token、TLS key 只存此目录且 mode `0600`。本轮没有将任何秘密写入仓库，也没有启动 repair Hub/Agent 或用例。
