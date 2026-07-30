# Synapse Roadmap

> 当前唯一 Roadmap 事实来源。README 只保留公开版摘要；`docs/60-后续开发基线与演进建议.md` 和 `docs/61-后续开发任务清单.md` 已归档为历史设计记录，不再作为当前开发计划。

审计日期：2026-07-30

## 结构选择

采用新增 `docs/roadmap.md` 作为唯一当前 Roadmap。

理由：

| 方案 | 取舍 |
|---|---|
| README 承载完整 Roadmap | 公开入口会变长，细节频繁变化时容易让 README 失焦。 |
| 改造 `docs/61-后续开发任务清单.md` | 文件名和内容都有旧阶段语义，保留历史会继续混淆“当前计划”和“历史拆解”。 |
| 新增 `docs/roadmap.md` | 文件名语义明确，README 可稳定引用，旧文档可保留历史分析且不会继续误导。 |

## 状态定义

| 状态 | 含义 |
|---|---|
| Completed | 当前代码已实现主要路径，并有测试、脚本或 Compose 证据支撑。 |
| Partial | 已有可运行实现，但生产化、持久化、覆盖面或操作边界仍不足。 |
| Planned | 尚未实现，已纳入 Synapse v0.2。 |
| Deferred | 暂不进入 v0.2，需要更明确的产品或部署前提。 |
| Deprecated | 旧设计或旧事实源已被替代，不应继续作为开发依据。 |

## 审计范围

本 Roadmap 不以旧文档为准，已检查以下实际代码和配置：

| 范围 | 重点 |
|---|---|
| `apps/web/src/features/` | Web 工程化拆分、记忆管理、Trace、Replay Diff、工具策略 UI。 |
| `services/gateway-go/internal/` | 认证、任务生命周期、SSE、审批、Replay、工具策略、Redis 队列、migration、execution lease。 |
| `services/ai-engine-py/app/` | Runtime、provider、AgentEvent V2、工具 provider、OpenAPI executor、MCP stdio、记忆、benchmark。 |
| `services/ai-engine-py/tests/` | runtime characterization、AgentEvent、OpenAPI、MCP、vector memory、benchmark 测试。 |
| `proto/` | gRPC 服务、typed AgentEvent、Memory RPC、ToolPolicy RPC。 |
| `scripts/` | proto 生成、migration、agent regression、Docker smoke。 |
| `docker-compose.yml` | Postgres/pgvector、Redis、migrate 一次性服务、AI Engine/Gateway/Web 配置。 |
| `.github/workflows/` | CI、proto check、Docker smoke workflow。 |

## 当前状态表

