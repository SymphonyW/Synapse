# Gateway 总览

## 1. 模块职责

Gateway 是系统控制平面核心，负责：

1. 对外暴露 HTTP API。
2. 执行认证与权限控制。
3. 管理任务状态与事件持久化。
4. 调度 Worker 消费队列并调用 AI Engine。
5. 向前端提供 SSE 增量事件流。

## 2. 关键入口

1. 进程入口： [services/gateway-go/cmd/server/main.go](../services/gateway-go/cmd/server/main.go)
2. 路由装配： [services/gateway-go/internal/api/router.go](../services/gateway-go/internal/api/router.go)
3. 任务处理： [services/gateway-go/internal/worker/processor.go](../services/gateway-go/internal/worker/processor.go)

## 3. 子模块映射

1. config：环境变量读取与默认值
2. api：HTTP 处理器、认证接口、SSE
3. domain：任务与认证领域模型
4. queue：队列抽象与实现（内存/Redis）
5. store：存储抽象与实现（内存/Postgres）
6. worker：队列消费、重试、取消、死信
7. agent：gRPC client，屏蔽 AI Engine 调用细节

## 4. 启动流程

1. 读取配置。
2. 建立 gRPC 到 AI Engine 的连接。
3. 初始化 TaskStore（优先 Postgres）。
4. 初始化 TaskQueue（优先 Redis）。
5. upsert 管理员账号。
6. 启动 Worker goroutine。
7. 启动 HTTP 服务并监听系统信号优雅关闭。

## 5. 对外能力

1. 认证：注册、登录、登出、当前用户。
2. 任务：创建、查询、取消、批量取消、重放。
3. 事件：按 taskID SSE 订阅（支持游标续传）。
4. 运维：死信查询。
5. 健康：联动 AI Engine 健康检查。

## 6. 关键工程特性

1. 软降级：依赖不可用时回退内存实现。
2. 幂等取消：已取消任务再次取消返回 200。
3. 权限边界：普通用户仅可访问自己的任务。
4. 可观测性：统一请求日志与任务事件留痕。
