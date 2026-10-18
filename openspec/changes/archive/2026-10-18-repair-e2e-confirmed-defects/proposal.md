## Why

kvm2 的真实 Hub-Agent 全功能 E2E 已确认 22 个独立缺陷，涉及部署恢复、日志保密、同步可信性、权限与页面操作闭环。用户于 2026-10-04 确认修复这些缺陷并由 Luna 子 Agent 执行具体复测，保留 504 条功能场景的真实证据与未验证项。

## What Changes

- 修复部署备份与 Journal 时序，崩溃恢复保留旧版文件，恢复失败明确反馈；日志跨块脱敏；坏 Script SHA 整帧拒绝；Remote TTL 过期或无效默认阻断。
- 重新纳管准确恢复 pending 状态并保留身份、标签和历史；用户创建拒绝空密码，创建/更新拒绝非法角色且避免半更新。
- 修复 Host Integration 的 nsenter 参数与真实宿主挂载校验，Artifact 下载沿用已配置私有 CA，不扩大容器权限。
- 修复会话 401 导航、已登录访问登录页、Secret 默认值显示、实时日志、应用健康/历史、跨页搜索、自降权、Task 取消/保存、Transfer 源变更、BLOCKED 原因及 system 主题。
- Execution/Audit 增加兼容旧请求的可选 `q`，Execution 增加可选 `trigger_type`；过滤、分页与总数一致，保留原响应结构。
- 在 kvm2 现有测试 VM 上隔离部署修复版本，复测已知缺陷并补齐功能场景；清理本轮独占资源，保留原 Hub/Agent、备份及不可删除历史。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent/applications`: 部署恢复的文件与 Journal 一致性、私有 CA Artifact 下载和 Host 操作真实性。
- `agent/executions`: 跨块 Secret 脱敏及 Remote State 有效性判断。
- `agent/core`: 同步前校验完整 Script 内容，坏帧不得导致 Revision 或物理状态变化。
- `agent-file-relay`: Host 文件操作仅写入经验证的真实宿主映射。
- `node-enrollment`: 撤销后重新纳管持久化 pending 状态而不新增重复身份。
- `hub/access-control`: 用户输入校验及失败原子性。
- `hub/api`: Execution/Audit 可选过滤与分页一致性。
- `web-ui`: 本轮十二项界面行为的完整状态与错误处理。
- `web-ui/theme`: 系统主题变化时页面与 theme-color 同步。

## Impact

涉及 `internal/agent`、`internal/hub`、`internal/store`、现有共享 Filter 类型及 `web/src`；不修改数据库 schema、不新增依赖、不修改 AGENTS.md、不执行 Git 交付或生产操作。部署恢复和错误 Host 挂载的负向路径可能由伪成功变为明确失败，验证仅针对独占 QA 资源。其他会话角色自动刷新、其他编辑器全面改造、新业务、Secret 存储体系重设计及历史数据批量修写均不纳入。
