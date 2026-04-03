# AI Engine 模块

## 1. 模块定位

AI Engine 是模型执行面，提供 gRPC 服务给 Gateway 调用，负责：

1. 健康检查响应
2. 任务输入消费
3. token 流式输出
4. 失败事件归一化

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

## 3. Runtime 关键行为

1. mock 模式：按字符块模拟流式 token，支持本地联调。
2. openai 模式：调用 OpenAI-compatible /chat/completions。
3. 优先走 stream=true 的 SSE 流。
4. 若 stream 在首包前失败，自动降级普通 completion。
5. 对 429/5xx/URLError 做有限重试。
6. 支持 metadata 中 model_messages_json 作为多轮消息输入。

## 4. gRPC 服务行为

1. Health：返回 status=ok 与 provider display。
2. SubmitTask：
3. 先发 started。
4. 逐 token 发 token 事件。
5. 正常结束发 completed。
6. 异常发 failed。

## 5. 可靠性细节

1. 运行时重试策略有上限，避免无限等待。
2. retry-after 头会影响实际退避时长。
3. 输出 trace_id 便于跨层追踪。

## 6. 当前限制

1. 未接入官方 SDK 的高级能力（函数调用、工具调用等）。
2. 尚未实现 provider 级路由策略与熔断。
3. 日志与指标仍以基础信息为主。

## 7. 扩展建议

1. 增加 provider adapter 层，抽离统一接口。
2. 引入熔断和限流中间层。
3. 将模型请求/响应摘要纳入可观测指标体系。
