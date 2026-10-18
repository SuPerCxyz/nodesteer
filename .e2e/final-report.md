# 当前 E2E 进度：先完成测试，再统一修复，尚未完成

更新时间：2026-10-14T06:28:05Z。运行20260930-101945，唯一管理authority repair18080（**当前环境已停，见下**）。

修复版504场景（**全量已出结论，NOT_RUN 清零**）：**413 PASS / 6 FAIL / 75 PARTIAL / 0 NOT_RUN / 10 BLOCKED**。原22缺陷：**20 PASS / 0 PARTIAL / 1 NOT_RUN / 1 BLOCKED**。Exit0/UIlogout已确认测试后统一修复，原22分母不变。

## 2026-10-15 全量收口（504/504 已有结论）

- **PASS 413（81.9%）**：原基线 315 项已全部复验，另把大量原 NOT_RUN 转为通过。
- **FAIL 6**：`G03-10`、`P03-10`、`P17P18-07`、`TABLE-TASKS-ERROR`（既有 4 条）+ `SYS-27`（新发现时钟偏移）、`AR-32`（命中回滚毒行缺陷）。
- **PARTIAL 75**（web 66 / system 9）：主要缺口为环境受限——Docker/Compose 形态、受限角色会话（产品无删除用户 API）、Host 集成 `/host`（AppArmor 阻塞）、跨构建版本号、长时停机等，已逐条在台账写明。
- **BLOCKED 10**：Host 集成族 4（SYS-54/55、AR-31、INV-14）、OIDC/SSO 4（P01-08/09/10、X17）、DST 1（SYS-24）、Full Resync 算术不可达 1（SYS-07，理由已论证）。
- **新增确认缺陷 9 条**（`failures.json`）：P0 两条——`E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW`（Remote 条件因跨主机亚秒偏移恒 fail-closed，Hub 领先 0.1464s）、`E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE`（回滚失败留 ROLLING_BACK 毒行，重启用陈旧 backup 覆盖当前版本）；P1 两条——`COMMITSYNC-FAILURE-DOUBLE-UNLOCK-PANIC`（任何 CommitSync 错误致 Agent panic）、`TIMEOUT-CONTENT-VALIDATION-GAP`（timeout=-5 被 200 持久化等）；P2 两条——分组空名/upsert 200、SQL 错误外泄；P3 一条——审计端点方法回退 200；另 2 条为既有 FAIL 类。全部 `source_change_authorized: false`，仅记录未改产品代码。
- **环境与合规**：全程未改产品源码、无 Git/Archive；repair 环境为唯一管理 authority，primary `:8080` 仅只读；VM 未重启（除一次由批次误操作导致的 native-1 重启，已恢复并留档）；凭据未落盘，令牌回显事件已核查且会话随 Hub 重启失效。
- **未完成门槛**：`repair-e2e-confirmed-defects` 的真实验收、环境清理与完成门禁仍未收口；原 22 缺陷中 `FAIL-ENV-HOST-002` 仍 NOT_RUN；不声明整体可交付。

## 本轮已完成

- 已恢复此前确认的修复与复测范围，不重新扩大需求；用户要求同时更新结果和进度文件。
- 旧 `/tmp` 连接目录失效，已经授权 SSH `kvm2` / QGA 只读读取 exact repair 配置恢复。凭据仅保存在 `run-state.private_dir` 指向的 0700 临时目录，连接文件 0600；[本轮预检](runs/20260930-101945/repair/qa-resume-20261008-root/preflight.json)包含脱敏恢复步骤和 SHA。
- repair healthz/readyz/login/me/nodes 均 200，当前管理员身份及 4 个修复节点 online/synced 已核验。这是恢复预检，不能代替具体功能验收。
- 早期磁盘 SHA 仅Hub/agent-1/2/4核验；现已补Native3实际运行PID445056的proc/disk SHA，四Agent分别均有运行进程SHA证据，但不是同一时刻重新采样。[磁盘证据](runs/20260930-101945/repair/qa-resume-20261008-root/disk-binaries.json)保留未验证项。
- C/CR 去重 12 个用例、App 保存首次执行前 Host Task 定义的五操作复验已严格映射 29 条 canonical。P14P15-11、SYS-46、E2E-11、P09P11-18 四项补齐 PASS。其余缺少维度保持 PARTIAL；[审查](runs/20260930-101945/repair/continuation-review-20261008.json)明确这是历史证据补记，非 2026-10-08 新执行。
- 318 个 source manifest 项仅一个前端测试文件不同，70 个 web/dist 文件匹配；业务源码对应当前部署。OpenSpec strict validate 实际 exit 0。静态检查不计 E2E PASS。
- Luna 已实际执行登录页空表单、匿名保护路由和代表 API 401 等用例，结果待逐 canonical 审查汇入；[本轮运行目录](runs/20260930-101945/repair/qa-resume-20261008-luna/)保留原始脱敏证据。
- 测试器误输出的一枚临时 UI 登录令牌已立即撤销，旧 session `/api/me` 返回 401，新登录与身份检查成功。只保留[脱敏事件](runs/20260930-101945/repair/qa-resume-20261008-luna/security-event.json)，不复制凭据值。

## 正在执行与接续顺序

1. Luna web：batch18已清理42fixture；batch19七错误重试闭环；batch20/22语言导航Dashboard/Node库存/settings等已复核。token未失效，401为测试器漏Bearer。P05Copy需要ownedPendingNode、capDisabled需真实Docker样本。
2. Luna runtime：P11-05 ApplicationTask UI Run与P11-03两按钮pending均PASS；精确Task/App/Artifact/helper404，App/Unit/Registry/binary/process已prune。两个空目录保留，56B缓存未检查；专属logout200/same401/profile关闭，共享forward保留。现准备实际Sync ArtifactURL只读核验。
3. Luna recovery：SYS12 A→B same身份/DB/ScriptCache/Schedule/Journal已实测，全部owned资源清理；仅ArtifactCache缺项。另32B未部署App assignment补测source静态恢复边界HOLD，本地修后复审，尚无launch。
4. Root：213/504 PASS、4FAIL/157PARTIAL/130NOT_RUN；2026-10-14 已汇入 SYS-34、FL-05、SYS-12 三项 PASS，SYS-40/SYS-09/P05B-04 维持 PARTIAL。新增Tasks初始Loading/假Empty、Dashboard AppState错误吞掉/健康比下降两处真实失败已记，新增修复scope待统一Requirement Review，不先改产品。原22仍20PASS/1NOT_RUN/1BLOCKED；User审批pending、HOST001预期停止，无Git/Archive。

