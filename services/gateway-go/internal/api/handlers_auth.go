package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
	"golang.org/x/crypto/bcrypt"
)

const (
	authSessionCookieName = "synapse_session_token"
	authSessionTTL        = 24 * time.Hour
	authMinUsernameLength = 3
	authMinPasswordLength = 6
)

type registerRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type loginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type authUserResponse struct {
	Username string          `json:"username"`
	Role     domain.UserRole `json:"role"`
}

type loginResponse struct {
	User      authUserResponse `json:"user"`
	ExpiresAt time.Time        `json:"expires_at"`
}

// RegisterUser 注册普通用户，密码仅以哈希形式存储。
func (h *Handler) RegisterUser(w http.ResponseWriter, r *http.Request) {
	var request registerRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	username := normalizeAuthUsername(request.Username)
	password := request.Password
	if len(username) < authMinUsernameLength {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "username must be at least 3 characters"})
		return
	}
	if len(password) < authMinPasswordLength {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "password must be at least 6 characters"})
		return
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to hash password"})
		return
	}

	err = h.store.CreateUser(domain.AuthUser{
		Username:     username,
		PasswordHash: string(hash),
		Role:         domain.UserRoleUser,
	})
	if err != nil {
		switch {
		case err == store.ErrUserAlreadyExists:
			writeJSON(w, http.StatusConflict, map[string]string{"error": "username already exists"})
		default:
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to create user"})
		}
		return
	}

	writeJSON(w, http.StatusCreated, map[string]any{
		"user": authUserResponse{
			Username: username,
			Role:     domain.UserRoleUser,
		},
	})
}

// LoginUser 验证用户名和密码并创建会话 Cookie。
func (h *Handler) LoginUser(w http.ResponseWriter, r *http.Request) {
	var request loginRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	username := normalizeAuthUsername(request.Username)
	password := request.Password
	if username == "" || strings.TrimSpace(password) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "username and password are required"})
		return
	}

	user, found, err := h.store.GetUserByUsername(username)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to query user"})
		return
	}
	if !found || bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(password)) != nil {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid username or password"})
		return
	}

	now := time.Now().UTC()
	session := domain.AuthSession{
		Token:     uuid.NewString(),
		Username:  user.Username,
		Role:      user.Role,
		CreatedAt: now,
		ExpiresAt: now.Add(authSessionTTL),
	}

	if err := h.store.CreateSession(session); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to create session"})
		return
	}
	_ = h.store.DeleteExpiredSessions(now)

	setAuthCookie(w, session)
	writeJSON(w, http.StatusOK, loginResponse{
		User: authUserResponse{
			Username: session.Username,
			Role:     session.Role,
		},
		ExpiresAt: session.ExpiresAt,
	})
}

// LogoutUser 清除当前登录会话。
func (h *Handler) LogoutUser(w http.ResponseWriter, r *http.Request) {
	cookie, err := r.Cookie(authSessionCookieName)
	if err == nil {
		_ = h.store.DeleteSession(cookie.Value)
	}

	clearAuthCookie(w)
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// GetCurrentUser 返回当前登录身份。
func (h *Handler) GetCurrentUser(w http.ResponseWriter, r *http.Request) {
	session, ok := h.readSessionFromRequest(w, r)
	if !ok {
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"user": authUserResponse{
			Username: session.Username,
			Role:     session.Role,
		},
		"expires_at": session.ExpiresAt,
	})
}

func (h *Handler) readSessionFromRequest(w http.ResponseWriter, r *http.Request) (domain.AuthSession, bool) {
	cookie, err := r.Cookie(authSessionCookieName)
	if err != nil || strings.TrimSpace(cookie.Value) == "" {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return domain.AuthSession{}, false
	}

	session, found, err := h.store.GetSession(cookie.Value)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to query session"})
		return domain.AuthSession{}, false
	}
	if !found {
		clearAuthCookie(w)
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return domain.AuthSession{}, false
	}

	return session, true
}

func setAuthCookie(w http.ResponseWriter, session domain.AuthSession) {
	maxAge := int(time.Until(session.ExpiresAt).Seconds())
	if maxAge < 0 {
		maxAge = 0
	}

	http.SetCookie(w, &http.Cookie{
		Name:     authSessionCookieName,
		Value:    session.Token,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		Secure:   false,
		MaxAge:   maxAge,
		Expires:  session.ExpiresAt,
	})
}

func clearAuthCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     authSessionCookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		Secure:   false,
		MaxAge:   -1,
		Expires:  time.Unix(0, 0).UTC(),
	})
}

func normalizeAuthUsername(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}
