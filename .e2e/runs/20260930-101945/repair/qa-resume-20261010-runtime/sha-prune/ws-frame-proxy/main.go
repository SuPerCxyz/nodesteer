package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/coder/websocket"
)

const (
	upstreamURL = "ws://192.168.100.209:18080/"
	wantAgent   = "agent-426aa507-af4c-4005-858f-ce497d2fc67d"
	maxMessage  = 8 << 20
)

type envelope struct {
	Type    string          `json:"type"`
	ID      string          `json:"id"`
	Payload json.RawMessage `json:"payload"`
}
type syncPayload struct {
	Snapshot   bool          `json:"snapshot"`
	FullResync bool          `json:"full_resync"`
	GlobalRev  int64         `json:"global_rev"`
	Scripts    []objectEntry `json:"scripts"`
	Apps       []objectEntry `json:"apps"`
}
type objectEntry struct {
	ID       string          `json:"id"`
	Revision int64           `json:"revision"`
	Data     json.RawMessage `json:"data"`
}
type scriptData struct {
	Content string `json:"content"`
	SHA256  string `json:"sha256"`
}
type control struct {
	AppID               string `json:"app_id"`
	GoodScriptID        string `json:"good_script_id"`
	GoodRevision        int64  `json:"good_revision"`
	BadScriptID         string `json:"bad_script_id"`
	BadRevision         int64  `json:"bad_revision"`
	GoodSHA256          string `json:"good_sha256"`
	BadSHA256           string `json:"bad_sha256"`
	FaultArmed          bool   `json:"fault_armed"`
	ReleaseGoodSnapshot bool   `json:"release_good_snapshot"`
}
type proxyState struct {
	mu          sync.Mutex
	badSent     bool
	ackNegative bool
	held        bool
	released    bool
}

var root string
var state proxyState
var writeMu sync.Mutex

