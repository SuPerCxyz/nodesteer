# CR-1 实施报告：收窄 superseded 谓词（方案 A）

- 实施人：资深 Go 工程师（子 Agent）
- 实施日期：2026-10-16
- 依据：`code-review-20261016.md` CR-1（major）；用户确认方案 A（记录终态成因 + 收窄谓词 + 边界测试）
- 仓库状态：HEAD `dddee4f` + 工作树未提交改动（本报告仅描述本次新增改动）
- 改动文件：`internal/agent/store.go`、`internal/agent/application.go`、`internal/agent/application_test.go`、`internal/agent/store_test.go`、`docs/ARCHITECTURE.md` §52、`docs/PRODUCT.md` §42
- 结论：CR-1 已按方案 A 实施并验证；`go build` / `go vet` / `go test ./internal/...` / `gofmt -l` 全绿；1 条向后兼容残余风险已如实记录（见第 7 节末）。

---

## 一、问题回顾（原缺陷）

`supersededByLaterDoneDeployment` 原先只要求「同 app 存在 `started_at` 更晚且 `phase='DONE'` 的行」。但本仓库 `DONE` 有三种来源：

| 写点 | 语义 | 是否代表“装了更新版本” |
|---|---|---|
| `application.go` `prepareFailed`（原 `:295`） | 准备阶段失败，**未做任何宿主替换** | 否 |
| `application.go` `failed()` 回滚成功分支（原 `:419`） | 该次尝试已回到起点 | 否 |
| `application.go` 部署成功（原 `:485`） | 真实部署成功 | 是 |
| `application.go` `RecoverDeployments` 恢复重放成功（原 `:778`） | 恢复收尾（回滚成功语义） | 否 |

因此更晚的「非成功 DONE」会把真正半完成、需要恢复的行误判为“已被取代”，**永久跳过重放且不落任何终态**（服务可能停在停止状态、二进制半替换）。

---

## 二、改动清单

### 2.1 `internal/agent/store.go`（journal 新增终态成因列）

| 位置 | 改动 |
|---|---|
| `:163` | `deployments` 建表新增 `completed_kind TEXT NOT NULL DEFAULT ''` |
| `:245` | `migrate()` 新增幂等加列（`duplicate column` 忽略，与 `app_revision`/三路径列同法） |
| `:695-717` | `DeploymentRow` 新增 `CompletedKind string` 字段（含语义注释） |
| `:719` | `deploymentColumns` 增列，所有读写路径（`GetActiveDeployments` / `ListDeploymentsByApplication` / `ListAllDeployments`）同时生效 |
| `:723-726` | `scanDeploymentRow` 增扫描位 |
| `:732-740` | `CreateDeployment` 写入该列（供恢复侧/测试直接落终态行） |
| `:763-770` | **新增 `CompleteDeployment`**：一条 UPDATE 同时写 `phase='DONE'` + `completed_kind` |

### 2.2 `internal/agent/application.go`（终态成因 + 谓词收窄）

- `:36-42` 新增常量：

```go
// Deployment 终态成因（journal completed_kind），仅 DONE 行有值。
// 只有“成功部署”产生的 DONE 才构成已被更晚部署取代；准备失败未做任何宿主替换，
// 回滚成功表示该次尝试已回到起点，两者都不代表装了更新版本。
const (
	CompletedKindDeployed      = "deployed"
	CompletedKindRolledBack    = "rolled_back"
	CompletedKindPrepareFailed = "prepare_failed"
)
```

- 四处 `DONE` 写入全部改为 `CompleteDeployment`，成因按下表落库：

| 调用点（改后行号） | 写入 |
|---|---|
| `:304` `prepareFailed` | `completed_kind = prepare_failed`，`backup_path = ""` |
| `:430` `failed()` 回滚成功 | `completed_kind = rolled_back` |
| `:496` 部署成功 | `completed_kind = deployed` |
| `:790` `RecoverDeployments` 重放成功收尾 | `completed_kind = rolled_back`（恢复=回滚语义） |

