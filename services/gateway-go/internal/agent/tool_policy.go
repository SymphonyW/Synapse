package agent

import (
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
)

func ToolPolicyToProto(policy domain.ToolPolicy) *agentv1.ToolPolicy {
	updatedAtUnixMS := int64(0)
	if !policy.UpdatedAt.IsZero() {
		updatedAtUnixMS = policy.UpdatedAt.UnixMilli()
	}

	roleAllow := make(map[string]*agentv1.StringList, len(policy.RoleAllow))
	for role, tools := range policy.RoleAllow {
		roleAllow[role] = &agentv1.StringList{Items: append([]string{}, tools...)}
	}

	return &agentv1.ToolPolicy{
		RoleAllow:        roleAllow,
		ApprovalRequired: append([]string{}, policy.ApprovalRequired...),
		DisabledTools:    append([]string{}, policy.DisabledTools...),
		Version:          policy.Version,
		UpdatedAtUnixMs:  updatedAtUnixMS,
		UpdatedBy:        policy.UpdatedBy,
		Description:      policy.Description,
	}
}

func ToolPolicyFromProto(policy *agentv1.ToolPolicy) domain.ToolPolicy {
	if policy == nil {
		return domain.ToolPolicy{}
	}

	roleAllow := make(map[string][]string, len(policy.GetRoleAllow()))
	for role, tools := range policy.GetRoleAllow() {
		roleAllow[role] = append([]string{}, tools.GetItems()...)
	}

	updatedAt := time.Time{}
	if policy.GetUpdatedAtUnixMs() > 0 {
		updatedAt = time.UnixMilli(policy.GetUpdatedAtUnixMs()).UTC()
	}

	return domain.ToolPolicy{
		RoleAllow:        roleAllow,
		ApprovalRequired: append([]string{}, policy.GetApprovalRequired()...),
		DisabledTools:    append([]string{}, policy.GetDisabledTools()...),
		Version:          policy.GetVersion(),
		UpdatedAt:        updatedAt,
		UpdatedBy:        policy.GetUpdatedBy(),
		Description:      policy.GetDescription(),
	}
}
