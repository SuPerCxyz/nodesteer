package monitoring

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/mail"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func testNotificationEvent() AlertEvent {
	return AlertEvent{
		ID:           "event-1",
		RuleID:       "rule-1",
		RuleName:     "High CPU",
		NodeID:       "node-1",
		NodeHostname: "node-1.example.test",
		Metric:       "cpu.usage",
		Series:       "cpu.usage|cpu=all",
		Operator:     AlertOperatorGT,
		Status:       AlertStatusFiring,
		Severity:     AlertSeverityCritical,
		Value:        float64Ptr(95.5),
		Threshold:    90,
		StartedAt:    time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		Message:      "firing",
		CreatedAt:    time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		UpdatedAt:    time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
	}
}

func newTestNotifier(repo *AlertRepository, logger *slog.Logger) *AlertNotifier {
	notifier := NewAlertNotifier(repo, logger)
	notifier.SetRetryPolicy(3, 0)
	notifier.SetClock(func() time.Time { return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC) })
	return notifier
}

func TestNotificationWebhookPayload(t *testing.T) {
	var captured []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("request = %s %s", r.Method, r.Header.Get("Content-Type"))
		}
		captured, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("NODESTEER_ALERT_TEST_WEBHOOK", server.URL+"/hook?secret=abc")

	_, repo := newAlertTestRepository(t)
	notifier := newTestNotifier(repo, nil)
	channel := NotificationChannel{
		ID: "channel-1", Name: "webhook", Type: AlertChannelWebhook,
		Config: ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_WEBHOOK"}, Enabled: true,
	}
	if err := notifier.sendToChannel(context.Background(), channel, testNotificationEvent()); err != nil {
		t.Fatalf("send: %v", err)
	}
	var payload map[string]any
	if err := json.Unmarshal(captured, &payload); err != nil {
		t.Fatalf("decode payload: %v", err)
	}
	if payload["event"] != "firing" || payload["metric"] != "cpu.usage" || payload["series"] != "cpu.usage|cpu=all" {
		t.Fatalf("payload = %#v", payload)
	}
	if payload["severity"] != "critical" || payload["threshold"] != float64(90) || payload["value"] != 95.5 {
		t.Fatalf("payload value fields = %#v", payload)
	}
	if payload["started_at"] == "" || payload["message"] == "" {
		t.Fatalf("payload timestamps = %#v", payload)
	}
	rule, ok := payload["rule"].(map[string]any)
	if !ok || rule["name"] != "High CPU" {
		t.Fatalf("rule payload = %#v", payload["rule"])
	}
	node, ok := payload["node"].(map[string]any)
	if !ok || node["id"] != "node-1" || node["hostname"] != "node-1.example.test" {
		t.Fatalf("node payload = %#v", payload["node"])
	}
	if strings.Contains(string(captured), "secret=abc") {
		t.Fatal("payload leaked the webhook URL")
	}
}

func TestNotificationSlackDingTalkAndWeComPayloads(t *testing.T) {
	cases := []struct {
		channelType string
		envName     string
		check       func(t *testing.T, payload map[string]any)
	}{
		{
			channelType: AlertChannelSlack, envName: "NODESTEER_ALERT_TEST_SLACK",
			check: func(t *testing.T, payload map[string]any) {
				if text, _ := payload["text"].(string); !strings.Contains(text, "High CPU") {
					t.Fatalf("slack payload = %#v", payload)
				}
			},
		},
		{
			channelType: AlertChannelDingTalk, envName: "NODESTEER_ALERT_TEST_DINGTALK",
			check: func(t *testing.T, payload map[string]any) {
				text, _ := payload["text"].(map[string]any)
				if payload["msgtype"] != "text" || !strings.Contains(text["content"].(string), "High CPU") {
					t.Fatalf("dingtalk payload = %#v", payload)
				}
			},
		},
		{
			channelType: AlertChannelWeCom, envName: "NODESTEER_ALERT_TEST_WECOM",
			check: func(t *testing.T, payload map[string]any) {
				text, _ := payload["text"].(map[string]any)
				if payload["msgtype"] != "text" || !strings.Contains(text["content"].(string), "High CPU") {
					t.Fatalf("wecom payload = %#v", payload)
				}
			},
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.channelType, func(t *testing.T) {
			var captured []byte
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				captured, _ = io.ReadAll(r.Body)
				w.WriteHeader(http.StatusOK)
			}))
			defer server.Close()
			t.Setenv(testCase.envName, server.URL)
			_, repo := newAlertTestRepository(t)
			notifier := newTestNotifier(repo, nil)
			channel := NotificationChannel{
				ID: "channel-1", Name: testCase.channelType, Type: testCase.channelType,
				Config: ChannelConfig{URLEnv: testCase.envName}, Enabled: true,
			}
			if err := notifier.sendToChannel(context.Background(), channel, testNotificationEvent()); err != nil {
				t.Fatalf("send: %v", err)
			}
			var payload map[string]any
			if err := json.Unmarshal(captured, &payload); err != nil {
				t.Fatalf("decode payload: %v", err)
			}
			testCase.check(t, payload)
		})
	}
}

