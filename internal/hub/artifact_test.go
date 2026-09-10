package hub

import (
	"context"
	"testing"

	"github.com/cadentra/cadentra/internal/store"
)

func TestArtifactDeleteIsIdempotent(t *testing.T) {
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })

	manager, err := NewArtifactManager(st, t.TempDir(), nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.Delete(context.Background(), "missing-artifact"); err != nil {
		t.Fatalf("repeated delete should be harmless: %v", err)
	}
}
