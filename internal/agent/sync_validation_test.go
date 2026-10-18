package agent

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func TestBadScriptSHARejectsBeforePrunedFiles(t *testing.T) {
	a := newTestAgent(t, Config{})
	h := &testHost{root: t.TempDir(), active: true}
	a.host = h
	a.appMgr.host = h
	app := models.Application{ID: "kept-app", BinaryPath: "/opt/kept", UnitName: "kept.service"}
	def, _ := json.Marshal(app)
	if err := a.store.BeginSync(a.ctx); err != nil {
		t.Fatal(err)
	}
	if err := a.store.UpsertApplication(a.ctx, app.ID, def, 1); err != nil {
		t.Fatal(err)
	}
	if err := a.store.SetGlobalRevision(7); err != nil {
		t.Fatal(err)
	}
	if err := a.store.CommitSync(); err != nil {
		t.Fatal(err)
	}
	if err := a.store.RegisterUnit(a.ctx, app.ID, app.UnitName); err != nil {
		t.Fatal(err)
	}
	if err := h.WriteFile(a.ctx, app.BinaryPath, []byte("old binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	script := models.Script{ID: "script", Content: "true", SHA256: "wrong", Revision: 1}
	scriptDef, _ := json.Marshal(script)
	p := protocol.SyncResponsePayload{GlobalRev: 8, Snapshot: true, Scripts: []protocol.ObjectEntry{{ID: script.ID, Data: scriptDef}}}
	a.OnSyncResponse(protocol.NewEnvelope(protocol.MsgSyncResponse, "sync", p))
	if data, err := h.ReadFile(a.ctx, app.BinaryPath); err != nil || string(data) != "old binary" || !h.active {
		t.Fatalf("invalid frame affected old files: %q %v", data, err)
	}
	if rev, _ := a.store.GetGlobalRevision(); rev != 7 {
		t.Fatalf("bad frame advanced revision: %d", rev)
	}
	if _, err := a.store.GetScript(a.ctx, script.ID); err == nil {
		t.Fatal("bad script persisted")
	}
	if appData := a.appMgr.loadApplication(app.ID); len(appData) == 0 {
		t.Fatal("old app pruned")
	}
	sum := sha256.Sum256([]byte(script.Content))
	script.SHA256 = hex.EncodeToString(sum[:])
	p.Scripts[0].Data, _ = json.Marshal(script)
	a.OnSyncResponse(protocol.NewEnvelope(protocol.MsgSyncResponse, "retry", p))
	if rev, _ := a.store.GetGlobalRevision(); rev != 8 {
		t.Fatalf("good frame not applied: %d", rev)
	}
	if _, err := h.Stat(a.ctx, app.BinaryPath); !os.IsNotExist(err) {
		t.Fatalf("good frame did not prune owned app: %v", err)
	}
}

func TestRemoteStateValidity(t *testing.T) {
	now := time.Now()
	remaining := func(v int64) *int64 { return &v }
	cases := []struct {
		name, observed, value string
		ttl                   int64
		remaining             *int64
		valid                 bool
	}{
		// 新鲜度只看 ttl_remaining_sec，不比较 ObservedAt/TTL。
		// Hub 时钟领先 Agent（observed_at 在未来）或观测时刻很旧，均不影响判定。
		{"remaining positive with future observed", now.Add(time.Hour).Format(time.RFC3339Nano), "online", 0, remaining(59), true},
		{"remaining positive with stale observed", now.Add(-time.Hour).Format(time.RFC3339Nano), "SUCCESS", 60, remaining(1), true},
		{"remaining zero", now.Format(time.RFC3339Nano), "SUCCESS", 60, remaining(0), false},
		{"remaining negative", now.Format(time.RFC3339Nano), "SUCCESS", 60, remaining(-1), false},
		// 字段缺失（旧 Hub 报文）→ 视为未知，Fail Closed，不再回退时钟比较。
		{"remaining missing fail closed", now.Format(time.RFC3339Nano), "SUCCESS", 60, nil, false},
		{"remaining missing with fresh observed", now.Add(-time.Second).Format(time.RFC3339Nano), "SUCCESS", 60, nil, false},
		{"remaining missing with zero ttl", now.Format(time.RFC3339Nano), "SUCCESS", 0, nil, false},
		{"remaining missing with future observed", now.Add(time.Second).Format(time.RFC3339Nano), "SUCCESS", 60, nil, false},
		{"remaining missing with invalid observed", "invalid", "SUCCESS", 60, nil, false},
		{"remaining with empty value", now.Format(time.RFC3339Nano), "", 60, remaining(10), false},
		{"remaining with unknown value", now.Format(time.RFC3339Nano), protocol.RemoteStateUnknown, 60, remaining(10), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			payload := protocol.RemoteStatePayload{
				Value: tc.value, ObservedAt: tc.observed, TTL: tc.ttl, TTLRemainingSec: tc.remaining,
			}
			if got := validRemoteState(payload); got != tc.valid {
				t.Fatalf("valid=%v want=%v", got, tc.valid)
			}
		})
	}
}

// TestRemoteStatePayloadJSON 锁定新语义下的 JSON 行为：
// 缺少 ttl_remaining_sec 的报文（旧 Hub）反序列化为 nil → Fail Closed，不再回退时钟比较；
// 携带 ttl_remaining_sec 的报文即使 observed_at 落在未来（时钟偏移）也判定有效。
func TestRemoteStatePayloadJSON(t *testing.T) {
	now := time.Now()
	var withoutRemaining protocol.RemoteStatePayload
	if err := json.Unmarshal([]byte(`{"value":"online","observed_at":"`+now.Add(-time.Second).Format(time.RFC3339Nano)+`","ttl_sec":60}`), &withoutRemaining); err != nil {
		t.Fatal(err)
	}
	if withoutRemaining.TTLRemainingSec != nil {
		t.Fatal("payload without ttl_remaining_sec must decode with nil")
	}
	if validRemoteState(withoutRemaining) {
		t.Fatal("missing ttl_remaining_sec must be rejected (fail-closed, no clock fallback)")
	}

	var withRemaining protocol.RemoteStatePayload
	if err := json.Unmarshal([]byte(`{"value":"online","observed_at":"`+now.Add(time.Second).Format(time.RFC3339Nano)+`","ttl_sec":60,"ttl_remaining_sec":42}`), &withRemaining); err != nil {
		t.Fatal(err)
	}
	if withRemaining.TTLRemainingSec == nil || *withRemaining.TTLRemainingSec != 42 {
		t.Fatalf("remaining TTL = %v, want 42", withRemaining.TTLRemainingSec)
	}
	// Hub 时钟领先 Agent：新字段存在时不受 observed_at 未来时间影响
	if !validRemoteState(withRemaining) {
		t.Fatal("skewed but fresh payload must be accepted when remaining TTL is present")
	}
}