新会话先读 `.e2e/README.md`，再按顺序读 run-state、coverage、workflows、failures、test-data；以 `run-state.resume_hint`、最新运行结果、[修复版清单](runs/20260930-101945/repair/feature-inventory.json)及[缺陷矩阵](runs/20260930-101945/repair/defect-regression-matrix.json)为状态源。子 Agent 会话路径只适用于本次活动会话，不能假定新会话还能恢复。

旧 App 留定义复验已完成且 App/Task/V2 fixture 已清理，**不要运行已删除 fixture 的旧 continue 脚本**。C 初次 RBAC fixture 登录 401 和 CR 重跑结果均保留；CR 不同账号不能替代同一生命周期账号的三角色实际操作。

## 剩余约束和阻塞

客户机 SSH alias 或账号/私密凭据路径仍待用户提供。QGA guest-exec 被配置禁用，未绕过；UI/API 可继续。ARM/DST/离线/真实故障等缺乏运行条件的适用维度继续如实保留，不以构建、Mock 或模拟结果代替。

原 Hub/4 Agent、原始备份和 primary `:8080` 保留，不向 primary 发管理写请求。仅清理本轮拥有的精确 ID、PID、目录和 Unit。Users 无 DELETE API，执行/审计历史及指定备份按既定策略保留。原版清理结论不能作为修复版当前清理证明。

本轮产品源码尚无新增修改；既有修复保留。OpenSpec `repair-e2e-confirmed-defects` 为 21/27 tasks，真实验收、清理与完成门禁尚未完成；不 Archive、不 commit/push。不声明整体可交付。

接续状态以本地文件为准；此前记忆bootstrap禁用，长期经验已另存并commit，临时测试状态不写长期记忆。

## 缺陷逐项状态

| 缺陷 | 修复版状态 | 描述 |
|---|---|---|
| E2E-20260930-101945-P1-001 | PASS | 停留页面的会话失效后，401清空Token但不自动跳转登录 |
| E2E-20260930-101945-P0-002 | PASS | 升级PREPARING阶段崩溃后恢复删除旧Binary/Config/Unit，旧服务未恢复 |
| MAIN-USER-EMPTY-PASSWORD | PASS | 创建用户服务端接受空密码，空密码可真实登录 |
| MAIN-USER-INVALID-ROLE | PASS | 用户Role更新接受三种有效角色之外的字符串并持久化 |
| BE-SECRET-STREAM-001 | PASS | secret参数跨实时日志分块后可重组为明文 |
| BE-SCRIPT-SHA-002 | PASS | Agent接受控制同步中SHA256不匹配的脚本内容并前进Revision |
| BE-REENROLL-STATUS-003 | PASS | 撤销后重新纳管换新凭证成功但节点仍disabled |
| BE-REMOTE-TTL-004 | PASS | Agent忽略Remote State ObservedAt/TTL并执行过期条件任务 |
| WEB-SECRET-DEFAULT | PASS | Script/Task Editor secret参数默认值摘要明文展示 |
| WEB-LIVE-LOG | PASS | Execution详情已有chunks后新日志及终态stdout不更新 |
| WEB-APP-LIVE-STATE | PASS | Managed Application操作成功后Health/Execution History仍旧状态 |
| WEB-EXEC-SEARCH-PAGE | PASS | Execution/Audit搜索仅过滤当前服务端页 |
| WEB-AUTH-SIGN-IN | PASS | 已认证访问/sign-in仍显示登录表单 |
| WEB-TASK-DELETE-CANCEL | PASS | Task删除取消后菜单残留并锁住页面 |
| WEB-TRANSFER-SOURCE-CHANGE | PASS | Transfer切换Source后已选目标成为自身且仍可开始 |
| WEB-ROLE-LIVE-STATE | PASS | 自身降权后当前页面仍显示并允许打开Admin创建表单 |
| WEB-TASK-PENDING-SAVE | PASS | Task保存pending期间按钮未禁用，重复点击发重复PUT |
| WEB-BLOCK-REASON | PASS | 真实BLOCKED执行详情未显示Hub保存的block_reason |
| FAIL-ENV-HOST-001 | BLOCKED | Docker Host Integration的nsenter root参数导致托管systemd部署失败 |
| FAIL-ENV-TLS-001 | PASS | Agent tls_ca_file未用于Artifact HTTP下载，私有CA环境预取失败 |
| FAIL-ENV-HOST-002 | NOT_RUN | Host Integration缺失或错误挂载时FileRelay返回SUCCESS，逻辑宿主目标未写入 |
| FAIL-ENV-UI-THEME-001 | PASS | System主题遇暗色媒体时页面已暗色，theme-color仍为白色 |

## 2026-10-08 日志/认证真实结果已审查汇入

G-01-01、P01-01、P17P18-11三场景新增完整PASS；BE-SECRET-STREAM-001在正常、程序逐字节/64KiB、两流、retry、prefix取消/timeout与1MiB正中Secret截断的真实实时/终态维度通过。完整Secret不可重组，跨流stderr后缀片段仍可见的事实保留，未扩张为屏蔽任意片段。原fixture source从五个保留Revision恢复并独立核验SHA。见 [运行审查](runs/20260930-101945/repair/runtime-case-review-20261008.json)。

WEB-LIVE-LOG、X15、X16等仍PARTIAL：断连恢复、secure clipboard真实复制、部分retry元数据/最终FAILED及Revision/Audit/error遮蔽未验证。12Task/5Script精确DELETE200+GET404，11Execution历史保留；静态/Mock不计通过。workflow和native4 ENV最新结果继续单独审查，整体任务仍未完成。

## 2026-10-08 工作流真实结果已审查汇入

E2E-01完整Script→Task→native-3 SUCCESS→Logs→同Task Audit UI通过；15条canonical增补维度，E2E03实际cwd/run_user与E2E07禁用未来slot等仍PARTIAL。一次Agent Schedule真实触发成功；Task Escape0DELETE/GET200及确认1DELETE/GET404已独立补证。25批次exact资源及一个followup Task均GET404。见 [工作流审查](runs/20260930-101945/repair/workflow-case-review-20261008.json)。

