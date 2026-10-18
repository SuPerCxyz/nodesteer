# Root 裁决记录（2026-10-17）

依据：用户明确授权「裁决你做 + git 提交也归档 + 全部修复延伸 + 继续」。
本文件记录 Root 对全部待决项的最终裁决；执行与证据在对应批次目录。

## A. 待决项裁决（原「需要用户一句话」）

| 事项 | 裁决 | 理由 / 后续 |
|---|---|---|
| `SYS-40` per-attempt 审计粒度 | **接受为设计 + 文档化 → 按 C3 口径升 PASS** | 取证确证：审计粒度 = 逻辑触发（1 条 execute 行），attempt 细节在日志（双流 marker×3）。要求 attempt 级审计属新能力，不在一期范围。文档化位置：`docs/PRODUCT.md` 审计章节 + 台账注释。观测记录：3 次 attempt <50ms 无退避（不构成缺陷） |
| `P09P11-12` 残留惰性字段 | **修复**（从任务保存 payload 剔除 application_id/script_id/group_ids/label_*） | 与已修复的 app_operation 剔除一致；惰性但属契约卫生 |
| 10 条 BLOCKED 正式口径 | **记为「环境不可行 + 逐条理由 + 不视为产品失败」** | Host 族 4（AppArmor 拒绝 /proc/1/root）、OIDC 4（未启用+无 IdP）、DST 1（需真实宿主机 TZ 跳变）、Full Resync 1（算术不可达）。不硬凑 PASS，不长期挂 NOT_RUN |
| 密码最小长度 | **维持现状（不加校验）** | 现网证据把「1 字符密码创建」记为 200；自加阈值会翻转已记录契约且属产品策略；如产品要规则再立项 |
| 注册令牌轮换（SEC-20261014） | **不轮换**（沿用早前决定），保留为卫生建议 | 令牌未落盘；轮换会打断 repair 环境与其他批次 |
| CR-3 后续（部署 journal 只读查询面） | **不做**（记录为可选增强） | 终态化已消除两个症状；查询面属新能力，不在一期范围 |
| `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED` | **闭环**（已由 Web 修复批修复并验证） | 四编辑器 pending 禁用已上线并复验 |
| `OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST` | **闭环为非缺陷**（harness 点击投递问题，已最小实验证明） | 产品逻辑未改；harness 侧记录 |
| `OBS-20261014-GROUP-DUPLICATE-ERROR-RAW-SQLITE` | **闭环**（G04-03 已复验归一化生效） | 15 项断言无 SQLite 原文 |
| `OBS-20261008-EXECUTION-ZERO-EXIT-CODE` / `OBS-20261010-UI-LOGOUT-NOT-REVOKED` / `OBS-20261014-REMOTE-CONDITION-CLOCK-SKEW` / `OBS-20261014-AUDIT-YEAR-METHOD-200` / `OBS-20261016-G1-USER-INPUT-NOT-TRIMMED` / `OBS-20261017-SCRIPT-PUT-TYPE-UNVALIDATED` | **闭环**（均已修复并验证） | 见 fixall 验证批证据 |
| `OBS-20261012-TASKS-INITIAL-LOADING-FEEDBACK` / `OBS-20261012-DASHBOARD-APP-STATE-ERROR-SUPPRESSED` / `OBS-20261016-STATE-CARD-MISSING-FIELDS` | **修复中**（Web 批） | 对应 G03-10 / TABLE-TASKS-ERROR / P03-10 / P14P15-13 |
| `OBS-20261016-G5-STALE-PAYLOAD-FIELDS-CARRIED` | **修复**（同 P09P11-12） | 同一根因 |
| `OBS-20261012-EVIDENCE-FILE-POST-EDIT` | **记录为 QA 过程告警**（不改产品） | 已在证据目录标注；后续批次禁止手改 evidence |
| `OBS-20261014-BROWSER-SESSION-RESIDUE` | **文档化，不清理他人会话** | 环境教训：禁 `close --all`；仅自有 session 可关 |
| `OBS-20261014-SUBAGENT-DEPTH-LIMIT-1` | **记录为平台约束**（主 Agent 铺平批次） | 已在 run-state 与批次规范中体现 |
| `OBS-20261012-ARTCACHE-QA-PARTIAL-ROOT-CLEANUP` | **执行清理**（owned 目录，主机侧） | 并入最终清理批 |
| `OBS-20261016-G1-APP-DEPLOY-UNIT-SIDE-EFFECT` | **闭环**（已清理并复核 not-found） | 已记录 |
| `OBS-20261016-P17P18-04-SYSTEM-TRIGGER-NO-SOURCE` | **待验证后定**（先查 PRODUCT.md 是否一期范围） | 一期有「系统触发」→ 保留枚举并文档化；无 → 从筛选枚举移除 |

## B. 全部修复延伸（46 条未结观察 → 分桶）

1. **待运行验证的候选缺陷**（7 条）：APP-NODES-NULL-SHAPE、PAGESIZE-OUT-OF-RANGE-BLANK、AR26-ROLLBACK-UNCLOSED、DASHBOARD-FAULT-ORACLE、SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED、PAST-ONETIME-SAVE-ENABLED、ENROLL-INTERMITTENT-500（含 AGAIN 复现）→ 验证批 T1
2. **已确认轻微缺口（直接修）**：EXECUTION-SCRIPT-BACKLINK-GAP、TRANSFER-STATUS-BADGE-NOT-LOCALIZED、STATUSBADGE-MAINTENANCE-DISABLED-NO-ZH、INVALID-TASK-DEEPLINK-NO-ERROR、NAV-ROLE-UNFILTERED、CROSSPAGE-LINK-GAPS、G5-AND-CONDITION-NO-SUMMARY-NO-CLEAR、G1-USER-DUP-SQL-MESSAGE、JOURNAL-FROM-VERSION、API-CONTRACT-DEVIATIONS-LOW(8)、XFER-API-NO-SELF-TRANSFER-CHECK、EXEC-DISABLED-BRANCH-UNREACHABLE、ARTIFACT-DOWNLOAD-NO-READ-TIMEOUT、G23-RECONNECT-WINDOW
3. **代码级验证后定**（2 条）：REMOTE-TTL-PATH-UNREACHABLE（疑死代码→删除或文档化）、OFFLINE-NODE-MANUAL-ONLINE-NO-SWEEP（看 dispatch 影响：静默挂起→修；明确报错→文档化）
4. **环境不可行/卫生（文档化）**：ARM-RUNTIME、PHASE-STOPPED、HOST-PROC-ROOT-PERMISSION、OIDC-ENV-BLOCKED、QA-LOGIN-SESSION-UNREVOKED、SECRET-HISTORY、HARNESS-IDENTITY-POLLUTION、REGTOKEN、BATCH29-CAPABILITY-WINDOW、REPAIR-UNIT-REGISTRY-ROLLBACK

## C. 交付授权

- **Git 提交：已授权**（用户「git提交也归档」）→ 最终验证通过后执行 `git add`（含 25 个新增文件）+ 分层提交；**不 push**（未授权远程操作）
- **OpenSpec Archive：已授权** → tasks 勾满、artifact 与代码一致后执行
- `.e2e/**` 是否入库：**随交付一并提交**（测试记录，用户已授权提交；如不希望入库可在 commit 前剔除）
- `web/e2e/**` 14 个会话前既有改动：**与本次修复分开提交**（单独 commit，注明为会话前既有改动）
