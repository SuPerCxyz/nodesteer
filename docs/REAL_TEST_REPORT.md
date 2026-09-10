# Cadentra 真实功能测试与失败项复验报告

测试日期：2026-09-07  
测试环境：KVM2 Hub `192.168.100.249`，Web/API `8080`，Agent Gateway `8443`；Native Agent 节点 `192.168.100.212`。完整用例、页面关系和按钮清单见 [TEST_PLAN.md](./TEST_PLAN.md)。

## 结论

本轮已修复并真实复验上一轮发现的 DEF-001～DEF-006。Native Agent 主链路、页面操作、REST/RBAC、应用部署回滚和清理回归通过；Docker Agent 因缺少可用镜像且构建/拉取受环境阻塞，E2E-17/E2E-18 未执行。因此项目当前结论仍为 **PARTIAL**，不能宣称全部功能已通过。

## 状态说明

- `✅`：真实执行并达到预期。
- `❌`：真实执行并发现缺陷。
- `⛔`：环境阻塞，未执行。
- `⬜`：测试计划中尚未执行，不作通过结论。

## 修复项复验

| 编号 | 修复内容 | 真实复验结果 |
|---|---|---|
| DEF-001 | 节点纳管弹窗关闭后重置表单、错误、结果和复制状态 | ✅ 填写非法地址触发错误，关闭后重新打开，节点名称/地址为空、错误消失、Native 页签恢复。未产生新节点。 |
| DEF-002 | systemd health check 稳定态判断，失败升级回滚 | ✅ 临时退出码为 1 的制品升级产生 Execution `6874b7fe-e709-41b4-8e3d-381f8caee28a`，状态 `FAILED`、原因 `health check failed; rolled back`；`.212` 上旧版本 unit 最终为 active/running，退出结果成功、无重复重启。应用和临时制品随后清理。 |
| DEF-003 | Artifact DELETE 幂等 | ✅ 对已删除的两个临时制品重复调用 DELETE，均返回 HTTP 200；新增 manager 单测和真实 HTTP API 单测。 |
| DEF-004 | Web 页面按角色隐藏越权操作入口 | ✅ operator 无添加节点/新建/资源写入口，但保留任务“立即运行”；viewer 无写入和运行入口。节点状态、设置、用户写入以及 viewer 运行任务 API 均返回 403；最终部署版本进一步隐藏了文件传输创建/目标/开始/重试/取消入口。 |
| DEF-005 | Label Group 成员数按当前节点 Label 动态计算 | ✅ `qa_env=real` 临时 Label Group 在匹配节点存在时列表显示成员数 `1`，与实际节点匹配数一致；临时组已删除。 |
| DEF-006 | 移除默认管理员口令并增加启动门禁 | ✅ `DefaultConfig` 管理员口令为空；无显式口令启动退出码为 1，仅输出安全的配置错误；Compose 未设置 `ADMIN_PASSWORD` 解析失败，设置测试占位值后解析成功。 |

## 真实测试结果

| 范围 | 状态 | 结果/证据 |
|---|---|---|
| KVM2/Hub/Native Agent 基线 | ✅ | Hub `active`；`healthz=200`、`readyz=200`、Gateway TCP 可达；4 个 Native Agent service active，节点均为 `online/synced`，当前 global revision 均为 197。 |
| 公共 Header、Sidebar、语言、主题、Profile | ✅ | 中/英切换、深色主题、Sidebar 折叠/展开、Profile 菜单、Logout 确认取消均实际点击。 |
| DataTable 公共能力 | ✅ | 列视图、搜索命中/无结果、Reset、分页和页容量实际验证。 |
| 节点纳管 | ✅ | Native/docker run/Compose 命令生成、复制、非法地址提示、关闭重置和节点详情入口实际验证。 |
| 节点→任务/调度/应用联动 | ✅ | 节点详情任务列表、调度列表、托管应用回链及目标一致性实际验证。 |
| 脚本 CRUD、参数、环境变量、克隆、修订历史 | ✅ | 创建、编辑到 r2、克隆、查看 r1/r2、删除和任务引用实际验证。 |
| 任务 CRUD、Node/Group/Label 目标、立即运行 | ✅ | Command Task、Script Task、Group/Label 目标创建并执行；取消确认分支实际验证。 |
| Execution、日志 | ✅ | SUCCESS、stdout/stderr、搜索、stdout 选择、Wrap、Follow、Copy Logs 实际验证。 |
| Timeout/Cancel/Retry/Streaming/Log 截断 | ✅ | 既有测试任务真实得到 `TIMED_OUT`、`CANCELED`、`SUCCESS`；流式日志按时间到达；输出达到 1 MiB 上限并标记 `stdout_truncated=true`；客户机无残留进程。 |
| Static/Label Group | ✅ | 静态/标签分组创建、编辑、删除和 Label Group 目标任务成功；动态成员数修复后显示正确。 |
| Cron/Interval/One-Time Schedule | ✅ | 类型切换、创建、UTC、allow offline、run once、启停字段保存并通过 API 回读。 |
| Artifact | ✅ | 上传、版本/架构、SHA256、下载、引用展示和删除确认实际验证；重复 DELETE 已返回 200。 |
| Application/systemd | ✅ | 制品→应用→节点分配→deploy→start/stop→health；成功版本升级；失败升级检测、FAILED 记录和旧版本回滚均实际验证。 |
| REST API/RBAC | ✅ | 管理员核心 GET/API 200；未认证 API 401、healthz 无认证 200；operator/viewer 越权写入/纳管/运行 403；页面入口按角色收紧，最终部署版本包含文件传输写操作门禁。 |
| Native Agent 重启 | ✅ | Agent 重启后 active，节点恢复 online/synced，revision 保持。 |
| Hub 重启 | ✅ | Hub 重启后 healthz/readyz 正常，Agent 自动重连并恢复 online/synced；内存会话失效后重新登录可用。 |
| Docker Agent/Host Integration | ⛔ | 独立 Docker 测试 VM 预检完成，但无预构建镜像；构建时 VM 负载异常，镜像拉取又在网络层停滞，未达到安全、可重复的执行条件。 |
| Go/前端回归 | ✅ | `go test -buildvcs=false ./...`、`go vet -buildvcs=false ./...`、`cd web && npm test`（9 files/54 tests）、`npm run lint`（0 errors/3 个既有 warning）、`npm run build` 均通过；Playwright `--list` 共 55 tests/15 files，既有 smoke 53 passed。 |

