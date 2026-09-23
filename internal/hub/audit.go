package hub

import (
	"context"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
)

type auditContextKey struct{}

// WithAuditInfo 将当前用户信息传入领域服务，使变更审计发生在通知之前。
func WithAuditInfo(ctx context.Context, userID, username string) context.Context {
	return context.WithValue(ctx, auditContextKey{}, models.AuditLog{UserID: userID, Username: username})
}

func recordMutationAudit(ctx context.Context, st store.Store, resource, resourceID, action string) error {
	return recordMutationAuditDetail(ctx, st, resource, resourceID, action, "")
}

// recordMutationAuditDetail 与 recordMutationAudit 相同，但写入可读 detail（如对象名称）。
func recordMutationAuditDetail(ctx context.Context, st store.Store, resource, resourceID, action, detail string) error {
	a, ok := ctx.Value(auditContextKey{}).(models.AuditLog)
	if !ok {
		return nil
	}
	a.Action = action
	a.Resource = resource
	a.ResourceID = resourceID
	a.Detail = detail
	return st.AddAudit(ctx, &a)
}
