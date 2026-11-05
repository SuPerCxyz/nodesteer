package monitoring

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"log/slog"
	"mime"
	"mime/multipart"
	"net"
	"net/http"
	"net/mail"
	"net/smtp"
	"net/textproto"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// 通知渠道配置只保存非敏感字段与环境变量名。
//
// 约束：
//   - webhook/slack/dingtalk/wecom 的 URL 必须通过 url_env 引用；
//   - telegram 的 bot token 必须通过 token_env 引用；
//   - email 的 SMTP 密码通过 password_env 引用，收件人通过 to_env 引用；
//   - API 与日志只看到环境变量名，永远看不到实际值。

const (
	defaultNotificationTimeout = 10 * time.Second
	maxNotificationTimeout     = 30 * time.Second
	maxNotificationResponse    = 4096
	defaultNotificationRetries = 3
	defaultNotificationBackoff = time.Second

	// 邮件附件安全限制：数量、单文件与总大小均为硬上限。
	maxEmailAttachments          = 5
	maxEmailAttachmentBytes      = 5 << 20
	maxEmailAttachmentTotalBytes = 10 << 20

	notificationResponseStatusOnly   = ""
	notificationResponseTelegram     = "telegram"
	notificationResponseProviderCode = "provider"
)

// ChannelConfig 是所有渠道类型的联合配置。未使用字段由类型校验拒绝。
type ChannelConfig struct {
	URLEnv         string `json:"url_env,omitempty"`
	TokenEnv       string `json:"token_env,omitempty"`
	SecretEnv      string `json:"secret_env,omitempty"`
	ChatID         string `json:"chat_id,omitempty"`
	TimeoutSec     int    `json:"timeout_sec,omitempty"`
	SMTPHost       string `json:"smtp_host,omitempty"`
	SMTPPort       int    `json:"smtp_port,omitempty"`
	Username       string `json:"username,omitempty"`
	PasswordEnv    string `json:"password_env,omitempty"`
	From           string `json:"from,omitempty"`
	ToEnv          string `json:"to_env,omitempty"`
	TLSMode        string `json:"tls_mode,omitempty"`
	HTML           bool   `json:"html,omitempty"`
	AttachmentsEnv string `json:"attachments_env,omitempty"`
	AttachmentDir  string `json:"attachment_dir,omitempty"`
}

// UnmarshalJSON 拒绝未知字段，避免把 url/token/password 等字面量秘密
// 误存进数据库；调用方必须使用 *_env 引用。
func (config *ChannelConfig) UnmarshalJSON(data []byte) error {
	type alias ChannelConfig
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var value alias
	if err := decoder.Decode(&value); err != nil {
		return err
	}
	*config = ChannelConfig(value)
	return nil
}

