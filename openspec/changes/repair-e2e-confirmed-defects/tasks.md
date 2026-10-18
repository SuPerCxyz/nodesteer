## 1. 已确认范围与实施边界

- [x] 1.1 完成22项只读根因/影响面审查并获得用户完整修复与隔离复测确认
- [x] 1.2 核对现有OpenSpec项目、CLI、强约束文档与已有修改；建立proposal/specs/design

## 2. Agent安全、部署与Host（backend_fix_review，internal/agent）

- [x] 2.1 修复P0 PREPARING部署恢复及Journal/备份时序
- [x] 2.2 修复BE-SECRET-STREAM-001跨块实时日志脱敏
- [x] 2.3 修复BE-SCRIPT-SHA-002整帧校验且先于物理清理
- [x] 2.4 修复BE-REMOTE-TTL-004无效Remote状态默认阻断
- [x] 2.5 修复FAIL-ENV-HOST-001 nsenter托管Unit操作
- [x] 2.6 修复FAIL-ENV-TLS-001 Artifact私有CA
- [x] 2.7 修复FAIL-ENV-HOST-002真实宿主映射校验
- [x] 2.8 完成相关有意义的Go回归与定向检查

## 3. Hub共享路径（Root，internal/hub、internal/store、共享Filter）

- [x] 3.1 修复BE-REENROLL-STATUS-003及事务/人工状态保护
- [x] 3.2 修复MAIN-USER-EMPTY-PASSWORD与MAIN-USER-INVALID-ROLE及失败原子性
- [x] 3.3 配套Execution/Audit过滤、分页和Count一致性及相关回归

## 4. 页面行为（frontend_fix_review，web/src）

- [x] 4.1 修复P1-001会话过期导航及WEB-AUTH-SIGN-IN
- [x] 4.2 修复WEB-SECRET-DEFAULT
- [x] 4.3 修复WEB-LIVE-LOG与WEB-APP-LIVE-STATE
- [x] 4.4 修复WEB-EXEC-SEARCH-PAGE
- [x] 4.5 修复WEB-TASK-DELETE-CANCEL与WEB-TASK-PENDING-SAVE
- [x] 4.6 修复WEB-TRANSFER-SOURCE-CHANGE
- [x] 4.7 修复WEB-ROLE-LIVE-STATE与WEB-BLOCK-REASON
- [x] 4.8 修复FAIL-ENV-UI-THEME-001并完成前端定向检查

## 5. kvm2真实验收（luna_result_audit，具体步骤用gpt-6-luna）

- [ ] 5.1 预检并构建隔离版本，记录Hub/Agent/前端版本与SHA，保留原部署
- [ ] 5.2 独占资源执行22项缺陷正常/负向/恢复复测，保留失败与harness错误
- [ ] 5.3 完成504场景全部适用维度补测及跨页业务回归，不把静态/Mock计为运行通过
- [ ] 5.4 精确清理独占资源、保留必要备份、核验原Hub/4Agent最终健康
- [ ] 5.5 Root完成Code Review、架构不变量检查、证据/秘密/状态一致性核验；如实列出未验证项
- [ ] 5.6 仅实现与验证全部完成且artifact一致时Archive；不执行Git交付