| 能力 | 状态 | 代码证据 | 剩余缺口 |
|---|---|---|---|
| 认证与权限 | Partial | `services/gateway-go/internal/api/handlers_auth.go` 提供注册、登录、退出、`/me`；`domain/auth.go` 定义 `admin/user`；API 测试覆盖资源归属和管理员边界。 | 仍缺 production mode、Secure Cookie、CSRF/Origin 校验、登录限流、密码/身份体系策略。 |
| 任务生命周期 | Completed | `internal/domain/task.go`、`internal/api/handlers.go`、`internal/worker/processor.go` 覆盖 queued/running/paused/completed/failed/canceled、取消、死信、重放；相关 Go 测试存在。 | v0.2 继续补运行时 characterization 和多 Worker 压测，不改变核心状态机。 |
| SSE 断线续读 | Completed | `StreamTaskEvents` 支持 `last_event_id`；`apps/web/src/shared/hooks/useTaskEvents.ts` 用 EventSource 游标续读；前后端测试覆盖 terminal 和重试事件重建。 | 可补 `Last-Event-ID` header 兼容和长连接观测指标。 |
| 审批暂停恢复 | Completed | Gateway `ApproveTask` 写入 `approved_tool_call`；Worker 从 `approval_required` 提取恢复上下文；AI Engine Runtime 校验精确工具调用；测试覆盖 owner/admin 审批。 | 可补审批审计报表和通知，不再作为基础待办。 |
| 工具策略中心 | Completed | Gateway `/v1/admin/tool-policy`、`tool_policies` migration、AI Engine `GetToolPolicy/ApplyToolPolicy/ListTools`、Web `features/tool-policy` 已存在。 | 平台化阶段仍需 Prompt/Tool/Policy 版本绑定和发布审计。 |
| OpenAPI 工具 | Completed | `OpenAPIToolProvider`、`OpenAPIHTTPExecutor`、`SYNAPSE_OPENAPI_*` 配置和 `test_openapi_executor.py` 覆盖 allowlist、GET/POST、超时、响应限制。 | 暂不支持完整 OAuth、多种 content type 和公开 OpenAPI/Swagger 文档。 |
| MCP stdio | Completed | `StdioMCPAdapter`、`MCPToolProvider`、`SYNAPSE_MCP_STDIO_*` 配置和 `test_mcp_stdio_adapter.py` 覆盖进程启动、tools/list、tools/call、超时和错误。 | 暂不做远程 transport、多租户 MCP 或凭据隔离。 |
| 文件记忆 | Completed | `FileMemoryStore`、Gateway Memory API、AI Engine Memory RPC、Web `features/memory` 已打通。 | 文件后端不适合多副本生产共享存储。 |
| pgvector 记忆 | Partial | `PostgresVectorMemoryStore`、embedding provider、`SYNAPSE_MEMORY_BACKEND=vector`、`docker-compose.vector.env.example` 和 `test_vector_memory.py` 已存在。 | 表结构由 AI Engine 初始化，尚未纳入统一版本化 migration；需要真实 embedding smoke 和运维边界。 |
| Trace 工作台 | Completed | Web `features/trace/TraceWorkbench.tsx`、`traceParser.ts`、`TraceRawJsonPanel.tsx`、`TraceDiagnosisPanel.tsx` 和测试已存在。 | 可补跨任务搜索、导出格式稳定性和 trace 指标聚合。 |
| Replay Diff | Completed | Gateway `/v1/tasks/{taskID}/replays`、`/compare/{otherTaskID}`；Web `ReplayDiffPanel.tsx`、`traceDiff.ts` 和测试已存在。 | 当前偏交互分析；可补批量 diff 和 benchmark/report 输出。 |
| mock regression | Completed | `app/benchmarks/regression.py`、`cases.json`、CI 中 `python -m app.benchmarks.regression` 已存在。 | 可补更多 characterization case，但不再是未实现项。 |
| live benchmark | Partial | `live_benchmark.py`、`live_metrics.py`、`live_report.py`、`live_cases.json` 和 `test_benchmarks_live.py` 支持多 provider、dry-run、JSON/Markdown 报告。 | 未持久化到数据库，未进入必跑 CI，成本字段仍未由 provider usage 自动计算。 |
| CI/CD | Partial | `.github/workflows/ci.yml` 覆盖 Go vet/race test、Python unittest/regression、Web lint/build/test、proto check；`docker-smoke.yml` 跑 Compose smoke。 | 还没有发布流水线、镜像扫描、制品签名、回滚演练和分支保护说明。 |
| 数据库 migration | Partial | Gateway `migrations/000001` 到 `000005`、`cmd/migrate`、`scripts/migrate.ps1`、migration 测试已存在。 | pgvector memory schema 仍由 AI Engine 自建；生产 migration 审批、回滚演练和 drift check 未完成。 |
| Redis ack/reclaim | Completed | `TaskQueue` 抽象含 `Ack/Reclaim`；`RedisQueue` 使用 `XADD/XREADGROUP/XACK/XAUTOCLAIM`；Worker 显式 Ack，测试覆盖 reclaim 和 execution lease。 | 需要多 Worker Compose/压测脚本、队列指标和故障注入记录。 |
| 生产安全 | Planned | 当前代码能看到 local auth，但 Cookie `Secure: false`；未发现 CSRF/Origin、登录限流、secret 管理模式。 | v0.2 M5 统一补齐。 |
| OpenTelemetry | Planned | 未发现 `otel`、OpenTelemetry SDK、Prometheus metrics 或 traceparent 传播实现。 | v0.2 M5 建立 traces、metrics、readiness。 |
| OpenAPI/Swagger | Planned | 当前没有 Gateway HTTP API 的 OpenAPI/Swagger/Apifox/Postman 产物。 | v0.2 M6 补机器可读接口文档和生成检查。 |
| Agent Definition | Planned | 未发现 `AgentDefinition`、agent version、prompt/tool/policy 版本绑定模型。 | v0.2 M6 平台化阶段设计并实现。 |

## 旧文档中已不应继续作为待办的能力