// Validate 按渠道类型校验配置。错误信息只描述字段约束。
func (config ChannelConfig) Validate(channelType string) error {
	if !validEnvReference(config.URLEnv) && config.URLEnv != "" {
		return fmt.Errorf("%w: url_env is not a valid environment variable name", ErrInvalidNotificationCh)
	}
	if !validEnvReference(config.TokenEnv) && config.TokenEnv != "" {
		return fmt.Errorf("%w: token_env is not a valid environment variable name", ErrInvalidNotificationCh)
	}
	if !validEnvReference(config.SecretEnv) && config.SecretEnv != "" {
		return fmt.Errorf("%w: secret_env is not a valid environment variable name", ErrInvalidNotificationCh)
	}
	if !validEnvReference(config.PasswordEnv) && config.PasswordEnv != "" {
		return fmt.Errorf("%w: password_env is not a valid environment variable name", ErrInvalidNotificationCh)
	}
	if !validEnvReference(config.ToEnv) && config.ToEnv != "" {
		return fmt.Errorf("%w: to_env is not a valid environment variable name", ErrInvalidNotificationCh)
	}
	if !validEnvReference(config.AttachmentsEnv) && config.AttachmentsEnv != "" {
		return fmt.Errorf("%w: attachments_env is not a valid environment variable name", ErrInvalidNotificationCh)
	}
	if config.TimeoutSec < 0 || config.TimeoutSec > int(maxNotificationTimeout/time.Second) {
		return fmt.Errorf("%w: invalid timeout_sec", ErrInvalidNotificationCh)
	}
	if config.AttachmentDir != "" {
		if len(config.AttachmentDir) > 1024 || containsControlCharacters(config.AttachmentDir) ||
			!filepath.IsAbs(config.AttachmentDir) || filepath.Clean(config.AttachmentDir) != config.AttachmentDir {
			return fmt.Errorf("%w: invalid attachment_dir", ErrInvalidNotificationCh)
		}
	}
	switch channelType {
	case AlertChannelWebhook, AlertChannelSlack, AlertChannelWeCom:
		if config.URLEnv == "" {
			return fmt.Errorf("%w: url_env is required", ErrInvalidNotificationCh)
		}
		if config.TokenEnv != "" || config.SecretEnv != "" || config.ChatID != "" || config.SMTPHost != "" ||
			config.PasswordEnv != "" || config.From != "" || config.ToEnv != "" || config.TLSMode != "" ||
			config.HTML || config.AttachmentsEnv != "" || config.AttachmentDir != "" {
			return fmt.Errorf("%w: unsupported field for this channel type", ErrInvalidNotificationCh)
		}
	case AlertChannelDingTalk:
		if config.URLEnv == "" {
			return fmt.Errorf("%w: url_env is required", ErrInvalidNotificationCh)
		}
		if config.TokenEnv != "" || config.ChatID != "" || config.SMTPHost != "" ||
			config.PasswordEnv != "" || config.From != "" || config.ToEnv != "" || config.TLSMode != "" ||
			config.HTML || config.AttachmentsEnv != "" || config.AttachmentDir != "" {
			return fmt.Errorf("%w: unsupported field for this channel type", ErrInvalidNotificationCh)
		}
	case AlertChannelTelegram:
		if config.TokenEnv == "" || config.ChatID == "" {
			return fmt.Errorf("%w: token_env and chat_id are required", ErrInvalidNotificationCh)
		}
		if len(config.ChatID) > 128 || containsControlCharacters(config.ChatID) {
			return fmt.Errorf("%w: invalid chat_id", ErrInvalidNotificationCh)
		}
		if config.URLEnv != "" || config.SecretEnv != "" || config.SMTPHost != "" || config.PasswordEnv != "" ||
			config.From != "" || config.ToEnv != "" || config.TLSMode != "" ||
			config.HTML || config.AttachmentsEnv != "" || config.AttachmentDir != "" {
			return fmt.Errorf("%w: unsupported field for this channel type", ErrInvalidNotificationCh)
		}
	case AlertChannelEmail:
		if config.SMTPHost == "" || len(config.SMTPHost) > 253 || !validSMTPHost(config.SMTPHost) {
			return fmt.Errorf("%w: invalid smtp_host", ErrInvalidNotificationCh)
		}
		if config.SMTPPort < 1 || config.SMTPPort > 65535 {
			return fmt.Errorf("%w: invalid smtp_port", ErrInvalidNotificationCh)
		}
		if _, err := mail.ParseAddress(config.From); err != nil {
			return fmt.Errorf("%w: invalid from address", ErrInvalidNotificationCh)
		}
		if config.ToEnv == "" {
			return fmt.Errorf("%w: to_env is required", ErrInvalidNotificationCh)
		}
		if config.Username != "" && config.PasswordEnv == "" {
			return fmt.Errorf("%w: password_env is required when username is set", ErrInvalidNotificationCh)
		}
		if config.Username == "" && config.PasswordEnv != "" {
			return fmt.Errorf("%w: username is required when password_env is set", ErrInvalidNotificationCh)
		}
		switch config.TLSMode {
		case "", "none", "starttls", "tls":
		default:
			return fmt.Errorf("%w: invalid tls_mode", ErrInvalidNotificationCh)
		}
		if config.AttachmentsEnv != "" && config.AttachmentDir == "" {
			return fmt.Errorf("%w: attachment_dir is required when attachments_env is set", ErrInvalidNotificationCh)
		}
		if config.URLEnv != "" || config.TokenEnv != "" || config.SecretEnv != "" || config.ChatID != "" {
			return fmt.Errorf("%w: unsupported field for this channel type", ErrInvalidNotificationCh)
		}
	}
	return nil
}

func validEnvReference(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for index, character := range value {
		switch {
		case character >= 'a' && character <= 'z':
		case character >= 'A' && character <= 'Z':
		case character == '_':
		case character >= '0' && character <= '9' && index > 0:
		default:
			return false
		}
	}
	return true
}