- `:813-838` 谓词收窄（核心 diff）：

```go
-	for _, other := range history {
-		if other.ID == d.ID || other.Phase != PhaseDone {
+	for _, other := range history {
+		if other.ID == d.ID || other.Phase != PhaseDone || other.CompletedKind != CompletedKindDeployed {
 			continue
 		}
 		if deploymentStartedAfter(other.StartedAt, d.StartedAt) {
 			return true, nil
 		}
 	}
```

- `RecoverDeployments` 顶部注释同步为新语义（“更晚的成功部署 = `phase=DONE` 且 `completed_kind=deployed`”）。

### 2.3 文档同步（行为变化必须同步）

`doc-sync-report-20261016.md:96` 明确要求：若 CR-1 决议收窄谓词，需同步 `ARCHITECTURE.md` §52 与 `PRODUCT.md` §42 的括号说明。已按新语义更新（只改这几处，未动其它内容）：

- `docs/ARCHITECTURE.md:1223,1234`：journal 字段表新增 `completed_kind`，并新增一句成因枚举与“旧记录缺失视为未知”。
- `docs/ARCHITECTURE.md:1252`：Superseded 条目改为“`started_at` 更晚、`phase=DONE` 且 `completed_kind=deployed`；准备失败/回滚成功不构成取代；旧记录缺该字段视为未知”。
- `docs/PRODUCT.md:1186,1209`：字段示例与 Superseded 条目同步。

---

## 三、关于“版本更新”维度（评审提到的）——本次不采用，理由如下

任务允许我判断是否叠加 `app_revision` 比较。**决定不叠加**，只保留「更晚 + DONE + deployed」三个条件：

1. **该维度已被现有防护蕴含**：要进入重放路径，陈旧行必须先通过 binding 校验（`d.AppRevision == app.Revision`）；而 Hub 侧 application revision 单调递增（`internal/hub/application.go:54` 创建置 1、`:82` 更新 `+1`），因此“更晚”的部署行其 revision 必然 ≥ 陈旧行的 revision。追加比较不改变任何真实可达场景的判定结果。
2. **叠加会引入新的反向风险**：若要求“两边都有 revision 才比较、缺值不放行取代”，那么一条 `completed_kind=deployed` 但 `app_revision=0` 的更晚成功行将不再构成取代（可达性：应用定义未同步时 payload 与定义 revision 均为 0，`deploy` 仍可凭 payload 路径完成部署），此时陈旧 backup 会被重放并覆盖一次真实的成功部署 —— 正是 P0-2 要防的误覆盖。
3. **与架构不变量冲突**：`ARCHITECTURE.md` §62 规定“陈旧 Backup 不得覆盖更晚的 DONE 部署”。在“更晚的 DONE+deployed 但 revision 较旧”的构造场景（需 Hub revision 回退，正常不可达）里，加 revision 条件会放行重放、直接违反该不变量。
4. 结论：`completed_kind` 维度精确命中 CR-1 的缺陷本体；revision 维度收益为零、风险为负。若后续确需版本维度，建议改为“更晚行 revision 较旧时记日志告警”，而不是阻断取代。

---

## 四、新增列与迁移方式（向后兼容硬要求）

- **DDL**：`ALTER TABLE deployments ADD COLUMN completed_kind TEXT NOT NULL DEFAULT ''`，与今日已实施的 `app_revision`/`binary_path`/`config_path`/`unit_name` 三路径绑定列完全相同的方式（`migrate()` 内逐条执行，`duplicate column` 忽略）。
- **幂等**：重复打开同一数据库不会失败（`TestDeploymentCompletedKindMigration` 二次 `OpenLocalStore`）。
- **非破坏性**：只加列、不改写既有行、无索引/约束变更、`NOT NULL DEFAULT ''` 使旧行自动取空串；新库在建表语句中直接带该列。
- **写入原子性**：`CompleteDeployment` 单条 UPDATE 同时写 `phase` 与 `completed_kind`，避免“DONE 已落盘但成因未知”的崩溃窗口（若分两步写，崩溃后该行会退化为“未知成因”，从而不再构成取代）。
- **旧行取值**：`completed_kind = ''` → `!= CompletedKindDeployed` → **不构成取代**（符合任务要求）。