func TestNotificationTelegramPayload(t *testing.T) {
	var path, body string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		raw, _ := io.ReadAll(r.Body)
		body = string(raw)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()
	t.Setenv("NODESTEER_ALERT_TEST_TELEGRAM_TOKEN", "bot-token-value")
	_, repo := newAlertTestRepository(t)
	notifier := newTestNotifier(repo, nil)
	notifier.SetTelegramBaseURL(server.URL)
	channel := NotificationChannel{
		ID: "channel-1", Name: "telegram", Type: AlertChannelTelegram,
		Config: ChannelConfig{TokenEnv: "NODESTEER_ALERT_TEST_TELEGRAM_TOKEN", ChatID: "12345"}, Enabled: true,
	}
	if err := notifier.sendToChannel(context.Background(), channel, testNotificationEvent()); err != nil {
		t.Fatalf("send: %v", err)
	}
	if path != "/botbot-token-value/sendMessage" {
		t.Fatalf("path = %q", path)
	}
	if !strings.Contains(body, `"chat_id":"12345"`) || !strings.Contains(body, "High CPU") {
		t.Fatalf("telegram body = %s", body)
	}
}

func TestNotificationDoesNotFollowRedirects(t *testing.T) {
	var targetHits atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		targetHits.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusFound)
	}))
	defer redirect.Close()
	t.Setenv("NODESTEER_ALERT_TEST_WEBHOOK", redirect.URL)
	_, repo := newAlertTestRepository(t)
	notifier := newTestNotifier(repo, nil)
	channel := NotificationChannel{
		ID: "channel-1", Name: "webhook", Type: AlertChannelWebhook,
		Config: ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_WEBHOOK"}, Enabled: true,
	}
	err := notifier.sendToChannel(context.Background(), channel, testNotificationEvent())
	if err == nil || !strings.Contains(err.Error(), "status 302") {
		t.Fatalf("redirect error = %v", err)
	}
	if targetHits.Load() != 0 {
		t.Fatal("notifier followed a redirect")
	}
}

func TestNotificationEmailInjection(t *testing.T) {
	t.Setenv("NODESTEER_ALERT_TEST_MAIL_TO", "ops@example.test, oncall@example.test")
	_, repo := newAlertTestRepository(t)
	notifier := newTestNotifier(repo, nil)
	var subject, body string
	notifier.SetMailSender(func(_ context.Context, _ NotificationChannel, gotSubject, gotBody string) error {
		subject, body = gotSubject, gotBody
		return nil
	})
	channel := NotificationChannel{
		ID: "channel-1", Name: "email", Type: AlertChannelEmail, Enabled: true,
		Config: ChannelConfig{
			SMTPHost: "smtp.example.test", SMTPPort: 587,
			From: "alerts@example.test", ToEnv: "NODESTEER_ALERT_TEST_MAIL_TO",
			Username: "alerts", PasswordEnv: "NODESTEER_ALERT_TEST_MAIL_PASSWORD",
			TLSMode: "starttls",
		},
	}
	t.Setenv("NODESTEER_ALERT_TEST_MAIL_PASSWORD", "not-used-by-injected-sender")
	if err := notifier.sendToChannel(context.Background(), channel, testNotificationEvent()); err != nil {
		t.Fatalf("send: %v", err)
	}
	if !strings.Contains(subject, "High CPU") || !strings.Contains(subject, "CRITICAL") {
		t.Fatalf("subject = %q", subject)
	}
	if !strings.Contains(body, "cpu.usage") || !strings.Contains(body, "node-1") {
		t.Fatalf("body = %q", body)
	}
	if strings.Contains(body, "not-used-by-injected-sender") {
		t.Fatal("email body leaked the SMTP password")
	}
}

