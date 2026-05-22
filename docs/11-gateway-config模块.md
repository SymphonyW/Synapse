# Gateway Config 模块

## 1. 模块定位

Config 模块负责将环境变量映射为 Gateway 运行期配置对象，并提供默认值与基础容错。

实现文件： [services/gateway-go/internal/config/config.go](../services/gateway-go/internal/config/config.go)

## 2. 核心结构

Config 字段覆盖：

1. HTTP 地址与超时
2. AI Engine 地址
3. 数据库连接与超时
4. Redis 地址、密码、DB
5. 任务队列名
6. Worker 重试参数
7. 管理员账号初始配置

## 3. 环境变量清单

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| SYNAPSE_HTTP_ADDR | :8080 | Gateway 监听地址 |
| SYNAPSE_AI_ENGINE_ADDR | 127.0.0.1:50051 | AI Engine gRPC 地址 |
| SYNAPSE_DATABASE_URL | 空 | Postgres 连接串，空则不启用持久化存储 |
| SYNAPSE_DB_CONNECT_TIMEOUT | 5s | 数据库初始化超时 |
| SYNAPSE_REDIS_ADDR | 空 | Redis 地址，空则不启用 Redis 队列 |
| SYNAPSE_REDIS_PASSWORD | 空 | Redis 密码 |
| SYNAPSE_REDIS_DB | 0 | Redis DB 库号 |
| SYNAPSE_TASK_QUEUE | synapse:tasks | 队列名称 |
| SYNAPSE_TASK_MAX_ATTEMPTS | 3 | 最大重试次数 |
| SYNAPSE_TASK_RETRY_BACKOFF | 2s | 重试间隔 |
| SYNAPSE_TASK_EXEC_TIMEOUT | 120s | 单任务执行超时 |
| SYNAPSE_HTTP_READ_TIMEOUT | 15s | HTTP 读超时 |
| SYNAPSE_HTTP_WRITE_TIMEOUT | 60s | HTTP 写超时 |
| SYNAPSE_AUTH_ADMIN_USERNAME | admin | 管理员用户名 |
| SYNAPSE_AUTH_ADMIN_PASSWORD | 123456 | 管理员初始密码（必须在生产覆盖） |

## 4. 解析策略

1. 字符串：空值回退默认值。
2. int：解析失败回退默认值。
3. duration：time.ParseDuration 失败回退默认值。

## 5. 风险与建议

1. 默认管理员密码仅用于开发；生产必须强制覆盖。
2. 建议增加启动日志打印“关键配置摘要（脱敏）”。
3. 建议将配置校验与默认值策略抽象为独立 validator。
