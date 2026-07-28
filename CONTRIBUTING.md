# 贡献指南

感谢你愿意一起把 Synapse 打磨得更稳。这个仓库同时包含 Web、Gateway、AI Engine 和文档，提交前最重要的原则只有一个：**改动到哪里，验证和文档就跟到哪里**。

## 开发环境

| 依赖 | 用途 |
|---|---|
| Docker Desktop / Docker Compose v2 | 一键启动完整环境 |
| Go 1.25+ | Gateway 开发与测试 |
| Python 3.12+ | AI Engine 开发与测试 |
| Node.js 当前 LTS + npm | Web 开发与测试 |
| PowerShell | Windows 下复用仓库脚本 |

最短启动：

```powershell
.\scripts\dev.ps1 -Task up
```

更完整的启动说明见 [docs/02-部署与启动.md](docs/02-部署与启动.md)。

## 分支命名建议

推荐使用清晰的前缀：

| 类型 | 示例 |
|---|---|
| 功能 | `feat/trace-export` |
| 修复 | `fix/sse-reconnect` |
| 文档 | `docs/demo-scenarios` |
| 重构 | `refactor/web-task-panel` |

## 常用验证命令

```powershell
# Gateway
Set-Location services/gateway-go
go test ./...
Set-Location ..\..

# Gateway PostgreSQL migration
.\scripts\dev.ps1 -Task migrate-status
.\scripts\dev.ps1 -Task migrate-up

# AI Engine
Set-Location services/ai-engine-py
python -m unittest discover -s tests -p "test_*.py"
python -m app.benchmarks.regression
Set-Location ..\..

# Web
Set-Location apps/web
npm run lint
npm run build
npm run test
Set-Location ..\..
```

如果你改了真实 provider 评测逻辑，再补一次 dry-run：

```powershell
Set-Location services/ai-engine-py
python -m app.benchmarks.live_benchmark --provider openai --dry-run-config-check
Set-Location ..\..
```

## CI Gate

Run the same checks locally before opening a PR:

```powershell
# Gateway
Set-Location services/gateway-go
$unformatted = gofmt -l .
if ($unformatted) { $unformatted; exit 1 }
go vet ./...
go test -race ./...
Set-Location ..\..

# AI Engine
Set-Location services/ai-engine-py
python -m unittest discover -s tests -p "test_*.py"
python -m app.benchmarks.regression
Set-Location ..\..

# Web
Set-Location apps/web
npm ci
npm run lint
npm run build
npm run test
Set-Location ..\..

# Proto
.\scripts\dev.ps1 -Task proto
# or on Unix-like shells:
make proto
git diff --exit-code

# Docker smoke
docker compose up --build -d
bash scripts/ci-smoke.sh
docker compose logs
docker compose down -v
```

Before submitting a PR, run the checks for the modules you changed. If the PR touches shared proto, auth, task execution, or Compose wiring, run the full CI set above.

CI job responsibilities:

| Job | Responsibility |
|---|---|
| `gateway` | Checks Go formatting with `gofmt -l`, then runs `go vet ./...` and `go test -race ./...`. |
| `ai-engine` | Installs Python dependencies, runs unittest discovery, and runs the Agent regression suite in mock mode. |
| `web` | Runs `npm ci`, ESLint, production build, and Vitest in non-watch mode. |
| `proto-check` | Runs the existing proto generation command and fails if `git diff --exit-code` or generated-file tracking checks find drift. |
| `docker-smoke` | Builds the Compose stack in mock mode, checks `/healthz`, registers/logs in, creates a task, reads SSE events, and verifies the task completes. |

Mock regression is deterministic and must not use real model API keys. Live benchmark is for provider integration and quality checks; it may require provider credentials and should stay outside required PR CI unless explicitly requested.

## Database Migration

Gateway PostgreSQL schema is versioned under `services/gateway-go/migrations`. Runtime startup does not create or alter tables.

| Task | Command |
|---|---|
| Initialize or upgrade local DB | `.\scripts\dev.ps1 -Task migrate-up` |
| Show version and dirty state | `.\scripts\dev.ps1 -Task migrate-status` |
| Create next migration pair | `.\scripts\dev.ps1 -Task migrate-create -Name short_name` |
| Roll back one step | `.\scripts\dev.ps1 -Task migrate-down -Steps 1` |
| Baseline old ensureSchema DB | `.\scripts\dev.ps1 -Task migrate-baseline -Version 4` then `.\scripts\dev.ps1 -Task migrate-up` |

Before a PR that changes Gateway storage, auth/session persistence, task events, tool policy, Compose, or migration SQL, run Gateway tests and at least `migrate-up` plus `migrate-status` against a disposable Postgres database. Down migrations can delete data; back up real databases before using them outside disposable environments.

Queue changes should keep the `TaskQueue` Delivery/Ack/Reclaim contract intact. For Redis Streams integration tests, run with `SYNAPSE_TEST_REDIS_ADDR=127.0.0.1:6379`; without that env the tests skip and normal `go test ./...` stays local-only.

## AgentEvent V2 Migration

Agent runtime info events are migrating from legacy JSON-in-`message` to typed protobuf payloads:

| Stage | Behavior |
|---|---|
| 1 | AI Engine double-writes typed `AgentEvent` payloads and the legacy JSON `message`. |
| 2 | Gateway reads typed payloads first, falls back to legacy JSON, and persists structured `payload` plus `schema_version`. |
| 3 | Web reads `schema_version`, `event_name`, and `payload` first, then falls back to `parseAgentInfoEnvelope(message)` for historical V1 events. |
| 4 | Legacy removal is a future compatibility decision and must not happen in this phase. |

When changing Agent event fields, update `proto/synapse/v1/agent.proto`, regenerate Go/Python code, keep legacy JSON fields compatible, and run the Python, Gateway, Web, and proto checks listed above.

## 文档同步原则

| 如果你改了 | 也请同步 |
|---|---|
| HTTP API、状态码、事件 | `docs/03-协议与通信.md`、`docs/05-接口验证手册.md`、相关功能文档 |
| 环境变量、Compose、启动方式 | `README.md`、`docs/02-部署与启动.md` |
| 工具治理、审批、记忆、评测 | `docs/20-ai-engine模块.md`、对应 `docs/4x` 功能文档 |
| 面向新用户的关键体验 | 根 `README.md`、Demo 文档、截图资产位 |

## 提交 PR 前

- [ ] 改动范围清楚，未把无关重构塞进同一个 PR；
- [ ] 相关测试已运行，失败原因已说明；
- [ ] 新增行为有对应文档；
- [ ] 数据库 schema 改动有 migration、down 风险说明和升级文档；
- [ ] 新增配置有 `.example` 或安全说明；
- [ ] README / Demo / 文档入口没有坏链；
- [ ] 如果改了治理、审批或外联边界，已经额外检查默认安全行为。