func validSMTPHost(value string) bool {
	if value == "" || strings.ContainsAny(value, " \t\r\n/\\@") {
		return false
	}
	if net.ParseIP(value) != nil {
		return true
	}
	for _, label := range strings.Split(strings.TrimSuffix(value, "."), ".") {
		if label == "" || len(label) > 63 {
			return false
		}
		for index := 0; index < len(label); index++ {
			character := label[index]
			if (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
				(character >= '0' && character <= '9') || character == '-' {
				continue
			}
			return false
		}
	}
	return true
}

// EmailAttachment 是已经过目录、数量和大小校验的邮件附件。
type EmailAttachment struct {
	Filename    string
	ContentType string
	Data        []byte
}

// MailMessage 是渠道层生成的邮件内容，保留纯文本与 HTML 两个版本。
type MailMessage struct {
	Subject     string
	Text        string
	HTML        string
	Attachments []EmailAttachment
}

// MailSender 是兼容既有调用方的邮件发送签名（主题 + 纯文本正文）。
type MailSender func(ctx context.Context, channel NotificationChannel, subject, body string) error

// MailMessageSender 允许测试注入完整邮件内容（HTML/附件），避免真实发信。
type MailMessageSender func(ctx context.Context, channel NotificationChannel, message MailMessage) error

// AlertNotifier 负责发件箱扫描、渠道适配与有界重试。
type AlertNotifier struct {
	repo            *AlertRepository
	logger          *slog.Logger
	client          *http.Client
	mailSender      MailMessageSender
	legacyMail      MailSender
	telegramBaseURL string
	maxAttempts     int
	backoffBase     time.Duration
	now             func() time.Time
}

func NewAlertNotifier(repo *AlertRepository, logger *slog.Logger) *AlertNotifier {
	return &AlertNotifier{
		repo:   repo,
		logger: logger,
		client: &http.Client{
			// 明确不跟随重定向：避免通知被 302 到非预期目标。
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		mailSender:      sendEmailSMTP,
		telegramBaseURL: "https://api.telegram.org",
		maxAttempts:     defaultNotificationRetries,
		backoffBase:     defaultNotificationBackoff,
		now:             func() time.Time { return time.Now().UTC() },
	}
}

// SetMailSender 注入邮件发送实现（测试用）。
//
// 参数同时接受 MailSender（完整邮件内容）和 LegacyMailSender（主题 + 正文），
// 便于既有测试与需要检查 HTML/附件的测试共用同一入口。
func (n *AlertNotifier) SetMailSender(sender any) {
	switch value := sender.(type) {
	case nil:
		return
	case MailMessageSender:
		n.mailSender, n.legacyMail = value, nil
	case MailSender:
		n.mailSender, n.legacyMail = nil, value
	case func(context.Context, NotificationChannel, MailMessage) error:
		n.mailSender, n.legacyMail = value, nil
	case func(context.Context, NotificationChannel, string, string) error:
		n.mailSender, n.legacyMail = nil, value
	default:
		panic("monitoring: unsupported mail sender type")
	}
}

// SetTelegramBaseURL 设置 Telegram API 基址（测试用）。
func (n *AlertNotifier) SetTelegramBaseURL(baseURL string) {
	if strings.TrimSpace(baseURL) != "" {
		n.telegramBaseURL = strings.TrimRight(baseURL, "/")
	}
}

// SetRetryPolicy 设置最大尝试次数与指数退避基数。
func (n *AlertNotifier) SetRetryPolicy(maxAttempts int, base time.Duration) {
	if maxAttempts > 0 {
		n.maxAttempts = maxAttempts
	}
	if base >= 0 {
		n.backoffBase = base
	}
}

// SetClock 注入时钟（测试用）。
func (n *AlertNotifier) SetClock(now func() time.Time) {
	if now != nil {
		n.now = now
	}
}

// SendTest 发送一条合成测试通知。测试发送不受渠道 enabled 影响。
func (n *AlertNotifier) SendTest(ctx context.Context, channel NotificationChannel) error {
	now := n.now()
	event := AlertEvent{
		ID:           "test",
		RuleID:       "test",
		RuleName:     "NodeSteer test",
		NodeID:       "test-node",
		NodeHostname: "test-node",
		Metric:       "test.metric",
		Series:       "test.metric",
		Operator:     AlertOperatorGT,
		Status:       AlertStatusFiring,
		Severity:     AlertSeverityInfo,
		Value:        float64Ptr(1),
		Threshold:    0,
		StartedAt:    now,
		Message:      "NodeSteer test notification",
		CreatedAt:    now,
		UpdatedAt:    now,
	}
	return n.sendToChannel(ctx, channel, event)
}

// ProcessPending 扫描发件箱并发送。每次尝试都会写入 alert_notification_attempts，
// 失败按指数退避重试，达到上限后标记 failed。该函数由独立循环调用，不阻塞评估。
func (n *AlertNotifier) ProcessPending(ctx context.Context) error {
	now := n.now()
	notifications, err := n.repo.ListPendingNotifications(ctx, now, 50)
	if err != nil {
		return err
	}
	for _, notification := range notifications {
		if err := ctx.Err(); err != nil {
			return nil
		}
		event, err := n.repo.GetAlertEvent(ctx, notification.EventID)
		if err != nil {
			n.recordFailure(ctx, notification, "alert event not found", true, now)
			continue
		}
		channel, err := n.repo.GetChannel(ctx, notification.ChannelID)
		if err != nil {
			n.recordFailure(ctx, notification, "notification channel not found", true, now)
			continue
		}
		if !channel.Enabled {
			n.recordFailure(ctx, notification, "notification channel is disabled", true, now)
			continue
		}
		sendErr := n.sendToChannel(ctx, channel, event)
		if sendErr == nil {
			if err := n.repo.MarkNotificationSent(ctx, notification.ID, notification.Attempts+1, now); err != nil {
				return err
			}
			continue
		}
		message := normalizeNotificationError(sendErr)
		attempts := notification.Attempts + 1
		// 配置/环境变量错误重试也不会成功，直接标记失败。
		final := attempts >= n.maxAttempts || errors.Is(sendErr, ErrNotificationConfig)
		next := now.Add(n.backoff(attempts))
		if final {
			next = time.Time{}
		}
		if err := n.repo.RecordNotificationAttempt(ctx, notification.ID, attempts, "failed", message, final, next, now); err != nil {
			return err
		}
		if n.logger != nil {
			n.logger.Warn("alert notification failed",
				"notification", notification.ID,
				"channel", notification.ChannelID,
				"event", notification.EventID,
				"attempt", attempts,
				"final", final,
				"error", message)
		}
	}
	return nil
}

func (n *AlertNotifier) recordFailure(ctx context.Context, notification AlertNotification, message string, final bool, now time.Time) {
	attempts := notification.Attempts + 1
	if err := n.repo.RecordNotificationAttempt(ctx, notification.ID, attempts, "failed", message, final, time.Time{}, now); err != nil && n.logger != nil {
		n.logger.Warn("record alert notification failure", "notification", notification.ID, "error", normalizeNotificationError(err))
	}
	if n.logger != nil {
		n.logger.Warn("alert notification skipped",
			"notification", notification.ID,
			"channel", notification.ChannelID,
			"event", notification.EventID,
			"reason", message)
	}
}

func (n *AlertNotifier) backoff(attempt int) time.Duration {
	if n.backoffBase <= 0 || attempt <= 0 {
		return 0
	}
	shift := attempt - 1
	if shift > 8 {
		shift = 8
	}
	return n.backoffBase * time.Duration(1<<shift)
}

func (n *AlertNotifier) sendToChannel(ctx context.Context, channel NotificationChannel, event AlertEvent) error {
	if !ValidAlertChannelType(channel.Type) {
		return fmt.Errorf("%w: unsupported notification channel type", ErrNotificationConfig)
	}
	if err := channel.Config.Validate(channel.Type); err != nil {
		return fmt.Errorf("%w: invalid configuration", ErrNotificationConfig)
	}
	message := AlertEventMessage(event)
	timeout := notificationTimeout(channel.Config)
	switch channel.Type {
	case AlertChannelWebhook:
		endpoint, err := environmentValue(channel.Config.URLEnv)
		if err != nil {
			return err
		}
		payload := webhookPayload(event)
		return n.postJSON(ctx, endpoint, payload, timeout, notificationResponseStatusOnly)
	case AlertChannelSlack:
		endpoint, err := environmentValue(channel.Config.URLEnv)
		if err != nil {
			return err
		}
		return n.postJSON(ctx, endpoint, map[string]any{"text": message}, timeout, notificationResponseStatusOnly)
	case AlertChannelDingTalk:
		endpoint, err := environmentValue(channel.Config.URLEnv)
		if err != nil {
			return err
		}
		// 加签只追加 timestamp/sign 参数，不把 secret 写入日志或错误。
		if channel.Config.SecretEnv != "" {
			secret, err := environmentValue(channel.Config.SecretEnv)
			if err != nil {
				return err
			}
			endpoint, err = DingTalkSignedURL(endpoint, secret, n.now().UnixMilli())
			if err != nil {
				return err
			}
		}
		payload := map[string]any{"msgtype": "text", "text": map[string]string{"content": message}}
		return n.postJSON(ctx, endpoint, payload, timeout, notificationResponseProviderCode)
	case AlertChannelWeCom:
		endpoint, err := environmentValue(channel.Config.URLEnv)
		if err != nil {
			return err
		}
		payload := map[string]any{"msgtype": "text", "text": map[string]string{"content": message}}
		return n.postJSON(ctx, endpoint, payload, timeout, notificationResponseProviderCode)
	case AlertChannelTelegram:
		token, err := environmentValue(channel.Config.TokenEnv)
		if err != nil {
			return err
		}
		endpoint := n.telegramBaseURL + "/bot" + token + "/sendMessage"
		payload := map[string]any{"chat_id": channel.Config.ChatID, "text": message, "disable_web_page_preview": true}
		return n.postJSON(ctx, endpoint, payload, timeout, notificationResponseTelegram)
	case AlertChannelEmail:
		recipients, err := emailRecipients(channel.Config)
		if err != nil {
			return err
		}
		subject := fmt.Sprintf("[NodeSteer][%s] %s %s", strings.ToUpper(event.Severity), event.RuleName, event.Status)
		message := MailMessage{Subject: subject, Text: emailBody(event, recipients)}
		if channel.Config.HTML {
			message.HTML = emailHTMLBody(event, recipients)
		}
		attachments, err := emailAttachments(channel.Config)
		if err != nil {
			return err
		}
		message.Attachments = attachments
		if n.legacyMail != nil {
			body := message.Text
			if message.HTML != "" {
				body = message.HTML
			}
			return n.legacyMail(ctx, channel, subject, body)
		}
		if n.mailSender == nil {
			return errors.New("email sender is not configured")
		}
		return n.mailSender(ctx, channel, message)
	default:
		return errors.New("unsupported notification channel type")
	}
}

func notificationTimeout(config ChannelConfig) time.Duration {
	if config.TimeoutSec <= 0 {
		return defaultNotificationTimeout
	}
	timeout := time.Duration(config.TimeoutSec) * time.Second
	if timeout > maxNotificationTimeout {
		return maxNotificationTimeout
	}
	return timeout
}

func environmentValue(name string) (string, error) {
	if name == "" {
		return "", fmt.Errorf("%w: environment variable reference is missing", ErrNotificationConfig)
	}
	value, ok := os.LookupEnv(name)
	if !ok || strings.TrimSpace(value) == "" {
		return "", fmt.Errorf("%w: environment variable %s is not set", ErrNotificationConfig, name)
	}
	return value, nil
}

// DingTalkSignature 计算钉钉自定义机器人加签值：
// base64(HMAC-SHA256(secret, timestamp+"\n"+secret))。
func DingTalkSignature(secret string, timestamp int64) string {
	stringToSign := strconv.FormatInt(timestamp, 10) + "\n" + secret
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(stringToSign))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}

// DingTalkSignedURL 在保留原查询参数的前提下追加 timestamp 与 sign。
func DingTalkSignedURL(endpoint, secret string, timestamp int64) (string, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" || parsed.User != nil ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") {
		return "", fmt.Errorf("%w: notification endpoint URL is invalid", ErrNotificationConfig)
	}
	query := parsed.Query()
	query.Set("timestamp", strconv.FormatInt(timestamp, 10))
	query.Set("sign", DingTalkSignature(secret, timestamp))
	parsed.RawQuery = query.Encode()
	return parsed.String(), nil
}