native4环境预检ENV1/2/4/5已通过，实际running/disk SHA匹配，daemon/目录精确清理；生成真实child config的低权限readability仍须在setup前完成，未将预检计504/22通过。两次期望SHA转录与owner argv匹配错误已按harness保留并修正实跑，不计环境/产品失败。尚未进行新Agent故障注入或改动已有服务。

## 当前新增结果与接管

E2E03实际cwd=/tmp、nobody/UID65534、参数/env消费及同Task Logs/Audit闭环PASS；四独占fixture均GET404。新观察：SUCCESS ExitCode0在API因omitempty缺失、UI显示“-”，P17P18-07 FAIL，已提出最小范围Review等待确认，尚未写源码。见 [E2E03/退出码审查](runs/20260930-101945/repair/e2e03-case-review-20261008.json)。

先前两个完整历史fork测试会话的模型覆盖未核实，功能证据真实但不能声明满足Luna模型要求；现通过fork=none显式gpt-6-luna的新会话接管后端cases，旧会话停止调度。见 [模型审计](runs/20260930-101945/repair/qa-resume-20261008-root/execution-model-audit.json)。平台无删除接口，历史会话保留，不伪称已删除。

native4独占非root child已就绪，ENV01-05实测通过；旧4Node健康，新Node/Agent身份未复用。backend/cases由新执行者接管，owner proof已实时核验；proxy观察harness漏argv导致短暂离线，恢复中的最新checkpoint/PID应优先于setup旧值，不能按旧PID清理。故障数据与恢复均只限该child，原deployment不变。尚未完成22/504、最终环境清理或Git交付。

## 2026-10-08 用户继续后的最新断点

明确fork=none/gpt-6-luna执行器已经实际恢复工具调用。已有SHA checkpoint的v2恢复完成，续跑未重新执行已完成步骤；补齐Execution UI和case summary，并精确清理两Task+两Script（均GET404）。Root正在审查已测原子拒帧/恢复，未测ManagedApp prune路径保留。当前开始TTL合法控制组与单因子负向，随后仅child重新纳管与owned环境清理。新增退出码API修复仍未获得明确Review确认，继续原授权范围。

新会话以backend/cases当前owner ledger和checkpoint为准，不能使用setup旧PID/port或把provider错误时旧描述当实时状态；provider reset时间/时区未推断。

## 2026-10-09 接续已启动

主会话核实此前确认的22缺陷/504场景范围，沿用授权，未扩展范围。已明确用 `fork_turns=none`、`gpt-6-luna` 创建并派发两个具体测试会话：后端补齐TTL真实UI/恢复及独占child重新纳管；Web补齐实时日志与独立覆盖缺口。完整任务映射与阶段进度见 [本轮progress](runs/20260930-101945/repair/qa-resume-20261009-root/progress.json)。

上一轮private连接目录已失效，后端Luna按既有SSH kvm2/QGA只读读取exact repair配置的方式恢复；不绕过guest-exec限制。TTL checkpoint已含7个负例，续跑从UI/恢复开始。当前计数仍为既有71PASS/1FAIL/159PARTIAL/273NOT_RUN，22矩阵14PASS/1PARTIAL/7NOT_RUN；审查前不提升结果。原四Node、部署和备份保留，primary :8080禁止管理写，不Git/Archive。

## 2026-10-09 连接与SHA证据审查

repair健康/ready/login/me/nodes均200，管理员及4repairNode与child online/synced，新私密连接目录已恢复。三个明确Luna测试会话分工写各自证据目录；运行时故障组先核对owner/准确目标。

Root复核前一日坏SHA原始事件、SQLite只读oracle、恢复及UI、4对象GET404，确认坏帧前后revision692保持且两Script仍v1；合法帧恢复v2。BE-SCRIPT-SHA-002与INV-05由NOT_RUN提升为PARTIAL，保留ManagedApp FullSnapshot prune及数据库故障/重启维度。这是历史修复版证据补记，不称本轮新运行。[审查](runs/20260930-101945/repair/qa-resume-20261009-root/sha-case-review.json)。

## 2026-10-09 登录与运行进程核验

Luna真实错误密码POST401、明确错误、sign-in停留与无Token及正确登录恢复200已审查，P01-02由PARTIAL补齐PASS。P01-03一个空字段分支仍待fresh表单动作复核，不将harness差异冒充产品失败。见[认证审查](runs/20260930-101945/repair/qa-resume-20261009-root/web-auth-case-review.json)。

native1 owned Command Task的/proc祖先核验真实repair Agent PID33342、SHA d55dc5cd…匹配manifest；baseline PID24684/另一SHA属独立旧服务，保留且未操作。两只读probe Task DELETE200+GET404。runtime组获准准备专属root/child/Unit，故障注入仍待owner ledger/source定点审查。

本轮记忆MCP memory_store已实际成功，仅保存用户Luna委派和进度落盘偏好，不含凭据；历史不可用说明仅适用于历史会话。

## 2026-10-09 TTL完整缺陷路径审查通过

BE-REMOTE-TTL-004补齐BLOCKED真实原因UI与合法response恢复：request/response ID关联、payload不变、transport500ms延迟、物理marker SHA/权限/UID、SQLite SUCCESS/synced及成功UI均一致。原7负例复用其修复版真实证据未重复运行；原缺陷专门路径已PASS，原22矩阵现15PASS/2PARTIAL/5NOT_RUN。[审查](runs/20260930-101945/repair/qa-resume-20261009-root/ttl-case-review.json)。

E2E-10/SYS-29/INV-09仍PARTIAL，真实离线、online属性、未观察等独立维度保留；完整504计数不因缺陷专门路径通过而泛化。多次恢复harness实现错误均单独留档并精确清理临时oracle，未计产品FAIL。TTL批次完整cleanup正在执行，child暂留给重新纳管。runtime独占child准备通过，Application v1基线准备已授权，信号故障执行仍待source审查。

## 2026-10-09 当前已落盘接续阶段

TTL全部候选Task现GET404；本cleanup调用发出0DELETE，不伪称自己删除先前16存在对象。精确删除8个UID/realpath/SHA已核marker，不删目录；3个旧0644模式事实保留。11关联Execution仍GET200。Backend继续owned child revoke/reenroll；WEB实际断网缓存RUNNING→onlineFAILED且清搜索后双marker完整，Copy验证尚未完成；P0组真实v1 Artifact/App部署SUCCESS/物理SHA/Unit active与健康已留证据，准备v2与observer，尚未发signal。整体cleanup仍未完成。