| 旧待办能力 | 当前判断 |
|---|---|
| Web 工程化拆分 | Completed：`apps/web/src/features` 和 `shared` 已拆分，`App.tsx` 已变为组合入口。 |
| 记忆管理 UI | Completed：Web `features/memory` 已存在并复用 Gateway Memory API。 |
| Trace 工作台 | Completed：Web Trace parser/workbench/diagnosis/raw JSON 已存在。 |
| Replay Diff | Completed：Gateway compare API + Web diff UI + trace diff 测试已存在。 |
| OpenAPI 执行器 | Completed：受控 HTTP executor、allowlist、auth/header、超时和响应限制已实现。 |
| MCP stdio | Completed：stdio transport adapter、工具发现、调用和错误处理已实现。 |
| 向量记忆 | Partial：pgvector store 已实现，但 migration 和运行验收仍需补齐。 |
| 工具策略管理 | Completed：Gateway API、Postgres store、AI Engine hot apply、Web admin UI 已存在。 |
| 真实模型 Benchmark | Partial：live benchmark CLI 和报告已实现，但未持久化、未门禁化。 |

## Synapse v0.2

版本目标：Reliable Agent Runtime。

v0.2 不以“继续补功能清单”为主，而是把已经打通的 Agent runtime 变成可验证、可维护、可生产化演进的系统。下一阶段明确区分三类工作：

| 类别 | 目标 |
|---|---|
| 可靠性 | 让任务投递、事件、replay、migration 和 CI 可重复验证。 |
| 生产化 | 补安全模式、可观测性、readiness、metrics 和 secret 边界。 |
| 平台化 | 引入 Agent Definition、版本绑定、Benchmark 持久化、SDK 和 OpenAPI 文档。 |

### M1：质量基线

| 项目 | 状态 | 目标 | 主要模块 | 依赖项 | 验收标准 | 明确不做 |
|---|---|---|---|---|---|---|
| 统一 CI | Partial | 将 Go、Python、Web、proto、mock regression 和 smoke 的必跑边界写清。 | `.github/workflows/ci.yml`、`.github/workflows/docker-smoke.yml`、`scripts/` | 现有 CI workflow | PR 上 Go/Python/Web/proto check 全部必跑；Docker smoke 至少在 main 和手动触发可复现。 | 不做发布流水线。 |
| Runtime characterization tests | Partial | 锁定 planner/executor/approval/memory/synthesis 行为，避免重构改语义。 | `services/ai-engine-py/tests/test_runtime_characterization.py`、`app/runtime.py` | AgentEvent factory | 每个关键 agent_event 序列有 characterization 测试；重构前后测试结果一致。 | 不用真实模型做 characterization。 |
| Proto 生成检查 | Completed | 防止 proto 和 Go/Python 生成文件漂移。 | `proto/`、`Makefile`、`scripts/dev.ps1`、CI proto-check | protoc 工具 | CI 执行 `make proto` 后 `git diff --exit-code` 通过。 | 不引入新 IDL。 |
| Docker smoke test | Partial | 用 Compose 验证健康检查、注册登录、建任务、SSE、终态闭环。 | `docker-compose.yml`、`scripts/ci-smoke.sh`、`docker-smoke.yml` | mock provider | smoke 脚本在空环境启动全栈并完成一个 mock task。 | 不覆盖真实 provider 和外部工具。 |

### M2：核心模块可维护性

| 项目 | 状态 | 目标 | 主要模块 | 依赖项 | 验收标准 | 明确不做 |
|---|---|---|---|---|---|---|
| 拆分 OpenAI-compatible Provider | Completed | 模型 provider 与 runtime 解耦，便于 provider 测试和多兼容源配置。 | `app/providers/`、`app/runtime.py`、provider tests | 现有 runtime | `OpenAICompatibleProvider` 有独立错误、重试、stream 和 complete 测试；runtime 只依赖 provider protocol。 | 不做供应商专有 SDK 接入。 |
| 统一 Agent Event Factory | Completed | 所有结构化 info 事件由统一 factory 构造并映射到 proto typed payload。 | `app/events/`、`proto/`、Gateway event normalization | AgentEvent V2 | `test_agent_event_factory.py` 和 `test_agent_event_proto.py` 覆盖 typed payload 和 schema version。 | 不移除 legacy info JSON 兼容。 |
| 拆分 Runtime planner/executor/synthesis | Planned | 把 `runtime.py` 中 planner、executor、replanner、synthesis 从大类中拆出可测试模块。 | `app/runtime.py`、新增 `app/runtime_parts/` 或同等目录 | Runtime characterization tests | 拆分后现有 characterization、benchmark、tool policy 测试全通过；公共输入输出类型稳定。 | 不改 Agent 行为策略，不同时引入模型自由工具调用。 |