type webhookRule struct {
	ID        string  `json:"id"`
	Name      string  `json:"name"`
	Operator  string  `json:"operator"`
	Threshold float64 `json:"threshold"`
	Severity  string  `json:"severity"`
}

type webhookNode struct {
	ID       string `json:"id"`
	Hostname string `json:"hostname"`
}

type webhookEventPayload struct {
	Event      string      `json:"event"`
	Rule       webhookRule `json:"rule"`
	Node       webhookNode `json:"node"`
	Metric     string      `json:"metric"`
	Series     string      `json:"series"`
	Value      *float64    `json:"value"`
	Threshold  float64     `json:"threshold"`
	Severity   string      `json:"severity"`
	StartedAt  string      `json:"started_at,omitempty"`
	ResolvedAt string      `json:"resolved_at,omitempty"`
	Message    string      `json:"message"`
}

func webhookPayload(event AlertEvent) webhookEventPayload {
	payload := webhookEventPayload{
		Event: event.Status,
		Rule: webhookRule{
			ID:        event.RuleID,
			Name:      event.RuleName,
			Operator:  event.Operator,
			Threshold: event.Threshold,
			Severity:  event.Severity,
		},
		Node: webhookNode{
			ID:       event.NodeID,
			Hostname: event.NodeHostname,
		},
		Metric:    event.Metric,
		Series:    event.Series,
		Value:     event.Value,
		Threshold: event.Threshold,
		Severity:  event.Severity,
		Message:   AlertEventMessage(event),
	}
	if !event.StartedAt.IsZero() {
		payload.StartedAt = formatAlertTime(event.StartedAt)
	}
	if !event.ResolvedAt.IsZero() {
		payload.ResolvedAt = formatAlertTime(event.ResolvedAt)
	}
	return payload
}