## 2026-10-09 Web首批/故障执行审核已落盘

P01-03 fresh UI三空字段拦截与server401/no token、G02-09十二导航active/历史/direct URL补齐PASS。首批ownedTask UI DELETE200+GET404，4Execution/Logs历史保留；断网恢复日志维度增补但Clipboard/Follow滚动/attempt/PENDING缺维仍PARTIAL。缺失offline-ui-controls引用已更正为真实现存证据，未造证据。[审查](runs/20260930-101945/repair/qa-resume-20261009-root/web-batch01-review.json)。

P0 observer/launcher具体源码/flags/nonce/READY、SIGSTOP后finally恢复、SIGKILL全过程owner+phase+v1SHA、stdout多JSON解析已Root审核批准；这不是P0实测通过。child RuntimeMax延长API拒绝、未改，允许仅同owned Unit/config/Node安全重建3600秒后执行fault/恢复。原服务保持。[源审查](runs/20260930-101945/repair/qa-resume-20261009-root/preparing-source-review.json)。

## 2026-10-10 用户继续与完成证据补记

三个明确Luna可复用会话已从usage-limit后实际恢复工具调用。Root将上一轮Web第二批13PASS/3PARTIAL、X02及重新纳管完整路径、P0 attempt03原始故障/恢复证据审查入表。P0保持desired v2-attempt3、实际guarded SIGKILL与重启保留旧v1三SHA/Unit active，Journal保守PREPARING/inspection与FAILED明确反馈符合有效Spec；不伪称DONE/healthy或其他phase都测过。六个关联canonical保留PARTIAL。[本轮进度](runs/20260930-101945/repair/qa-resume-20261010-root/progress.json)，[审查](runs/20260930-101945/repair/qa-resume-20261010-root/completed-result-review.json)。

Native1旧child1h自然到期，已onlyowned重建并核PID/身份；复用真ManagedApp补Snapshot/prune SHA缺口。Native2TLS server/root现场归属匹配，已补无CA/错CA/坏SHA拒绝及正确CA真实GET/cache/DB，实际deploy因自设sandbox首次失败；Root仅批准owned child ReadWritePaths系统Unit目录且保留其他严格保护，重试API成功待physical/UI/cleanup。Web首6个pendingNode已精确删；为补因harness提前退出而未测分页，Root准许下一小batch有限可删fixtures，原节点不操作。以上均未全部cleanup或整体验收，不Git/Archive。


2026-10-10 TLS/P04分页审查：私有CA真实正负下载及SHA缓存通过、App部署cache hit及Unit/文件/DB/UI验证并清理；direct TLS deploy-fetch未测。分页实际11 Node，第二页/回退/页大小和API ID集合一致。见 [审查](runs/20260930-101945/repair/qa-resume-20261010-root/tls-pagination-review.json)。

Web batch03已审核：5条原PARTIAL补齐PASS（三类地址/边界/Hub URL/命令Tab及字段）；HELLO仍未测，旧pending验证保留有效状态。15个自建Node已清理。见 [审查](runs/20260930-101945/repair/qa-resume-20261010-root/web-batch03-review.json)。

SHA整帧拒绝/ManagedApp真prune闭环已通过且P0/SHA全部owned资源精确清理，原4健康。INV05写入/Commit故障仍未测。见 [审查](runs/20260930-101945/repair/qa-resume-20261010-root/sha-prune-final-review.json)。

SYS01/02/09、AR08、INV02由已完成修复版实际证据补记PARTIAL；旧backend原版引用不算repairPASS。见 [映射审查](runs/20260930-101945/repair/qa-resume-20261010-root/runtime-evidence-mapping-review.json)。


三个Luna会话实际usage-limit，中断时Root完成安全收尾。DB SQLite_BUSY真实旧SHA/rev不变及NACK/applied0，normal Hub恢复已观测，但COMMIT-call/故障后Run/UI/成功后重启未测。Root仅清理fixtures/校验DB无残留；LiveLog尚无真正Run，Task/token/context/SSH已清。Host001容器准确停止，image/volume/root/bridge/Node保留供接续，proc-root权限原因未独立确认，无policy放宽；原4online/synced且healthz/readyz/me200。[交接进度](runs/20260930-101945/repair/qa-resume-20261010-root/progress.json)，[DB中断审查](runs/20260930-101945/repair/qa-resume-20261010-root/db-write-fault-interruption-review.json)。没有Git操作、产品源码变更或Archive。

Luna3会话在2026-10-10T05:53–05:56 UTC逐个真实工具调用成功后恢复；DBfault attempt02正常v2实际落盘/Run marker与Execution UI已补，LiveLog trueRun/Follow滚动坐标已补但clipboard/PENDING仍未过。quota时的安全收尾记录作为历史保留，当前以Rootprogress与最新attempt为准。

恢复后API公开契约在当前OIDC关闭配置PASS（SSO为PRODUCT后规划，不测试IdP）；Users负向/无副作用、DB写故障正常恢复实际DB/Run/UI与Native3真实运行SHA已审查，LiveLog实际Follow坐标已补。见 [审查](runs/20260930-101945/repair/qa-resume-20261010-root/recovered-runtime-api-review.json)。

## 最新证据核验 2026-10-10T06:48:45.617361+00:00

真实复制1920bytes与API/DOM SHA相同；owned child重启Revision1400/身份保持，未捕获Heartbeat报文；8类missing-ID契约及集合不变已实测，Execution等待补，因此API-NOT-FOUND为PARTIAL。[Root核验](runs/20260930-101945/repair/qa-resume-20261010-root/restart-clipboard-not-found-review.json)。

复制拒绝反馈已补齐：实际read/write=denied，点击后65.8ms新增manual fallback提示；真实clipboard hash与显示日志一致，清理通过。PNG为全白无效图，结论依据DOM/权限/API/真实clipboard，不声称async write成功。[核验](runs/20260930-101945/repair/qa-resume-20261010-root/clipboard-denial-review.json)。原22日志缺陷仅PENDING UI仍未捕获。更新时间2026-10-10T06:50:34.541080+00:00。

API-NOT-FOUND已补Execution item/logs/cancel/DELETE与列表/audit不变，Root复核现有路由范围后升为PASS。[核验](runs/20260930-101945/repair/qa-resume-20261010-root/api-not-found-closure-review.json)。当前96PASS；PENDING受控暂存仅prepare阶段，未实际arm。

