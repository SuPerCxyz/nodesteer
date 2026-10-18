# Web E2E → Luna 交接（2026-10-03）

用户最新指定具体用例步骤由 Luna 执行。本高能力会话正式退休，不再启动新具体步骤。Root 保持主控/授权/整合；本交接只记录实际证据。

- 最新真实进程检查：own `web/run-playwright.py`/Playwright suite PID 列表为空。最后后台 `gap-mock-viewer-verified` 已实际 completed，exit=1，184.595s，GAP16失败、GAP17通过。最后 `pnpm exec eslint e2e --max-warnings=0` 已实际 completed，exit=0。
- 账本安全检查：已结构化 `test-data.json` 的 created=0；相对于该文件，新未合并 ledger open ID=0。上轮新增对象均在 finally 按 exact ID 删除；未新增 live 清理/SSH 检查。本轮没有部署 App/创建 Unit/写目标文件；仅命令执行历史与审计不可删除保留。311 deleted/90 retained_history/1 retained_account 是最近结构化快照，后续已删除记录仍需按 ledger 重算，不能当最新 live 全库计数。
- 本地 source Web=192 PASS/22 FAIL/118 PARTIAL/30 NOT_RUN；Root global=308 PASS/39 FAIL/134 PARTIAL/22 NOT_RUN/1 BLOCKED。两者不同；以 Root overlay 为全局事实，不覆盖已有失败。`remaining-web.json` 保留逐维待办。
- 新增确证缺陷仅 `WEB-TASK-PENDING-SAVE` 与 `WEB-BLOCK-REASON`，已写 `web/failures.json`。Task secret 扩展原 `WEB-SECRET-DEFAULT`；Audit 跨服务端页搜索扩展原 `WEB-EXEC-SEARCH-PAGE`；不重复计 bug。Role 仍 severity=P3/priority=P2，解释已入源记录。

## 凭据与所有权

private `/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json` 实际仍存在。fresh login，不复用/登出 Root 或 Env token，不打印凭据。唯一 Hub `http://192.168.100.209:8080`。业务仅既有允许的 Node1/4；Node3 daemon 不动；Node2 已由 Root 真实恢复 online/synced/dddee4f0。Root没有新增 offline 窗口授权给后续自由执行。

exact保留 QA Viewer `8ac0ef0f-fea5-4be3-8707-2c580363353b` 的强随机密码已在 GAP17 成功恢复，role保持 viewer；不必重复重置。凭据在 private `runweb/ui-users.jsonl` 最新该 user_id 且带 password 的行（忽略 synthetic secret 行）。独立 Viewer token 已实际 logout200。其余 User/原 Admin 均未修改。`agent1-shell.elf` 已由 Env 恢复；本轮未用它部署。

## 最新已执行而尚待合并的结果

- `gap-mock-viewer-verified-results.json` GAP17=PASS：`evidence/application-list-entry-cancel.json`（列表详情/编辑/删除/新建，列表无部署，编辑取消0PUT且Revision/名称不变）；`evidence/viewer-audit-immutable.json`（真实 Viewer、Audit无编辑/删除/保存控件，PUT/DELETE `/api/audit` 返回200只读数组，exact own Audit记录前后不变）。可据完整证据补 P14P15-01/P19-05；不要把 HTTP200误称审计 mutation，也不要假报403。
- GAP16 `[UI_MOCK]` 已完整跑11表503具体错误→Retry实际真实Hub请求→mock空数组。证据 `evidence/all-tables-error-retry-mock.json`，只证明模拟UI渲染/真实Retry恢复，不能变成真实Hub/Agent故障全链PASS。
- GAP16 mock空数组时 Tasks/Executions/Transfers 使用“没有结果。”，其余8表使用“暂无数据”。仅保留 UI 模拟观察；没有新增 confirmed defect 或全case PASS。Dashboard最后硬编码“暂无数据”断言是 harness 错误：实际有各面板具体空态文案。该断言中止了随后 mock `/healthz` 分支，`dashboard-empty-hub-unavailable-mock.json` 不存在，不能引用或声称跑过 Hub 指示。Env 正接真实 tunnel 断连/Retry，无需重建隔离环境或重复旧实证。
- `gap-mock-dashboard-viewer-results.json` GAP18=PASS、`evidence/dashboard-continuous-metrics-links.json`：真实 UI Login→Dashboard→Agents ViewAll→Node1 Detail→Task；actual limit50四指标含成功率、App health aggregate、sync counts与实际显示10详情链接。已入 source。
- `gap-physical-offline-verified-results.json` GAP15=PASS、`evidence/physical-offline-task-run.json`，Root `main/offline-task-ui-physical-result.json` 证明 Node2真实 service inactive/PID0/offline→UI Run400无Execution→finally恢复。首轮 `gap-physical-offline` 只是 flag等待超时、未点Run，已精确清理；不算产品FAIL。
- `gap-block-reason-real-results.json` GAP12=真实产品FAIL。`evidence/offline-node-condition-blocked.json`：Local false正确SKIPPED（不是BLOCKED）；真实 remote last_execution查neverRun Task→UNKNOWN→Agent BLOCKED，API有 block_reason，UI仅已阻断而无原因。

## 准备好但从未执行

`web/e2e/tests/gap-ui.spec.ts` GAP19（无引用Artifact UI取消0/确认1删除、对象与下载404）、GAP20（禁用Script UI引用400/启用恢复实际执行，以及duplicate/长内容/timeout API边界）仅写好并 lint，未启动。不要把代码存在当测试通过；API边界必须继续区分未做UI。GAP17/GAP18无需重跑。`update-gap-results.py` 当前未包括最新 GAP17 映射，更新后再执行合并；它只写 Web source，不能写全局 JSON。

## 当前 Root Web NOT_RUN ID

G04-02、G04-03、G04-07、P01-08、P02-05、P03-09、P03-10、P04-09、P07_P08-11、P14P15-01、P14P15-12、P14P15-17、P14P15-18、P17P18-14、P19-05、P22-10、P22-13、P22-15、FL-11、FL-12、FL-14、FL-16。

P14P15-01/P19-05已有上述未合并 PASS；其余对照 Root 的 `backend/remaining-web-system-crossmap.json` 与 Env 最新结果，逐维关联而非整组冒充实测。长文本/动态菜单/AX/其他实际UI剩余按原独占边界继续。共享Settings、环境故障、升级、恢复、TLS/OIDC均由Root/Env控制，不修产品、不写Git、不安装、不改AGENTS。

原完整交接 `web/handoff.md` 与所有 prior safe-run-index 已保留。最新执行入口仍为 `python3 .e2e/runs/20260930-101945/web/run-playwright.py LABEL --grep CASE`。Root/Luna必须独立验收 source/diff/证据；本旧会话声明不替代最终完成门禁。