func (n *AlertNotifier) postJSON(ctx context.Context, endpoint string, payload any, timeout time.Duration, responseMode string) error {
	if !validHTTPURL(endpoint) {
		return fmt.Errorf("%w: notification endpoint URL is invalid", ErrNotificationConfig)
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return errors.New("notification payload encoding failed")
	}
	requestContext, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	request, err := http.NewRequestWithContext(requestContext, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return errors.New("notification request creation failed")
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("User-Agent", "NodeSteer-Alert/1.0")
	response, err := n.client.Do(request)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return errors.New("notification request timed out")
		}
		if errors.Is(err, context.Canceled) {
			return errors.New("notification request canceled")
		}
		return errors.New("notification request failed")
	}
	defer response.Body.Close()
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, maxNotificationResponse))
	if err != nil {
		return errors.New("notification response read failed")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("notification endpoint returned status %d", response.StatusCode)
	}
	switch responseMode {
	case notificationResponseTelegram:
		var result struct {
			OK bool `json:"ok"`
		}
		if err := json.Unmarshal(responseBody, &result); err != nil || !result.OK {
			return errors.New("telegram API rejected the notification")
		}
	case notificationResponseProviderCode:
		// 钉钉/企业微信在 HTTP 200 下仍可能返回非零 errcode。
		if len(bytes.TrimSpace(responseBody)) > 0 {
			var result struct {
				ErrCode int `json:"errcode"`
			}
			if err := json.Unmarshal(responseBody, &result); err == nil && result.ErrCode != 0 {
				return errors.New("notification endpoint rejected the notification")
			}
		}
	}
	return nil
}

func validHTTPURL(value string) bool {
	if len(value) > 2048 || containsControlCharacters(value) {
		return false
	}
	parsed, err := url.Parse(value)
	if err != nil || parsed.Host == "" || parsed.User != nil {
		return false
	}
	return parsed.Scheme == "http" || parsed.Scheme == "https"
}

func emailRecipients(config ChannelConfig) ([]*mail.Address, error) {
	raw, err := environmentValue(config.ToEnv)
	if err != nil {
		return nil, err
	}
	parts := strings.Split(raw, ",")
	recipients := make([]*mail.Address, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		address, err := mail.ParseAddress(part)
		if err != nil {
			return nil, fmt.Errorf("%w: recipient address is invalid", ErrNotificationConfig)
		}
		recipients = append(recipients, address)
	}
	if len(recipients) == 0 {
		return nil, fmt.Errorf("%w: no email recipients configured", ErrNotificationConfig)
	}
	if len(recipients) > 32 {
		return nil, fmt.Errorf("%w: too many email recipients", ErrNotificationConfig)
	}
	return recipients, nil
}