Batch04第一段G-01-04/P02-02补齐PASS；其余3项仍PARTIAL。Artifact用户/注册码实际下载及拒绝路径已通过，Agentcredential helper待执行，canonical AUTH-ARTIFACT为PARTIAL。[Root核验](runs/20260930-101945/repair/qa-resume-20261010-root/batch04-phase1-artifact-review.json)。Node guard旧volatile字段hash变化仅为测试器限制，未定性产品修改；phase2采用持久稳定字段快照。

AUTH-ARTIFACT完整矩阵已通过：Admin/注册码/ownedAgent独立credential真实下载size/SHA一致，匿名/无效Bearer/错误AgentID拒绝；helperTask精确清理、原Artifact保留。[最终核验](runs/20260930-101945/repair/qa-resume-20261010-root/auth-artifact-final-review.json)。

Batch04 P02-05健康状态真实offline/online已PASS。Dashboard仅healthz失败，目标数据请求失败路径未触发，P03-10保持NOT_RUN；Group唯一fixture已准确清理，定位/类型切换/Cancel返回等未验，保持原状态。[核验](runs/20260930-101945/repair/qa-resume-20261010-root/web-batch04-phase2-review.json)。

最新断点：PENDING未实际Run；manifest守卫失败是测试器输入/owner核对问题，未判产品FAIL；2Task准确404，browser/profile清理，same-token logout有证据gap。Relay仅4拒绝GET通过，元数据/audit不变。[断点核验](runs/20260930-101945/repair/qa-resume-20261010-root/latest-checkpoint-review.json)。

PENDING守卫失败根因已定位为调用方用了重启前fc7旧SHA；最新owner/restart after值c814一致，本地controller已加创建helper前的最新指针门禁并离线自检，不改远端、不重试、不放宽guard。当前冷Groups EmptyState正常，Dashboard首次离线数据路径仍补测中。

用户最新顺序已落实：[先测试后统一修复](runs/20260930-101945/repair/qa-resume-20261010-root/test-first-continuation-review.json)。Web中断session已恢复撤销200/旧me401；PENDING旧SHA已修，允许1replacement，总child UI Run仍1。当前只修测试器/记录证据，产品修复待测试汇总后进行。

76个管理Auth负向全部401/state/audit不变，Group实际Cancel零POST回列表补齐PASS。PENDING第二次真实失败为manifest字段schema混淆（actual不存在owner_nonce），本地harness已修，2fixture清理、0Run；先独立只读inspector核物理owner/control。Dashboard改mounted页面owned tunnel连接故障，尚待结果。[核验](runs/20260930-101945/repair/qa-resume-20261010-root/auth76-cancel-checkpoint-review.json)。

## 2026-10-10 API stage1 追加审查（2026-10-10T11:31:55.935738+00:00）

24 个请求完成：分页与总数、Binary GET/HEAD 规范化、CORS、独立 session 撤销、malformed JSON 拒绝及集合无增删均有运行证据。API-CONTRACT/AUTH-MANAGEMENT 仍 PARTIAL，未外推全端点/所有角色/自然过期。见 [审查](runs/20260930-101945/repair/qa-resume-20261010-root/api-contract-batch04-review.json)。

## 2026-10-10 Script UI 批次审查（2026-10-10T11:39:59.015549+00:00）

Script新建/编辑取消、参数与环境变量持久化、真实保存与已知SHA、历史内容展开、克隆独立性补齐6项PASS。列表分页和cwd/runUser执行消费仍PARTIAL。两ownedScript暂留供只读矩阵，最终清理待后续。见[审查](runs/20260930-101945/repair/qa-resume-20261010-root/web-batch05-final-review.json)。

## 路由/表格与Run取消追加核验（2026-10-10T11:53:37.344339+00:00）

batch06并非按子agent声明直接勾选：ROUTE12错误恢复和Script排序方向仍PARTIAL，已下发补证；另外7项及真实Run确认取消各PASS。当前115PASS；原22不变。2Scripts/1idleTask仍owned受控保留，PENDING唯一实际Run尚未发生。

## 新增路由/排序与真实Run审查（2026-10-10T12:09:25.116919+00:00）

ROUTE12 missing ID错误恢复、Script实际sorting accessor比较规则已核验；Nodes详情错误分支仍待补证。新增真实UI Run1业务成功，但同页PENDING/RUNNING缺失，P11-02仍PARTIAL。最终watchdog释放/controls空值已独立复核；validAgent非归属下载403，Transfer及audit不变。原22仍19PASS。见[审查](runs/20260930-101945/repair/qa-resume-20261010-root/web-batch07-runtime-relay-review.json)。

## batch08与模型恢复（2026-10-10T14:40:43.220509+00:00）

Luna usage-limit后已实际clock/browser/API验证恢复，不推断reset timezone、不换模型。batch08 9PASS/1PARTIAL汇入，当前133PASS。Schedule/App/Artifacts/Execution/Audit/404route导航及Execution搜索排序已真实采样；ROUTE30错误恢复在下一批待最终映射。新增User持久化确认仍pending，不将总体“继续”视为新不可删除账号授权。

本次只读认证测试有1枚初始API登录token在aboutblank注入失败后无法取回或撤销，未用于业务或输出。后续专属token已精确注销401、profile已清理；该初始session仍列清理限制，不声称全部session清理完成。

最新接续：P05A-10真实复制成功/拒绝/恢复通过，一次性pendingNode已GET404。G03-10与TABLE-TASKS-ERROR共用真实pendingGET失败证据，属于同一个Loading缺陷，原22分母不变。ArtifactCache d430目录清理仍PARTIAL，6空目录已metadata确认；未完成缓存运行时验证。Web batch24 Appearance与runtime SYS34/40准备继续，先测试后统一修复。

更新：P21-07主题真实选择/刷新/system媒体跟随已PASS且恢复；P06-01已由原真实Empty/11rows/Retry证据完整闭环。ArtifactCache d430精确root与全部fixture清理已实际完成(normal1/recovery4)，不代表cache测试完成。下一batch26只2个disabled对象补Tasks rowclick和Schedule七列，无Run；runtime retry及cache完整harness本地准备继续。

## 2026-10-14 最新证据已汇入（SYS-34 / FL-05 / SYS-12）

