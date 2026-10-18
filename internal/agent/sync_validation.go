package agent

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func validateSyncScripts(p *protocol.SyncResponsePayload) error {
	for _, entry := range p.Scripts {
		if entry.Deleted {
			continue
		}
		var script models.Script
		if err := json.Unmarshal(entry.Data, &script); err != nil {
			return fmt.Errorf("decode script %s: %w", entry.ID, err)
		}
		sum := sha256.Sum256([]byte(script.Content))
		if script.SHA256 != hex.EncodeToString(sum[:]) {
			return fmt.Errorf("script %s sha256 mismatch", entry.ID)
		}
	}
	return nil
}

// validRemoteState 远程状态新鲜度判定。
// 只依据 Hub 计算下发的 TTLRemainingSec（自 Hub 判定时刻起的剩余秒数，向上取整），
// 不做任何跨主机时钟比较，从根本上消除 Hub/Agent 时钟偏移误判。
// Value 为空/UNKNOWN，或 TTLRemainingSec 缺失/<= 0（视为未知）一律 Fail Closed。
func validRemoteState(p protocol.RemoteStatePayload) bool {
	if p.Value == "" || p.Value == protocol.RemoteStateUnknown {
		return false
	}
	if p.TTLRemainingSec == nil || *p.TTLRemainingSec <= 0 {
		return false
	}
	return true
}
