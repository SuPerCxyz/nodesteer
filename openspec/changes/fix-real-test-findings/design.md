# Design: Fix Real Test Findings

## 1. Enrollment Dialog

保留输入变化时只清理请求结果的 clearEnrollment，新增关闭专用 reset：清除节点名称、地址、请求、错误、结果、复制状态并恢复 Native Tab；再次打开得到空白表单。

## 2. Stable systemd Health

ApplicationManager.checkOnce 对 systemd health 不接受启动瞬间的短暂 active。第一次状态为 active 后短暂等待，再次读取状态；只有两次均为 active 才通过。activating、auto-restart、failed 或非零退出结果均失败，进入既有 rollback 流程。用一个状态先 active 后 activating 的 fake HostAdapter 覆盖竞态。

## 3. Artifact Delete

ArtifactManager.Delete 对存储层的 sql.ErrNoRows 返回 nil，保持 DELETE 幂等；其它存储错误继续返回。已有对象仍删除数据库记录和存储文件。

## 4. Role-aware UI

新增最小权限 hooks：

- useCanWrite: administrator。
- useCanRun: administrator/operator。

列表页只显示对应 action；编辑页隐藏或禁用写入控件。Task/Execution 保留 operator 的 Run/Cancel；Artifact 保留非管理员的 Download。后端 RBAC 不改变，UI 门禁只是减少误导入口。

## 5. Dynamic Label Group Count

Groups 页面同时读取 nodes；static group 使用 members.length，label group 以 label_key/label_value 匹配当前节点计算数量。解析缺少节点数据时显示 0，不修改 Hub 数据模型。

## 6. Default Credential Gate

CLI DefaultConfig 不再提供管理员密码。应用配置和环境覆盖完成后，若管理员用户名或密码为空则输出不含敏感值的错误并退出。Docker Compose 改为要求 ADMIN_PASSWORD 显式设置。单元测试通过显式 Config 继续运行。

## 7. Docker Verification

先刷新 KVM2 模板泛化、目标域/磁盘、资源、QGA、IPv4、SSH 和 Docker 状态。只在独立目标域和持久卷满足条件时执行 Docker Agent enrollment/restart/recreate；不修改源模板和现有 Cadentra VM。