func emailBody(event AlertEvent, recipients []*mail.Address) string {
	var builder strings.Builder
	builder.WriteString(AlertEventMessage(event))
	builder.WriteString("\r\n\r\n")
	builder.WriteString("rule: " + event.RuleName + " (" + event.RuleID + ")\r\n")
	builder.WriteString("node: " + event.NodeID + "\r\n")
	builder.WriteString("metric: " + event.Metric + "\r\n")
	if event.Series != "" {
		builder.WriteString("series: " + event.Series + "\r\n")
	}
	builder.WriteString(fmt.Sprintf("threshold: %s %g\r\n", event.Operator, event.Threshold))
	if event.Value != nil {
		builder.WriteString(fmt.Sprintf("value: %g\r\n", *event.Value))
	}
	builder.WriteString("status: " + event.Status + "\r\n")
	builder.WriteString("severity: " + event.Severity + "\r\n")
	if !event.StartedAt.IsZero() {
		builder.WriteString("started_at: " + formatAlertTime(event.StartedAt) + "\r\n")
	}
	if !event.ResolvedAt.IsZero() {
		builder.WriteString("resolved_at: " + formatAlertTime(event.ResolvedAt) + "\r\n")
	}
	if len(recipients) > 0 {
		builder.WriteString("recipients: " + strconv.Itoa(len(recipients)) + "\r\n")
	}
	return builder.String()
}

// emailHTMLBody 生成转义后的 HTML 正文；字段与纯文本版本保持一致。
func emailHTMLBody(event AlertEvent, recipients []*mail.Address) string {
	var builder strings.Builder
	builder.WriteString("<!doctype html><html><body>")
	builder.WriteString("<h2>NodeSteer alert</h2>")
	builder.WriteString("<p><strong>")
	builder.WriteString(html.EscapeString(AlertEventMessage(event)))
	builder.WriteString("</strong></p><table>")
	rows := [][2]string{
		{"rule", event.RuleName + " (" + event.RuleID + ")"},
		{"node", event.NodeID},
		{"metric", event.Metric},
	}
	if event.Series != "" {
		rows = append(rows, [2]string{"series", event.Series})
	}
	rows = append(rows,
		[2]string{"threshold", fmt.Sprintf("%s %g", event.Operator, event.Threshold)},
		[2]string{"status", event.Status},
		[2]string{"severity", event.Severity},
	)
	if event.Value != nil {
		rows = append(rows, [2]string{"value", fmt.Sprintf("%g", *event.Value)})
	}
	if !event.StartedAt.IsZero() {
		rows = append(rows, [2]string{"started_at", formatAlertTime(event.StartedAt)})
	}
	if !event.ResolvedAt.IsZero() {
		rows = append(rows, [2]string{"resolved_at", formatAlertTime(event.ResolvedAt)})
	}
	if len(recipients) > 0 {
		rows = append(rows, [2]string{"recipients", strconv.Itoa(len(recipients))})
	}
	for _, row := range rows {
		builder.WriteString("<tr><th align=\"left\">")
		builder.WriteString(html.EscapeString(row[0]))
		builder.WriteString("</th><td>")
		builder.WriteString(html.EscapeString(row[1]))
		builder.WriteString("</td></tr>")
	}
	builder.WriteString("</table></body></html>")
	return builder.String()
}

// emailAttachments 读取并校验邮件附件。任何配置或文件安全错误都直接失败，
// 不进入重试，也不在错误文本中回显绝对路径。
func emailAttachments(config ChannelConfig) ([]EmailAttachment, error) {
	if config.AttachmentsEnv == "" {
		return nil, nil
	}
	if config.AttachmentDir == "" {
		return nil, fmt.Errorf("%w: attachment_dir is not configured", ErrNotificationConfig)
	}
	raw, err := environmentValue(config.AttachmentsEnv)
	if err != nil {
		return nil, err
	}
	paths := make([]string, 0, maxEmailAttachments)
	for _, part := range strings.Split(raw, ",") {
		part = strings.TrimSpace(part)
		if part != "" {
			paths = append(paths, part)
		}
	}
	if len(paths) == 0 {
		return nil, nil
	}
	if len(paths) > maxEmailAttachments {
		return nil, fmt.Errorf("%w: too many email attachments", ErrNotificationConfig)
	}
	base := filepath.Clean(config.AttachmentDir)
	if !filepath.IsAbs(base) {
		return nil, fmt.Errorf("%w: attachment directory is not allowed", ErrNotificationConfig)
	}
	baseResolved, err := filepath.EvalSymlinks(base)
	if err != nil {
		return nil, fmt.Errorf("%w: attachment directory is not available", ErrNotificationConfig)
	}
	info, err := os.Stat(baseResolved)
	if err != nil || !info.IsDir() {
		return nil, fmt.Errorf("%w: attachment directory is not available", ErrNotificationConfig)
	}
	attachments := make([]EmailAttachment, 0, len(paths))
	total := 0
	for _, rawPath := range paths {
		attachment, size, err := loadEmailAttachment(base, baseResolved, rawPath)
		if err != nil {
			return nil, err
		}
		total += size
		if total > maxEmailAttachmentTotalBytes {
			return nil, fmt.Errorf("%w: email attachments are too large", ErrNotificationConfig)
		}
		attachments = append(attachments, attachment)
	}
	return attachments, nil
}

