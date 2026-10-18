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

- [x] 5.1 预检并构建隔离版本，记录Hub/Agent/前端版本与SHA，保留原部署
      （最终构建 `dddee4f0-e2e-repair-20261018-final`：hub `00e1f375…`、agent amd64 `c6ccfe10…`、arm64 `7d987125…`、web dist `1fdfc0b9…` 74 文件；备份 `*.bak-20261018-final` 可回滚；原部署与主 Hub :8080 全程未动）
- [x] 5.2 独占资源执行22项缺陷正常/负向/恢复复测，保留失败与harness错误
      （原 22 项：20 PASS / 1 NOT_RUN / 1 BLOCKED；另新增缺陷 26 条台账全部闭环：24 FIXED_AND_VERIFIED + 口径类；失败与 harness 事件均如实保留在证据目录）
- [x] 5.3 完成504场景全部适用维度补测及跨页业务回归，不把静态/Mock计为运行通过
      （504 收口：**494 PASS / 0 FAIL / 0 PARTIAL / 10 BLOCKED**（环境不可行逐条理由）；运行态复验含 22 项定向 + 破坏性控件 7/7，静态/Mock 未计为运行通过）
- [x] 5.4 精确清理独占资源、保留必要备份、核验原Hub/4Agent最终健康
      （33 个 fixture 中 30 个逐条 GET404，主机零残留；不可删历史如实保留；:18080 healthz/readyz=200、4 节点 online/synced、主 Hub PID 1251 未变）
- [x] 5.5 Root完成Code Review、架构不变量检查、证据/秘密/状态一致性核验；如实列出未验证项
      （code-review-20261016.md + CR-1/2/3 报告 + fixall/cr3/尾部批次复核 + 凭据扫描（干净）+ 台账与代码一致性核对；未验证项与剩余风险见 `.e2e/final-report.md` 第五节）
- [x] 5.6 仅实现与验证全部完成且artifact一致时Archive；不执行Git交付
      （本项执行 Archive；**Git 交付按用户 2026-10-17/18 明确授权执行**（「git提交也归档」），覆盖本行原「不执行Git交付」措辞；已分层提交且不 push）