func TestNotificationRetryAndAttemptRecording(t *testing.T) {
	var hits atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":"supersecret-token"}`))
	}))
	defer server.Close()
	t.Setenv("NODESTEER_ALERT_TEST_WEBHOOK", server.URL+"/secret-path")

	st, repo := newAlertTestRepository(t)
	channel := createAlertTestChannel(t, repo)
	rule := createAlertTestRule(t, repo, channel.ID)
	event := testNotificationEvent()
	event.ID = ""
	event.RuleID = rule.ID
	event.RuleName = rule.Name
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	if err := repo.ApplyDecision(context.Background(), rule, AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: event.NodeID, Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusFiring, LastEvaluatedAt: now, LastNotifiedAt: now, UpdatedAt: now,
		},
		Event:  &event,
		Notify: true,
	}); err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	var buffer bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&buffer, nil))
	notifier := newTestNotifier(repo, logger)
	for attempt := 0; attempt < 3; attempt++ {
		if err := notifier.ProcessPending(context.Background()); err != nil {
			t.Fatalf("process %d: %v", attempt, err)
		}
	}
	if hits.Load() != 3 {
		t.Fatalf("hits = %d, want 3", hits.Load())
	}
	var status string
	var attempts int
	var lastError string
	if err := st.DB().QueryRow(`SELECT status, attempts, last_error FROM alert_notifications LIMIT 1`).Scan(&status, &attempts, &lastError); err != nil {
		t.Fatalf("notification: %v", err)
	}
	if status != "failed" || attempts != 3 {
		t.Fatalf("notification = %s/%d", status, attempts)
	}
	if !strings.Contains(lastError, "status 500") {
		t.Fatalf("last_error = %q", lastError)
	}
	for _, secret := range []string{"supersecret-token", server.URL, "secret-path"} {
		if strings.Contains(lastError, secret) || strings.Contains(buffer.String(), secret) {
			t.Fatalf("secret %q leaked in error/log: %q / %q", secret, lastError, buffer.String())
		}
	}
	var recorded int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM alert_notification_attempts`).Scan(&recorded); err != nil {
		t.Fatalf("attempts: %v", err)
	}
	if recorded != 3 {
		t.Fatalf("recorded attempts = %d, want 3", recorded)
	}
}

func TestNotificationSuccessRecordsSent(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("NODESTEER_ALERT_TEST_WEBHOOK", server.URL)
	st, repo := newAlertTestRepository(t)
	channel := createAlertTestChannel(t, repo)
	rule := createAlertTestRule(t, repo, channel.ID)
	event := testNotificationEvent()
	event.ID = ""
	event.RuleID = rule.ID
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	if err := repo.ApplyDecision(context.Background(), rule, AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: event.NodeID, Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusFiring, LastEvaluatedAt: now, LastNotifiedAt: now, UpdatedAt: now,
		},
		Event:  &event,
		Notify: true,
	}); err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	notifier := newTestNotifier(repo, nil)
	if err := notifier.ProcessPending(context.Background()); err != nil {
		t.Fatalf("process: %v", err)
	}
	var status string
	var attempts int
	if err := st.DB().QueryRow(`SELECT status, attempts FROM alert_notifications LIMIT 1`).Scan(&status, &attempts); err != nil {
		t.Fatalf("notification: %v", err)
	}
	if status != "sent" || attempts != 1 {
		t.Fatalf("notification = %s/%d", status, attempts)
	}
}

func TestDingTalkSignatureAndSignedURL(t *testing.T) {
	const secret = "SEC0123456789abcdef"
	const timestamp = int64(1700000000000)
	const expected = "TSZbRFUuvaSQaRKUpF970OPCb2/LcQAP3wOvwZIzBZk="
	if got := DingTalkSignature(secret, timestamp); got != expected {
		t.Fatalf("signature = %q, want %q", got, expected)
	}
	signed, err := DingTalkSignedURL("https://oapi.dingtalk.com/robot/send?access_token=abc&foo=bar", secret, timestamp)
	if err != nil {
		t.Fatalf("sign url: %v", err)
	}
	parsed, err := url.Parse(signed)
	if err != nil {
		t.Fatalf("parse signed url: %v", err)
	}
	query := parsed.Query()
	if query.Get("timestamp") != "1700000000000" || query.Get("sign") != expected {
		t.Fatalf("signed query = %#v", query)
	}
	if query.Get("access_token") != "abc" || query.Get("foo") != "bar" {
		t.Fatalf("existing query was not preserved: %#v", query)
	}
	if strings.Contains(signed, secret) {
		t.Fatal("signed URL leaked the secret")
	}
}

func TestDingTalkSendSigningAndLegacyBehavior(t *testing.T) {
	var queries []url.Values
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		queries = append(queries, r.URL.Query())
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("NODESTEER_ALERT_TEST_DINGTALK_URL", server.URL+"/robot/send?access_token=abc")
	t.Setenv("NODESTEER_ALERT_TEST_DINGTALK_SECRET", "SEC0123456789abcdef")

	_, repo := newAlertTestRepository(t)
	notifier := newTestNotifier(repo, nil)
	fixed := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	notifier.SetClock(func() time.Time { return fixed })

	signedChannel := NotificationChannel{
		ID: "channel-signed", Name: "dingtalk signed", Type: AlertChannelDingTalk,
		Config: ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_DINGTALK_URL", SecretEnv: "NODESTEER_ALERT_TEST_DINGTALK_SECRET"},
	}
	if err := notifier.sendToChannel(context.Background(), signedChannel, testNotificationEvent()); err != nil {
		t.Fatalf("send signed: %v", err)
	}
	legacyChannel := NotificationChannel{
		ID: "channel-legacy", Name: "dingtalk legacy", Type: AlertChannelDingTalk,
		Config: ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_DINGTALK_URL"},
	}
	if err := notifier.sendToChannel(context.Background(), legacyChannel, testNotificationEvent()); err != nil {
		t.Fatalf("send legacy: %v", err)
	}
	if len(queries) != 2 {
		t.Fatalf("requests = %d, want 2", len(queries))
	}
	wantTimestamp := strconv.FormatInt(fixed.UnixMilli(), 10)
	if queries[0].Get("timestamp") != wantTimestamp {
		t.Fatalf("timestamp = %q, want %q", queries[0].Get("timestamp"), wantTimestamp)
	}
	if queries[0].Get("sign") != DingTalkSignature("SEC0123456789abcdef", fixed.UnixMilli()) {
		t.Fatalf("sign = %q", queries[0].Get("sign"))
	}
	if queries[1].Get("timestamp") != "" || queries[1].Get("sign") != "" {
		t.Fatalf("legacy channel unexpectedly signed: %#v", queries[1])
	}
}

func TestNotificationConfigNewFieldsValidation(t *testing.T) {
	dingtalk := ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_DINGTALK_URL", SecretEnv: "NODESTEER_ALERT_TEST_DINGTALK_SECRET"}
	if err := dingtalk.Validate(AlertChannelDingTalk); err != nil {
		t.Fatalf("dingtalk secret_env: %v", err)
	}
	if err := dingtalk.Validate(AlertChannelWebhook); err == nil {
		t.Fatal("webhook accepted secret_env")
	}
	email := ChannelConfig{
		SMTPHost: "smtp.example.test", SMTPPort: 587, From: "alerts@example.test",
		ToEnv: "NODESTEER_ALERT_TEST_MAIL_TO", HTML: true,
		AttachmentsEnv: "NODESTEER_ALERT_TEST_ATTACHMENTS", AttachmentDir: "/var/lib/nodesteer/attachments",
	}
	if err := email.Validate(AlertChannelEmail); err != nil {
		t.Fatalf("email html/attachments: %v", err)
	}
	if err := email.Validate(AlertChannelWebhook); err == nil {
		t.Fatal("webhook accepted html/attachments")
	}
	missingDir := email
	missingDir.AttachmentDir = ""
	if err := missingDir.Validate(AlertChannelEmail); err == nil {
		t.Fatal("attachments_env without attachment_dir was accepted")
	}
	relativeDir := email
	relativeDir.AttachmentDir = "relative/path"
	if err := relativeDir.Validate(AlertChannelEmail); err == nil {
		t.Fatal("relative attachment_dir was accepted")
	}
}

func TestEmailHTMLMessage(t *testing.T) {
	from := &mail.Address{Address: "alerts@example.test"}
	recipients := []*mail.Address{{Address: "ops@example.test"}}
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	plain := buildEmailMessage(from, recipients, MailMessage{Subject: "alert", Text: "plain body"}, now)
	if !strings.Contains(plain, "Content-Type: text/plain; charset=UTF-8\r\n") || !strings.Contains(plain, "plain body") {
		t.Fatalf("plain message = %q", plain)
	}
	htmlMessage := buildEmailMessage(from, recipients, MailMessage{
		Subject: "alert", Text: "plain body", HTML: "<html><body><strong>html body</strong></body></html>",
	}, now)
	if !strings.Contains(htmlMessage, "Content-Type: text/html; charset=UTF-8\r\n") || !strings.Contains(htmlMessage, "html body") {
		t.Fatalf("html message = %q", htmlMessage)
	}
	if strings.Contains(htmlMessage, "text/plain") {
		t.Fatalf("html-only message contains text/plain: %q", htmlMessage)
	}
}

func TestEmailHTMLSenderAndAttachmentMIME(t *testing.T) {
	dir := t.TempDir()
	attachmentPath := filepath.Join(dir, "report.txt")
	if err := os.WriteFile(attachmentPath, []byte("attachment payload"), 0o600); err != nil {
		t.Fatalf("write attachment: %v", err)
	}
	t.Setenv("NODESTEER_ALERT_TEST_MAIL_TO", "ops@example.test")
	t.Setenv("NODESTEER_ALERT_TEST_ATTACHMENTS", attachmentPath)
	_, repo := newAlertTestRepository(t)
	notifier := newTestNotifier(repo, nil)
	var captured MailMessage
	notifier.SetMailSender(func(_ context.Context, _ NotificationChannel, message MailMessage) error {
		captured = message
		return nil
	})
	channel := NotificationChannel{
		ID: "channel-1", Name: "email", Type: AlertChannelEmail, Enabled: true,
		Config: ChannelConfig{
			SMTPHost: "smtp.example.test", SMTPPort: 587, From: "alerts@example.test",
			ToEnv: "NODESTEER_ALERT_TEST_MAIL_TO", HTML: true,
			AttachmentsEnv: "NODESTEER_ALERT_TEST_ATTACHMENTS", AttachmentDir: dir,
		},
	}
	if err := notifier.sendToChannel(context.Background(), channel, testNotificationEvent()); err != nil {
		t.Fatalf("send: %v", err)
	}
	if captured.HTML == "" || !strings.Contains(captured.HTML, "<html>") {
		t.Fatalf("html body = %q", captured.HTML)
	}
	if len(captured.Attachments) != 1 || captured.Attachments[0].Filename != "report.txt" ||
		string(captured.Attachments[0].Data) != "attachment payload" {
		t.Fatalf("attachments = %+v", captured.Attachments)
	}

	raw := buildEmailMessage(
		&mail.Address{Address: "alerts@example.test"},
		[]*mail.Address{{Address: "ops@example.test"}},
		captured,
		time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
	)
	message, err := mail.ReadMessage(strings.NewReader(raw))
	if err != nil {
		t.Fatalf("read message: %v", err)
	}
	mediaType, params, err := mime.ParseMediaType(message.Header.Get("Content-Type"))
	if err != nil || mediaType != "multipart/mixed" {
		t.Fatalf("content type = %q, %v", message.Header.Get("Content-Type"), err)
	}
	reader := multipart.NewReader(message.Body, params["boundary"])
	alternative, err := reader.NextPart()
	if err != nil {
		t.Fatalf("alternative part: %v", err)
	}
	if !strings.HasPrefix(alternative.Header.Get("Content-Type"), "multipart/alternative") {
		t.Fatalf("first part = %q", alternative.Header.Get("Content-Type"))
	}
	attachment, err := reader.NextPart()
	if err != nil {
		t.Fatalf("attachment part: %v", err)
	}
	if !strings.Contains(attachment.Header.Get("Content-Disposition"), "report.txt") ||
		attachment.Header.Get("Content-Transfer-Encoding") != "base64" {
		t.Fatalf("attachment headers = %#v", attachment.Header)
	}
	encoded, err := io.ReadAll(attachment)
	if err != nil {
		t.Fatalf("read attachment: %v", err)
	}
	data, err := base64.StdEncoding.DecodeString(strings.ReplaceAll(string(encoded), "\r\n", ""))
	if err != nil || string(data) != "attachment payload" {
		t.Fatalf("attachment body = %q, %v", data, err)
	}
}

func TestEmailAttachmentSecurityLimits(t *testing.T) {
	dir := t.TempDir()
	outside := t.TempDir()
	write := func(name string, size int64) string {
		path := filepath.Join(dir, name)
		file, err := os.Create(path)
		if err != nil {
			t.Fatalf("create %s: %v", name, err)
		}
		if err := file.Truncate(size); err != nil {
			_ = file.Close()
			t.Fatalf("truncate %s: %v", name, err)
		}
		if err := file.Close(); err != nil {
			t.Fatalf("close %s: %v", name, err)
		}
		return path
	}
	write("ok.txt", 4)
	write("big.bin", maxEmailAttachmentBytes+1)
	for index := 0; index < 3; index++ {
		write(fmt.Sprintf("total-%d.bin", index), 4<<20)
	}
	outsidePath := filepath.Join(outside, "outside.txt")
	if err := os.WriteFile(outsidePath, []byte("outside"), 0o600); err != nil {
		t.Fatalf("write outside: %v", err)
	}
	linkPath := filepath.Join(dir, "link.txt")
	if err := os.Symlink(outsidePath, linkPath); err != nil {
		t.Fatalf("symlink: %v", err)
	}
	cases := []struct {
		name    string
		paths   string
		dir     string
		message string
	}{
		{name: "too many", paths: strings.Join([]string{
			filepath.Join(dir, "ok.txt"), filepath.Join(dir, "ok.txt"), filepath.Join(dir, "ok.txt"),
			filepath.Join(dir, "ok.txt"), filepath.Join(dir, "ok.txt"), filepath.Join(dir, "ok.txt"),
		}, ","), dir: dir, message: "too many"},
		{name: "too large", paths: filepath.Join(dir, "big.bin"), dir: dir, message: "too large"},
		{name: "total too large", paths: strings.Join([]string{
			filepath.Join(dir, "total-0.bin"), filepath.Join(dir, "total-1.bin"), filepath.Join(dir, "total-2.bin"),
		}, ","), dir: dir, message: "too large"},
		{name: "traversal", paths: filepath.Join(dir, "..", filepath.Base(outside), "outside.txt"), dir: dir, message: "not allowed"},
		{name: "outside", paths: outsidePath, dir: dir, message: "not allowed"},
		{name: "symlink", paths: linkPath, dir: dir, message: "not allowed"},
		{name: "relative", paths: "ok.txt", dir: dir, message: "not allowed"},
		{name: "no dir", paths: filepath.Join(dir, "ok.txt"), dir: "", message: "not configured"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			t.Setenv("NODESTEER_ALERT_TEST_ATTACHMENTS", testCase.paths)
			_, err := emailAttachments(ChannelConfig{
				AttachmentsEnv: "NODESTEER_ALERT_TEST_ATTACHMENTS",
				AttachmentDir:  testCase.dir,
			})
			if err == nil || !strings.Contains(err.Error(), testCase.message) {
				t.Fatalf("error = %v, want %q", err, testCase.message)
			}
			if strings.Contains(err.Error(), outsidePath) {
				t.Fatalf("error leaked outside path: %v", err)
			}
		})
	}
}

func TestDingTalkSecretConfigOnlyStoresEnvReference(t *testing.T) {
	t.Setenv("NODESTEER_ALERT_TEST_DINGTALK_SECRET", "super-secret-value")
	config := ChannelConfig{
		URLEnv:    "NODESTEER_ALERT_TEST_DINGTALK_URL",
		SecretEnv: "NODESTEER_ALERT_TEST_DINGTALK_SECRET",
	}
	raw, err := json.Marshal(config)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if strings.Contains(string(raw), "super-secret-value") {
		t.Fatalf("config leaked secret: %s", raw)
	}
	if !strings.Contains(string(raw), `"secret_env":"NODESTEER_ALERT_TEST_DINGTALK_SECRET"`) {
		t.Fatalf("config missing env reference: %s", raw)
	}
}
