package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	sqlite "modernc.org/sqlite"
	sqlite3 "modernc.org/sqlite/lib"
)

// SQLiteStore SQLite 实现
type SQLiteStore struct {
	db *sql.DB
}

type txContextKey struct{}

type txExecer interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

type txQueryer interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}

func (s *SQLiteStore) txFrom(ctx context.Context) *sql.Tx {
	tx, _ := ctx.Value(txContextKey{}).(*sql.Tx)
	return tx
}

func (s *SQLiteStore) execer(ctx context.Context) txExecer {
	if tx := s.txFrom(ctx); tx != nil {
		return tx
	}
	return s.db
}

func (s *SQLiteStore) queryer(ctx context.Context) txQueryer {
	if tx := s.txFrom(ctx); tx != nil {
		return tx
	}
	return s.db
}

// WithTx 在一个 SQLite 事务中执行 Desired State 变更及其 Revision/Audit。
func (s *SQLiteStore) WithTx(ctx context.Context, fn func(context.Context) error) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	txCtx := context.WithValue(ctx, txContextKey{}, tx)
	if err := fn(txCtx); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit()
}

// OpenSQLite 打开 SQLite 数据库并应用迁移。
//
// _txlock=immediate：写事务（WithTx）在 BEGIN 阶段即取写锁，而不是 WAL 下
// deferred 事务的「先读后写再升级」。升级路径一旦发现其它连接已推进快照，
// SQLite 会立刻返回 SQLITE_BUSY/BUSY_SNAPSHOT，且不受 busy_timeout 保护，
// 表现为并发纳管等写路径随机 500（缺陷 OBS-20261016-G1-ENROLL-TRANSIENT-500）。
// 改为 immediate 后锁等待统一走 busy_timeout（5s），并发写按序完成。
func OpenSQLite(path string) (*SQLiteStore, error) {
	dsn := path
	if path != ":memory:" {
		dsn = path + "?_txlock=immediate&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)"
	}
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	db.SetMaxOpenConns(8)
	db.SetMaxIdleConns(8)
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("ping sqlite: %w", err)
	}
	if err := Migrate(db); err != nil {
		db.Close()
		return nil, err
	}
	return &SQLiteStore{db: db}, nil
}

// OpenInMemory 测试用内存库
func OpenInMemory() (*SQLiteStore, error) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		return nil, err
	}
	if err := Migrate(db); err != nil {
		db.Close()
		return nil, err
	}
	return &SQLiteStore{db: db}, nil
}

// IsBusyError 判定 SQLite 忙锁类错误（SQLITE_BUSY / SQLITE_LOCKED 家族，含
// BUSY_SNAPSHOT）。这类错误是可重试语义：调用方应归一化为 503，而不是把驱动
// 原文（"database is locked (5) (SQLITE_BUSY)"）当 500 回给客户端。
func IsBusyError(err error) bool {
	if err == nil {
		return false
	}
	var se *sqlite.Error
	if errors.As(err, &se) {
		switch se.Code() & 0xff {
		case sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED:
			return true
		}
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "database is locked") ||
		strings.Contains(msg, "database table is locked") ||
		strings.Contains(msg, "sqlite_busy") ||
		strings.Contains(msg, "sqlite_locked")
}

// IsUniqueViolation 判定唯一约束冲突（重复用户名/重名等）。调用方据此归一化
// 业务文案（如 "username already exists"），不外泄 SQLite UNIQUE 原文。
func IsUniqueViolation(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "unique constraint failed") ||
		strings.Contains(msg, "constraint failed: unique")
}

// IsDriverError 判定错误来自数据库驱动层（sqlite / database/sql），而非上层业务校验。
// 用于 API 错误归一化：驱动原文（如 "sql: database is closed"、UNIQUE 约束原文）
// 不得回给客户端，只能进日志；业务校验错误仍保留既有 4xx 文案。
// 注意：调用方应先处理 sql.ErrNoRows（存在性语义），本函数对 ErrNoRows 返回 false。
func IsDriverError(err error) bool {
	if err == nil || errors.Is(err, sql.ErrNoRows) {
		return false
	}
	var se *sqlite.Error
	if errors.As(err, &se) {
		return true
	}
	msg := strings.ToLower(err.Error())
	if strings.Contains(msg, "sqlite") || strings.Contains(msg, "sql:") {
		return true
	}
	// 驱动错误文本可能不含 "sqlite"（如 UNIQUE/约束类），按已知 SQLite 文案兜底。
	for _, marker := range []string{
		"constraint failed", "no such table", "no such column", "datatype mismatch",
		"database disk image is malformed", "database is locked", "file is not a database",
	} {
		if strings.Contains(msg, marker) {
			return true
		}
	}
	return false
}

// DB 暴露底层连接（供测试与扩展使用）
func (s *SQLiteStore) DB() *sql.DB { return s.db }

func (s *SQLiteStore) Close() error { return s.db.Close() }

var _ Store = (*SQLiteStore)(nil)

// now 统一时间格式
func now() string { return time.Now().UTC().Format(time.RFC3339Nano) }

func parseTime(s string) time.Time {
	if s == "" {
		return time.Time{}
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Time{}
	}
	return t
}

func nullStr(v string) any {
	if v == "" {
		return nil
	}
	return v
}
