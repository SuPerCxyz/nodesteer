## ADDED Requirements

### Requirement: 会话失效与登录入口一致
Web SHALL 在受保护请求真实 401 后清除认证状态并导航登录，保留安全内部 path/search/hash；直接上传/下载亦适用。已有有效会话访问登录页 SHALL 返回安全业务目标；网络错误、403和500不得伪装会话过期。

#### Scenario: 自然 TTL 到期
- **WHEN** 用户停留页面且真实会话自然到期，后续请求返回401
- **THEN** 自动显示登录，登录后回到安全内部目标，无导航循环

### Requirement: Secret 默认值不可见
Script/Task Secret 默认值 SHALL 使用保密输入并在摘要、标题和可访问文本中固定遮蔽，同时保留真实编辑值与非 Secret 展示。

#### Scenario: Secret 编辑与重载
- **WHEN** 用户新增、编辑、移除或重载 Secret 参数
- **THEN** 明文不出现在页面摘要或可访问文本，保存及执行参数仍正确

### Requirement: 实时日志与应用最终状态一致
Web SHALL 在执行 PENDING/RUNNING 及结束时更新日志，保持两流、顺序、搜索、复制与跟随一致；应用操作 SHALL 跟踪真实在途执行并更新健康和历史，失败后可再次操作。

#### Scenario: 无刷新查看真实执行结果
- **WHEN** 新日志块到达或真实应用 deploy/start/stop/restart/upgrade 完成，包含失败后的自动回滚
- **THEN** 当前页面自动显示最新日志及实际健康/历史，终态不被旧缓存遮蔽，多节点混合结果分别反馈

### Requirement: 搜索分页与总数一致
Web SHALL 把 Execution/Audit 的搜索和触发条件提交服务端，条件变化回第一页，分页总数与已过滤集合一致。

#### Scenario: 搜索第二页对象
- **WHEN** 搜索原位于第二页之后的对象或组合过滤条件
- **THEN** 正确显示该对象及准确总数，清除条件后恢复正常分页

### Requirement: Task 操作可恢复且防重入
Task 保存 SHALL 在途阻止重复提交并显示反馈，错误后保留草稿并恢复编辑；删除菜单与确认框 SHALL 在取消/Escape后恢复页面和焦点，确认只删除指定Task。

#### Scenario: 慢保存与取消删除
- **WHEN** 慢请求中重复点击保存，或打开删除后取消/Escape
- **THEN** 保存仅产生一次请求；取消零删除请求，菜单关闭且侧栏、搜索和页面可操作

### Requirement: Transfer 源目标一致
Web SHALL 在切换 Source 后移除冲突 Target 并保留其他目标草稿，在添加及提交时拒绝 Source 与 Target 相同。

#### Scenario: 已选目标变为源
- **WHEN** 将 Source 从 A 改为已选 Target B
- **THEN** B 不再作为Target，不可发起自身传输，其他节点与路径保留

### Requirement: 本人角色与阻断反馈及时同步
Web SHALL 在本人角色修改成功后同步共享身份与当前控件，关闭已失去权限的打开表单；Execution SHALL 显示后端真实非空 block_reason，支持长文本且不执行HTML。

#### Scenario: 自降权与真实BLOCKED
- **WHEN** 专属QA本人降为Operator/Viewer，或打开真实BLOCKED执行
- **THEN** 当前页面权限与API一致，不残留可写表单；阻断原因与API一致，无需刷新
