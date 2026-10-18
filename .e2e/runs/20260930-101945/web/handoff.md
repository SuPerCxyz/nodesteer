# Web 执行交接 — 2026-10-01 新上下文

目标和授权：用户已确认全功能E2E+两轮补测并多次继续。只改测试/证据/提示词，不修产品、不改AGENTS/根配置、不做Git写操作。本旧会话已由Root决定退休，新会话复用同工具/继承能力/同权限；无需重摸底或重做Review。

当前事实：本地504条={'PASS': 170, 'FAIL': 17, 'PARTIAL': 130, 'NOT_RUN': 187}；web362={'PASS': 170, 'FAIL': 17, 'PARTIAL': 130, 'NOT_RUN': 45}；134system/8API由Root `main/integrate_results.py` 做最终判定。本地与Root global统计不等价。案例的covered_dimensions/remaining_dimensions、evidence与status在 `case-inventory.json`，剩余逐模块见 `remaining-web.json`。不能因Compound缺一维抹去已测事实，也不能把API/Mock算未做的UI。

写范围：`.e2e/full-feature-prompts.md`、本run `web/**`、`web/e2e/**`独占。Root负责所有共享Settings、原生故障/升级、system/API与global JSON。Env RoleUI44页/26读见environment/role-ui-results.json；Settings新边界/持久/广播及Loginpending由main证据，勿重复写共享状态。SSO/restore隔离端口已撤除，仅引用已有证据。

凭据与入口：private `/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json`；base URL由该文件读，密钥禁打印。私密 fixture `/runweb/agent1-shell.elf`（路径相对于private）；新User密码/token仅private `runweb/ui-users.jsonl`，0600，runner会全量替换对应secret到REDACTED。Named browser `e2e-web-20260930-101945`，vault同名；Root/Env各自browser不共享。执行入口 `python3 .e2e/runs/20260930-101945/web/run-playwright.py LABEL --grep CASE`，现装Playwright无需安装。

全部新安全组结果索引在 `safe-run-index.json`。关键最终PASS：FULL09 managed-app-task-traced→765608ef via_task=true五操作/5不同Task/Unit/config/SHA/health+UI删App+exactcleanup；792c2de9旧首轮五项真实且Root独立GET佐证，a0f5c8c1旧cleanup409已superseded并精确恢复4定义。FULL08 direct原页异步状态缺陷仍FAIL，不被Task路径fresh App页反馈正确覆盖。

本地产品缺陷：WEB-SECRET-DEFAULT（Script摘要Secret默认值）；WEB-LIVE-LOG（已加载chunk后新片段/终态不刷）；WEB-APP-LIVE-STATE（Direct App操作后Health/History旧值）；WEB-EXEC-SEARCH-PAGE（q仅当前server page）；WEB-AUTH-SIGN-IN（me200已认证仍/sign-in）；WEB-TASK-DELETE-CANCEL（取消后open menu/aria-hidden/pointer-events:none，Escape不能恢复，必须reload）；WEB-TRANSFER-SOURCE-CHANGE（Target selector排除Source但换Source后旧Target成为self仍Start enabled，APIself200是允许契约，非API缺陷）；WEB-ROLE-LIVE-STATE P2（仅QA本人Admin→Viewer，API即时403，等5秒UI仍NewUser1并实际提交403，reload0）。完整结构化见 `failures.json`。ROOT-SESSION-401仅关联main实际401未跳转；backend跨块Secret另关联，不重复缺陷。

清理快照：ledger={'deleted': 237, 'retained_history': 63, 'retained_account': 1}，无created/open；live Hub定义nodes/groups/scripts/tasks/apps/artifacts中的本轮对象均0；Agent4 own qa_web_ label keys0；Node1本轮Unit files0/app directories0/matching units0；suite/runner process0。证据 `final-live-resources-check.json`、`final-owned-unit-check.json`、`runner-process-check.json` 均在evidence。Own历史63（完整逐ID在test-data.json）按不可删除边界保留，数量仅已记录ID，不宣称全DB历史。唯一永久QA账号8ac0ef0f-fea5-4be3-8707-2c580363353b / qa-20260930-101945-web-user-0adb7862，最终Viewer强私密密码，所有该账号相关测试tokens已logout；无User DELETE，保持Viewer，禁止删原Admin/其他账号。

工具事实：CLI screenshot实际返回tmp path，需复制实际path，不能虚报目标存在。Guest `guest-inspect.py`仅Node1 own Unit/目录，复用Env stdin `environment/guest-exec.py`（不得并写）。`inspect-script-cache.py`只读Node1 private data_dir/state.db按脚本UUID计数，SAFE23验证1→0；不DML/不读凭据。API created observer仅旁观真实fetch，保存ID跨硬导航，不修改payload、不算Mock。

契约纠正已写提示词：独立/schedules+旧?view重定向；无详情Tabs；12Sidebar+Artifacts间接入口；Task app Definition名称文本无href；Settings当前4key无gateway；OIDC allow_local_login与SSO互斥；DELETE missing当前200；每ManualRun新UUID新意图；on_start每进程一次；不新增DST/并发锁/卷缺失拒注册契约。Cron/OneTime GET可有忽略的interval_sec60/zeroTime，payload仅on_start全裁、非OneTime run_at裁，不误记缺陷。

未决观察：快速硬导航/history曾清Token跳/sign-in，无401；稳定networkidle条件SAFE17全PASS，可能旧/me取消catch无mounted guard。仅 `detail-deep-auth-observation.json` 候选证据，不已确诊；需新会话独立trace requestfailed/实际me。HTTP Copy只有manual提示，HTTPS Clipboard成功未测；trigger证据只manual，不能泛化schedule/system；theme-color未独立断言。

最后验证：`pnpm exec eslint e2e --max-warnings=0`退出0；3 Python helpers py_compile退出0。Public249文本文件扫描admin和QA真实密码/token命中0，见private-secret-scan.json。没有任何仍在跑的suite，未运行产品修复/Git。

下一优先（不扩大产品）：Task四类型保存往返/隐藏字段残留、参数secret与缺字段验证/多Group/直接Label动态；Artifact引用删除cancel/confirm/空文件/重复/headers；Enrollment Retry与pending；Audit跨server page；表格selection/所有列隐藏恢复和细节。当前NOT_RUN45个ID：

G04-01, G04-02, G04-03, G04-07, P01-08, P01-09, P01-10, P02-05, P03-09, P03-10, P04-09, P05A-07, P05A-11, P07_P08-11, P09P11-22, P11-04, P11-08, P14P15-01, P14P15-12, P14P15-17, P14P15-18, P16-04, P16-08, P16-09, P16-10, P17P18-13, P17P18-14, P19-02, P19-05, P21-03, P21-04, P21-05, P21-06, P22-10, P22-13, P22-15, FL-01, FL-02, FL-06, FL-11, FL-12, FL-14, FL-16, X08, X20

复合PARTIAL130逐项covered+remaining及evidence见remaining-web.json；Root有system/API/Env/main对应证据可逐维度关联，不能把这些本地未测项直接删掉。