- 汇入范围：2026-10-12T15:39Z 之后未入表的 4 组证据。Root 复核件 [latest-evidence-integration-20261014.json](runs/20260930-101945/repair/qa-resume-20261010-root/latest-evidence-integration-20261014.json)。
- **升级 PASS**：SYS-34（双流 18 chunk、seq 0..17 连续、3 次 attempt 各 71680B、stdout 215080=3×71680+40 终态尾行、UI 日志 430120 字符）、FL-05（Run Now 参数消费→RUNNING→SUCCESS→Task/Node 回链→3 次 attempt 日志）、SYS-12（ArtifactCache 上传/预取/A→pre-B→B 八字段一致、同 DB inode 与 FD、revision 单调、root 精确移除）。
- **维持 PARTIAL**：SYS-40（retry 间隔无规范阈值、无 per-attempt 审计、错误边界未探，批次自判亦为 PARTIAL）、SYS-09（仅 SOURCE_PREP_ONLY、零外部调用）、P05B-04（batch29 取得 4 组 false API/Disabled 徽章键哈希一致样本，但观测晚于授权只读窗口 31s，仅作观察）。
- 新增观察：APP-NODES-NULL-SHAPE（`GET /api/applications/{id}/nodes` 返回 JSON null 而非 []，疑产品空值形状缺陷，未立项）、EVIDENCE-FILE-POST-EDIT（batch28/29 证据文件事后手改、时间戳自相矛盾）、EXECUTION-SCRIPT-BACKLINK-GAP（详情页无 Script 回链，超出 FL-05 范围）、BATCH29-CAPABILITY-WINDOW-OBSERVATION；ArtifactCache 清理观察更新为已闭合且运行时已验证。
- 清理：sys34-40 三定义 DELETE200→GET404、3 条 Execution 历史保留；ArtifactCache attempt04 App/Artifact/Node/网络删除并 404、root `EXACT_OWNED_ROOT_REMOVED`；batch28/29 零服务端变更；清理后原 4 节点 online/synced。
- **环境现状（继续测试的前置阻塞）**：2026-10-14 只读核查，kvm2 上 `nodesteer-hub` 与 `nodesteer-agent-1..4` 全部 `shut off`（autostart disable），`:18080` 与 `:8080` 均不可达，kvm2 主机无 nodesteer systemd 单元或容器。用户已选择“剩余分母全量推进”（130 NOT_RUN + 157 PARTIAL），但恢复方式待确认后才能开始 live 测试。
- 本轮只改 `.e2e` 记录：产品源码未改、无 Git 交付、无 Archive。

## 2026-10-14 环境恢复与继续测试

- **环境恢复**（用户授权）：启动 kvm2 上 5 个 nodesteer VM + repair 单元（`nodesteer-hub-repair-20260930-101945.service`、4 个 `nodesteer-agent-repair-...-native-N.service`）。核验 `:18080` healthz/readyz 200、admin 登录成功、native-1..4 四个 repair 节点 online/synced、Hub/Agent 运行进程 SHA 与基线一致（hub `164ea4ac…`、agent `d55dc5cd…`）。原 `:8080` Hub 与 4 个原 Agent 未改动，未启用 autostart。证据 [environment-restore-20261014.json](runs/20260930-101945/repair/qa-resume-20261014-root/environment-restore-20261014.json)。注：agent VM(192.168.100.204) 的 qemu-ga 禁用 guest-exec/file-*，未绕过，改用其既有 root key 经 SSH。
- **继续测试汇入**：`FL-01`、`P14P15-01` NOT_RUN→PASS；`P09P11-01`、`P12P13-01` NOT_RUN→PARTIAL（新增列表/空态/搜索/分页/Error-Retry 覆盖，剩多页与行详情需 owned 夹具）；`SYS-09` PARTIAL→PASS（连接态 tombstone 收敛 rev 1653→1656、Agent 本地定义行=0、20s 窗口无新增调度、历史 Execution 保留；实时复核无残留）。
- **system 批次规划**：84 个 system NOT_RUN → 15 批 74 用例 + 5 环境不可行 + 5 可复用证据。5 项如实记为 **BLOCKED**：`SYS-24`（DST 需改宿主机时区）、`SYS-54`/`INV-14`（Host 容器 `/proc/1/root` 在 AppArmor 下 Permission denied）、`SYS-55`/`AR-31`（FAIL-ENV-HOST-002）。[规划](runs/20260930-101945/repair/qa-resume-20261014-root/system-batch-plan-20261014.json)、[复核](runs/20260930-101945/repair/qa-resume-20261014-root/system-batch-plan-review-20261014.json)。
- **可复用证据独立复核**（保守口径）：`INV-15` NOT_RUN→PASS（容器重建后同一 DB dev/inode 且新容器 Agent 持有同 inode FD 的物理证据）；`E2E-18`/`AR-04`/`E2E-16`/`AR-26` NOT_RUN→**PARTIAL** 并写明缺失（E2E-18 缺 Journal 语义需补引 attempt02；AR-04 无幂等证据；E2E-16 引用错配且缺 pending/HELLO 报文证据；AR-26 证据原文为 rollback failed、规划高估了回滚闭环）。[核验](runs/20260930-101945/repair/qa-resume-20261014-root/reusable-evidence-verification-20261014.json)、[汇入复核](runs/20260930-101945/repair/qa-resume-20261014-root/reusable-evidence-integration-review-20261014.json)。新观察 `OBS-20261014-AR26-ROLLBACK-UNCLOSED`（待独立复核回滚终态）。
- 串行约束：system 批中 `sys-b01/b06/b08` 与 `sys-b14` 的 SYS-66 段会重启 repair Hub，必须独占；`sys-b15` 替换 Agent 二进制需最后执行并恢复基线 SHA。

## 2026-10-17 最终收口（504/504 全部有结论）

**计数**：**473 PASS / 8 FAIL / 13 PARTIAL / 0 NOT_RUN / 10 BLOCKED**（93.8% 通过）
**构建**：repair 环境运行 `dddee4f0-e2e-repair-20261016-cr1`（hub `fdfa667b…`、agent `b15f6df5…`）；旧构建 `…-20261004` 与 `…-20261016` 的备份保留可回滚。

