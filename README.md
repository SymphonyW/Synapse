# Synapse

> 一个可观测、可治理、可恢复的 Agent 运行平台。

Synapse 把任务编排、工具调用、审批暂停与恢复、长期记忆和回归评测收束到同一套控制面与执行面里。它不是“聊两句就结束”的聊天 Demo，也不是只能跑一次的脚本；它更像一个面向真实 Agent 工作流的控制层：任务会异步运行，事件会被保存，风险动作可以被暂停并恢复，开发者还能在 Web 中追踪、重放和评测它。

| 30 秒先看这 5 件事 | 为什么值得看 |
|---|---|
| 异步任务生命周期 | `queued / running / paused / completed / failed / canceled` 全链路可追踪 |
| 工具治理 | 角色授权、禁用、审批、审计统一收口，不让外部工具绕过边界 |
| 审批暂停与恢复 | 高风险调用先停住，再按精确工具调用恢复，而不是粗暴放行 |
| 长期记忆 | 既能本地零依赖运行，也能切到向量记忆做语义召回 |
| Trace + Regression | 既能看清一次任务怎么走，也能持续验证 Runtime 没退化 |

[5 分钟跑起来](#5-分钟跑起来) · [10 分钟看完整 Workflow](#10-分钟看完整-workflow) · [技术文档](doc/README.md) · [贡献指南](CONTRIBUTING.md)

![Synapse 总体架构图](doc/assets/architecture-overview.svg)

```mermaid
flowchart LR
    A[提交任务] --> B[持久化 + 入队]
    B --> C[Agent Runtime]
    C --> D{需要高风险工具?}
    D -->|否| E[流式返回 token]
    D -->|是| F[paused + 等待审批]
    F --> G[approve + resume]
    G --> C
    E --> H[SSE + Trace + Replay]
```

## 为什么不是普通 Agent Demo

| 普通 Demo 常见做法 | Synapse 的做法 |
|---|---|
| 一次请求、一次回答 | 任务异步执行，而且可以暂停、恢复、取消、重放 |
| 只看最终输出 | 事件先持久化，再通过 SSE 增量推送 |
| 高风险动作靠 prompt 约束 | 有工具策略、角色白名单、审批恢复和审计 |
| 失败就结束 | 有重试、死信和 replay |
| 每次都像第一次见面 | 有长期记忆和 recall |
| 手工冒烟测试 | 有 mock regression 和真实模型 benchmark |

## 10 分钟看完整 Workflow

| Demo | 你会看到什么 |
|---|---|
| [审批型浏览 Agent](doc/70-demo-审批型浏览Agent.md) | `approval_required -> paused -> approve -> resume` 的完整治理闭环 |
| [记忆型助手](doc/71-demo-记忆型助手.md) | 写入、召回、retrieval、回答复用同一条长期记忆链路 |
| [OpenAPI 工具 Agent](doc/72-demo-OpenAPI工具Agent.md) | 外部 API 如何被发现、治理、审批并执行 |

## 界面预览

当前仓库先提供规范化资产位，方便后续直接替换真实截图而不改 README 链接。

| Web 控制台 | Agent Trace 工作台 |
|---|---|
| <img src="doc/assets/web-console.png" alt="Web 控制台示例" width="420" /> | <img src="doc/assets/trace-workbench.png" alt="Agent Trace 工作台示例" width="420" /> |

| 审批暂停 | 记忆管理页 |
|---|---|
| <img src="doc/assets/approval-pause.png" alt="审批暂停示例" width="420" /> | <img src="doc/assets/memory-page.png" alt="记忆管理页示例" width="420" /> |

## 适合谁

- 想研究 Agent runtime，而不是只看聊天壳子的开发者；
- 想学习任务编排、SSE、审批恢复的后端开发者；
- 想做企业内部 Agent 控制层的人；
- 想基于 MCP / OpenAPI 扩展工具的人。

## 核心功能

| 功能模块 | 功能说明 | 实现位置 | 状态 |
|---|---|---|---|
| 用户认证 | 注册、登录、退出、当前用户查询，使用 HttpOnly Cookie Session 和 bcrypt 密码哈希 | [handlers_auth.go](services/gateway-go/internal/api/handlers_auth.go) | 已完成 |
| 任务管理 | 创建任务、查询任务、按状态列出任务，任务状态包含 queued/running/paused/completed/failed/canceled | [handlers.go](services/gateway-go/internal/api/handlers.go) | 已完成 |
| 事件流 | 基于 SSE 输出任务增量事件，支持 `last_event_id` 续传和 terminal 终态事件 | [handlers.go](services/gateway-go/internal/api/handlers.go) | 已完成 |
| Worker 执行 | 从队列消费任务，调用 AI Engine gRPC stream，持久化 started/info/token/completed/failed 事件 | [processor.go](services/gateway-go/internal/worker/processor.go) | 已完成 |
| 取消能力 | 支持单任务取消、批量取消、重复取消幂等、终态冲突保护 | [handlers.go](services/gateway-go/internal/api/handlers.go) | 已完成 |
| 重试、死信与重放 | Worker 有界重试，失败耗尽后写入死信，可通过接口重放 | [processor.go](services/gateway-go/internal/worker/processor.go) | 已完成 |
| 审批暂停与恢复 | 高风险工具触发 `approval_required` 后任务进入 paused，审批后写入恢复元数据并重新入队 | [handlers.go](services/gateway-go/internal/api/handlers.go), [runtime.py](services/ai-engine-py/app/runtime.py) | 已完成 |
| 会话上下文 | Web 会话按 `conversation_id` 聚合，Gateway 从历史任务和事件构建 `model_messages_json` | [handlers.go](services/gateway-go/internal/api/handlers.go), [App.tsx](apps/web/src/App.tsx) | 已完成 |
| 会话删除 | 删除当前用户指定会话下的任务、事件和死信记录 | [handlers.go](services/gateway-go/internal/api/handlers.go) | 已完成 |
| 长期记忆 | Agent 自动写入和召回文件型记忆，Gateway 暴露记忆管理 API，Web 支持列表、召回测试、手工写入和删除 | [memory.py](services/ai-engine-py/app/memory.py), [handlers_memory.go](services/gateway-go/internal/api/handlers_memory.go), [features/memory](apps/web/src/features/memory) | 已完成 |
| 工具治理 | 内置 retrieval/calculator/browser/http/code/json/browser 操作工具，支持角色授权、审批、禁用、审计和 Web 管理中心 | [tools](services/ai-engine-py/app/tools), [ToolPolicyPanel.tsx](apps/web/src/features/tool-policy/ToolPolicyPanel.tsx) | 已完成 |
| 工具扩展 | 支持 LocalClass provider、OpenAPI HTTP executor 和 MCP stdio adapter provider 接入 | [providers.py](services/ai-engine-py/app/tools/providers.py), [openapi_executor.py](services/ai-engine-py/app/tools/openapi_executor.py), [mcp_stdio.py](services/ai-engine-py/app/tools/mcp_stdio.py) | 部分完成 |
| Web 控制台 | 用户聊天视图、长期记忆管理视图、运维视图、工具策略页、任务列表、审批恢复、死信重放、Agent Trace 工作台 | [App.tsx](apps/web/src/App.tsx), [features/memory](apps/web/src/features/memory), [features/tool-policy](apps/web/src/features/tool-policy), [features/trace](apps/web/src/features/trace) | 已完成 |
| Agent 回归评测 | 覆盖工具、浏览、记忆、审批、失败恢复等 mock 回归用例 | [regression.py](services/ai-engine-py/app/benchmarks/regression.py) | 已完成 |
| 真实模型 Agent Benchmark | 对比不同真实 provider 在同一套 Agent Runtime 下的质量、延迟、工具、审批与记忆表现 | [live_benchmark.py](services/ai-engine-py/app/benchmarks/live_benchmark.py) | 已完成 |

## 技术栈

| 分类 | 技术 |
|---|---|
| 后端语言 | Go 1.25.0 |
| Gateway 框架 | Go 标准库 `net/http` |
| AI Engine 语言 | Python 3.12 |
| AI Engine 通信 | `grpcio` / `grpcio-tools` 1.76.0 |
| 前端 | React 19.2.4、TypeScript 5.9.3、Vite 8.0.1 |
| 协议 | Protocol Buffers proto3、gRPC streaming、HTTP、SSE |
| 数据库 | PostgreSQL 16 alpine，可回退内存存储 |
| 缓存/队列 | Redis 7 alpine list 队列，可回退内存队列 |
| 数据访问 | Go `database/sql` + `github.com/lib/pq`，无 ORM |
| 鉴权方式 | Cookie Session，HttpOnly，SameSite=Lax，`Secure=false` |
| 模型接入 | Mock provider、OpenAI-compatible `/chat/completions`，使用 Python 标准库 `urllib` |
| 部署方式 | Dockerfile、Docker Compose |
| 测试工具 | Go test、Python unittest、Agent regression、Live benchmark、前端 Vitest / React Testing Library / ESLint / TypeScript / Vite build |
| 接口文档 | 暂无 Swagger/OpenAPI/Apifox/Postman，待补充 |

## 系统架构

项目是一个前后端分离的模块化多服务工程。Gateway 是控制面，AI Engine 是执行面，Web 是交互面，Postgres 和 Redis 是可选的持久化/队列基础设施。

```mermaid
flowchart TD
    Browser[Web Console / Browser] -->|HTTP JSON| Router[Gateway Router]
    Browser -->|SSE EventSource| SSE[Task Event Stream]
    Router --> Auth[Auth and Permission]
    Auth --> Handler[API Handler]
    Handler --> Store[(TaskStore)]
    Handler --> Queue[(TaskQueue)]
    Handler --> Worker[Task Worker]
    Worker --> Queue
    Worker --> Store
    Worker -->|gRPC SubmitTask stream| AI[AI Engine Runtime]
    Handler -->|gRPC Memory RPC| AI
    AI --> Model[Mock or OpenAI-compatible Provider]
    AI --> Memory[(File or Vector Memory Store)]
    AI --> Audit[(Tool Audit Log)]
    Store --> Postgres[(PostgreSQL)]
    Queue --> Redis[(Redis List)]
```

请求链路：

1. Web 调用 Gateway HTTP API，认证依赖 `synapse_session_token` Cookie。
2. Gateway 通过 Handler 校验会话、角色和资源归属。
3. 创建任务时 Gateway 写入 TaskStore，并将 task id 放入 TaskQueue。
4. Worker 从队列取任务，调用 AI Engine `SubmitTask` gRPC 流。
5. AI Engine 输出 `started/info/token/completed/failed` 事件。
6. Gateway 持久化事件，并通过 `/v1/tasks/{taskID}/events` SSE 增量输出。
7. 如果 AI Engine 输出 `approval_required`，Worker 将任务切到 `paused`，等待 `/approve` 恢复。
8. 长期记忆默认仍由 AI Engine 文件后端保存，也可通过配置切换到向量后端；Gateway 的 `/v1/memories` API 继续转发同一套 gRPC Memory RPC。

架构优点：

1. 控制面和模型执行面解耦，Gateway 不直接依赖模型 SDK。
2. gRPC 内部强类型协议和浏览器 SSE 外部协议各司其职。
3. Postgres/Redis 不可用时可回退内存实现，便于本地启动。
4. 任务事件持久化后再输出，支持断线续传和运维审计。

当前限制：

1. Compose 已包含 Web 服务；默认可一键拉起前后端，也仍可在 `apps/web` 本地执行 `npm run dev` 做开发调试。
2. 数据库由启动时自动建表，没有版本化 migration。
3. Redis 队列使用 List + BRPop，没有 ack/reclaim 语义。
4. 生产安全能力不足，缺少 HTTPS、Cookie Secure、CSRF、防爆破、Secret Manager 和 CI/CD。

## 目录结构

| 路径 | 说明 |
|---|---|
| [apps/web](apps/web) | React + Vite Web 控制台 |
| [services/gateway-go](services/gateway-go) | Go Gateway，包含 HTTP API、Worker、队列、存储、认证 |
| [services/ai-engine-py](services/ai-engine-py) | Python AI Engine，包含 gRPC 服务、Runtime、工具、记忆、评测 |
| [proto/synapse/v1/agent.proto](proto/synapse/v1/agent.proto) | Gateway 和 AI Engine 共享的 gRPC/proto 契约 |
| [scripts/dev.ps1](scripts/dev.ps1) | Windows PowerShell 开发脚本入口 |
| [scripts/post_gen.py](scripts/post_gen.py) | Python proto 生成后补齐 `__init__.py` |
| [docker-compose.yml](docker-compose.yml) | gateway、ai-engine、postgres、redis 本地编排 |
| [docker-compose.*.env.example](.) | OpenAI/Gemini/智谱/镜像源配置模板 |
| [Makefile](Makefile) | 类 Unix 环境常用命令入口 |
| [doc](doc) | 模块级和功能级技术文档 |

## 环境准备

| 依赖 | 用途 | 要求 |
|---|---|---|
| Docker Desktop / Docker Compose v2 | 启动 gateway、ai-engine、postgres、redis | 推荐 |
| PowerShell | 使用 `scripts/dev.ps1` | Windows 推荐 |
| Go | 本地运行 Gateway 和测试 | `go.mod` 声明 1.25.0 |
| Python | 本地运行 AI Engine 和测试 | Dockerfile 使用 3.12 |
| Node.js + npm | 本地运行 Web | `package.json` 未声明 engines，版本待确认，建议使用当前 LTS |
| protoc | 重新生成 proto 代码 | 仅修改 proto 时需要 |
| protoc-gen-go / protoc-gen-go-grpc | 生成 Go proto 代码 | 仅修改 proto 时需要 |

安装 Go proto 插件：

```powershell
go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1
```

## 环境变量

Gateway 环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SYNAPSE_HTTP_ADDR` | `:8080` | Gateway HTTP 监听地址 |
| `SYNAPSE_AI_ENGINE_ADDR` | `127.0.0.1:50051` | AI Engine gRPC 地址 |
| `SYNAPSE_DATABASE_URL` | 空 | 配置后启用 Postgres |
| `SYNAPSE_DB_CONNECT_TIMEOUT` | `5s` | 数据库连接超时 |
| `SYNAPSE_REDIS_ADDR` | 空 | 配置后启用 Redis 队列 |
| `SYNAPSE_REDIS_PASSWORD` | 空 | Redis 密码 |
| `SYNAPSE_REDIS_DB` | `0` | Redis DB |
| `SYNAPSE_TASK_QUEUE` | `synapse:tasks` | Redis list 名称 |
| `SYNAPSE_TASK_MAX_ATTEMPTS` | `3` | Worker 最大尝试次数 |
| `SYNAPSE_TASK_RETRY_BACKOFF` | `2s` | Worker 重试退避 |
| `SYNAPSE_TASK_EXEC_TIMEOUT` | `120s` | 单任务执行超时，Compose 默认为 `300s` 以支持长文本 |
| `SYNAPSE_HTTP_READ_TIMEOUT` | `15s` | HTTP 读超时 |
| `SYNAPSE_HTTP_WRITE_TIMEOUT` | `60s` | HTTP 写超时，Compose 默认为 `300s` 以支持长 SSE |
| `SYNAPSE_AUTH_ADMIN_USERNAME` | `admin` | 启动时 upsert 的管理员账号 |
| `SYNAPSE_AUTH_ADMIN_PASSWORD` | `123456` | 管理员密码，生产必须覆盖 |

AI Engine 环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SYNAPSE_AI_BIND_ADDR` | `0.0.0.0:50051` | gRPC 监听地址 |
| `SYNAPSE_MODEL_PROVIDER` | `mock` | `mock` 或 `openai`，`gemini`/`zhipu` 会归一到 openai 路径 |
| `SYNAPSE_MODEL_PROVIDER_ALIAS` | 空 | 健康检查展示别名 |
| `SYNAPSE_OPENAI_API_KEY` | 空 | openai 模式必填 |
| `SYNAPSE_OPENAI_BASE_URL` | 空 | 默认使用 `https://api.openai.com/v1` |
| `SYNAPSE_OPENAI_MODEL` | `gpt-4o-mini` | 模型名 |
| `SYNAPSE_OPENAI_TEMPERATURE` | `0.2` | 温度 |
| `SYNAPSE_OPENAI_MAX_TOKENS` | `512` | 最大输出 token |
| `SYNAPSE_OPENAI_HTTP_TIMEOUT_SECONDS` | `45` | 模型 HTTP 超时 |
| `SYNAPSE_OPENAI_MAX_RETRIES` | `3` | 模型请求最大重试 |
| `SYNAPSE_OPENAI_RETRY_BACKOFF_SECONDS` | `1.5` | 模型请求退避 |
| `SYNAPSE_OPENAI_CONTINUATION_MAX_ROUNDS` | `8` | 长文本/截断输出的最大自动续写轮数 |
| `SYNAPSE_OPENAI_LONG_FORM_MIN_CHARS` | `2400` | 长文本请求在接受完成标记前的最低字符预算 |
| `SYNAPSE_AGENT_ENABLED_DEFAULT` | `true` | 默认启用 Agent loop |
| `SYNAPSE_AGENT_MAX_PLAN_STEPS` | `6` | 最大计划步数 |
| `SYNAPSE_AGENT_GENERATION_TIMEOUT_SECONDS` | `30` | Agent 最终回复首 token 等待超时 |
| `SYNAPSE_AGENT_STREAM_IDLE_TIMEOUT_SECONDS` | `15` | 模型流式输出空闲超时 |
| `SYNAPSE_AGENT_REQUIRE_APPROVAL_FOR_HIGH_RISK` | `true` | 高风险工具默认需要审批 |
| `SYNAPSE_MEMORY_BACKEND` | `file` | 长期记忆后端：`file` 或 `vector` |
| `SYNAPSE_AGENT_MEMORY_FILE` | `/tmp/synapse-agent-memory.json` | 文件型长期记忆路径 |
| `SYNAPSE_AGENT_MEMORY_MAX_ENTRIES_PER_USER` | `80` | 每用户最大记忆条数 |
| `SYNAPSE_AGENT_MEMORY_RECALL_LIMIT` | `3` | 每次召回条数 |
| `SYNAPSE_VECTOR_DATABASE_URL` | 空 | `vector` 后端使用的 PostgreSQL 连接串 |
| `SYNAPSE_VECTOR_EMBEDDING_PROVIDER` | 空 | embedding provider，当前支持 `openai_compatible` |
| `SYNAPSE_VECTOR_EMBEDDING_MODEL` | 空 | embedding 模型名 |
| `SYNAPSE_VECTOR_EMBEDDING_BASE_URL` | 空 | OpenAI-compatible embeddings base URL |
| `SYNAPSE_VECTOR_EMBEDDING_API_KEY` | 空 | embeddings API key |
| `SYNAPSE_VECTOR_EMBEDDING_DIMENSION` | `0` | 向量维度；启用 `vector` 时必须显式配置 |
| `SYNAPSE_VECTOR_MEMORY_TOP_K` | `10` | pgvector 初筛候选数 |
| `SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST` | 空 | HTTP/浏览工具允许访问的域名列表，逗号分隔 |
| `SYNAPSE_AGENT_TOOL_HTTP_TIMEOUT_SECONDS` | `12` | HTTP/浏览工具超时 |
| `SYNAPSE_AGENT_ENABLE_CODE_EXECUTION` | `false` | 是否启用 code_exec |
| `SYNAPSE_AGENT_TOOL_POLICY_JSON` | 空 | AI Engine 启动默认策略；若存在 Gateway 持久化策略，则运行时由管理中心策略覆盖 |
| `SYNAPSE_AGENT_TOOL_AUDIT_LOG_FILE` | `/tmp/synapse-agent-tool-audit.log` | 工具审计日志 |
| `SYNAPSE_OPENAPI_ENABLED` | `false` | 是否启用 OpenAPI 工具 provider |
| `SYNAPSE_OPENAPI_SPEC_FILE` | 空 | OpenAPI spec 文件路径，启用时必填；JSON 内置支持，YAML 需运行环境提供 PyYAML |
| `SYNAPSE_OPENAPI_BASE_URL_OVERRIDE` | 空 | 覆盖 spec `servers[0].url` |
| `SYNAPSE_OPENAPI_STATIC_HEADERS_JSON` | `{}` | OpenAPI 调用默认 headers，JSON 对象 |
| `SYNAPSE_OPENAPI_BEARER_TOKEN` | 空 | 注入 `Authorization: Bearer <token>` |
| `SYNAPSE_OPENAPI_API_KEY_HEADER` | 空 | API Key header 名称 |
| `SYNAPSE_OPENAPI_API_KEY_VALUE` | 空 | API Key header 值 |
| `SYNAPSE_OPENAPI_HTTP_TIMEOUT_SECONDS` | `12` | OpenAPI HTTP 调用超时 |
| `SYNAPSE_OPENAPI_MAX_RESPONSE_BYTES` | `65536` | OpenAPI 响应最大读取字节数，超限返回结构化失败和截断预览 |
| `SYNAPSE_OPENAPI_ALLOWED_SCHEMES` | `http,https` | OpenAPI 允许的 URL scheme |
| `SYNAPSE_MCP_STDIO_ENABLED` | `false` | 是否启用 MCP stdio 工具接入 |
| `SYNAPSE_MCP_STDIO_COMMAND` | 空 | MCP Server 启动命令，启用 MCP 时必填 |
| `SYNAPSE_MCP_STDIO_ARGS_JSON` | `[]` | MCP Server 命令行参数，JSON 字符串数组 |
| `SYNAPSE_MCP_STDIO_ENV_JSON` | `{}` | 传给 MCP Server 的额外环境变量，JSON 对象 |
| `SYNAPSE_MCP_STDIO_WORKDIR` | 空 | MCP Server 工作目录 |
| `SYNAPSE_MCP_STDIO_TIMEOUT_SECONDS` | `10` | MCP initialize、tools/list、tools/call 超时 |
| `SYNAPSE_MCP_TOOL_NAME_PREFIX` | `mcp` | MCP 工具注册到 Synapse 后的本地名称前缀 |

OpenAPI provider 最小配置示例：

```powershell
$env:SYNAPSE_OPENAPI_ENABLED = "true"
$env:SYNAPSE_OPENAPI_SPEC_FILE = "D:\\apis\\demo-openapi.json"
$env:SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST = "api.example.com"
$env:SYNAPSE_OPENAPI_BEARER_TOKEN = "replace-with-secret"
$env:SYNAPSE_OPENAPI_HTTP_TIMEOUT_SECONDS = "8"
$env:SYNAPSE_OPENAPI_MAX_RESPONSE_BYTES = "32768"
```

最小 OpenAPI JSON：

```json
{
  "openapi": "3.0.3",
  "servers": [{"url": "https://api.example.com"}],
  "paths": {
    "/items/{item_id}": {
      "get": {
        "operationId": "getItem",
        "parameters": [
          {"name": "item_id", "in": "path", "required": true, "schema": {"type": "string"}},
          {"name": "verbose", "in": "query", "schema": {"type": "boolean"}}
        ]
      }
    },
    "/items": {
      "post": {
        "operationId": "createItem",
        "requestBody": {
          "required": true,
          "content": {"application/json": {"schema": {"type": "object"}}}
        }
      }
    }
  }
}
```

等价 YAML 形态也可以用于说明 spec；当前运行时内置 JSON 解析，`.yaml/.yml` 文件需要运行环境提供 PyYAML：

```yaml
openapi: 3.0.3
servers:
  - url: https://api.example.com
paths:
  /items/{item_id}:
    get:
      operationId: getItem
      parameters:
        - name: item_id
          in: path
          required: true
          schema: {type: string}
```

OpenAPI 工具默认注册为 `openapi_<operationId>`；GET 会把 `path/query/header` 参数从 `ToolCall.arguments` 组装进请求，POST/PUT/PATCH 可通过 `body` 发送 JSON request body。`servers[0].url` 或 `SYNAPSE_OPENAPI_BASE_URL_OVERRIDE` 的主机必须命中 `SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST`，跨域 redirect 会被拒绝。POST/PUT/PATCH/DELETE 默认是高风险工具，仍会进入 `approval_required`，审批恢复继续使用 `approved_tool_call`。生产环境不要把 Bearer token 或 API Key 硬编码进 spec；使用环境变量或 Secret Manager 注入。

MCP stdio 最小配置示例：

```powershell
$env:SYNAPSE_MCP_STDIO_ENABLED = "true"
$env:SYNAPSE_MCP_STDIO_COMMAND = "python"
$env:SYNAPSE_MCP_STDIO_ARGS_JSON = '["-m","my_mcp_server"]'
$env:SYNAPSE_MCP_STDIO_ENV_JSON = '{"MCP_TOKEN":"dev-token"}'
$env:SYNAPSE_MCP_STDIO_WORKDIR = "D:\\mcp-server"
$env:SYNAPSE_MCP_STDIO_TIMEOUT_SECONDS = "10"
$env:SYNAPSE_MCP_TOOL_NAME_PREFIX = "mcp"
```

启用后，AI Engine 启动时会通过 `StdioMCPAdapter` 完成 MCP `initialize` 和 `tools/list`，再由 `MCPToolProvider` 把远端工具注册为 `mcp_<tool>`。Agent 选择工具后，Runtime 仍按现有链路执行 `ToolRegistry -> ToolPolicy -> approval -> ToolAuditLogger`，高风险或声明 `requires_approval=true` 的 MCP 工具会触发 `approval_required` 暂停；审批恢复继续使用 `approved_tool_call` 精确匹配。当前只实现 stdio transport，HTTP/SSE MCP transport 后续可作为新的 adapter 接入同一个 provider 抽象。

Provider 模板：

| 文件 | 用途 |
|---|---|
| [docker-compose.openai.env.example](docker-compose.openai.env.example) | OpenAI |
| [docker-compose.gemini.env.example](docker-compose.gemini.env.example) | Gemini OpenAI-compatible |
| [docker-compose.zhipu.env.example](docker-compose.zhipu.env.example) | 智谱 OpenAI-compatible |
| [docker-compose.vector.env.example](docker-compose.vector.env.example) | 向量长期记忆 |
| [docker-compose.mirror.env.example](docker-compose.mirror.env.example) | Docker 镜像源覆盖 |

## 数据库初始化

当前没有 SQL migration 目录，也没有独立初始化脚本。Postgres 初始化由 Gateway 启动时自动完成：

1. `services/gateway-go/internal/store/postgres.go` 中 `ensureSchema` 执行 `CREATE TABLE IF NOT EXISTS`。
2. 创建表：`tasks`、`task_events`、`dead_letter_tasks`、`auth_users`、`auth_sessions`、`tool_policies`。
3. 创建索引：任务事件索引、会话任务索引、会话用户与过期时间索引。
4. Gateway 启动后自动 upsert 管理员账号。

AI Engine 的 `vector` 记忆后端当前也采用轻量 schema ensure：启动时会执行 `CREATE EXTENSION IF NOT EXISTS vector`，并自动确保 `vector_memories` 表与索引存在。它是第一版工程化落点，后续应与 Gateway 一起迁移到版本化 migration。

Docker Compose 默认数据库连接：

```text
postgres://synapse:synapse@postgres:5432/synapse?sslmode=disable
```

本地 Gateway 连接 Docker Postgres 时使用：

```powershell
$env:SYNAPSE_DATABASE_URL = "postgres://synapse:synapse@127.0.0.1:15432/synapse?sslmode=disable"
$env:SYNAPSE_REDIS_ADDR = "127.0.0.1:6379"
```

生产建议：将自动建表迁移为版本化 migration，并在发布流程中显式执行。

如果要启用语义长期记忆，可复制 [docker-compose.vector.env.example](docker-compose.vector.env.example) 为 `docker-compose.vector.env`，填入 embedding 凭据后执行：

```powershell
docker compose --env-file docker-compose.vector.env up --build -d
```

默认 Compose 仍使用 `file` backend；Postgres 镜像已切到自带 pgvector 扩展的版本，因此 mock 模式与 vector 模式都能直接拉起。

## 5 分钟跑起来

### 1. 先用 Mock 模式跑通全链路

这条路径最短，不需要 API Key，并且会一次拉起 `gateway`、`ai-engine`、`postgres`、`redis` 和生产模式 `web`：

```powershell
.\scripts\dev.ps1 -Task up
# 或
docker compose up --build -d
```

打开：

| 服务 | 地址 |
|---|---|
| Web | http://127.0.0.1:5173 |
| Gateway API | http://127.0.0.1:8080 |

登录默认管理员 `admin / 123456`，即可直接开始跑任务、看 SSE、切 Trace、做审批恢复。

### 2. 再切真实模型

Mock 看懂链路后，再接真实 provider：

```powershell
Copy-Item docker-compose.openai.env.example docker-compose.openai.env
# 编辑 docker-compose.openai.env，填写 SYNAPSE_OPENAI_API_KEY
.\scripts\dev.ps1 -Task up-openai
```

Gemini、智谱也有对应模板：

```powershell
Copy-Item docker-compose.gemini.env.example docker-compose.gemini.env
.\scripts\dev.ps1 -Task up-gemini

Copy-Item docker-compose.zhipu.env.example docker-compose.zhipu.env
.\scripts\dev.ps1 -Task up-zhipu
```

如果你想在容器里直接使用前端开发模式：

```powershell
docker compose --profile web-dev up --build web-dev
```

### 3. 需要本地联调时，再分组件启动

```powershell
cd apps/web
npm install
cd ..\..

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r services/ai-engine-py/requirements.txt

docker compose up -d postgres redis

$env:SYNAPSE_DATABASE_URL = "postgres://synapse:synapse@127.0.0.1:15432/synapse?sslmode=disable"
$env:SYNAPSE_REDIS_ADDR = "127.0.0.1:6379"
$env:SYNAPSE_AI_ENGINE_ADDR = "127.0.0.1:50051"

# 三个终端分别执行
.\scripts\dev.ps1 -Task ai
.\scripts\dev.ps1 -Task gateway
.\scripts\dev.ps1 -Task web
```

停止整套服务：

```powershell
.\scripts\dev.ps1 -Task down
```

更完整的部署说明见 [doc/02-部署与启动.md](doc/02-部署与启动.md)。

## 验证运行成功

以下命令假设 Gateway 已在 `127.0.0.1:8080` 运行。

```powershell
$base = "http://127.0.0.1:8080"
curl.exe -s "$base/healthz"
```

预期返回包含：

```json
{"status":"ok","ai_engine":"ok","model_provider":"mock"}
```

登录默认管理员：

```powershell
curl.exe -s -c .\synapse.cookies `
  -H "Content-Type: application/json" `
  -d '{"username":"admin","password":"123456"}' `
  "$base/v1/auth/login"
```

创建任务：

```powershell
$response = curl.exe -s -b .\synapse.cookies `
  -H "Content-Type: application/json" `
  -d '{"prompt":"hello synapse, summarize current runtime","metadata":{"client_view":"chat","agent_enabled":"true","memory_write_enabled":"true"}}' `
  "$base/v1/tasks"

$task = $response | ConvertFrom-Json
$task.id
```

查询任务：

```powershell
curl.exe -s -b .\synapse.cookies "$base/v1/tasks/$($task.id)"
```

查看 SSE：

```powershell
curl.exe -N -b .\synapse.cookies "$base/v1/tasks/$($task.id)/events"
```

验证长期记忆 API：

```powershell
curl.exe -s -b .\synapse.cookies "$base/v1/memories?limit=5"
curl.exe -s -b .\synapse.cookies "$base/v1/memories/recall?query=synapse&limit=3"
```

Web 验证：

1. 打开 http://127.0.0.1:5173。
2. 使用 `admin` / `123456` 登录。
3. 在用户视图创建任务，确认聊天流有 token 输出。
4. 进入记忆页，确认可以刷新列表、手工写入一条记忆、执行 recall 并看到 score、删除该记忆。
5. 管理员在记忆页输入目标 `user_id` 后可查询指定用户记忆；普通用户不会看到该输入控件。
6. 切到运维视图，确认任务列表、Agent Trace 工作台、取消、死信列表等面板可见。
7. 在 Trace 工作台中切换“结构化 Trace / 原始事件 JSON”，并导出当前任务 JSON。
8. 切到“工具策略”页，确认可以查看工具目录、切换禁用/审批、编辑 `user` / `admin` 白名单并保存。

## HTTP 接口

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| GET | `/healthz` | Gateway 通过 gRPC 检查 AI Engine | 否 |
| POST | `/v1/auth/register` | 注册普通用户 | 否 |
| POST | `/v1/auth/login` | 登录并写入 Cookie | 否 |
| POST | `/v1/auth/logout` | 退出登录 | 可选 |
| GET | `/v1/auth/me` | 查询当前用户 | 是 |
| GET | `/v1/tasks` | 查询任务列表，支持 `limit`、`status` | 是 |
| POST | `/v1/tasks` | 创建任务 | 是 |
| GET | `/v1/tasks/{taskID}` | 查询单个任务 | 是 |
| GET | `/v1/tasks/{taskID}/events` | SSE 事件流，支持 `last_event_id` | 是 |
| POST | `/v1/tasks/{taskID}/cancel` | 取消单任务 | 是 |
| POST | `/v1/tasks/cancel` | 批量取消 | 是 |
| POST | `/v1/tasks/{taskID}/approve` | 审批 paused 任务并恢复 | 是 |
| POST | `/v1/tasks/{taskID}/replay` | 重放非 running 任务 | 是 |
| DELETE | `/v1/conversations/{conversationID}` | 删除当前用户会话下所有任务 | 是 |
| GET | `/v1/dead-letters` | 查询死信任务 | 管理员 |
| GET | `/v1/admin/tool-policy` | 查询当前工具策略 | 管理员 |
| PUT | `/v1/admin/tool-policy` | 全量更新工具策略并热应用 | 管理员 |
| POST | `/v1/admin/tool-policy/reload` | 重新下发已保存工具策略 | 管理员 |
| GET | `/v1/admin/tools` | 查询当前工具目录和有效策略状态 | 管理员 |
| GET | `/v1/memories` | 查询长期记忆，支持 `limit`、管理员 `user_id` | 是 |
| POST | `/v1/memories` | 手工写入长期记忆 | 是 |
| GET | `/v1/memories/recall` | 召回长期记忆，支持 `query`、`limit`、管理员 `user_id` | 是 |
| DELETE | `/v1/memories/{memoryID}` | 删除长期记忆，管理员可带 `user_id` | 是 |

更多字段和示例见 [doc/05-接口验证手册.md](doc/05-接口验证手册.md)。

## gRPC 协议

协议定义在 [proto/synapse/v1/agent.proto](proto/synapse/v1/agent.proto)。

| RPC | 说明 |
|---|---|
| `Health` | 返回 AI Engine 运行状态和模型 provider |
| `SubmitTask` | Gateway 提交任务，AI Engine 流式返回 AgentEvent |
| `MemoryWrite` | 写入长期记忆 |
| `MemoryRecall` | 召回长期记忆 |
| `MemoryDelete` | 删除长期记忆 |
| `MemoryList` | 列出长期记忆 |
| `GetToolPolicy` | 读取 AI Engine 当前有效工具策略 |
| `ApplyToolPolicy` | 把 Gateway 持久化策略热应用到 AI Engine |
| `ListTools` | 列出工具目录及当前有效治理状态 |

`SubmitTask` 事件类型：

1. `AGENT_EVENT_TYPE_STARTED`
2. `AGENT_EVENT_TYPE_TOKEN`
3. `AGENT_EVENT_TYPE_INFO`
4. `AGENT_EVENT_TYPE_COMPLETED`
5. `AGENT_EVENT_TYPE_FAILED`

Gateway 会将这些事件归一为小写字符串并持久化。`paused`、`approval_granted`、`resume_requested`、`terminal` 等事件由 Gateway 派生。

## 开发命令

PowerShell：

```powershell
.\scripts\dev.ps1 -Task proto
.\scripts\dev.ps1 -Task proto-go
.\scripts\dev.ps1 -Task proto-py
.\scripts\dev.ps1 -Task ai
.\scripts\dev.ps1 -Task gateway
.\scripts\dev.ps1 -Task web
.\scripts\dev.ps1 -Task agent-regression
.\scripts\dev.ps1 -Task verify-agent-mode
.\scripts\dev.ps1 -Task up
.\scripts\dev.ps1 -Task up-mirror
.\scripts\dev.ps1 -Task up-openai
.\scripts\dev.ps1 -Task up-openai-mirror
.\scripts\dev.ps1 -Task up-gemini
.\scripts\dev.ps1 -Task up-gemini-mirror
.\scripts\dev.ps1 -Task up-zhipu
.\scripts\dev.ps1 -Task up-zhipu-mirror
.\scripts\dev.ps1 -Task down
```

Makefile：

```bash
make proto
make gateway
make ai
make web
make agent-regression
make up
make down
```

## 测试

Go 单元测试：

```powershell
Set-Location services/gateway-go
go test ./...
Set-Location ..\..
```

Python 单元测试：

```powershell
Set-Location services/ai-engine-py
python -m unittest discover -s tests -p "test_*.py"
Set-Location ..\..
```

Agent 回归评测：

```powershell
Set-Location services/ai-engine-py
python -m app.benchmarks.regression
Set-Location ..\..
```

真实模型 Agent Benchmark：

```powershell
Set-Location services/ai-engine-py
python -m app.benchmarks.live_benchmark --provider openai --markdown
python -m app.benchmarks.live_benchmark --providers openai,zhipu --output live-benchmark-output
Set-Location ..\..
```

如果需要先检查 provider 配置而不真正发起模型请求：

```powershell
Set-Location services/ai-engine-py
python -m app.benchmarks.live_benchmark --provider openai --dry-run-config-check
Set-Location ..\..
```

前端检查：

```powershell
Set-Location apps/web
npm run lint
npm run build
npm run test
Set-Location ..\..
```

本次真实模型 benchmark 更新已验证：

| 命令 | 结果 |
|---|---|
| `python -m unittest discover -s tests -p "test_*.py"` | 89 个测试通过 |
| `python -m app.benchmarks.regression` | 12/12 通过 |
| `python -m app.benchmarks.live_benchmark --provider openai --dry-run-config-check` | 配置校验可运行；无 key 时返回明确失败 |
| `go test ./...` | 本次未运行，未修改 Go 代码 |
| `npm run build` | 本次未运行，未修改 Web 代码 |

## 部署说明

当前仓库只提供 Docker Compose 本地编排：

```powershell
docker compose up --build -d
```

镜像构建：

1. Gateway Dockerfile 会安装 protoc 和 Go proto 插件，构建静态二进制 `/gateway`。
2. AI Engine Dockerfile 会安装 Python requirements，生成 Python proto 代码，并以 `python -m app.main` 启动。
3. Web 通过 [apps/web/Dockerfile](apps/web/Dockerfile) 提供 `dev` / `production` 两个镜像目标，并由 Compose 默认以生产静态模式启动。

生产部署待确认项：

1. Web 静态产物托管方式。
2. Gateway 和 AI Engine 的服务发现、横向扩缩容和健康检查策略。
3. Postgres migration 和备份恢复策略。
4. Redis 队列可靠投递语义。
5. HTTPS、Cookie Secure、CSRF、防爆破、Secret 管理。
6. CI/CD、镜像扫描、发布版本和回滚流程。

## 工程亮点

| 设计 | 价值 |
|---|---|
| gRPC stream + SSE | 内部强类型流式协议，外部浏览器原生消费 |
| 事件持久化 | 支持断线续传、终态判断和运维审计 |
| 存储/队列回退 | 本地开发不依赖完整基础设施 |
| 精确工具审批 | `approved_tool_call` 匹配工具名、输入、风险和恢复步点 |
| 双记忆后端 | 默认保留 `FileMemoryStore` 的轻量行为，按配置切换到 `PostgresVectorMemoryStore` 获得语义召回 |
| ToolProvider 抽象 | 为本地 Python 工具、OpenAPI 工具和 MCP adapter 预留统一扩展入口 |
| Agent regression | 用稳定 mock case 做工具、审批、记忆和失败恢复门禁 |
| Live benchmark | 用真实 provider 对照同一套 Agent Runtime，输出 provider / case / tag 三级报告 |

## Roadmap

| 方向 | 状态 | 说明 |
|---|---|---|
| MCP transport | 部分完成 | 已支持 stdio；HTTP/SSE transport 待扩展 |
| OpenAPI executor | 已完成 | 已支持受控 HTTP 执行、allowlist、鉴权、超时和响应限制 |
| Trace workbench | 已完成 | 已有结构化 Trace、导出和 Replay Diff |
| policy center | 已完成 | 已有管理员策略页、热更新和持久化策略 |
| vector memory | 已完成 | 已支持 `file` / `vector` 双后端与 pgvector 召回 |
| benchmark | 已完成 | 已有 mock regression 与真实 provider benchmark |
| replay diff | 已完成 | 已支持 replay 子任务与结构化 diff |

下一批更像“生产化底座”而不是功能补洞：数据库 migration、可靠队列、HTTPS/CSRF/密钥管理、OpenAPI 复杂参数、MCP 远程 transport，以及更完整的可观测性。

## 文档索引

技术文档在 [doc/README.md](doc/README.md)：

1. [总体架构](doc/01-总体架构.md)
2. [部署与启动](doc/02-部署与启动.md)
3. [协议与通信](doc/03-协议与通信.md)
4. [数据库与存储](doc/04-数据库与存储.md)
5. [接口验证手册](doc/05-接口验证手册.md)
6. [Gateway API 模块](doc/12-gateway-api模块.md)
7. [AI Engine 模块](doc/20-ai-engine模块.md)
8. [Agent 工具治理与审批策略](doc/45-功能-Agent工具治理与审批策略.md)
9. [工具策略管理中心](doc/48-功能-工具策略管理中心.md)
10. [Agent Trace 工作台](doc/47-功能-Agent-Trace工作台.md)
11. [真实模型 Benchmark](doc/51-功能-真实模型Benchmark.md)
12. [Replay 对比与 Trace Diff](doc/52-功能-Replay对比与TraceDiff.md)
13. [审批型浏览 Agent Demo](doc/70-demo-审批型浏览Agent.md)
14. [记忆型助手 Demo](doc/71-demo-记忆型助手.md)
15. [OpenAPI 工具 Agent Demo](doc/72-demo-OpenAPI工具Agent.md)


## Replay 对比与 Trace Diff

Replay 现在会创建新的子任务，而不是把原任务重新洗回 `queued`。子任务通过正式字段 `replay_of_task_id` 关联原任务，原始 trace 因而可以保留，Web Trace 工作台也能在同一页面查看 replay 列表，并对比状态、耗时、计划步骤、工具序列、审批、重规划、记忆召回、`evaluate` 指标与最终回答文本差异。

新增接口：

1. `GET /v1/tasks/{taskID}/replays`
2. `GET /v1/tasks/{taskID}/compare/{otherTaskID}`

当前数据库仍沿用启动时自建表方案；`PostgresStore.ensureSchema` 会用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 补齐 `replay_of_task_id`，但仓库尚未引入独立 migration 版本管理。