func loadEmailAttachment(base, baseResolved, rawPath string) (EmailAttachment, int, error) {
	var attachment EmailAttachment
	if len(rawPath) > 4096 || containsControlCharacters(rawPath) || !filepath.IsAbs(rawPath) {
		return attachment, 0, fmt.Errorf("%w: email attachment is not allowed", ErrNotificationConfig)
	}
	clean := filepath.Clean(rawPath)
	relative, err := filepath.Rel(base, clean)
	if err != nil || relative == "." || relative == ".." ||
		strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
		return attachment, 0, fmt.Errorf("%w: email attachment is not allowed", ErrNotificationConfig)
	}
	current := baseResolved
	for _, part := range strings.Split(relative, string(filepath.Separator)) {
		if part == "" || part == "." || part == ".." {
			return attachment, 0, fmt.Errorf("%w: email attachment is not allowed", ErrNotificationConfig)
		}
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if err != nil {
			return attachment, 0, fmt.Errorf("%w: email attachment is not available", ErrNotificationConfig)
		}
		// 显式拒绝符号链接，避免通过链接读取 attachment_dir 之外的文件。
		if info.Mode()&os.ModeSymlink != 0 {
			return attachment, 0, fmt.Errorf("%w: email attachment is not allowed", ErrNotificationConfig)
		}
	}
	file, err := os.Open(current)
	if err != nil {
		return attachment, 0, fmt.Errorf("%w: email attachment is not available", ErrNotificationConfig)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return attachment, 0, fmt.Errorf("%w: email attachment is not allowed", ErrNotificationConfig)
	}
	if info.Size() > maxEmailAttachmentBytes {
		return attachment, 0, fmt.Errorf("%w: email attachment is too large", ErrNotificationConfig)
	}
	data, err := io.ReadAll(io.LimitReader(file, maxEmailAttachmentBytes+1))
	if err != nil {
		return attachment, 0, fmt.Errorf("%w: email attachment could not be read", ErrNotificationConfig)
	}
	if len(data) > maxEmailAttachmentBytes {
		return attachment, 0, fmt.Errorf("%w: email attachment is too large", ErrNotificationConfig)
	}
	filename := filepath.Base(clean)
	if filename == "" || filename == "." || filename == string(filepath.Separator) {
		return attachment, 0, fmt.Errorf("%w: email attachment is not allowed", ErrNotificationConfig)
	}
	contentType := mime.TypeByExtension(filepath.Ext(filename))
	if parsed, _, err := mime.ParseMediaType(contentType); err == nil {
		contentType = parsed
	}
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	return EmailAttachment{Filename: filename, ContentType: contentType, Data: data}, len(data), nil
}

func sendEmailSMTP(ctx context.Context, channel NotificationChannel, message MailMessage) error {
	config := channel.Config
	from, err := mail.ParseAddress(config.From)
	if err != nil {
		return fmt.Errorf("%w: smtp from address is invalid", ErrNotificationConfig)
	}
	recipients, err := emailRecipients(config)
	if err != nil {
		return err
	}
	password := ""
	if config.PasswordEnv != "" {
		password, err = environmentValue(config.PasswordEnv)
		if err != nil {
			return err
		}
	}
	timeout := notificationTimeout(config)
	address := net.JoinHostPort(config.SMTPHost, strconv.Itoa(config.SMTPPort))
	tlsConfig := &tls.Config{ServerName: config.SMTPHost, MinVersion: tls.VersionTLS12}
	mode := config.TLSMode
	if mode == "" {
		mode = "starttls"
	}

	var connection net.Conn
	dialer := &net.Dialer{Timeout: timeout}
	switch mode {
	case "tls":
		connection, err = tls.DialWithDialer(dialer, "tcp", address, tlsConfig)
	case "starttls", "none":
		connection, err = dialer.DialContext(ctx, "tcp", address)
	default:
		return errors.New("invalid smtp TLS mode")
	}
	if err != nil {
		return errors.New("smtp connection failed")
	}
	defer connection.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = connection.SetDeadline(deadline)
	} else {
		_ = connection.SetDeadline(time.Now().Add(timeout))
	}

	client, err := smtp.NewClient(connection, config.SMTPHost)
	if err != nil {
		return errors.New("smtp handshake failed")
	}
	defer client.Close()
	if mode == "starttls" {
		if err := client.StartTLS(tlsConfig); err != nil {
			return errors.New("smtp starttls failed")
		}
	}
	if config.Username != "" {
		auth := smtp.PlainAuth("", config.Username, password, config.SMTPHost)
		if err := client.Auth(auth); err != nil {
			return errors.New("smtp authentication failed")
		}
	}
	if err := client.Mail(from.Address); err != nil {
		return errors.New("smtp sender rejected")
	}
	for _, recipient := range recipients {
		if err := client.Rcpt(recipient.Address); err != nil {
			return errors.New("smtp recipient rejected")
		}
	}
	writer, err := client.Data()
	if err != nil {
		return errors.New("smtp data command failed")
	}
	raw := buildEmailMessage(from, recipients, message, time.Now().UTC())
	if _, err := writer.Write([]byte(raw)); err != nil {
		_ = writer.Close()
		return errors.New("smtp message write failed")
	}
	if err := writer.Close(); err != nil {
		return errors.New("smtp message close failed")
	}
	if err := client.Quit(); err != nil {
		return errors.New("smtp quit failed")
	}
	return nil
}

