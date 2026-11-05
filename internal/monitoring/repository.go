package monitoring

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/google/uuid"
)

// Target stores configuration only. Credentials are read from the process
// environment at observation time, never stored with a target.
type Target struct {
	ID              string       `json:"id"`
	Name            string       `json:"name"`
	Kind            string       `json:"kind,omitempty"`
	SourceNodeID    string       `json:"source_node_id,omitempty"`
	MonitoredNodeID string       `json:"monitored_node_id,omitempty"`
	Metric          string       `json:"metric,omitempty"`
	Series          string       `json:"series,omitempty"`
	MaxAgeSec       int          `json:"max_age_sec,omitempty"`
	URL             string       `json:"url"`
	JSONPath        []string     `json:"json_path"`
	QueryParams     []QueryParam `json:"query_params,omitempty"`
	CredentialEnv   string       `json:"credential_env,omitempty"`
	IntervalSec     int          `json:"interval_sec"`
	TimeoutSec      int          `json:"timeout_sec"`
	Enabled         bool         `json:"enabled"`
}

type QueryParam struct {
	Name      string `json:"name"`
	Value     string `json:"value,omitempty"`
	SecretEnv string `json:"secret_env,omitempty"`
}

var credentialEnvPattern = regexp.MustCompile(`^NODESTEER_MONITOR_SECRET_[A-Z0-9_]+$`)
var ErrTargetHasRules = errors.New("monitoring target is referenced by rules")

func (t Target) Validate() error {
	if strings.TrimSpace(t.Name) == "" || len(t.Name) > 128 || t.IntervalSec < 5 || t.IntervalSec > 86400 || t.TimeoutSec < 1 || t.TimeoutSec > 60 || t.TimeoutSec >= t.IntervalSec {
		return errors.New("invalid monitoring target")
	}
	if t.Kind == "node_metric" {
		if t.MonitoredNodeID == "" || len(t.MonitoredNodeID) > 128 ||
			!store.ValidNativeMetric(t.Metric) || t.Series == "" || len(t.Series) > 1024 ||
			t.MaxAgeSec < 0 || t.MaxAgeSec > 300 ||
			t.URL != "" || t.SourceNodeID != "" || t.CredentialEnv != "" || len(t.JSONPath) != 0 || len(t.QueryParams) != 0 {
			return errors.New("invalid node metric target")
		}
		metric, _, ok := store.ParseSeriesKey(t.Series)
		if !ok || metric != t.Metric {
			return errors.New("invalid node metric series")
		}
		return nil
	}
	u, err := url.Parse(t.URL)
	if err != nil || u.Host == "" || u.User != nil || u.Fragment != "" || u.RawQuery != "" {
		return errors.New("invalid monitoring target name or URL")
	}
	switch t.Kind {
	case "", "http":
		if u.Scheme != "http" && u.Scheme != "https" {
			return errors.New("HTTP target requires HTTP or HTTPS URL")
		}
	case "tcp":
		if u.Scheme != "tcp" || u.Hostname() == "" || u.Port() == "" || u.Path != "" || t.CredentialEnv != "" || len(t.QueryParams) != 0 || len(t.JSONPath) != 0 {
			return errors.New("invalid TCP target")
		}
		port, err := strconv.Atoi(u.Port())
		if err != nil || port < 1 || port > 65535 {
			return errors.New("invalid TCP port")
		}
	case "icmp":
		if u.Scheme != "icmp" || u.Hostname() == "" || u.Port() != "" || u.Path != "" || t.CredentialEnv != "" || len(t.QueryParams) != 0 || len(t.JSONPath) != 0 {
			return errors.New("invalid ICMP target")
		}
	default:
		return errors.New("unsupported monitoring target kind")
	}
	if t.CredentialEnv != "" && (!credentialEnvPattern.MatchString(t.CredentialEnv) || u.Scheme != "https") {
		return errors.New("credential reference requires HTTPS and NODESTEER_MONITOR_SECRET_ prefix")
	}
	if t.IntervalSec < 5 || t.IntervalSec > 86400 || t.TimeoutSec < 1 || t.TimeoutSec > 60 || t.TimeoutSec >= t.IntervalSec {
		return errors.New("invalid interval or timeout")
	}
	if len(t.JSONPath) > 16 {
		return errors.New("JSON path is too deep")
	}
	if len(t.QueryParams) > 32 {
		return errors.New("too many query parameters")
	}
	for _, param := range t.QueryParams {
		if param.Name == "" || len(param.Name) > 128 || len(param.Value) > 1024 || (param.SecretEnv != "" && (param.Value != "" || !credentialEnvPattern.MatchString(param.SecretEnv) || u.Scheme != "https")) {
			return errors.New("invalid query parameter")
		}
		normalized := strings.ReplaceAll(strings.ToLower(param.Name), "-", "_")
		for _, sensitive := range []string{"token", "secret", "password", "api_key", "apikey", "authorization", "auth"} {
			if strings.Contains(normalized, sensitive) && param.SecretEnv == "" {
				return errors.New("sensitive query parameter requires credential reference")
			}
		}
	}
	for _, part := range t.JSONPath {
		if part == "" || len(part) > 128 {
			return errors.New("invalid JSON path")
		}
	}
	return nil
}