### M3：协议和持久化

| 项目 | 状态 | 目标 | 主要模块 | 依赖项 | 验收标准 | 明确不做 |
|---|---|---|---|---|---|---|
| Typed AgentEvent V2 | Completed | 在 proto 中表达 trace、tool、memory、approval、evaluation 等结构化事件。 | `proto/synapse/v1/agent.proto`、`app/events/proto.py`、Gateway event store | AgentEvent Factory | 新事件带 `schema_version=synapse.agent.event.v2`；Web 和 Gateway 能兼容 legacy info。 | 不删除旧 `message` 载荷。 |
| 版本化 PostgreSQL migration | Partial | 所有 Gateway 表结构和跨服务共享持久化结构走 up/down migration。 | `services/gateway-go/migrations`、`scripts/migrate.ps1`、`app/vector_memory.py` | migration tool | 新表/列/索引必须有 up/down；pgvector memory schema 不再只靠运行时 auto-DDL。 | 不做在线无锁迁移框架。 |
| 历史事件兼容 | Partial | Web/Gateway 能读取旧 info JSON 和 V2 typed payload，不破坏历史任务 Trace。 | `internal/agentinfo`、`app/events/proto.py`、`features/trace/traceParser.ts` | Typed AgentEvent V2 | 使用旧事件 fixture 和 V2 fixture 都能解析 plan/tool/approval/memory/evaluation。 | 不迁移旧事件数据为新格式。 |

### M4：任务可靠交付

| 项目 | 状态 | 目标 | 主要模块 | 依赖项 | 验收标准 | 明确不做 |
|---|---|---|---|---|---|---|
| Redis Streams | Completed | 使用 Consumer Group 实现至少一次任务投递。 | `internal/queue/redis.go`、`docker-compose.yml` | Redis | `Claim` 使用 `XREADGROUP`；消息含 task_id 和 Redis message id。 | 不引入 Kafka/NATS。 |
| Ack/Reclaim | Completed | Worker 获取消息后必须显式 Ack；崩溃后消息可由其他 Consumer reclaim。 | `internal/queue/queue.go`、`internal/worker/processor.go` | Redis Streams | `Ack` 调用 `XACK`；`Reclaim` 调用 `XAUTOCLAIM`；集成测试覆盖 ack 后 pending 清零和 reclaim 后再 ack。 | 不保证 exactly-once。 |
| Worker execution lease | Completed | 防止重复投递导致同一任务被多个 Worker 同时执行。 | `internal/store/*`、`000005_task_execution_lease.*.sql`、Worker tests | Ack/Reclaim | 并发 Worker 中只有一个能获得 lease 并提交 AI Engine；重复 delivery 被 Ack。 | 不做分布式锁服务。 |
| 多 Worker 验证 | Planned | 在 Compose 或集成测试中验证两个以上 Worker 的崩溃、reclaim、lease 和死信语义。 | `docker-compose.yml`、`internal/worker`、`scripts/` | Worker execution lease | 故障注入测试能让 worker A 崩溃，worker B 在 visibility timeout 后 reclaim 并完成或死信。 | 不做大规模性能压测。 |

### M5：生产安全和可观测性

| 项目 | 状态 | 目标 | 主要模块 | 依赖项 | 验收标准 | 明确不做 |
|---|---|---|---|---|---|---|
| development/production mode | Planned | 用显式模式控制安全默认值和本地便利默认值。 | `internal/config`、`app/config.py`、Compose env | 当前 config | `SYNAPSE_ENV=production` 下缺少必需 secret 或不安全默认值时启动失败。 | 不自动推断部署环境。 |
| Secure Cookie | Planned | production 下 Session Cookie 必须 `Secure`、`HttpOnly`、合适 SameSite。 | `handlers_auth.go`、config | production mode | 测试覆盖 dev 可本地 HTTP，production 必须 Secure 且不能硬编码 false。 | 不改成 JWT。 |
| CSRF/Origin 校验 | Planned | 对 Cookie 认证的写请求增加 Origin/CSRF 防护。 | Gateway middleware、Web client | Secure Cookie | 非允许 Origin 的 POST/PUT/DELETE 返回 403；同源请求和明确 allowlist 通过。 | 不做 OAuth 登录。 |
| 登录限流 | Planned | 限制爆破登录和注册撞库。 | `handlers_auth.go`、store 或内存 limiter | production mode | 同一用户名/IP 超阈值返回 429，并有可配置窗口。 | 不做复杂风控。 |
| secret 管理 | Planned | 移除生产默认明文口令和隐式 API key 读取风险。 | Compose env、config、docs | production mode | production 缺 admin password、cookie secret、provider key 时按场景 fail fast；日志不输出 secret。 | 不集成云 KMS。 |
| readiness | Planned | 区分 liveness 和 readiness，覆盖 Postgres、Redis、AI Engine 依赖状态。 | Gateway API、AI Engine service、Compose healthcheck | healthz | `/readyz` 在依赖不可用时返回非 ready；`/healthz` 仍表示进程存活。 | 不做完整 SLO 平台。 |
| OpenTelemetry | Planned | 贯穿 Gateway、Worker、AI Engine 的 trace_id、span 和关键错误。 | Go/Python OTel SDK、gRPC metadata、event trace_id | readiness | 单任务能在 Gateway request、queue delivery、AI Engine SubmitTask 中关联同一 trace。 | 不绑定特定 vendor。 |
| metrics | Planned | 暴露任务、队列、SSE、工具、benchmark 的基础指标。 | Gateway `/metrics`、AI Engine metrics | OpenTelemetry | 至少包含任务状态计数、队列 pending/reclaim、工具调用结果、SSE 连接数。 | 不做告警规则库。 |

