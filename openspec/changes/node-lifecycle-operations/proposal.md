## Why

节点运维缺少四项一期必要能力：① 安装配置里 `deployment_mode`/`agent_version`/`host_integration` 等参数靠纳管命令硬编码手写，版本不会随构建更新（CI 未注入版本变量）、Hub 对上报值零校验；② 节点「暂停」语义不完整 —— 手动执行/调度/应用部署虽已拦截，但文件传输与制品预取仍下发、agent 侧无拒收防御、调度被静默跳过无留痕、无法批量操作；③ agent 无法自升级，升级需逐台手工登录；④ 离线运行能力经探索确认已完整，需以测试锁定其与暂停的交互语义。

## What Changes

- **参数自动生成与上报**：`agent_version` 改为编译期 ldflags 注入（真实构建版本）；`deployment_mode` 未显式配置时自动探测容器/native；`host_integration` 由部署模式推导；纳管命令与配置模板去除硬编码键（旧配置向后兼容）；Hub 对上报的 `deployment_mode` 做枚举白名单校验。`data_dir` 为纯本地路径，保持不上报。
- **暂停语义完善**：暂停（maintenance）拦截任务型下发 —— 在既有手动执行/调度/应用部署之上补拦文件传输与制品预取；settings 下发与变更通知（配置同步）不受影响；agent 在暂停态拒收执行/部署指令（双保险）；调度因暂停跳过时产生 SKIPPED 执行记录（留痕替代静默跳过）；已部署常驻服务继续运行、运行中任务不被中止；支持批量暂停/恢复。
- **agent 自助升级**：新增 `agent_upgrade` 执行类型走既有下发→执行→回报全链；agent 下载 Hub 当前构建二进制并 SHA256 校验、备份自身、替换、重启服务、失败回滚；版本粒度为「升至 Hub 同版本」；仅 native 部署形态支持，docker 形态 UI 隐藏升级入口。
- **离线交互锁定**：以测试锁定「暂停时本地离线调度停止、已部署常驻服务不受影响、未暂停时离线脚本/服务正常运行」。
- **批量操作 UI**：节点列表启用多选（复用既有 bulk-actions 组件），支持批量升级（native）、批量暂停/恢复，逐节点产出独立执行/状态结果。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `hub/nodes`: ADDED「节点暂停语义」—— 拦截清单、agent 拒收、调度留痕、常驻服务与运行中任务边界、批量暂停。
- `node-enrollment`: ADDED「Agent 自动检测并上报运行参数」—— 编译注入版本、模式探测、模板去硬编码、Hub 枚举校验（新增独立需求，与未归档 `download-agent-binary-enrollment` 的同名 MODIFIED 需求无冲突）。
- `agent/core`: ADDED「Agent 自助升级」—— 下载校验、备份替换重启、回滚、形态限制、版本上报。
- `web-ui`: ADDED「节点批量操作」—— 多选、批量升级/暂停/恢复与逐节点结果反馈。

## Impact

- 协议：新增 `agent_upgrade` 执行类型/消息（`internal/protocol`）；执行模型与能力映射扩展。
- Hub：下发路径状态拦截（file_transfer、artifact）、调度跳过留痕、HELLO 上报校验、批量 API（复用 `node_ids` 数组先例）。
- Agent：版本变量与探测、暂停态拒收、自升级执行（`os.Executable`、备份回滚、服务重启）。
- 构建：`build.yml` 与 Makefile 增加 `-X main.version` 注入。
- Web：节点多选基建、批量工具条、升级入口形态过滤。
- 无数据库 schema 迁移（SKIPPED 留痕复用既有执行状态）；`data_dir` 不上报；多版本制品库、docker 自升级、进度百分比为明确排除项。
