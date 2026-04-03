# 配置说明

本文档覆盖当前实现中可生效的全部配置项。

## 1. 配置加载规则

### 1.1 Gateway（Go）

- 入口：`services/gateway-go/internal/config/config.go`
- 行为：读取环境变量；若解析失败（例如 duration/int 格式错误）则回退默认值。

### 1.2 AI Engine（Python）

- 入口：`services/ai-engine-py/app/config.py`
- 行为：读取环境变量；数值型解析失败时回退默认值。

### 1.3 Docker Compose

- 入口：`docker-compose.yml`
- 行为：使用 `${VAR:-default}` 形式提供默认值。

## 2. Gateway 环境变量

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| `SYNAPSE_HTTP_ADDR` | `:8080` | 网关监听地址 |
| `SYNAPSE_AI_ENGINE_ADDR` | `127.0.0.1:50051` | AI 引擎 gRPC 地址 |
| `SYNAPSE_DATABASE_URL` | 空 | PostgreSQL DSN，空表示禁用持久化 |
| `SYNAPSE_DB_CONNECT_TIMEOUT` | `5s` | 数据库初始化超时 |
| `SYNAPSE_REDIS_ADDR` | 空 | Redis 地址，空表示内存队列 |
| `SYNAPSE_REDIS_PASSWORD` | 空 | Redis 密码 |
| `SYNAPSE_REDIS_DB` | `0` | Redis DB 索引 |
| `SYNAPSE_TASK_QUEUE` | `synapse:tasks` | Redis 列表名 |
| `SYNAPSE_TASK_MAX_ATTEMPTS` | `3` | 最大重试次数 |
| `SYNAPSE_TASK_RETRY_BACKOFF` | `2s` | 固定重试间隔 |
| `SYNAPSE_TASK_EXEC_TIMEOUT` | `120s` | 单次执行超时 |
| `SYNAPSE_HTTP_READ_TIMEOUT` | `15s` | HTTP 读超时 |
| `SYNAPSE_HTTP_WRITE_TIMEOUT` | `60s` | HTTP 写超时 |

## 3. AI Engine 环境变量

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| `SYNAPSE_AI_BIND_ADDR` | `0.0.0.0:50051` | gRPC 监听地址 |
| `SYNAPSE_MODEL_PROVIDER` | `mock` | Provider：`mock` 或 `openai` |
| `SYNAPSE_MODEL_PROVIDER_ALIAS` | 空 | `/healthz` 展示别名 |
| `SYNAPSE_OPENAI_API_KEY` | 空 | `openai` 模式必填 |
| `SYNAPSE_OPENAI_BASE_URL` | 空 | OpenAI 兼容网关基地址；空时使用官方地址 |
| `SYNAPSE_OPENAI_MODEL` | `gpt-4o-mini` | 模型名称 |
| `SYNAPSE_OPENAI_TEMPERATURE` | `0.2` | 温度 |
| `SYNAPSE_OPENAI_MAX_TOKENS` | `512` | 最大输出 token |
| `SYNAPSE_OPENAI_HTTP_TIMEOUT_SECONDS` | `45` | HTTP 超时秒数 |
| `SYNAPSE_OPENAI_MAX_RETRIES` | `3` | 上游请求重试次数 |
| `SYNAPSE_OPENAI_RETRY_BACKOFF_SECONDS` | `1.5` | 线性退避基数（秒） |

运行时补充规则：

- `SYNAPSE_MODEL_PROVIDER=zhipu` 或 `gemini` 时，会自动映射到 `openai` 通道，并把展示名设置为对应别名。
- `SYNAPSE_OPENAI_HTTP_TIMEOUT_SECONDS` 最小值会被钳制到 `5.0`。
- `SYNAPSE_OPENAI_MAX_RETRIES` 最小值会被钳制到 `1`。
- `SYNAPSE_OPENAI_RETRY_BACKOFF_SECONDS` 最小值会被钳制到 `0.2`。

## 4. Docker Compose 变量

### 4.1 构建镜像参数

| 变量名 | 默认值 | 用途 |
| --- | --- | --- |
| `PYTHON_BASE` | `docker.io/library/python:3.12-slim` | AI 引擎基础镜像 |
| `GOLANG_BASE` | `docker.io/library/golang:1.25` | Gateway 构建镜像 |
| `RUNTIME_BASE` | `gcr.io/distroless/static-debian12:latest` | Gateway 运行镜像 |

### 4.2 运行镜像参数

| 变量名 | 默认值 | 用途 |
| --- | --- | --- |
| `POSTGRES_IMAGE` | `docker.io/library/postgres:16-alpine` | PostgreSQL 镜像 |
| `REDIS_IMAGE` | `docker.io/library/redis:7-alpine` | Redis 镜像 |

## 5. 提供方配置文件

仓库内提供以下模板：

- `docker-compose.openai.env.example`
- `docker-compose.gemini.env.example`
- `docker-compose.zhipu.env.example`
- `docker-compose.mirror.env`

建议做法：

1. 从 `*.example` 复制到实际文件（例如 `docker-compose.openai.env`）。
2. 填写真实密钥。
3. 通过 `scripts/dev.ps1` 对应任务启动。

## 6. 常用场景配置

### 6.1 纯本地开发（无 Redis/Postgres）

- 不设置 `SYNAPSE_DATABASE_URL`
- 不设置 `SYNAPSE_REDIS_ADDR`
- 使用内存存储和内存队列

### 6.2 完整联调（Redis + Postgres）

- 通过 Compose 启动全栈，或手动设置：

```text
SYNAPSE_DATABASE_URL=postgres://synapse:synapse@127.0.0.1:5432/synapse?sslmode=disable
SYNAPSE_REDIS_ADDR=127.0.0.1:6379
```

### 6.3 OpenAI 兼容提供方

OpenAI：

```text
SYNAPSE_MODEL_PROVIDER=openai
SYNAPSE_OPENAI_API_KEY=<your-key>
```

Gemini（兼容接口）：

```text
SYNAPSE_MODEL_PROVIDER=openai
SYNAPSE_MODEL_PROVIDER_ALIAS=gemini
SYNAPSE_OPENAI_API_KEY=<your-gemini-key>
SYNAPSE_OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
SYNAPSE_OPENAI_MODEL=gemini-2.0-flash
```

Zhipu（兼容接口）：

```text
SYNAPSE_MODEL_PROVIDER=openai
SYNAPSE_MODEL_PROVIDER_ALIAS=zhipu
SYNAPSE_OPENAI_API_KEY=<your-zhipu-key>
SYNAPSE_OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
SYNAPSE_OPENAI_MODEL=glm-4-flash
```

## 7. 安全建议

- 不要把真实 API Key 提交到仓库。
- 建议使用本地未跟踪文件或 Secret 管理系统注入凭据。
- 生产环境避免将数据库、Redis 直接暴露公网。