## 环境阻塞与未完成范围

| 编号 | 范围 | 原因 | 补测条件 |
|---|---|---|---|
| ENV-001 | KVM2 地址发现 | `domifaddr --source agent` 受客户机接口数超过 libvirt 2048 上限影响；本轮用 QGA 按 MAC 筛选 IPv4 验证。 | 修复接口数量/libvirt 限制，或固化 QGA MAC 筛选环境约定。 |
| ENV-002 | Docker Agent/Host Integration | 没有现成可用 Agent 镜像；独立 VM 的构建/registry 拉取不稳定，已中止，未执行 Docker enrollment/recreate。 | 提供预构建镜像或稳定 registry，并验证持久卷、网络、Host Integration/systemd 权限。 |
| PLAN-001 | 计划中的系统故障注入、离线调度、文件中继故障恢复、OIDC 等 | 本轮未为这些独立场景改变环境或引入故障，不能用 Native 主链路代替。 | 按 [TEST_PLAN.md](./TEST_PLAN.md) 的 SYS/E2E/API 用例逐项执行。 |
| PLAN-002 | 文件传输页最终版本的 Operator/Viewer UI 复验 | 最终前端补丁已构建并部署，源码门禁和管理员页面已检查；受控 Operator/Viewer profile 已清理，未重新登录做该页的角色可见性点击复验。 | 从受控凭据源重新登录 Operator/Viewer，确认创建/目标/开始/重试/取消入口均不可见。 |

## 清理与数据保护

| 对象 | 结果 |
|---|---|
| 纳管弹窗复验数据 | ✅ 非法地址仅触发校验；早先产生的 `reset-check` pending 节点已核对无任何引用后删除。 |
| 临时应用/制品 | ✅ Hub 应用和两个临时 Artifact 均不存在；重复 DELETE 已复验；`.212` 临时 systemd unit、binary、config 已移除并 daemon-reload。 |
| 临时 Label Group | ✅ 已通过 UI 验证取消删除和确认删除分支，组已不存在。 |
| Hub 数据库 | ✅ 删除测试节点前创建 `/var/lib/cadentra-hub/hub.db.before-qa-fix-reset-check-cleanup-20260907.sqlite` 备份。既有业务数据和历史执行未删除。 |
| KVM/测试 VM | ✅ Docker 临时 VM 已 undefine 并删除其精确磁盘文件；源模板、Hub VM 和既有 Native VM 未删除。 |
| 临时认证 profile | ✅ `cadentra-operator`、`cadentra-viewer` 仅用于受控复验，已删除；不写入仓库、报告或截图。最终文件传输补丁使用代码/构建校验，未重新创建已清理的 RBAC 凭据。 |

## 当前验收结论

本轮确认的 DEF-001～DEF-006 已修复并通过真实复验，代码级回归通过；但 Docker Agent 两条端到端用例和 TEST_PLAN 中标记未执行的系统级场景仍是剩余验收项。因此当前版本为 **PARTIAL**，不是“全部功能通过”或最终交付结论。