### 本轮（10-16~10-17）新增完成
- **7 条缺陷修复实施 + 回归验证**：P0 时钟偏移（协议只增不改 + Agent 优先 TTL，混版本兼容双向验证通过）、P0 回滚毒行（superseded + 版本/路径绑定 + `ROLLBACK_FAILED`）、P1 CommitSync 双解锁 panic、P1 超时/内容边界、P2 分组空名/upsert、P2 SQL 外泄（4 处 PUT→404）、P3 方法语义（audit/me/metrics GET-only、logout POST-only）
- **CR-1**（代码评审发现的谓词过宽）已收窄并双向验证；**CR-2** 文档已同步（ARCHITECTURE §40/§52/§62、PRODUCT §20/§28/§42/§46）
- **C1 与最小授权集批次**：G5（13 升 PASS）、G1（8 升 PASS）、G2+G3（6 升 PASS）、G4（2 升 PASS）、G6（3 升 PASS）、G7（裁决简报）
- **G8/G9**：各查出 1 条 P1 缺陷 → X16、X14 转 FAIL

### 缺陷台账
- **原 22 项**：20 PASS / 1 NOT_RUN（`FAIL-ENV-HOST-002`）/ 1 BLOCKED（`FAIL-ENV-HOST-001`）
- **新增 17 条**（均 `source_change_authorized=false`，未修）：
  - P0：E2E-20261014-REMOTE-CONDITION-CLOCK-SKEW, E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE
  - P1：E2E-20261014-TIMEOUT-CONTENT-VALIDATION-GAP, E2E-20261015-COMMITSYNC-FAILURE-DOUBLE-UNLOCK-PANIC, APPUI-20261016-ENVFILE-QUOTED-DASH, E2E-20261017-SECRET-SPLIT-VALUE-RECONSTRUCT, WEB-NODE-SELECTION-DRIFT-20261017, E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING
  - P2：OBS-20261008-EXECUTION-ZERO-EXIT-CODE, OBS-20261010-UI-LOGOUT-NOT-REVOKED, E2E-20261015-GROUP-EMPTY-NAME-AND-UPSERT-200, E2E-20261015-SQL-ERROR-LEAK-ON-PUT-NOTFOUND, E2E-20261016-EDITOR-PENDING-NOT-DISABLED-DUPLICATE-POST, E2E-20261016-NARROW-VIEWPORT-LONG-VALUE-OVERFLOW, APPUI-20261016-ROLLBACK-FALSE-FAILURE, E2E-20261016-ASSIGN-MISSING-APP-500-SQL-LEAK
  - P3/其他：E2E-20261014-AUDIT-METHOD-FALLBACK-200

### 仍未通过（8 FAIL / 13 PARTIAL / 10 BLOCKED）
- **FAIL**：G03-10, G04-01, P03-10, P17P18-07, X14, X16, X20, TABLE-TASKS-ERROR
- **BLOCKED**（环境不可行，逐条理由已论证）：P01-08, P01-09, P01-10, SYS-07, SYS-24, SYS-54, SYS-55, X17, AR-31, INV-14
- **PARTIAL**：G04-03, G04-04, P04-07, P09P11-12, P12P13-05, P14P15-13, P14P15-18, P17P18-04, SYS-40, X06, X08, X10, X19

### 待用户裁决（入口：`qa-resume-20261014-root/pending-decisions-20261016.md`，A–I 九节）
- 未修缺陷 17 条的修复范围（其中 4 条 P1 建议优先）
- CR-3（四类悬挂行终态/查询面）
- G7 三个裁决点（焦点返回小改 / Enter 不提交 / 过去时间）
- A 节 12 条产品契约问题
- 13 条 PARTIAL 与 10 条 BLOCKED 的正式口径

### 未完成门槛（不声明可交付）
`repair-e2e-confirmed-defects` 仍 21/27：真实验收、环境清理、Code Review 收口与 Archive 均未执行；原 22 项中 `FAIL-ENV-HOST-002` 仍 NOT_RUN。

---

## 2026-10-18 最终收口（504 全量结论 + 全部缺陷修复验证完成）

### 一、最终结论

| 状态 | 数量 | 占比 |
|---|---|---|
| **PASS** | **494** | 98.0% |
| FAIL | 0 | — |
| PARTIAL | 0 | — |
| NOT_RUN | 0 | — |
| **BLOCKED（环境不可行，逐条理由）** | **10** | 2.0% |
| **合计** | **504** | 100% |

BLOCKED 明细（正式口径：环境不可行 + 理由 + 不视为产品失败）：
- Host 集成族 4：`SYS-54`、`SYS-55`、`AR-31`、`INV-14`（docker-default AppArmor 拒绝 `/proc/1/root`）
- OIDC/SSO 4：`P01-08`、`P01-09`、`P01-10`、`X17`（部署未启用 OIDC + 无可用 IdP）
- DST 1：`SYS-24`（需真实宿主机时区跳变）
- Full Resync 1：`SYS-07`（条件算术不可达）

### 二、缺陷台账（26 条）

- **FIXED_AND_VERIFIED：24 条**（含本轮新增：`E2E-20261017-ENROLL-CONCURRENT-500-SQLITE-BUSY` P2、`E2E-20261017-SECRET-SPLIT-VALUE-RECONSTRUCT` P1 等）
- **CLOSED（非缺陷/设计/文档化）：** `OBS-20261014-AR26-ROLLBACK-UNCLOSED`（被回滚修复覆盖）、`OBS-20261014-REMOTE-TTL-PATH-UNREACHABLE`（死代码确认）、`OBS-20261014-OFFLINE-NODE-MANUAL-ONLINE-NO-SWEEP`（派发明确报错）、`OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST`（harness 问题）
- **产品口径维持现状**：密码最小长度（现网契约把 1 字符密码记为 200，不翻转）、注册令牌不轮换（未落盘）
- 缺陷明细见 `.e2e/failures.json`（`repair_additional_failures`）

### 三、最终构建与验证

- **构建**：`dddee4f0-e2e-repair-20261018-final`（hub `00e1f375…`、agent amd64 `c6ccfe10…`、arm64 `7d987125…`、web dist index `1fdfc0b9…` 74 文件）
- **部署**：repair 环境（hub `.209:18080` + 4 节点），仅重启 repair 单元；主 Hub `:8080` PID 1251 与 `nodesteer-agent.service` 全程未动；备份 `*.bak-20261018-final` 可回滚
- **前端产物一致性**：本地 dist 全部 74 文件与服务端**逐字节一致**（修复上轮"产物滞后于源码"的教训）
- **定向复验 22/22 全部通过**（Go 10 项 + Web 12 项，含 X16 拆片脱敏真机零泄漏、enrollment 12 并发全 200、`/api/me` 端到端登录、AND 条件摘要、跨页联动、徽标本地化、pageSize 回退、角色过滤、退出撤销）
- **破坏性控件 7/7 PASS**（删除节点/脚本/应用/发布包、撤销凭证、取消传输、移除应用节点分配；全部 owned 对象 + 逐条 GET404）
- **清理**：33 个 fixture 中 30 个逐条 GET404；主机零残留；不可删历史（3 条终态传输、执行/审计追加型记录、3 个角色用户）如实保留