---

## 五、测试清单（含“测试确实能抓住缺陷”的变异验证）

### 5.1 恢复语义边界（`internal/agent/application_test.go`）

| 测试 | 覆盖 |
|---|---|
| `TestRecoverySupersedeRequiresSuccessfulDeploy`（新增，`:407`） | 表驱动 4 例：更晚 DONE 成因 = `deployed` → **跳过重放**（文件保持新版本、不启服务、陈旧行原样保留 VERIFYING）；= `rolled_back` / `prepare_failed` / 缺列（`""`）→ **仍重放**（文件回滚到旧版本、启动旧服务、陈旧行收敛 DONE；且恢复收尾成因断言为 `rolled_back`，不会冒充成功部署） |
| `TestRecoverySkipsSupersededStaleDeployment`（更新，`:361`） | AR-32 主链防护：更晚成功行补 `CompletedKind: CompletedKindDeployed`，断言不变（不动文件、不启服务、陈旧行保留） |
| `TestDeploymentStartedAfterBoundaries`（新增，`:508`） | 时间戳边界固定既有行为：同秒 → 不构成更晚；`.5Z` vs `Z` → 更晚；`.500Z` vs `.5Z` → 等价不更晚；更早 → false；任一侧解析失败 → 字典序回退（左右两例方向都固定，避免 CR-9 误读） |
| `TestDeployJournalRecordsCompletedKind`（新增，`:533`） | 三个写点端到端落库正确：成功部署 → `deployed`；STARTED 相位 DB 触发器注入失败 → 回滚成功 → `rolled_back`（并断言二进制已还原）；非法 unit 名 → 准备失败 → `prepare_failed`（并断言未触碰宿主文件） |

### 5.2 存储层（`internal/agent/store_test.go`）

| 测试 | 覆盖 |
|---|---|
| `TestDeploymentCompletedKindRoundTrip`（新增，`:418`） | 成因列持久化/读回；未写成因的旧行读为空；`CompleteDeployment` 同时落 DONE + 成因 + backup_path；DONE 行不再出现在 active 集合；`ListAllDeployments` 正常 |
| `TestDeploymentCompletedKindMigration`（新增，`:460`） | 旧库（`deployments` 无 `completed_kind` 列、含一行 `DONE`+`app_revision=7`）打开后：加列成功、旧行原样保留且成因读为空；关闭再打开（迁移二次执行）不报错 |

### 5.3 变异验证（证明新测试确实针对 CR-1，而非同义反复）

临时把谓词改回 `other.Phase != PhaseDone`（去掉成因条件）后运行：

```text
--- FAIL: TestRecoverySupersedeRequiresSuccessfulDeploy (0.01s)
    --- FAIL: TestRecoverySupersedeRequiresSuccessfulDeploy/rolled_back (0.00s)
        application_test.go:469: non-successful later DONE must not supersede: /opt/owned/config = "new config" <nil>
    --- FAIL: TestRecoverySupersedeRequiresSuccessfulDeploy/prepare_failed (0.00s)
        application_test.go:469: non-successful later DONE must not supersede: /opt/owned/config = "new config" <nil>
    --- FAIL: TestRecoverySupersedeRequiresSuccessfulDeploy/legacy_missing_kind (0.00s)
        application_test.go:469: non-successful later DONE must not supersede: /opt/owned/config = "new config" <nil>
FAIL
```

