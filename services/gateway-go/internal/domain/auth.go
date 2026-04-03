package domain

import "time"

// UserRole 表示用户权限角色。
type UserRole string

const (
	// UserRoleAdmin 拥有运维能力。
	UserRoleAdmin UserRole = "admin"
	// UserRoleUser 为普通用户。
	UserRoleUser UserRole = "user"
)

// AuthUser 是认证用户主记录。
type AuthUser struct {
	Username     string    `json:"username"`
	PasswordHash string    `json:"-"`
	Role         UserRole  `json:"role"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

// AuthSession 表示登录会话。
type AuthSession struct {
	Token     string    `json:"token"`
	Username  string    `json:"username"`
	Role      UserRole  `json:"role"`
	ExpiresAt time.Time `json:"expires_at"`
	CreatedAt time.Time `json:"created_at"`
}
