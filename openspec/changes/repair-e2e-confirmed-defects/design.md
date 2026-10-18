## Context

动机和已确认边界见 proposal.md。现有实现使用 Go、SQLite、React/TanStack Query；以 docs/PRODUCT.md 与 docs/ARCHITECTURE.md §67 为强约束。当前原生基线为 dddee4f0，已确认缺陷与证据在 .e2e/failures.json，504 场景唯一分母保持不变。

## Goals / Non-Goals

**Goals:** 用现有组件和存储机制补齐实际操作、持久化、失败恢复和 UI 反馈；保持旧客户端、原始测试数据与部署对照可用。

**Non-Goals:** 不引入新依赖、schema、产品能力、特权容器或 Secret 存储策略，不修写历史用户/Journal，不修改 AGENTS.md 或执行 Git 交付。

## Decisions

1. 部署在停止或替换旧文件之前，完整备份并可靠写入 Journal 和真实 STOPPED 阶段。既有备份字段仅在本次确认原文件不存在时记录内部固定不存在哨兵，真实备份仍为绝对路径，不改 schema；旧空字段不能表示已确认不存在，状态不确定时保守失败。PREPARING 未改变旧版时不能依据空备份删除文件；恢复错误不得伪装 DONE，首次安装仅清理可证明由本次创建的文件。旧版本读取新哨兵可能保守恢复失败，因此修复版仅部署隔离测试资源，不让原对照服务读取新 Journal。
2. 日志每个执行尝试和每个流保留有界的可能匹配尾部，跨块脱敏，结束/取消/超时统一 flush；终态仍脱敏。仅逐块替换会泄漏拆开的秘密，仅屏蔽整个日志会损失可用性。
3. 同步帧先校验所有 Script 内容 SHA，再做任何本地事务或 pruned Application 物理清理，拒绝帧保持 Revision 与对象不变。Remote 条件检查有效时间与正 TTL，异常默认 UNKNOWN/BLOCKED，不新增离线缓存。
4. 重新纳管在准确路径用现有事务持久 pending、sync pending、新凭证和撤销标记；保留身份、标签、历史，普通 HELLO 不覆盖人工 disabled/maintenance。User POST/PUT 在任何写入前完成角色、密码及哈希校验。
5. Host Adapter 共用入口校验映射路径与真实宿主路径身份，无法证明对应时在 mkdir/write 前失败；nsenter 使用附着 root 参数。Artifact Cache 注入既有带私有 CA 的 HTTPClient，不跳过 TLS/SHA。
6. Execution/Audit 沿用现有 Store List/Count 的共享 WHERE，新增可选 q/trigger_type，参数化字面子串匹配，TaskName 用关联查询；不拉取全库。前端条件改变回第一页，请求和总数使用相同过滤。
7. 认证统一处理真实 401，清 Token、共享身份与旧敏感 Query 缓存并跳安全内部登录目标，覆盖直接上传/下载；403、网络错误或 500 不当作登录失效。登录页核验已有会话；本人角色变更同步当前 Auth 与控件。
8. 复用现有 Query/Mutation、菜单和确认框：日志及应用真实在途执行刷新到最终状态，Task 保存 pending 防重入且失败保草稿，删除取消恢复焦点/页面，Transfer 改源清冲突目标并提交校验。Secret 摘要固定遮蔽，BLOCKED 原因显示后端真实值；主题由 Provider 统一解析系统变化并更新 class/meta。

## Risks / Trade-offs

- [部署恢复可能修改真实文件] → 故障用例仅使用独占 QA Application、服务和目录；核对旧版 SHA、配置、Unit 与执行状态。
- [错误 Host 挂载从伪成功变为失败] → 验证缺失、错绑、正确、只读、越界和 symlink 路径，保留最小挂载/capabilities。
- [共享类型与 API 写入冲突] → Root 串行管理 internal/hub、internal/store 与共享 Filter；后端子 Agent 只写 internal/agent，前端子 Agent 只写 web/src。
- [测试脚本错误污染结论] → 独立保存 harness 失败；PASS 必须有真实 Hub→Agent→业务效果→UI/API 证据，不靠 Mock 或静态阅读升级覆盖。
- [ARM/DST 没有实际运行条件] → 如实保留环境限制，不用构建或模拟时钟代替真实验证。

## Migration Plan

无 schema 或历史数据迁移。先完成源代码和定向检查，再构建带修复版本标识的独立二进制/前端产物；在 kvm2 现有 VM 使用独立 authority、凭证、数据、进程与临时服务部署。原 Hub/Agent 和备份保持。Luna 执行已知缺陷与关联回归、补齐场景，Root 核验证据并更新任务状态。需要撤销测试部署时，仅停止本轮拥有的资源并保留必要私密数据库备份；未通过不 Archive。
