package hub

import (
	"context"
	"errors"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type revokedDeleteFailureStore struct{ *store.SQLiteStore }

func (s revokedDeleteFailureStore) DeleteSetting(context.Context, string) error {
	return errors.New("injected revoked flag deletion failure")
}

func TestReenrollmentFailurePreservesIdentityStatusAndCredentials(t *testing.T) {
	ctx := t.Context()
	st := newTestStore(t)
	nm := NewNodeManager(st, NewRevisionManager(st))
	n, oldCredential, err := nm.RegisterAgent(ctx, "original-host", "192.0.2.90", "linux", "amd64", "v1", "native", false, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.UpdateNodeStatus(ctx, n.ID, models.NodeStatusDisabled); err != nil {
		t.Fatal(err)
	}
	if err := st.SetSetting(ctx, "revoked:"+n.ID, "true"); err != nil {
		t.Fatal(err)
	}
	failing := revokedDeleteFailureStore{st.(*store.SQLiteStore)}
	nm = NewNodeManager(failing, NewRevisionManager(failing))
	if _, err := nm.PrepareEnrollment(ctx, "changed-host", n.IP, "native"); err == nil {
		t.Fatal("failed revoked flag deletion must reject re-enrollment")
	}
	persisted, err := st.GetNode(ctx, n.ID)
	if err != nil || persisted.Status != models.NodeStatusDisabled || persisted.Hostname != n.Hostname || persisted.AgentVersion != "v1" {
		t.Fatalf("failed re-enrollment partially changed node: node=%+v err=%v", persisted, err)
	}
	credential, err := st.GetSetting(ctx, "cred:"+n.ID)
	if err != nil || credential != oldCredential {
		t.Fatalf("failed re-enrollment changed credential: err=%v", err)
	}
	revoked, err := st.GetSetting(ctx, "revoked:"+n.ID)
	if err != nil || revoked != "true" {
		t.Fatalf("failed re-enrollment cleared revoked flag: value=%q err=%v", revoked, err)
	}
}
