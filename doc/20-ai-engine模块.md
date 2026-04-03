# AI Engine 模块

## 1. 模块定位

AI Engine 是模型执行面，提供 gRPC 服务给 Gateway 调用，负责：

1. 健康检查响应
2. 任务输入消费与 Agent 规划循环
3. token/info 流式输出
4. 工具调用策略、审批门禁与审计
5. 失败事件归一化

关联文件：

1. [services/ai-engine-py/app/main.py](../services/ai-engine-py/app/main.py)
2. [services/ai-engine-py/app/config.py](../services/ai-engine-py/app/config.py)
3. [services/ai-engine-py/app/runtime.py](../services/ai-engine-py/app/runtime.py)
4. [services/ai-engine-py/app/service.py](../services/ai-engine-py/app/service.py)

## 2. 配置模型

配置来源为环境变量，核心项包括：

1. SYNAPSE_MODEL_PROVIDER：mock/openai
2. SYNAPSE_MODEL_PROVIDER_ALIAS：用于健康检查显示别名（如 gemini, zhipu）
3. SYNAPSE_OPENAI_API_KEY
4. SYNAPSE_OPENAI_BASE_URL
5. SYNAPSE_OPENAI_MODEL
6. SYNAPSE_OPENAI_TEMPERATURE
7. SYNAPSE_OPENAI_MAX_TOKENS
8. SYNAPSE_OPENAI_HTTP_TIMEOUT_SECONDS
9. SYNAPSE_OPENAI_MAX_RETRIES
10. SYNAPSE_OPENAI_RETRY_BACKOFF_SECONDS
11. SYNAPSE_AGENT_ENABLED_DEFAULT
12. SYNAPSE_AGENT_MAX_PLAN_STEPS
13. SYNAPSE_AGENT_REQUIRE_APPROVAL_FOR_HIGH_RISK
14. SYNAPSE_AGENT_MEMORY_FILE
15. SYNAPSE_AGENT_MEMORY_MAX_ENTRIES_PER_USER
16. SYNAPSE_AGENT_MEMORY_RECALL_LIMIT
17. SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST
18. SYNAPSE_AGENT_TOOL_HTTP_TIMEOUT_SECONDS
19. SYNAPSE_AGENT_ENABLE_CODE_EXECUTION
20. SYNAPSE_AGENT_TOOL_POLICY_JSON
21. SYNAPSE_AGENT_TOOL_AUDIT_LOG_FILE

## 3. Runtime 关键行为

1. mock 模式：按字符块模拟流式 token，支持本地联调。
2. openai 模式：调用 OpenAI-compatible /chat/completions。
3. 优先走 stream=true 的 SSE 流。
4. 若 stream 在首包前失败，自动降级普通 completion。
5. 对 429/5xx/URLError 做有限重试。
6. 支持 metadata 中 model_messages_json 作为多轮消息输入。
7. Agent 模式下执行 perceive -> plan -> act -> observe -> reflect -> evaluate 循环。
8. 内置工具：retrieval、calculator、browser_fetch、http_api、code_exec、json_echo。
9. 根据角色策略和审批策略判定工具可用性；高风险工具默认可要求审批。
10. 若触发审批门禁，会输出 approval_required 并暂停执行，等待网关恢复。

## 4. gRPC 服务行为

1. Health：返回 status=ok 与 provider display。
2. SubmitTask：
3. 先发 started。
4. 过程中按需发送 info（结构化 agent_event + payload）。
5. 逐 token 发 token 事件。
6. 正常结束发 completed。
7. 异常发 failed。
8. 若运行时进入 pause，服务会提前返回，由 Gateway 侧将任务维持在 paused 语义。

## 5. 可靠性细节

1. 运行时重试策略有上限，避免无限等待。
2. retry-after 头会影响实际退避时长。
3. 输出 trace_id 便于跨层追踪。
4. HTTP 工具支持 host allowlist，避免任意外联。
5. 工具执行审计可落盘，记录角色、输入摘要、结果和耗时。

## 6. 当前限制

1. 当前工具选择策略基于轻量启发式，尚未接入更强规划器。
2. 仍未实现 provider 级路由策略与熔断。
3. 工具策略与审批链路依赖 metadata 协议，缺少独立配置中心。

## 7. 扩展建议

1. 增加 provider adapter 层，抽离统一接口。
2. 引入熔断和限流中间层。
3. 将模型请求/响应摘要与工具审计纳入可观测指标体系。
4. 把 app/benchmarks/regression.py 接入 CI 作为 Agent 回归门禁。
