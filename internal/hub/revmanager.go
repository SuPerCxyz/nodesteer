package hub

import (
	"context"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// RevisionManager 管理全局与对象 Revision
type RevisionManager struct {
	store store.Store
}

// NewRevisionManager 创建 Revision 管理器
func NewRevisionManager(st store.Store) *RevisionManager {
	return &RevisionManager{store: st}
}

// Next 分配下一个全局 Revision
func (rm *RevisionManager) Next(ctx context.Context) (int64, error) {
	return rm.store.NextGlobalRevision(ctx)
}

// Current 当前全局 Revision
func (rm *RevisionManager) Current(ctx context.Context) (int64, error) {
	return rm.store.CurrentGlobalRevision(ctx)
}

// RecordChange 记录变更到 Change Log
func (rm *RevisionManager) RecordChange(ctx context.Context, globalRev int64, objectType, objectID string, objectRev int64, operation string) error {
	return rm.store.AppendChangeLog(ctx, globalRev, objectType, objectID, objectRev, operation)
}
