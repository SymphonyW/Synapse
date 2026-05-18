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

更完整的启动说明见 [doc/02-部署与启动.md](doc/02-部署与启动.md)。

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

## 文档同步原则

| 如果你改了 | 也请同步 |
|---|---|
| HTTP API、状态码、事件 | `doc/03-协议与通信.md`、`doc/05-接口验证手册.md`、相关功能文档 |
| 环境变量、Compose、启动方式 | `README.md`、`doc/02-部署与启动.md` |
| 工具治理、审批、记忆、评测 | `doc/20-ai-engine模块.md`、对应 `doc/4x` 功能文档 |
| 面向新用户的关键体验 | 根 `README.md`、Demo 文档、截图资产位 |

## 提交 PR 前

- [ ] 改动范围清楚，未把无关重构塞进同一个 PR；
- [ ] 相关测试已运行，失败原因已说明；
- [ ] 新增行为有对应文档；
- [ ] 新增配置有 `.example` 或安全说明；
- [ ] README / Demo / 文档入口没有坏链；
- [ ] 如果改了治理、审批或外联边界，已经额外检查默认安全行为。
