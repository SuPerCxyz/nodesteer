package auth

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
)

func newAuthTestManager(t *testing.T) (*Manager, *store.SQLiteStore) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	// 内存库按连接隔离，限制为单连接以支持并发用例。
	st.DB().SetMaxOpenConns(1)
	t.Cleanup(func() { st.Close() })
	return New(st, time.Hour), st
}

func createTestUser(t *testing.T, st *store.SQLiteStore, username, password, role string) *models.User {
	t.Helper()
	hash, err := HashPassword(password)
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	u := &models.User{Username: username, PasswordHash: hash, Role: role}
	if err := st.CreateUser(context.Background(), u); err != nil {
		t.Fatalf("create user: %v", err)
	}
	return u
}

// 角色变更（含降权）必须对已登录会话立即生效，且不改写共享 Session。
func TestAuthenticateUsesCurrentRoleFromStore(t *testing.T) {
	m, st := newAuthTestManager(t)
	ctx := context.Background()
	u := createTestUser(t, st, "ops", "secret", models.RoleOperator)

	sess, err := m.Login(ctx, "ops", "secret")
	if err != nil {
		t.Fatalf("login: %v", err)
	}
	if !HasPermission(sess.Role, "run") {
		t.Fatalf("operator should be allowed to run, got role %q", sess.Role)
	}

	// 管理员降权
	if err := st.UpdateUserRole(ctx, u.ID, models.RoleViewer); err != nil {
		t.Fatalf("update role: %v", err)
	}

	got, ok := m.Authenticate(ctx, sess.Token)
	if !ok {
		t.Fatal("authenticate should succeed for existing user")
	}
	if got.Role != models.RoleViewer {
		t.Fatalf("authenticate role = %q, want %q (current DB role)", got.Role, models.RoleViewer)
	}
	if HasPermission(got.Role, "run") {
		t.Fatal("viewer must not keep operator permission after demotion")
	}

	// 返回副本：篡改副本不影响后续鉴权
	got.Role = models.RoleAdministrator
	again, ok := m.Authenticate(ctx, sess.Token)
	if !ok {
		t.Fatal("second authenticate should succeed")
	}
	if again.Role != models.RoleViewer {
		t.Fatalf("second authenticate role = %q, want %q", again.Role, models.RoleViewer)
	}

	// 共享 Session 未被原地修改（仍保留登录时快照）
	m.mu.RLock()
	stored := m.sessions[sess.Token]
	m.mu.RUnlock()
	if stored == nil {
		t.Fatal("session should still be registered")
	}
	if stored.Role != models.RoleOperator {
		t.Fatalf("shared session role mutated in place: got %q, want %q", stored.Role, models.RoleOperator)
	}

	// 升权同样立即生效
	if err := st.UpdateUserRole(ctx, u.ID, models.RoleAdministrator); err != nil {
		t.Fatalf("promote role: %v", err)
	}
	got, ok = m.Authenticate(ctx, sess.Token)
	if !ok || got.Role != models.RoleAdministrator {
		t.Fatalf("promote not reflected: ok=%v role=%q", ok, got.Role)
	}
}

// OIDC 会话同样以数据库当前角色为准。
func TestAuthenticateSSOSessionUsesCurrentRole(t *testing.T) {
	m, st := newAuthTestManager(t)
	ctx := context.Background()

	sess, err := m.SSOLogin(ctx, "sso-user", models.RoleOperator)
	if err != nil {
		t.Fatalf("sso login: %v", err)
	}
	u, err := st.GetUserByUsername(ctx, "sso-user")
	if err != nil {
		t.Fatalf("get user: %v", err)
	}
	if err := st.UpdateUserRole(ctx, u.ID, models.RoleViewer); err != nil {
		t.Fatalf("update role: %v", err)
	}
	got, ok := m.Authenticate(ctx, sess.Token)
	if !ok {
		t.Fatal("authenticate should succeed")
	}
	if got.Role != models.RoleViewer {
		t.Fatalf("sso session role = %q, want %q", got.Role, models.RoleViewer)
	}
}

// 用户已不存在时会话失效并被清理。
func TestAuthenticateInvalidatesDeletedUser(t *testing.T) {
	m, st := newAuthTestManager(t)
	ctx := context.Background()
	u := createTestUser(t, st, "gone", "secret", models.RoleAdministrator)

	sess, err := m.Login(ctx, "gone", "secret")
	if err != nil {
		t.Fatalf("login: %v", err)
	}
	if _, err := st.DB().ExecContext(ctx, `DELETE FROM users WHERE id = ?`, u.ID); err != nil {
		t.Fatalf("delete user: %v", err)
	}
	if _, ok := m.Authenticate(ctx, sess.Token); ok {
		t.Fatal("authenticate should fail for deleted user")
	}
	m.mu.RLock()
	_, exists := m.sessions[sess.Token]
	m.mu.RUnlock()
	if exists {
		t.Fatal("session of deleted user should be removed")
	}
}

// 并发 Authenticate 与角色变更/注销不应触发数据竞争（配合 -race 验证）。
func TestAuthenticateConcurrentRoleChange(t *testing.T) {
	m, st := newAuthTestManager(t)
	ctx := context.Background()
	u := createTestUser(t, st, "racer", "secret", models.RoleOperator)

	sess, err := m.Login(ctx, "racer", "secret")
	if err != nil {
		t.Fatalf("login: %v", err)
	}

	var wg sync.WaitGroup
	stop := make(chan struct{})
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
					m.Authenticate(ctx, sess.Token)
				}
			}
		}()
	}
	for i := 0; i < 50; i++ {
		role := models.RoleViewer
		if i%2 == 0 {
			role = models.RoleOperator
		}
		if err := st.UpdateUserRole(ctx, u.ID, role); err != nil {
			t.Errorf("update role: %v", err)
			break
		}
	}
	close(stop)
	wg.Wait()
}