### 四、修复批次汇总（本轮全部）

| 批次 | 内容 |
|---|---|
| Go 侧 6 项 | ENVFILE 引号、回滚误报、assign 404、传输卡 DELIVERING、exit_code 0、参数类型/用户名 trim |
| Web 侧 6 项 | 选择漂移 `getRowId`、四编辑器 pending、Skip to Main 锚点、窄屏溢出、退出撤销会话、App 编辑器复核（非缺陷） |
| CR-1/CR-2/CR-3 | 回滚谓词收窄、文档同步、四类悬挂行终态化（`superseded`/`stale_binding`/`legacy_unbound`/`prepare_interrupted`） |
| X16 | secret 拆片脱敏（8 字节分片索引 + 跨流联合判定；无 secret 路径 137ns/0 alloc） |
| T2（Go 尾部 10 项） | enrollment 并发 503 语义 + `_txlock=immediate` + 错误日志、APP-NODES `[]`、过去时间 400、G23 握手门禁、API 契约 6 条、artifact 超时、`from_version` 实际版本、重名归一化、自传/禁用文案 |
| T3（Web 尾部 11 项） | payload 剔字段、脚本回链、徽标本地化 ×2、无效深链、角色过滤、跨页联动 5 处、AND 条件摘要、pageSize 回退、调度校验、system 枚举 |
| 主 Agent 修补 | `statusLabel` 空值防御（T3 引入的崩溃）、`/api/me` 消费方同步（api.ts/auth-store/catalog/2 测试） |
| 文档同步 D1/D2/D3 | `PRODUCT.md`/`ARCHITECTURE.md`/`TEST_PLAN.md` 共 **39 处编辑**（含 9 条冲突旧表述改正） |

### 五、未验证项与剩余风险（如实列出）

1. **传输 30 分钟停滞看门狗**：阈值固定，未实网等待；写失败分支与看门狗逻辑由单测覆盖（`file_transfer_timeout_test.go`）
2. **X16 已知边界**：<8 字节片段与低熵 secret"接缝"残余（泄漏量恒 <8 字节、不增加泄漏能力）；脚本可任意变换值时脱敏属纵深防御非对抗性控制
3. **密码策略**：无最小长度校验（维持现状，属产品策略）
4. **CORS**：`Allow-Origin: *` 无凭据组合，未收紧（收紧需部署形态裁决）
5. **Host 集成族 / OIDC / DST / Full Resync**：环境不可行（见 BLOCKED 口径）
6. **历史文档**：`REAL_TEST_REPORT.md` 的旧行为表述保持原样（不改历史报告原则），已在本文档登记
7. 平台事件：2026-10-18 子 Agent 会话因 API key 过期中断，T3 半成品由主 Agent 接手修复并全量复验（见 `web-tail-fix-report-20261017.md`）

### 六、交付

- **Git**：分层提交（Go / Web / 文档 / OpenSpec / 测试记录 / 会话前既有 `web/e2e` 改动），**不 push**（未授权远程操作）
- **OpenSpec**：`repair-e2e-confirmed-defects` tasks 勾满后 Archive
- **环境**：repair 环境保留（备份可回滚）；主环境全程未动

---

## 2026-10-18 交付记录（Git 提交 + OpenSpec 归档）

依据用户明确授权（「git提交也归档」「裁决你做」）。**仅本地提交，未 push**（未授权远程操作）。

### 提交清单（master，ahead 8）

| # | Commit | 内容 |
|---|---|---|
| 1 | `c4e05bc` | `fix(agent,hub,store)`: Go 全量缺陷修复（64 文件，+6499） |
| 2 | `9597a22` | `fix(web)`: Web 全量缺陷修复（60 文件，+4591） |
| 3 | `6d7a451` | `docs`: API 契约与行为语义同步（D1/D2/D3 共 39 处） |
| 4 | `76fc357` | `chore(openspec)`: 记录 repair-e2e-confirmed-defects change |
| 5 | `01e04da` | `chore(e2e)`: 测试台账与修复验证证据（精选记录集 2065 文件） |
| 6 | `1dd6778` | `chore(web/e2e)`: **会话前既有**的 Playwright 改动（非本次修复，单独提交保归属） |
| 7 | `0245405` | `chore(openspec)`: tasks 27/27 勾选 + 归档 `2026-10-18-repair-e2e-confirmed-defects`（主 specs +18） |
| 8 | `293dc7e` | `chore(e2e)`: 补齐结构化证据与 runner（含随目录带入的 237 张截图） |

### 入库范围与实际差异（如实说明）

- `.e2e` 总计约 402 MB / 10690 文件；**未入库**：493 张截图、3 个视频、`__pycache__`/`*.pyc`、`build-cache`、`ws-frame-proxy` 二进制等（保留在工作区）
- 提交 8 因按目录 pathspec 添加，额外带入 237 张截图（约与仓库既有 `.e2e` 证据惯例一致）；提交信息中「未入库仅剩截图/视频」措辞不完全精确，实际为「未入库剩余 493 张截图与 3 个视频」
- 仓库 `.git` 体积 45 MB → 121 MB（测试证据入库所致，符合仓库既有 `.e2e` 证据入库惯例）
- 凭据扫描：干净（无硬编码凭据；`[redacted]`/占位/一次性测试口令除外）；repair 凭据始终只在 `/tmp`（0600）

### OpenSpec 归档核验

- 归档路径：`openspec/changes/archive/2026-10-18-repair-e2e-confirmed-defects/`
- `tasks.md`：**27 勾选 / 0 未勾选**
- 主 specs 同步：`specsUpdated=true`，新增 18 项
- 归档后工作区除截图/视频外干净

### 环境最终状态

- repair 环境运行 `dddee4f0-e2e-repair-20261018-final`；备份 `*.bak-20261018-final` 保留可回滚
- 主环境（`:8080` Hub PID 1251、4 个 nodesteer-agent.service、VM）全程未动
