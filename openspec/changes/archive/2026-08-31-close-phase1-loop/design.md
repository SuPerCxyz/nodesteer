# Design: Close Cadentra Phase 1 End-to-End Loop

## 目标

以现有 Modular Monolith、WebSocket、SQLite 和 Agent Core 为基础修复断链；新增协议字段和表只服务于可靠确认、凭证、应用状态与事务恢复。

## 关键方案

1. Hub 更新 Desired Object、assignment、group/label 影响范围时，在同一个 SQLite 事务中写对象、global revision、change log、audit 和删除标记；事务提交后再通知 Agent。
2. Sync Response 明确 full-resync 与 desired object 集合。Agent 在事务内 upsert 当前对象、删除不再属于本节点的对象、消费 tombstone，提交后刷新 scheduler。
3. Agent HELLO 首次使用 registration token；Hub 返回 credential 后，后续仅使用 credential。增加认证失败和撤销路径，兼容已有本地库迁移。
4. Execution Finished 使用 ACK/重试语义；只有收到 Hub 确认才将本地 Journal 标为 synced。Hub 对相同 ID、slot 和已完成状态幂等处理。
5. Agent-owned application execution 同时更新普通 Execution 与 Deployment Journal；Hub 保存应用节点最近健康和部署结果，Web UI 从真实状态读取。
6. Artifact 下载统一走 `ArtifactCache`：检查 HTTP 200、临时文件、SHA256、原子 rename 和缓存登记；部署失败立即回滚或进入明确失败状态。
7. ContainerHostAdapter 只接收逻辑宿主机路径，统一 clean、allowlist、symlink 校验后再映射到 `/host`；Host Inventory 的所有可访问数据使用同一 host root。
8. TLS 使用 Go 标准库 `ListenAndServeTLS` / WebSocket TLS URL，证书配置为空时保持开发环境 HTTP 兼容，但生产示例默认展示 TLS 配置。

## 验证策略

- Go 单元测试、`go test -race`、`go vet`。
- 前端 TypeScript/Vite build。
- Hub/Agent WebSocket 集成：注册、重连、同步、丢通知、离线执行、执行 ACK。
- Docker Compose 配置与镜像构建 smoke。
- 有权限时验证 systemd、Host Integration、应用升级/健康失败回滚；无权限时保留明确未验证项。

