# Synapse 技术文档总览

本目录用于沉淀 Synapse 的工程级、模块级和功能级文档。根目录 [README.md](../README.md) 面向第一次接触项目的新开发者，本目录文档用于继续深入架构、接口、存储和核心功能。

## 文档导航

| 文档 | 适合阅读场景 |
|---|---|
| [01-总体架构](01-总体架构.md) | 理解组件边界、数据流和架构限制 |
| [02-部署与启动](02-部署与启动.md) | 从零启动后端、前端和依赖服务 |
| [03-协议与通信](03-协议与通信.md) | 理解 gRPC、SSE、metadata 和事件语义 |
| [04-数据库与存储](04-数据库与存储.md) | 理解 Postgres 表、Redis 队列、内存回退和记忆文件 |
| [05-接口验证手册](05-接口验证手册.md) | 用可复制命令验证健康检查、认证、任务、SSE、记忆接口 |
| [10-gateway-总览](10-gateway-总览.md) | 理解 Gateway 整体职责 |
| [11-gateway-config模块](11-gateway-config模块.md) | 理解 Gateway 环境变量 |
| [12-gateway-api模块](12-gateway-api模块.md) | 查 HTTP API 路由、权限、请求响应和状态码 |
| [13-gateway-domain模块](13-gateway-domain模块.md) | 理解任务、事件、死信、用户和会话模型 |
| [14-gateway-queue模块](14-gateway-queue模块.md) | 理解 Redis/InMemory 队列 |
| [15-gateway-store模块](15-gateway-store模块.md) | 理解 Postgres/InMemory TaskStore |
| [16-gateway-worker模块](16-gateway-worker模块.md) | 理解 Worker、重试、取消和死信 |
| [20-ai-engine模块](20-ai-engine模块.md) | 理解 Runtime、模型 provider、工具、记忆和评测 |
| [30-web模块](30-web模块.md) | 理解 Web 控制台能力和数据流 |
| [31-web前端工程结构](31-web前端工程结构.md) | 理解前端目录边界、hooks、测试与配置 |
| [40-功能-认证与权限](40-功能-认证与权限.md) | 理解 Cookie Session、角色和资源边界 |
| [41-功能-任务生命周期与事件流](41-功能-任务生命周期与事件流.md) | 理解状态机与 SSE |
| [42-功能-会话上下文](42-功能-会话上下文.md) | 理解对话上下文构建 |
| [43-功能-重试死信与重放](43-功能-重试死信与重放.md) | 理解失败处理闭环 |
| [44-功能-审批暂停与恢复](44-功能-审批暂停与恢复.md) | 理解 paused/approve/resume |
| [45-功能-Agent工具治理与审批策略](45-功能-Agent工具治理与审批策略.md) | 理解工具、角色、审批、审计和扩展 provider |
| [46-功能-Agent回归评测与门禁](46-功能-Agent回归评测与门禁.md) | 理解 mock 回归评测和门禁指标 |
| [47-功能-Agent-Trace工作台](47-功能-Agent-Trace工作台.md) | 理解结构化 Trace 展示、导出和排障语义 |
| [48-功能-工具策略管理中心](48-功能-工具策略管理中心.md) | 理解管理员工具策略控制面、API、热更新和 env 优先级 |
| [49-功能-向量长期记忆](49-功能-向量长期记忆.md) | 理解 file/vector 双后端、pgvector、embedding provider 和召回语义 |
| [50-运维排障手册](50-运维排障手册.md) | 排查启动、模型、队列、SSE 和死信问题 |
| [51-功能-真实模型Benchmark](51-功能-真实模型Benchmark.md) | 理解真实 provider 评测、配置、报告与当前限制 |
| [52-功能-Replay对比与TraceDiff](52-功能-Replay对比与TraceDiff.md) | 理解 replay 子任务、compare API 和结构化 diff |
| [70-demo-审批型浏览Agent](70-demo-审批型浏览Agent.md) | 10 分钟跑通审批暂停与恢复闭环 |
| [71-demo-记忆型助手](71-demo-记忆型助手.md) | 体验长期记忆写入、召回与回答复用 |
| [72-demo-OpenAPI工具Agent](72-demo-OpenAPI工具Agent.md) | 体验外部 API 工具的发现、治理与执行 |

## 阅读顺序

| 目标 | 推荐路径 |
|---|---|
| 第一次启动项目 | 根 README -> 02 -> 05 |
| 第一次演示项目 | 根 README -> 70 / 71 / 72 |
| 理解后端架构 | 01 -> 03 -> 10 -> 12 -> 15 -> 16 |
| 理解 AI Engine | 20 -> 45 -> 48 -> 46 -> 51 |
| 理解数据与可靠性 | 04 -> 41 -> 43 -> 44 |
| 开发前端 | 根 README -> 30 -> apps/web/README.md |

## 维护原则

1. 修改接口、状态码、metadata 或事件类型时，同步更新 [12-gateway-api模块](12-gateway-api模块.md)、[03-协议与通信](03-协议与通信.md) 和 [05-接口验证手册](05-接口验证手册.md)。
2. 修改环境变量、Docker Compose 或启动脚本时，同步更新根 README 和 [02-部署与启动](02-部署与启动.md)。
3. 修改数据库结构时，同步更新 [04-数据库与存储](04-数据库与存储.md) 和 [15-gateway-store模块](15-gateway-store模块.md)。
4. 修改 Agent 工具、审批或记忆行为时，同步更新 [20-ai-engine模块](20-ai-engine模块.md)、[45-功能-Agent工具治理与审批策略](45-功能-Agent工具治理与审批策略.md) 和 [46-功能-Agent回归评测与门禁](46-功能-Agent回归评测与门禁.md)。
5. 当前仓库没有 OpenAPI/Swagger 文档。若后续补齐，应在本页增加入口，并在根 README 的接口文档状态中同步更新。
