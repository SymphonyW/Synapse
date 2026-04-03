package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/agent"
	"github.com/synapse/synapse/services/gateway-go/internal/api"
	"github.com/synapse/synapse/services/gateway-go/internal/config"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
	"github.com/synapse/synapse/services/gateway-go/internal/worker"
)

// main 负责组装网关依赖，并启动 HTTP 服务与后台任务处理器。
func main() {
	// 启动时一次性加载运行配置。
	cfg := config.Load()

	// 网关通过 gRPC 连接 AI 引擎；若连接失败直接退出，避免进入不可用状态。
	agentClient, err := agent.NewGRPCClient(cfg.AIEngineAddr)
	if err != nil {
		log.Fatalf("failed to connect to AI engine: %v", err)
	}
	defer agentClient.Close()

	// appCtx 统一控制后台循环（worker）和优雅退出流程。
	appCtx, stopApp := context.WithCancel(context.Background())
	defer stopApp()

	// 任务存储默认使用内存实现；若 Postgres 可用则升级为持久化存储。
	taskStore := store.TaskStore(store.NewInMemory())

	if cfg.DatabaseURL != "" {
		// 数据库初始化设置超时，避免启动阶段无限阻塞。
		dbCtx, cancel := context.WithTimeout(context.Background(), cfg.DatabaseConnectTimeout)
		postgresStore, err := store.NewPostgres(dbCtx, cfg.DatabaseURL)
		cancel()
		if err != nil {
			// 软降级到内存存储，保证本地/开发环境仍可运行。
			log.Printf("failed to initialize postgres store, fallback to in-memory: %v", err)
		} else {
			defer postgresStore.Close()
			taskStore = postgresStore
			log.Printf("task store backend=postgres")
		}
	}

	// 队列同样采用“内存默认、Redis 优先”的可用性策略。
	taskQueue := queue.TaskQueue(queue.NewInMemoryQueue(1024))
	if cfg.RedisAddr != "" {
		// Redis 初始化超时设置较短，优先保障启动响应速度。
		redisCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		redisQueue, err := queue.NewRedisQueue(redisCtx, cfg.RedisAddr, cfg.RedisPassword, cfg.RedisDB, cfg.TaskQueueName)
		cancel()
		if err != nil {
			// 回退到内存队列，保证开发场景下功能可用。
			log.Printf("failed to initialize redis queue, fallback to in-memory: %v", err)
		} else {
			taskQueue = redisQueue
			log.Printf("task queue backend=redis")
		}
	}
	defer taskQueue.Close()

	// 处理器负责“出队 -> 执行 -> 重试/死信”完整流程。
	processor := worker.NewTaskProcessor(taskStore, taskQueue, agentClient, worker.ProcessorOptions{
		ExecutionTimeout: cfg.TaskExecutionTimeout,
		MaxAttempts:      cfg.TaskMaxAttempts,
		RetryBackoff:     cfg.TaskRetryBackoff,
	})
	go processor.Run(appCtx)

	// HTTP Handler 依赖任务存储、队列和处理器取消能力。
	handler := api.NewHandler(taskStore, agentClient, taskQueue, processor)

	srv := &http.Server{
		Addr:         cfg.HTTPAddr,
		Handler:      api.NewRouter(handler),
		ReadTimeout:  cfg.ReadTimeout,
		WriteTimeout: cfg.WriteTimeout,
		IdleTimeout:  60 * time.Second,
	}

	// 在后台启动 HTTP 服务，主协程用于等待系统退出信号。
	go func() {
		log.Printf("gateway listening on %s", cfg.HTTPAddr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("gateway crashed: %v", err)
		}
	}()

	// 等待退出信号，再级联通知后台任务停止。
	stopCh := make(chan os.Signal, 1)
	signal.Notify(stopCh, syscall.SIGINT, syscall.SIGTERM)
	<-stopCh
	stopApp()

	// 给进行中的请求留出有限优雅退出时间。
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Printf("graceful shutdown failed: %v", err)
	}
}