func sha(s string) string { v := sha256.Sum256([]byte(s)); return hex.EncodeToString(v[:]) }
func readControl() (control, error) {
	var c control
	b, err := os.ReadFile(filepath.Join(root, "controls.json"))
	if err != nil {
		return c, err
	}
	err = json.Unmarshal(b, &c)
	return c, err
}
func event(name string, fields map[string]any) {
	fields["event"] = name
	fields["at_utc"] = time.Now().UTC().Format(time.RFC3339Nano)
	b, _ := json.Marshal(fields)
	writeMu.Lock()
	defer writeMu.Unlock()
	f, err := os.OpenFile(filepath.Join(root, "events.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err == nil {
		_, _ = f.Write(append(b, '\n'))
		_ = f.Close()
	}
	log.Print(string(b))
}
func syncDetails(e envelope, p syncPayload, c control) map[string]any {
	selected := make([]map[string]any, 0, 2)
	for _, entry := range p.Scripts {
		if entry.ID != c.GoodScriptID && entry.ID != c.BadScriptID {
			continue
		}
		var data scriptData
		_ = json.Unmarshal(entry.Data, &data)
		selected = append(selected, map[string]any{"id": entry.ID, "revision": entry.Revision, "content_sha256": sha(data.Content), "declared_sha256": data.SHA256})
	}
	appPresent := false
	for _, app := range p.Apps {
		if app.ID == c.AppID {
			appPresent = true
		}
	}
	return map[string]any{"envelope_id": e.ID, "snapshot": p.Snapshot, "full_resync": p.FullResync, "global_rev": p.GlobalRev, "script_entry_count": len(p.Scripts), "target_scripts": selected, "target_app_present": appPresent}
}
func shouldHoldForArm(c control, badSent, released bool) bool {
	return !released && !c.FaultArmed && !badSent
}
func shouldHoldAfterFault(badSent, released bool) bool { return badSent && !released }

func targetAppOmitted(p syncPayload, c control) bool {
	if c.AppID == "" || (!p.Snapshot && !p.FullResync) {
		return false
	}
	for _, app := range p.Apps {
		if app.ID == c.AppID {
			return false
		}
	}
	return true
}
func shouldBlockAppPrune(p syncPayload, c control, released bool) bool {
	return !released && targetAppOmitted(p, c) && !matching(p, c)
}
func matching(p syncPayload, c control) bool {
	if !p.Snapshot || c.AppID == "" || c.GoodScriptID == "" || c.BadScriptID == "" {
		return false
	}
	for _, app := range p.Apps {
		if app.ID == c.AppID {
			return false
		}
	}
	good, bad := false, false
	for _, entry := range p.Scripts {
		if entry.ID == c.GoodScriptID && entry.Revision == c.GoodRevision {
			good = true
		}
		if entry.ID == c.BadScriptID && entry.Revision == c.BadRevision {
			bad = true
		}
	}
	return good && bad
}
func sameTargets(a, b control) bool {
	return a.AppID == b.AppID && a.GoodScriptID == b.GoodScriptID && a.GoodRevision == b.GoodRevision && a.GoodSHA256 == b.GoodSHA256 && a.BadScriptID == b.BadScriptID && a.BadRevision == b.BadRevision && a.BadSHA256 == b.BadSHA256
}
func validTargetScripts(p syncPayload, c control) bool {
	good, bad := false, false
	for _, entry := range p.Scripts {
		if entry.ID != c.GoodScriptID && entry.ID != c.BadScriptID {
			continue
		}
		var data scriptData
		if json.Unmarshal(entry.Data, &data) != nil {
			return false
		}
		if entry.ID == c.GoodScriptID && entry.Revision == c.GoodRevision && data.SHA256 == c.GoodSHA256 && sha(data.Content) == c.GoodSHA256 {
			good = true
		}
		if entry.ID == c.BadScriptID && entry.Revision == c.BadRevision && data.SHA256 == c.BadSHA256 && sha(data.Content) == c.BadSHA256 {
			bad = true
		}
	}
	return good && bad
}
func negativeAckMatches(e envelope, c control) bool {
	if e.Type != "SYNC_ACK" {
		return false
	}
	var p struct {
		OK         bool   `json:"ok"`
		AppliedRev int64  `json:"applied_rev"`
		Error      string `json:"error"`
	}
	if json.Unmarshal(e.Payload, &p) != nil {
		return false
	}
	return !p.OK && p.AppliedRev == 0 && p.Error == fmt.Sprintf("script %s sha256 mismatch", c.BadScriptID)
}
func waitForArmed(ctx context.Context, expected control) (control, bool) {
	event("matching_snapshot_held_until_fault_armed", map[string]any{"app_id": expected.AppID, "good_script_id": expected.GoodScriptID, "bad_script_id": expected.BadScriptID})
	for {
		c, err := readControl()
		if err == nil {
			if !sameTargets(expected, c) {
				event("control_target_changed_while_unarmed", map[string]any{"app_id": c.AppID, "good_script_id": c.GoodScriptID, "bad_script_id": c.BadScriptID})
				return control{}, false
			}
			if c.FaultArmed {
				return c, true
			}
		}
		select {
		case <-ctx.Done():
			return control{}, false
		case <-time.After(200 * time.Millisecond):
		}
	}
}
func waitForNegativeRelease(ctx context.Context, expected control) bool {
	for {
		c, err := readControl()
		if err == nil {
			if !sameTargets(expected, c) || !c.FaultArmed {
				event("control_target_changed_while_held", map[string]any{"app_id": c.AppID, "good_script_id": c.GoodScriptID, "bad_script_id": c.BadScriptID})
				return false
			}
			state.mu.Lock()
			ack := state.ackNegative && state.badSent
			state.mu.Unlock()
			if ack && c.ReleaseGoodSnapshot {
				return true
			}
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(200 * time.Millisecond):
		}
	}
}
func corruptBad(raw []byte, c control) ([]byte, bool, string, error) {
	var e map[string]json.RawMessage
	if err := json.Unmarshal(raw, &e); err != nil {
		return nil, false, "", err
	}
	var p map[string]json.RawMessage
	if err := json.Unmarshal(e["payload"], &p); err != nil {
		return nil, false, "", err
	}
	var scripts []json.RawMessage
	if err := json.Unmarshal(p["scripts"], &scripts); err != nil {
		return nil, false, "", err
	}
	changed, mutatedSHA := false, ""
	for i, rawEntry := range scripts {
		var entry map[string]json.RawMessage
		if json.Unmarshal(rawEntry, &entry) != nil {
			continue
		}
		var id string
		var revision int64
		_ = json.Unmarshal(entry["id"], &id)
		_ = json.Unmarshal(entry["revision"], &revision)
		if id != c.BadScriptID || revision != c.BadRevision {
			continue
		}
		var data map[string]json.RawMessage
		if json.Unmarshal(entry["data"], &data) != nil {
			return nil, false, "", errors.New("bad Script data JSON")
		}
		var content, declared string
		if json.Unmarshal(data["content"], &content) != nil || json.Unmarshal(data["sha256"], &declared) != nil {
			return nil, false, "", errors.New("bad Script content/SHA JSON")
		}
		if sha(content) != c.BadSHA256 || declared != c.BadSHA256 {
			return nil, false, "", errors.New("bad Script original content/SHA did not match control")
		}
		content += "\n# qa-invalid-sha-870358cd92ee\n"
		mutatedSHA = sha(content)
		data["content"], _ = json.Marshal(content)
		entry["data"], _ = json.Marshal(data)
		scripts[i], _ = json.Marshal(entry)
		changed = true
		break
	}
	if !changed {
		return nil, false, "", errors.New("exact bad Script entry not found")
	}
	p["scripts"], _ = json.Marshal(scripts)
	e["payload"], _ = json.Marshal(p)
	out, err := json.Marshal(e)
	return out, true, mutatedSHA, err
}
func recordNegativeAck(b []byte) {
	var e envelope
	if json.Unmarshal(b, &e) != nil || e.Type != "SYNC_ACK" {
		return
	}
	c, err := readControl()
	if err != nil {
		return
	}
	var ack struct {
		OK         bool   `json:"ok"`
		AppliedRev int64  `json:"applied_rev"`
		Error      string `json:"error"`
	}
	if json.Unmarshal(e.Payload, &ack) != nil {
		return
	}
	state.mu.Lock()
	accepted := state.badSent && c.FaultArmed && negativeAckMatches(e, c)
	if accepted {
		state.ackNegative = true
	}
	state.mu.Unlock()
	event("sync_ack", map[string]any{"envelope_id": e.ID, "ok": ack.OK, "applied_rev": ack.AppliedRev, "error_bytes": len(ack.Error), "error_sha256": sha(ack.Error), "accepted_as_bad_sha_rejection": accepted})
}
func relayClient(ctx context.Context, client, hub *websocket.Conn) {
	for {
		typ, b, err := client.Read(ctx)
		if err != nil {
			return
		}
		recordNegativeAck(b)
		if err = hub.Write(ctx, typ, b); err != nil {
			return
		}
	}
}
func relayHub(ctx context.Context, hub, client *websocket.Conn) {
	for {
		typ, b, err := hub.Read(ctx)
		if err != nil {
			return
		}
		var env envelope
		if json.Unmarshal(b, &env) == nil && env.Type == "SYNC_RESPONSE" {
			c, controlErr := readControl()
			if controlErr != nil {
				event("sync_response_dropped_control_error", map[string]any{"error_bytes": len(controlErr.Error()), "error_sha256": sha(controlErr.Error())})
				continue
			}
			var p syncPayload
			if json.Unmarshal(env.Payload, &p) == nil {
				if controlErr == nil {
					state.mu.Lock()
					released := state.released
					state.mu.Unlock()
					if shouldBlockAppPrune(p, c, released) {
						event("target_snapshot_input_mismatch_blocked", map[string]any{"frame": syncDetails(env, p, c), "reason": "target App omitted but exact Script IDs/revisions are incomplete"})
						continue
					}
				}
				if controlErr == nil && matching(p, c) {
					state.mu.Lock()
					badSent, ackSeen, released := state.badSent, state.ackNegative, state.released
					state.mu.Unlock()
					if shouldHoldForArm(c, badSent, released) {
						var ready bool
						c, ready = waitForArmed(ctx, c)
						if !ready {
							return
						}
					}
					details := syncDetails(env, p, c)
					state.mu.Lock()
					badSent, ackSeen, released = state.badSent, state.ackNegative, state.released
					state.mu.Unlock()
					if c.FaultArmed && !badSent && !released {
						out, ok, mutated, err := corruptBad(b, c)
						if err != nil || !ok {
							reason := "invalid target frame"
							if err != nil {
								reason = err.Error()
							}
							event("fault_guard_rejected", map[string]any{"reason": reason, "frame": details})
							return
						}
						state.mu.Lock()
						sendBad := !state.badSent
						if sendBad {
							state.badSent = true
						}
						state.mu.Unlock()
						if sendBad {
							if err = client.Write(ctx, typ, out); err != nil {
								event("bad_sha_frame_write_failed", map[string]any{"frame": details})
								return
							}
							event("bad_sha_frame_forwarded", map[string]any{"frame": details, "bad_script_id": c.BadScriptID, "original_content_sha256": c.BadSHA256, "mutated_content_sha256": mutated, "only_content_field_changed": true})
							continue
						}
					}
					state.mu.Lock()
					badSent, ackSeen, released = state.badSent, state.ackNegative, state.released
					if shouldHoldAfterFault(badSent, released) {
						state.held = true
					}
					state.mu.Unlock()
					if shouldHoldAfterFault(badSent, released) {
						valid := validTargetScripts(p, c)
						event("valid_snapshot_held_for_negative_oracle", map[string]any{"frame": details, "negative_ack_seen": ackSeen, "valid_target_script_shas": valid})
						if !valid || !waitForNegativeRelease(ctx, c) {
							continue
						}
						if err = client.Write(ctx, typ, b); err != nil {
							return
						}
						state.mu.Lock()
						state.released = true
						state.mu.Unlock()
						event("valid_snapshot_released_after_negative_oracle", map[string]any{"frame": details, "negative_ack_seen": true, "applied_rev_zero": true, "release_flag_seen": true})
						continue
					}
				}
			}
		}
		if err = client.Write(ctx, typ, b); err != nil {
			return
		}
	}
}
func handle(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	client, err := websocket.Accept(w, r, &websocket.AcceptOptions{OriginPatterns: []string{"*"}, Subprotocols: []string{"nodesteer"}})
	if err != nil {
		return
	}
	defer client.Close(websocket.StatusNormalClosure, "proxy done")
	client.SetReadLimit(maxMessage)
	typ, hello, err := client.Read(ctx)
	if err != nil {
		return
	}
	var he envelope
	var hp struct {
		AgentID string `json:"agent_id"`
	}
	if json.Unmarshal(hello, &he) != nil || he.Type != "HELLO" || json.Unmarshal(he.Payload, &hp) != nil || hp.AgentID != wantAgent {
		event("hello_rejected", map[string]any{"type": he.Type, "agent_id_matches": hp.AgentID == wantAgent, "message_type": int(typ)})
		client.Close(websocket.StatusPolicyViolation, "unexpected AgentID")
		return
	}
	event("hello_verified", map[string]any{"agent_id": hp.AgentID, "message_type": int(typ)})
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	hub, _, err := websocket.Dial(dialCtx, upstreamURL, &websocket.DialOptions{Subprotocols: []string{"nodesteer"}})
	if err != nil {
		event("upstream_dial_failed", map[string]any{"error": err.Error()})
		return
	}
	defer hub.Close(websocket.StatusNormalClosure, "proxy done")
	hub.SetReadLimit(maxMessage)
	if err = hub.Write(ctx, typ, hello); err != nil {
		return
	}
	go relayClient(ctx, client, hub)
	relayHub(ctx, hub, client)
}
func main() {
	if len(os.Args) != 2 {
		log.Fatal("usage: ws-frame-proxy <owned-root>")
	}
	root = os.Args[1]
	if fi, err := os.Stat(root); err != nil || !fi.IsDir() {
		log.Fatal("proxy root unavailable")
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatal(err)
	}
	defer ln.Close()
	addr := ln.Addr().(*net.TCPAddr)
	meta := map[string]any{"listen": "127.0.0.1", "port": addr.Port, "upstream_host": "192.168.100.209", "upstream_port": 18080, "agent_id": wantAgent, "unit_pid": os.Getpid()}
	b, _ := json.Marshal(meta)
	tmp := filepath.Join(root, "ready.json.tmp")
	if err = os.WriteFile(tmp, b, 0644); err != nil {
		log.Fatal(err)
	}
	if err = os.Rename(tmp, filepath.Join(root, "ready.json")); err != nil {
		log.Fatal(err)
	}
	fmt.Printf("READY %s\n", b)
	server := &http.Server{Handler: http.HandlerFunc(handle), ReadHeaderTimeout: 5 * time.Second}
	if err = server.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) && !errors.Is(err, io.EOF) {
		log.Fatal(err)
	}
}