### M6：平台化能力

| 项目 | 状态 | 目标 | 主要模块 | 依赖项 | 验收标准 | 明确不做 |
|---|---|---|---|---|---|---|
| Agent Definition | Planned | 定义 Agent 的 prompt、tools、policy、memory、provider 配置入口。 | Gateway domain/store/API、AI Engine runtime config、Web | M3/M5 基线 | 可创建、读取、选择一个 Agent Definition 发起任务；任务记录 definition id。 | 不做可视化拖拽编排器。 |
| Agent Version | Planned | Agent Definition 每次发布生成不可变版本，任务绑定版本。 | DB migration、Gateway API、Web | Agent Definition | 同一 definition 可有多个版本；历史任务可回看当时版本配置。 | 不自动迁移历史任务到新版本。 |
| Prompt/Tool/Policy 版本绑定 | Planned | 任务执行时固定 prompt、tool catalog、policy version，保证 replay 可解释。 | Tool policy store、AI Engine policy、Replay Diff | Agent Version | replay 显示原始版本和当前版本差异；策略变化不会改变历史任务解释。 | 不做复杂审批工作流。 |
| Benchmark 持久化 | Planned | 将 live/mock benchmark 结果落库或产物目录索引，支持趋势比较。 | `app/benchmarks`、Gateway store/API、Web | live benchmark | 每次 run 有 run_id、provider、case、quality、latency、cost 字段；Web/API 可查询最近结果。 | 不让真实 benchmark 成为 PR 必跑门禁。 |
| SDK | Planned | 提供最小客户端封装创建任务、监听 SSE、审批、memory 和 tool policy 管理。 | 新增 `sdk/` 或语言子目录 | OpenAPI/Swagger | SDK 示例能跑通 create task、stream events、approve paused task。 | 不同时维护多语言完整生态。 |
| OpenAPI 文档 | Planned | 生成 Gateway HTTP API 的机器可读文档。 | `services/gateway-go/internal/api`、`docs/`、CI | API schema 稳定 | 文档覆盖 auth、tasks、events、approve、replay、memory、tool policy；示例请求可执行。 | 不把 gRPC proto 转成 HTTP 文档替代品。 |
| 平台权限边界 | Deferred | Agent Definition、benchmark 和 policy 发布需要更细粒度权限。 | Gateway auth/domain | Agent Version | 暂在 v0.2 后明确组织/项目模型再拆权限。 | v0.2 不做多租户 RBAC。 |

## 尚无法仅通过代码确认

| 项目 | 原因 |
|---|---|
| live benchmark 是否已用真实 provider 定期运行 | 代码支持 dry-run 和真实执行，但仓库不能证明外部 key、成本和历史结果。 |
| Redis 多 Worker 在真实长任务下的稳定性 | 单元/集成测试覆盖 ack/reclaim/lease，仍缺 Compose 级故障注入记录。 |
| production 安全配置是否由部署层补齐 | 代码中尚无 production mode，无法确认外部反向代理是否补齐 HTTPS、Origin 和限流。 |
| pgvector 记忆的生产 schema 管理 | AI Engine 可自建表，但还没有统一 migration 和回滚策略。 |
| OpenTelemetry 后端与指标采集 | 仓库中未发现实现或部署配置。 |

