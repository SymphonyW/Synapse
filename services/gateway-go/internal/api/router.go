package api

import (
	"log"
	"net/http"
	"time"
)

// statusRecorder 在透传响应写入的同时记录状态码，供访问日志使用。
type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}

// Flush 保持对 SSE/流式接口的兼容性。
func (r *statusRecorder) Flush() {
	flusher, ok := r.ResponseWriter.(http.Flusher)
	if !ok {
		return
	}
	flusher.Flush()
}

// NewRouter 注册网关对外暴露的全部 HTTP 路由。
func NewRouter(handler *Handler) http.Handler {
	mux := http.NewServeMux()
	// 健康检查与任务相关 API。
	mux.HandleFunc("GET /healthz", handler.Healthz)
	mux.HandleFunc("POST /v1/auth/register", handler.RegisterUser)
	mux.HandleFunc("POST /v1/auth/login", handler.LoginUser)
	mux.HandleFunc("POST /v1/auth/logout", handler.LogoutUser)
	mux.HandleFunc("GET /v1/auth/me", handler.GetCurrentUser)
	mux.HandleFunc("GET /v1/memories", handler.ListMemories)
	mux.HandleFunc("POST /v1/memories", handler.WriteMemory)
	mux.HandleFunc("GET /v1/memories/recall", handler.RecallMemory)
	mux.HandleFunc("DELETE /v1/memories/{memoryID}", handler.DeleteMemory)
	mux.HandleFunc("GET /v1/tasks", handler.ListTasks)
	mux.HandleFunc("POST /v1/tasks", handler.CreateTask)
	mux.HandleFunc("DELETE /v1/conversations/{conversationID}", handler.DeleteConversation)
	mux.HandleFunc("POST /v1/tasks/cancel", handler.BatchCancelTasks)
	mux.HandleFunc("GET /v1/dead-letters", handler.ListDeadLetters)
	mux.HandleFunc("GET /v1/admin/tool-policy", handler.GetToolPolicy)
	mux.HandleFunc("PUT /v1/admin/tool-policy", handler.PutToolPolicy)
	mux.HandleFunc("POST /v1/admin/tool-policy/reload", handler.ReloadToolPolicy)
	mux.HandleFunc("GET /v1/admin/tools", handler.ListAdminTools)
	mux.HandleFunc("GET /v1/tasks/{taskID}", handler.GetTask)
	mux.HandleFunc("GET /v1/tasks/{taskID}/replays", handler.ListTaskReplays)
	mux.HandleFunc("GET /v1/tasks/{taskID}/compare/{otherTaskID}", handler.CompareReplayTasks)
	mux.HandleFunc("POST /v1/tasks/{taskID}/cancel", handler.CancelTask)
	mux.HandleFunc("POST /v1/tasks/{taskID}/approve", handler.ApproveTask)
	mux.HandleFunc("POST /v1/tasks/{taskID}/replay", handler.ReplayTask)
	mux.HandleFunc("GET /v1/tasks/{taskID}/events", handler.StreamTaskEvents)

	return requestLogMiddleware(mux)
}

// requestLogMiddleware 为每个请求输出一条结构化日志，提供基础可观测性。
func requestLogMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		start := time.Now()

		next.ServeHTTP(recorder, r)

		log.Printf("method=%s path=%s status=%d duration_ms=%d", r.Method, r.URL.Path, recorder.status, time.Since(start).Milliseconds())
	})
}
