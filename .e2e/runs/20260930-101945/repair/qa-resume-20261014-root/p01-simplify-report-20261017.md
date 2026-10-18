# P0-1 时钟偏移修复简化报告（移除混版本回退路径）

- 日期：2026-10-17
- 仓库：/home/superc/code/nodesteer
- 依据：用户新指示「不需要历史版本兼容」，彻底移除跨时钟比较回退路径
- 范围：P0-1 Remote State 新鲜度判定及其文档；未触碰 P0-2/CR-1 逻辑，无 Git 操作，未改 .e2e/** 状态文件

## 一、结论

`validRemoteState` 现在只依据 Hub 下发的 `ttl_remaining_sec` 判定新鲜度：

- Value 为空或 `UNKNOWN` → false（Fail Closed）
- `ttl_remaining_sec` 缺失（nil）或 <= 0 → false（视为未知，Fail Closed）
- 其余 → true

代码中已不存在任何 `time.Now()` 与 `ObservedAt` 的比较；旧 Hub（不下发 `ttl_remaining_sec`）的报文一律被拒绝，不再有回退路径。

## 二、改动文件与函数

| 文件 | 改动 |
|---|---|
| `internal/agent/sync_validation.go` | `validRemoteState`：删除 ObservedAt+TTL 绝对时间比较回退分支；签名去掉不再需要的 `now time.Time` 参数；删除 `time` 导入 |
| `internal/agent/agent.go` | `queryRemoteCondition` 调用点：`validRemoteState(p, time.Now())` → `validRemoteState(p)`（agent.go:1715） |
| `internal/protocol/protocol.go` | `RemoteStatePayload` 注释重写；`ObservedAt`/`TTL` 行内标注「仅诊断」，`TTLRemainingSec` 标注为新鲜度判定唯一依据（字段保留，不删除） |
| `internal/agent/sync_validation_test.go` | 删除「旧 Hub 回退」用例与 `TestRemoteStatePayloadMixedVersionJSON`；新增缺失 `ttl_remaining_sec` 的 fail-closed 用例；重写为 `TestRemoteStatePayloadJSON` |
| `docs/ARCHITECTURE.md` §40 | 删除「旧 Hub 回退 observed_at + ttl_sec（混版本行为与修复前一致）」措辞，改为新语义 |
| `docs/PRODUCT.md` §28 | 同上；「必须包含」列表补充 TTL Remaining Sec；Observed At/TTL 注明仅诊断 |

## 三、关键 diff

### 1. `internal/agent/sync_validation.go`（核心）

改动前：

```go
func validRemoteState(p protocol.RemoteStatePayload, now time.Time) bool {
	if p.Value == "" || p.Value == protocol.RemoteStateUnknown {
		return false
	}
	if p.TTLRemainingSec != nil {
		// 剩余 TTL <= 0 → 已过期，Fail Closed。
		return *p.TTLRemainingSec > 0
	}
	if p.TTL <= 0 {
		return false
	}
	observed, err := time.Parse(time.RFC3339Nano, p.ObservedAt)
	if err != nil || observed.After(now) {
		return false
	}
	// 直接比较秒数，避免 TTL 的 Duration 溢出及极旧时间的 Sub 饱和。
	age := float64(now.Unix()-observed.Unix()) + float64(now.Nanosecond()-observed.Nanosecond())/1e9
	return age < float64(p.TTL)
}
```

改动后：

```go
// validRemoteState 远程状态新鲜度判定。
// 只依据 Hub 计算下发的 TTLRemainingSec（自 Hub 判定时刻起的剩余秒数，向上取整），
// 不做任何跨主机时钟比较，从根本上消除 Hub/Agent 时钟偏移误判。
// Value 为空/UNKNOWN，或 TTLRemainingSec 缺失/<= 0（视为未知）一律 Fail Closed。
func validRemoteState(p protocol.RemoteStatePayload) bool {
	if p.Value == "" || p.Value == protocol.RemoteStateUnknown {
		return false
	}
	if p.TTLRemainingSec == nil || *p.TTLRemainingSec <= 0 {
		return false
	}
	return true
}
```

同时移除 `time` 导入（该文件其余逻辑不需要）。

### 2. `internal/agent/agent.go` 调用点

```diff
 	case p := <-respCh:
-		if !validRemoteState(p, time.Now()) {
-			// 空值或 UNKNOWN → Fail Closed（视为不可评估）
+		if !validRemoteState(p) {
+			// 空值/UNKNOWN 或缺少剩余 TTL → Fail Closed（视为不可评估）
 			return "", false, nil
 		}
```

### 3. `internal/protocol/protocol.go`

```diff
-// RemoteStatePayload 远程状态。
-// TTL 为观测窗口长度（秒），由旧版语义保留；TTLRemainingSec 为自 Hub 判定时刻起的
-// 剩余有效秒数（向上取整），新增字段。旧 Hub 不下发该字段（nil），Agent 回退到
-// ObservedAt + TTL 判定，保证双向混版本兼容。
+// RemoteStatePayload 远程状态。
+// TTLRemainingSec 为自 Hub 判定时刻起的剩余有效秒数（向上取整），是 Agent 新鲜度
+// 判定的唯一依据：字段缺失或 <= 0 一律视为未知并 Fail Closed；判定不做跨主机时钟
+// 比较，不承诺与不下发该字段的旧 Hub 兼容。
+// ObservedAt 与 TTL 保留但仅作诊断/展示用途（TTL 在 Agent 上报缓存时仍由 Hub 用于
+// 有效期计算），不参与 Agent 新鲜度判定。
 type RemoteStatePayload struct {
 	NodeID          string `json:"node_id"`
 	Property        string `json:"property"`
 	Value           string `json:"value"`
-	ObservedAt      string `json:"observed_at"`
-	TTL             int64  `json:"ttl_sec"`
-	TTLRemainingSec *int64 `json:"ttl_remaining_sec,omitempty"`
+	ObservedAt      string `json:"observed_at"`                 // 仅诊断：观测时刻
+	TTL             int64  `json:"ttl_sec"`                     // 观测窗口长度（秒）：上报用于 Hub 缓存有效期，下发仅诊断
+	TTLRemainingSec *int64 `json:"ttl_remaining_sec,omitempty"` // 新鲜度判定唯一依据，缺失/<=0 视为未知
 }
```

### 4. 测试 `internal/agent/sync_validation_test.go`

- 删除：`legacy fresh / legacy expired / legacy zero ttl / legacy negative ttl / legacy missing observed / legacy invalid observed / legacy future observed / legacy unknown` 共 8 个回退用例。
- 删除：`TestRemoteStatePayloadMixedVersionJSON`（其断言「旧报文应经回退保持有效」与新语义矛盾）。
- 新增/改写用例（`TestRemoteStateValidity`，11 个子用例）：

```go
// 新鲜度只看 ttl_remaining_sec，不比较 ObservedAt/TTL。
{"remaining positive with future observed", ...future observed, ttl=0,  remaining(59), true},
{"remaining positive with stale observed",  ...stale observed,  ttl=60, remaining(1),  true},
{"remaining zero",                           ..., remaining(0),  false},
{"remaining negative",                       ..., remaining(-1), false},
// 字段缺失（旧 Hub 报文）→ 视为未知，Fail Closed，不再回退时钟比较。
{"remaining missing fail closed",            ..., nil, false},
{"remaining missing with fresh observed",    ...1s ago, ttl=60, nil, false},
{"remaining missing with zero ttl",          ..., ttl=0,  nil, false},
{"remaining missing with future observed",   ...future, ttl=60, nil, false},
{"remaining missing with invalid observed",  "invalid", ttl=60, nil, false},
{"remaining with empty value",               "", remaining(10), false},
{"remaining with unknown value",             UNKNOWN, remaining(10), false},
```

- 新 `TestRemoteStatePayloadJSON`：无 `ttl_remaining_sec` 的 JSON 解码为 nil 且判定 false；带 `ttl_remaining_sec=42` 且 `observed_at` 在未来的报文判定 true（锁定时钟偏移无关性）。

### 5. 文档

`docs/ARCHITECTURE.md` §40：

```diff
 Remote State 下发字段：
 - value
-- observed_at（诊断字段）
-- ttl_sec（观测窗口长度）
+- observed_at（仅诊断，不参与新鲜度判定）
+- ttl_sec（观测窗口长度，仅诊断）
 - ttl_remaining_sec（自 Hub 判定时刻起的剩余有效秒数，向上取整）
 
-Agent 优先用 Hub 计算的 `ttl_remaining_sec` 判定新鲜度，不做跨主机时钟比较；
-旧 Hub 未下发该字段时回退 `observed_at + ttl_sec` 判定（混版本行为与修复前一致）；
-仅 Hub 与 Agent 均升级后消除跨主机时钟偏移误判。
+Agent 仅以 Hub 计算的 `ttl_remaining_sec` 判定新鲜度，不做跨主机时钟比较，
+彻底消除 Hub/Agent 时钟偏移误判；该字段缺失或 <= 0 一律视为未知并 Fail Closed，
+不承诺与不下发该字段的旧 Hub 兼容。
```

`docs/PRODUCT.md` §28：

```diff
 Remote State 必须包含：
 - Value
 - Observed At
 - TTL
+- TTL Remaining Sec
 
-新 Hub 随 Remote State 下发自判定时刻起的剩余有效秒数（`ttl_remaining_sec`）；
-Agent 优先据此判定新鲜度，不做跨主机时钟比较；旧 Hub 未下发该字段时回退 Observed At + TTL。
-仅 Hub 与 Agent 均升级后消除跨主机时钟偏移误判。Observed At 保留为诊断字段。
+Hub 随 Remote State 下发自判定时刻起的剩余有效秒数（`ttl_remaining_sec`）；
+Agent 仅据此判定新鲜度，不做跨主机时钟比较，消除 Hub/Agent 时钟偏移误判；
+该字段缺失或 <= 0 视为未知并 Fail Closed，不承诺与不下发该字段的旧 Hub 兼容。
+Observed At 与 TTL 保留为诊断字段，不参与新鲜度判定。
```

## 四、删除的代码路径

1. `validRemoteState` 中 `p.TTL <= 0` 分支、`time.Parse(time.RFC3339Nano, p.ObservedAt)` 解析、`observed.After(now)` 未来时间拒绝、`age < TTL` 秒数比较——全部删除。
2. 函数参数 `now time.Time` 及 `internal/agent/sync_validation.go` 的 `time` 导入删除。
3. 调用点 `agent.go` 中的 `time.Now()` 实参删除（该文件其余位置的 `time.Now()` 与新鲜度判定无关，未动）。
4. 测试中 8 个回退用例与 `TestRemoteStatePayloadMixedVersionJSON` 删除。

验证：`grep ObservedAt internal/agent/` 仅剩 `sync_validation_test.go` 中构造测试载荷的引用；`sync_validation.go` 内不再出现 `ObservedAt`、`TTL` 或 `time`。

## 五、字段保留/删除理由（以代码实际使用为准）

- `TTLRemainingSec`：保留，新鲜度判定唯一依据。
- `TTL`（`ttl_sec`）：**保留**。仍被 Hub 侧使用：`internal/hub/gateway.go:571` 的 `handleRemoteState`（Agent 上报缓存路径）将 `p.TTL` 传给 `store.SetRemoteState` 作为缓存有效期；`internal/hub/remote_state_test.go` 亦依赖该字段。下发方向已不再被 Agent 使用，注释标注「仅诊断」。
- `ObservedAt`（`observed_at`）：**保留**。Hub 下发时仍填充（`gateway.go:585`），`internal/hub/remote_state_test.go:31` 断言其非零；删除会改动公共协议字段并影响 Hub 测试。注释标注「仅诊断：观测时刻」，不参与 Agent 新鲜度判定。
- 未删除任何公共协议字段，未改动 Hub 下发逻辑（仍同时带 ObservedAt/TTL/TTLRemainingSec，前者仅诊断）。

## 六、四条命令真实结果

| 命令 | 结果 |
|---|---|
| `go build ./...` | ✅ 通过（exit 0，无输出，打印 BUILD_OK） |
| `go vet ./...` | ✅ 通过（exit 0，无输出，打印 VET_OK） |
| `gofmt -l internal/` | ⚠️→✅ 首次列出 `internal/protocol/protocol.go`（我新增的行内注释对齐未符合 gofmt）；执行 `gofmt -w internal/protocol/protocol.go` 后复查，输出为空（clean） |
| `go test ./internal/... -count=1` | ✅ 全部通过（exit 0）：agent 34.114s / agent/condition / agent/connection / agent/execution / agent/host / agent/scheduler / hub / hub/agentbundle / hub/api / hub/auth / hubserver / store 均 ok；metrics、models、protocol 无测试文件 |

定点复测：

```text
go test ./internal/agent/ -run 'TestRemoteState' -v -count=1
--- PASS: TestRemoteStateValidity (11/11 子用例)
--- PASS: TestRemoteStatePayloadJSON
ok  github.com/SuPerCxyz/nodesteer/internal/agent  0.003s
```

## 七、未验证项与备注

1. 未做真实 Hub↔Agent 进程级联调（本次为纯判定逻辑简化，且边界禁止访问远程主机）；行为由单元测试与全量 internal 测试覆盖。
2. 观察到 `openspec/changes/repair-e2e-confirmed-defects/` 中仍有旧语义措辞：`specs/agent/executions/spec.md` 第 11/14 行「有效观察时间和正 TTL」、`design.md` Decision 3「检查有效时间与正 TTL」。按本次边界（仅 ARCHITECTURE.md §40 与 PRODUCT.md §28）未改动，建议由 Root 在 OpenSpec artifact 收口时同步为「以 Hub 下发剩余 TTL 为唯一依据，缺失/非正 Fail Closed」。
3. 工作区存在大量其他会话的在途修改，本报告仅涉及上述 6 个文件中的 P0-1 相关改动；未执行任何 Git 操作。
