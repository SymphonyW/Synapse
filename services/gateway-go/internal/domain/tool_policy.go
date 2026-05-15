package domain

import "time"

// ToolPolicy 是 Gateway 持久化的管理员策略快照。
type ToolPolicy struct {
	RoleAllow        map[string][]string `json:"role_allow"`
	ApprovalRequired []string            `json:"approval_required"`
	DisabledTools    []string            `json:"disabled_tools"`
	Version          int64               `json:"version"`
	UpdatedAt        time.Time           `json:"updated_at"`
	UpdatedBy        string              `json:"updated_by"`
	Description      string              `json:"description,omitempty"`
}
