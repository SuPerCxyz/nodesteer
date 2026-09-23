package auth

import (
	"context"
	"database/sql"
	"errors"
	"sync"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"
)

// ErrInvalidCredentials 凭证无效
var ErrInvalidCredentials = errors.New("invalid credentials")

// Session 用户会话
type Session struct {
	Token    string
	UserID   string
	Username string
	Role     string
	Expires  time.Time
}

// Manager 认证管理器
type Manager struct {
	store      store.Store
	sessions   map[string]*Session
	mu         sync.RWMutex
	sessionTTL time.Duration
	adminToken string
	oidc       *OIDC
}

// New 创建认证管理器
func New(st store.Store, sessionTTL time.Duration) *Manager {
	return &Manager{
		store:      st,
		sessions:   map[string]*Session{},
		sessionTTL: sessionTTL,
	}
}

// SetOIDC 设置 OIDC 客户端（设置后本地登录被禁用）
func (m *Manager) SetOIDC(o *OIDC) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.oidc = o
}

// OIDC 返回 OIDC 客户端
func (m *Manager) OIDC() *OIDC {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.oidc
}

// LocalLoginDisabled OIDC 启用后本地账号密码登录是否禁用
func (m *Manager) LocalLoginDisabled() bool {
	return m.OIDC().Enabled()
}

// HashPassword 密码哈希
func HashPassword(password string) (string, error) {
	b, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	return string(b), err
}

// EnsureDefaultAdmin 若无用户则创建默认管理员
func (m *Manager) EnsureDefaultAdmin(ctx context.Context, username, password string) error {
	if _, err := m.store.GetUserByUsername(ctx, username); err == nil {
		return nil
	}
	hash, err := HashPassword(password)
	if err != nil {
		return err
	}
	return m.store.CreateUser(ctx, &models.User{
		Username:     username,
		PasswordHash: hash,
		Role:         models.RoleAdministrator,
	})
}

// Login 用户登录
func (m *Manager) Login(ctx context.Context, username, password string) (*Session, error) {
	u, err := m.store.GetUserByUsername(ctx, username)
	if err != nil {
		return nil, ErrInvalidCredentials
	}
	if bcrypt.CompareHashAndPassword([]byte(u.PasswordHash), []byte(password)) != nil {
		return nil, ErrInvalidCredentials
	}
	s := &Session{
		Token:    uuid.NewString(),
		UserID:   u.ID,
		Username: u.Username,
		Role:     u.Role,
		Expires:  time.Now().Add(m.sessionTTL),
	}
	m.mu.Lock()
	m.sessions[s.Token] = s
	m.mu.Unlock()
	return s, nil
}

// Authenticate 校验 Token。
// 返回的会话副本以数据库当前角色为准，使角色变更（含降权）对已登录会话立即生效；
// 不改写共享 Session，避免并发数据竞争。用户已不存在时使会话失效并清理。
func (m *Manager) Authenticate(ctx context.Context, token string) (*Session, bool) {
	m.mu.RLock()
	s, ok := m.sessions[token]
	var snapshot Session
	if ok {
		snapshot = *s
	}
	m.mu.RUnlock()
	if !ok {
		return nil, false
	}
	if time.Now().After(snapshot.Expires) {
		m.Logout(token)
		return nil, false
	}
	u, err := m.store.GetUserByID(ctx, snapshot.UserID)
	if err != nil {
		// 用户已删除：会话失效并清理；读取失败同样 Fail Closed。
		if errors.Is(err, sql.ErrNoRows) {
			m.Logout(token)
		}
		return nil, false
	}
	// 副本携带数据库当前角色/用户名，不污染共享会话
	snapshot.Role = u.Role
	snapshot.Username = u.Username
	return &snapshot, true
}

// Logout 注销
func (m *Manager) Logout(token string) {
	m.mu.Lock()
	delete(m.sessions, token)
	m.mu.Unlock()
}

// SSOLogin 通过 OIDC 建立会话：不存在则自动创建本地用户
func (m *Manager) SSOLogin(ctx context.Context, username, role string) (*Session, error) {
	if username == "" || !ValidRole(role) {
		return nil, ErrInvalidCredentials
	}
	u, err := m.store.GetUserByUsername(ctx, username)
	if err != nil {
		// 自动创建
		u = &models.User{
			Username: username,
			Role:     role,
		}
		if err := m.store.CreateUser(ctx, u); err != nil {
			return nil, err
		}
	} else if u.Role != role {
		// 按 IdP 最新 claim 同步角色
		if err := m.store.UpdateUserRole(ctx, u.ID, role); err != nil {
			return nil, err
		}
		u.Role = role
	}
	s := &Session{
		Token:    uuid.NewString(),
		UserID:   u.ID,
		Username: u.Username,
		Role:     u.Role,
		Expires:  time.Now().Add(m.sessionTTL),
	}
	m.mu.Lock()
	m.sessions[s.Token] = s
	m.mu.Unlock()
	return s, nil
}

// ValidRole 仅允许产品定义的三种角色，SSO 映射异常时 Fail Closed。
func ValidRole(role string) bool {
	switch role {
	case models.RoleAdministrator, models.RoleOperator, models.RoleViewer:
		return true
	default:
		return false
	}
}

// HasPermission 判断角色权限
func HasPermission(role, action string) bool {
	switch role {
	case models.RoleAdministrator:
		return true
	case models.RoleOperator:
		switch action {
		case "read", "run", "deploy", "app_operate", "view_logs":
			return true
		}
		return false
	case models.RoleViewer:
		return action == "read" || action == "view_logs"
	}
	return false
}