三个非成功成因子例在旧谓词下全部失败（`deployed` 子例仍通过），说明测试对 CR-1 缺陷敏感。变异代码已用备份文件还原并校验哈希一致（`sha256 eb3e2d06…acde1a`），当前工作树为修复版。

---

## 六、验证结果（真实执行）

| 命令 | 结果 |
|---|---|
| `go build ./...` | **通过**（exit 0，无输出） |
| `go vet ./...` | **通过**（exit 0，无输出） |
| `go test ./internal/... -count=1` | **全部 ok**（exit 0）：`agent 34.151s`、`agent/condition`、`agent/connection`、`agent/execution 5.027s`、`agent/host`、`agent/scheduler`、`hub 0.473s`、`hub/agentbundle`、`hub/api 1.489s`、`hub/auth 1.028s`、`hubserver 3.494s`、`store 0.078s`；FAIL 行数 0 |
| `gofmt -l internal/` | **无输出**（0 个文件需格式化） |

未执行项：真实 Hub-Agent 重启/环境级 AR-32 复跑（不在本任务边界，仍按计划在回归批执行）。本报告只声明单测层结论。

---

## 七、向后兼容推理与确认（“旧行是否仍不会误覆盖”）

### 7.1 结论：**成立**（对环境中现存的历史行）

历史行（本批次绑定列上线之前写入）的特征是 `app_revision = 0` 且 `binary_path / config_path / unit_name` 全为空。`deploymentBindingMatches`（`application.go:798`）对 `AppRevision <= 0` 或任一路径为空直接返回 false，因此这些行：

1. 无论 superseded 判定结果如何，**都不可能进入重放路径**（`RecoverDeployments` 在 supersede 分支之后紧跟 binding 校验，两条分支都不触碰任何文件）；
2. 因此“`completed_kind` 为空 → 不构成取代”不会让它们重放、也就不会用陈旧 backup 覆盖更晚的成功部署；
3. 行为差异仅为日志文案：改前记 “superseded by a later successful deployment”，改后记 “binding mismatch; skip replay, requires inspection”（两者都不动文件、不改相位）。

即：**“未知不取代”不会重新引入原缺陷（误覆盖）**，这是 binding 防护提供的独立兜底，与 `completed_kind` 判定解耦。

### 7.2 残余风险（如实记录，需上级裁决是否处理）

上述推理依赖“缺 `completed_kind` ⇒ 缺绑定列”。存在一个**中间态行集合**不满足该前提：由本批次中间版本（已含绑定列、尚无 `completed_kind`）写入的行 —— 其 binding 完整且可能匹配当前定义，但成因列为空。若这种行是真实的成功部署，则它在修复版重启后**不再构成取代**，同 app 的陈半完成行会被重放，可能覆盖该成功部署：

- 触发条件：中间版本构建曾在真实环境跑过一次成功部署，且其后仍存在一条 binding 匹配的陈旧半完成行，随后升级到本次修复版并重启。
- 可达性评估：本批次按“一次构建、一次部署”交付（`build-deploy-regression-review-20261016.json`），中间版本未单独下发；环境现存 journal 行早于绑定列（无 binding）→ 当前不可达。
- 若上游判断中间版本曾在某环境运行过：建议对这批复旧行做一次人工核对（或按“需要人工检查”处理），不要直接依赖本次谓词；进一步可在下一次迭代按“成因缺失但 binding 完整”做显式标记。

---

## 八、未触碰范围（合规说明）

- 未修改 `.e2e/**` 既有状态与证据文件（仅新增本报告）。
- 未做任何 Git 操作；未装依赖；未访问远程主机。
- 未顺手改动无关代码；`deploymentStartedAfter` 保持原实现（CR-9 属独立条目，本次仅以测试固定其行为）。
- 未新增 `SUPERSEDED` 相位（沿用实施报告的既有取舍：被取代行保留原 phase，仅日志标记）。