func (t Target) MetricMaxAge() time.Duration {
	if t.MaxAgeSec > 0 {
		return time.Duration(t.MaxAgeSec) * time.Second
	}
	age := time.Duration(t.IntervalSec*2) * time.Second
	if age > 5*time.Minute {
		age = 5 * time.Minute
	}
	return age
}

type Repository struct{ db *sql.DB }

type auditActor struct{ userID, username string }
type auditKey struct{}

func WithAuditActor(ctx context.Context, userID, username string) context.Context {
	return context.WithValue(ctx, auditKey{}, auditActor{userID, username})
}

func writeAudit(ctx context.Context, tx *sql.Tx, resource, action, id string) error {
	return writeAuditDetail(ctx, tx, resource, action, id, "")
}

func writeAuditDetail(ctx context.Context, tx *sql.Tx, resource, action, id, detail string) error {
	actor, ok := ctx.Value(auditKey{}).(auditActor)
	if !ok {
		return nil
	}
	_, err := tx.ExecContext(ctx, `INSERT INTO audit_logs (id, user_id, username, action, resource, resource_id, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`, uuid.NewString(), actor.userID, actor.username, action, resource, id, detail, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}

func NewRepository(db *sql.DB) *Repository { return &Repository{db: db} }

func (r *Repository) Create(ctx context.Context, target *Target) error {
	if target.ID != "" {
		return errors.New("target id must be generated by the server")
	}
	if err := target.Validate(); err != nil {
		return err
	}
	target.ID = uuid.NewString()
	data, err := json.Marshal(target)
	if err != nil {
		return err
	}
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `INSERT INTO monitoring_http_targets (id, config_json, updated_at) VALUES (?, ?, ?)`, target.ID, string(data), time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		return err
	}
	if err := writeAudit(ctx, tx, "monitoring_http_target", "create", target.ID); err != nil {
		return err
	}
	return tx.Commit()
}

func (r *Repository) List(ctx context.Context) ([]Target, error) {
	rows, err := r.db.QueryContext(ctx, `SELECT config_json FROM monitoring_http_targets ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Target{}
	for rows.Next() {
		var raw string
		var target Target
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(raw), &target); err != nil {
			return nil, fmt.Errorf("decode monitoring target: %w", err)
		}
		out = append(out, target)
	}
	return out, rows.Err()
}

func (r *Repository) Get(ctx context.Context, id string) (Target, error) {
	var raw string
	var target Target
	if err := r.db.QueryRowContext(ctx, `SELECT config_json FROM monitoring_http_targets WHERE id = ?`, id).Scan(&raw); err != nil {
		return target, err
	}
	if err := json.Unmarshal([]byte(raw), &target); err != nil {
		return target, err
	}
	return target, nil
}

func (r *Repository) Update(ctx context.Context, target Target) error {
	if target.ID == "" {
		return errors.New("target id required")
	}
	if err := target.Validate(); err != nil {
		return err
	}
	data, err := json.Marshal(target)
	if err != nil {
		return err
	}
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var pending int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM monitoring_rules WHERE target_id = ? AND pending_id <> ''`, target.ID).Scan(&pending); err != nil {
		return err
	}
	if pending > 0 {
		return errors.New("monitoring target has an unresolved rule execution")
	}
	result, err := tx.ExecContext(ctx, `UPDATE monitoring_http_targets SET config_json = ?, updated_at = ? WHERE id = ?`, string(data), time.Now().UTC().Format(time.RFC3339Nano), target.ID)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count == 0 {
		return sql.ErrNoRows
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM monitoring_rule_samples WHERE rule_id IN (SELECT id FROM monitoring_rules WHERE target_id=?)`, target.ID); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE monitoring_rules SET active_since='', last_observed='', last_sample='', last_confirmed_at='', not_before=? WHERE target_id=?`, time.Now().UTC().Format(time.RFC3339Nano), target.ID); err != nil {
		return err
	}
	if err := writeAudit(ctx, tx, "monitoring_http_target", "update", target.ID); err != nil {
		return err
	}
	return tx.Commit()
}

func (r *Repository) Delete(ctx context.Context, id string) error {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var references int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM monitoring_rules WHERE target_id = ?`, id).Scan(&references); err != nil {
		return err
	}
	if references > 0 {
		return ErrTargetHasRules
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM monitoring_http_observations WHERE target_id = ?`, id); err != nil {
		return err
	}
	result, err := tx.ExecContext(ctx, `DELETE FROM monitoring_http_targets WHERE id = ?`, id)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count == 0 {
		return sql.ErrNoRows
	}
	if err := writeAudit(ctx, tx, "monitoring_http_target", "delete", id); err != nil {
		return err
	}
	return tx.Commit()
}

