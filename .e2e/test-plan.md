# 当前版本全功能真实 E2E：20260930-101945

用户已确认原测试范围，并于2026-10-04确认修复22项已确认缺陷与隔离复测。原对照版本 dddee4f0；Hub http://192.168.100.209:8080；kvm2 的 nodesteer-hub 与 nodesteer-agent-1～4。旧状态已备份至本轮 historical-state。当前修复规划、规格和任务仅由 [repair-e2e-confirmed-defects](../openspec/changes/repair-e2e-confirmed-defects/proposal.md) 组织，本文件继续记录测试边界。

## 范围与验收

覆盖当前全部页面、API/角色、Hub-Agent实际业务联动，以及 ARCHITECTURE §66 的35场景和 §67 的18不变量。包括认证/OIDC、Dashboard、节点纳管/批量/升级/生命周期、分组/标签、脚本/参数/环境/修订、任务/条件/目标、四类调度/owner/misfire/离线、日志/执行控制、制品/托管应用部署健康回滚、文件中继、用户/设置/审计、公共控件/深链/缓存/键盘/响应式。

每条真实 E2E 结果须含用户或调用方操作、实际请求、状态变化、客户机业务效果、UI/调用方反馈及证据。Mock/组件/单元检查仅为补充；失败和环境阻塞逐项保留，不以静态检查代替实际通过。

## 分工与边界

- 主 Agent：阶段调度、全局状态、共享设置/故障注入串行控制、独立复核与最终验收。
- Web执行单元：full-feature-prompts.md、web/e2e与本轮web证据；独占 qa-20260930-101945-web-* 对象。
- Backend执行单元：本轮backend runner/证据；独占 qa-20260930-101945-be-* 对象。
- Environment执行单元：基线/备份、Agent2–4安装纳管、本轮environment证据；第二阶段隔离实例经主阶段调度后启动。

用户此前指定具体用例步骤使用 Luna，本轮继续此配置。`/root/luna_runtime`（gpt-6-luna）独占 qa-resume-20261008-luna 日志/页面证据；`/root/luna_workflows`（gpt-6-luna）独占 qa-resume-20261008-workflows 及 native-3 的业务 fixture；`/root/e2e_recovery` 继承主 Agent 能力，只写 continuation-review-20261008.json 并做只读分析。Root 独占全局台账、共享路径与最终验收；不允许并发改同一文件或 fixture。测试结果经过审查才更新 canonical 状态。

产品修改仅限本次已确认22缺陷及必要的关联正常/异常/恢复路径；不改AGENTS、KVM宿主、模板、无关资源、依赖、schema或Git历史。新版本使用独立authority/凭证/数据目录/进程，新建容器/网络/卷/端口/路径/托管Unit必须独占；原Hub/4Agent和备份保留对照。临时设置恢复，临时资源精确ID清理。不可删除的用户/执行/审计/传输历史单列。证据不包含真实密码、Registration Token、Authorization、Cookie、私钥。

## 可见阶段清单

- [x] 代码、页面、后端和环境摸底，补查遗漏；用户明确确认完整范围。
- [x] 创建独立运行目录与旧状态快照。
- [x] 固定版本、在线备份、Agent2–4真实安装纳管与健康基线。
- [x] 完整提示词与逐项覆盖清单；过时E2E与不安全清理修正。
- [ ] 页面、公共控件、全部正常跨页业务链路与API/RBAC真实测试。
- [ ] 隔离Docker/HostIntegration/OIDC/TLS及备份恢复测试。
- [ ] 同步/调度/执行/部署/传输故障恢复与35场景18不变量验证。
- [ ] 失败复现、精确清理、设置恢复、最终健康Smoke。
- [ ] 汇总真实覆盖、缺陷、阻塞、剩余环境和最终报告。

当前阶段：2026-10-08 已恢复隔离环境认证和四修复节点健康，历史 C/CR/App 留定义证据严格映射已汇入。修复版71PASS/1FAIL/159PARTIAL/273NOT_RUN（504）；原22缺陷14PASS/1PARTIAL/7NOT_RUN（22），新增退出码0独立缺陷待Review确认。两组 Luna 正执行本轮真实日志边界与业务闭环；精确结果与下一步写入 run-state/coverage/final-report，旧基线不能冒充新版通过。Guest SSH 仍待有效连接信息，独立 UI/API 继续。

OpenSpec：CLI 1.8.0，项目根与 config/生成的 OpenCode Apply 能力已只读核验；沿用已确认 change `repair-e2e-confirmed-defects`，apply ready、21/27 tasks，strict validate exit0。不初始化新项目、不新建平行 Plan、不 Archive。尚未完成全部真实验收与清理，任务第5阶段不勾选完成。

2026-10-08调度纠正：早期luna_runtime/luna_workflows使用完整历史fork时模型覆盖未核验，功能证据真实但模型要求不能宣称满足。后续luna_backend_cases采用fork=none显式gpt-6-luna接管；旧测试会话停止调度、历史留存。source模型审核见qa-resume-20261008-root/execution-model-audit.json；不新增模型未知的具体用例。

## 2026-10-09 已授权接续测试批次

沿用2026-10-04确认的22缺陷与504场景，不扩产品范围。具体步骤全部由明确 `fork_turns=none` / `gpt-6-luna` 的会话执行；Root只负责scope、授权/owner复核、源码审查、证据整合和最终验收。

| 会话/slot | 测试与目标 | 写入边界 | 验收 |
| --- | --- | --- | --- |
| e2e_recovery / 1 | native4独占child；TTL剩余UI与恢复→child revoke/reenroll | qa-resume-20261009-backend | 请求/response关联、SQLite/物理oracle、真实UI、exact cleanup |
| luna_web_cases / 2 | native3 owned Task；WEB-LIVE-LOG / X15 / X16 retry FAILED、断网重连、secure clipboard；独立Web缺口 | qa-resume-20261009-web | 用户操作→真实请求→状态/业务→UI反馈；每canonical全部维度核对 |
| luna_runtime_cases / 3 | native1只读owned Command probe→PREPARING故障与Host/TLS可行隔离方案 | qa-resume-20261009-runtime | Root先核对准确owned PID/starttime/UID/argv/SHA/路径/Unit，再允许注入；原服务保留 |

所有authority仅repair :18080，primary :8080禁止管理写。共享全局.e2e状态由Root串行写；各Agent分开证据/progress。现有TTL7负例不重跑，旧PID不沿用。新ExitCode0 API修复单独Review待答复，未实施。运行证据与静态/旧版证据分开；未验证维度保留PARTIAL，原22与504分母不变。