func buildEmailMessage(from *mail.Address, recipients []*mail.Address, message MailMessage, now time.Time) string {
	subject := strings.ReplaceAll(strings.ReplaceAll(message.Subject, "\r", " "), "\n", " ")
	var builder strings.Builder
	builder.WriteString("From: " + from.String() + "\r\n")
	addresses := make([]string, 0, len(recipients))
	for _, recipient := range recipients {
		addresses = append(addresses, recipient.String())
	}
	builder.WriteString("To: " + strings.Join(addresses, ", ") + "\r\n")
	builder.WriteString("Subject: " + subject + "\r\n")
	builder.WriteString("Date: " + now.Format(time.RFC1123Z) + "\r\n")
	builder.WriteString("MIME-Version: 1.0\r\n")

	if len(message.Attachments) == 0 {
		if message.HTML != "" {
			builder.WriteString("Content-Type: text/html; charset=UTF-8\r\n")
			builder.WriteString("\r\n")
			builder.WriteString(message.HTML)
			return builder.String()
		}
		builder.WriteString("Content-Type: text/plain; charset=UTF-8\r\n")
		builder.WriteString("\r\n")
		builder.WriteString(message.Text)
		return builder.String()
	}

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	_ = writer.SetBoundary("nodesteer-mixed-boundary")
	if message.HTML != "" {
		alternative, err := writer.CreatePart(textproto.MIMEHeader{
			"Content-Type": {"multipart/alternative; boundary=\"nodesteer-alt-boundary\""},
		})
		if err == nil {
			altWriter := multipart.NewWriter(alternative)
			_ = altWriter.SetBoundary("nodesteer-alt-boundary")
			writeEmailTextPart(altWriter, "text/plain; charset=UTF-8", message.Text)
			writeEmailTextPart(altWriter, "text/html; charset=UTF-8", message.HTML)
			_ = altWriter.Close()
		}
	} else {
		writeEmailTextPart(writer, "text/plain; charset=UTF-8", message.Text)
	}
	for _, attachment := range message.Attachments {
		header := textproto.MIMEHeader{}
		contentType := mime.FormatMediaType(attachment.ContentType, map[string]string{"name": attachment.Filename})
		if contentType == "" {
			contentType = "application/octet-stream"
		}
		header.Set("Content-Type", contentType)
		disposition := mime.FormatMediaType("attachment", map[string]string{"filename": attachment.Filename})
		if disposition == "" {
			disposition = "attachment"
		}
		header.Set("Content-Disposition", disposition)
		header.Set("Content-Transfer-Encoding", "base64")
		part, err := writer.CreatePart(header)
		if err != nil {
			continue
		}
		writeBase64Lines(part, attachment.Data)
	}
	_ = writer.Close()

	builder.WriteString("Content-Type: multipart/mixed; boundary=\"" + writer.Boundary() + "\"\r\n")
	builder.WriteString("\r\n")
	builder.Write(body.Bytes())
	return builder.String()
}

func writeEmailTextPart(writer *multipart.Writer, contentType, body string) {
	header := textproto.MIMEHeader{}
	header.Set("Content-Type", contentType)
	header.Set("Content-Transfer-Encoding", "8bit")
	part, err := writer.CreatePart(header)
	if err != nil {
		return
	}
	_, _ = part.Write([]byte(body))
}

func writeBase64Lines(writer io.Writer, data []byte) {
	encoded := base64.StdEncoding.EncodeToString(data)
	for len(encoded) > 76 {
		_, _ = io.WriteString(writer, encoded[:76]+"\r\n")
		encoded = encoded[76:]
	}
	if encoded != "" {
		_, _ = io.WriteString(writer, encoded+"\r\n")
	}
}

func normalizeNotificationError(err error) string {
	if err == nil {
		return ""
	}
	message := strings.TrimSpace(err.Error())
	if message == "" {
		return "notification failed"
	}
	// 只保留归一化后的业务文本；驱动/传输层原文可能包含 URL、token 或响应体。
	lower := strings.ToLower(message)
	for _, marker := range []string{"http://", "https://", "sql:", "sqlite", "token", "password", "authorization", "bot", "secret"} {
		if strings.Contains(lower, marker) {
			return "notification failed"
		}
	}
	runes := []rune(message)
	if len(runes) > 256 {
		message = string(runes[:256]) + "..."
	}
	return message
}