type StoredObservation struct {
	ID         int64           `json:"id"`
	TargetID   string          `json:"target_id"`
	ObservedAt string          `json:"observed_at"`
	StatusCode int             `json:"status_code"`
	DurationNS int64           `json:"duration_ns"`
	Value      json.RawMessage `json:"value"`
	Error      string          `json:"error,omitempty"`
}

func (r *Repository) AddObservation(ctx context.Context, targetID string, obs HTTPObservation, observationErr error) error {
	return r.addObservation(ctx, targetID, "", obs, observationErr)
}

func (r *Repository) addObservation(ctx context.Context, targetID, expectedConfig string, obs HTTPObservation, observationErr error) error {
	value := "null"
	if obs.HasValue {
		data, err := json.Marshal(obs.Value)
		if err != nil {
			return err
		}
		value = string(data)
	}
	message := ""
	if observationErr != nil {
		message = observationErr.Error()
	}
	if obs.ObservedAt.IsZero() {
		obs.ObservedAt = time.Now().UTC()
	}
	result, err := r.db.ExecContext(ctx, `INSERT INTO monitoring_http_observations (target_id, observed_at, status_code, duration_ns, value_json, error) SELECT id, ?, ?, ?, ?, ? FROM monitoring_http_targets WHERE id = ? AND (? = '' OR config_json = ?)`, obs.ObservedAt.UTC().Format(time.RFC3339Nano), obs.StatusCode, int64(obs.Duration), value, message, targetID, expectedConfig, expectedConfig)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count == 0 {
		return sql.ErrNoRows
	}
	return nil
}

func (r *Repository) Observations(ctx context.Context, targetID string, limit int) ([]StoredObservation, error) {
	if limit < 1 || limit > 1000 {
		return nil, errors.New("invalid observation limit")
	}
	rows, err := r.db.QueryContext(ctx, `SELECT id, observed_at, status_code, duration_ns, value_json, error FROM monitoring_http_observations WHERE target_id = ? ORDER BY id DESC LIMIT ?`, targetID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []StoredObservation{}
	for rows.Next() {
		var obs StoredObservation
		var value string
		if err := rows.Scan(&obs.ID, &obs.ObservedAt, &obs.StatusCode, &obs.DurationNS, &value, &obs.Error); err != nil {
			return nil, err
		}
		obs.TargetID, obs.Value = targetID, json.RawMessage(value)
		result = append(result, obs)
	}
	return result, rows.Err()
}

// LatestObservations returns one recent observation per target, in bounded pages.
func (r *Repository) LatestObservations(ctx context.Context, after string, limit int) ([]StoredObservation, string, error) {
	if limit < 1 || limit > 1000 {
		return nil, "", errors.New("invalid observation limit")
	}
	rows, err := r.db.QueryContext(ctx, `SELECT o.id,o.target_id,o.observed_at,o.status_code,o.duration_ns,o.value_json,o.error FROM monitoring_http_targets t JOIN monitoring_http_observations o ON o.id=(SELECT id FROM monitoring_http_observations WHERE target_id=t.id ORDER BY id DESC LIMIT 1) WHERE t.id>? ORDER BY t.id LIMIT ?`, after, limit+1)
	if err != nil {
		return nil, "", err
	}
	defer rows.Close()
	result := []StoredObservation{}
	for rows.Next() {
		var obs StoredObservation
		var value string
		if err := rows.Scan(&obs.ID, &obs.TargetID, &obs.ObservedAt, &obs.StatusCode, &obs.DurationNS, &value, &obs.Error); err != nil {
			return nil, "", err
		}
		obs.Value = json.RawMessage(value)
		result = append(result, obs)
	}
	if err := rows.Err(); err != nil {
		return nil, "", err
	}
	next := ""
	if len(result) > limit {
		result = result[:limit]
		next = result[len(result)-1].TargetID
	}
	return result, next, nil
}

// PruneObservations caps collection growth to 30 days and 100000 samples per target.
func (r *Repository) PruneObservations(ctx context.Context, now time.Time) error {
	cutoff := now.UTC().Add(-30 * 24 * time.Hour).Format(time.RFC3339Nano)
	if _, err := r.db.ExecContext(ctx, `DELETE FROM monitoring_http_observations WHERE julianday(observed_at) < julianday(?)`, cutoff); err != nil {
		return err
	}
	if _, err := r.db.ExecContext(ctx, `DELETE FROM monitoring_rule_events WHERE status NOT IN ('pending', 'unknown', 'failed') AND julianday(observed_at) < julianday(?)`, cutoff); err != nil {
		return err
	}
	_, err := r.db.ExecContext(ctx, `DELETE FROM monitoring_http_observations WHERE id IN (
		SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY target_id ORDER BY id DESC) AS ordinal FROM monitoring_http_observations) WHERE ordinal > 100000
	)`)
	return err
}
