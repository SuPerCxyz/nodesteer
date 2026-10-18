package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"testing"
)

func TestSnapshotFilterRequiresExactSameFrame(t *testing.T) {
	c := control{AppID: "app", GoodScriptID: "good", GoodRevision: 3, BadScriptID: "bad", BadRevision: 2}
	p := syncPayload{Snapshot: true, Scripts: []objectEntry{{ID: "good", Revision: 3}, {ID: "bad", Revision: 2}}}
	if !matching(p, c) {
		t.Fatal("exact Snapshot with both Script revisions should match")
	}
	p.Snapshot = false
	if matching(p, c) {
		t.Fatal("non-Snapshot frame must not match")
	}
	p.Snapshot = true
	p.Apps = []objectEntry{{ID: "app"}}
	if matching(p, c) {
		t.Fatal("frame containing target App must not match")
	}
	p.Apps = nil
	p.Scripts[1].Revision++
	if matching(p, c) {
		t.Fatal("wrong Script revision must not match")
	}
}

func TestTargetScriptSHAValidation(t *testing.T) {
	goodContent, badContent := "good-v2", "bad-v2"
	h := func(s string) string { x := sha256.Sum256([]byte(s)); return hex.EncodeToString(x[:]) }
	c := control{GoodScriptID: "good", GoodRevision: 3, GoodSHA256: h(goodContent), BadScriptID: "bad", BadRevision: 2, BadSHA256: h(badContent)}
	mk := func(id string, rev int64, content, declared string) objectEntry {
		b, _ := json.Marshal(scriptData{Content: content, SHA256: declared})
		return objectEntry{ID: id, Revision: rev, Data: b}
	}
	p := syncPayload{Scripts: []objectEntry{mk("good", 3, goodContent, c.GoodSHA256), mk("bad", 2, badContent, c.BadSHA256)}}
	if !validTargetScripts(p, c) {
		t.Fatal("valid target Script SHAs should pass")
	}
	p.Scripts[1] = mk("bad", 2, "corrupt", c.BadSHA256)
	if validTargetScripts(p, c) {
		t.Fatal("content mismatch must not qualify as a releasable frame")
	}
	p.Scripts[1] = mk("bad", 2, badContent, h("wrong declared SHA"))
	if validTargetScripts(p, c) {
		t.Fatal("declared SHA mismatch must not qualify as a releasable frame")
	}
}

func TestNegativeAckMustMatchExactBadScriptAndZeroRevision(t *testing.T) {
	c := control{BadScriptID: "bad-id"}
	payload := func(ok bool, rev int64, err string) json.RawMessage {
		b, _ := json.Marshal(map[string]any{"ok": ok, "applied_rev": rev, "error": err})
		return b
	}
	good := envelope{Type: "SYNC_ACK", Payload: payload(false, 0, fmt.Sprintf("script %s sha256 mismatch", c.BadScriptID))}
	if !negativeAckMatches(good, c) {
		t.Fatal("exact bad Script SHA rejection should match")
	}
	cases := []envelope{{Type: "ERROR", Payload: good.Payload}, {Type: "SYNC_ACK", Payload: payload(false, 1, "script bad-id sha256 mismatch")}, {Type: "SYNC_ACK", Payload: payload(true, 0, "script bad-id sha256 mismatch")}, {Type: "SYNC_ACK", Payload: payload(false, 0, "script other-id sha256 mismatch")}}
	for i, e := range cases {
		if negativeAckMatches(e, c) {
			t.Fatalf("nonmatching ack case %d accepted", i)
		}
	}
}

func TestFaultLatchesFailClosedAcrossReconnectAndBeforeArm(t *testing.T) {
	c := control{FaultArmed: false}
	if !shouldHoldForArm(c, false, false) {
		t.Fatal("matching frame before arm must be held")
	}
	c.FaultArmed = true
	if shouldHoldForArm(c, false, false) {
		t.Fatal("armed first fault frame should proceed to corruption")
	}
	if !shouldHoldAfterFault(true, false) {
		t.Fatal("valid frame after bad send must be held")
	}
	// `held` is intentionally not an input: a new connection must still hold it.
	if !shouldHoldAfterFault(true, false) {
		t.Fatal("reconnected stream must not bypass the outstanding latch")
	}
	if shouldHoldAfterFault(true, true) {
		t.Fatal("after explicit release, valid frames may pass")
	}
}

func TestAppPruneBlockedWhenTargetScriptsMissing(t *testing.T) {
	c := control{AppID: "app", GoodScriptID: "good", GoodRevision: 3, BadScriptID: "bad", BadRevision: 2}
	p := syncPayload{Snapshot: true, Scripts: []objectEntry{{ID: "good", Revision: 3}}}
	if !shouldBlockAppPrune(p, c, false) {
		t.Fatal("App omission with incomplete Script entries must fail closed")
	}
	p.Scripts = append(p.Scripts, objectEntry{ID: "bad", Revision: 2})
	if shouldBlockAppPrune(p, c, false) {
		t.Fatal("complete exact target Snapshot may proceed to arm/rejection checks")
	}
	p.FullResync = true
	p.Snapshot = false
	p.Scripts = p.Scripts[:1]
	if !shouldBlockAppPrune(p, c, false) {
		t.Fatal("FullResync App prune must fail closed on incomplete Script entries")
	}
	if shouldBlockAppPrune(p, c, true) {
		t.Fatal("after explicit release, no further App prune latch is needed")
	}
}
