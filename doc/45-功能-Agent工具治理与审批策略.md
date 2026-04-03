# 功能：Agent 工具治理与审批策略

## 1. 功能目标

为 Agent 执行过程建立“可控工具边界”：谁能调用、哪些工具高风险、何时必须审批、如何审计调用。

## 2. 相关实现

1. [services/ai-engine-py/app/runtime.py](../services/ai-engine-py/app/runtime.py)
2. [services/ai-engine-py/app/tools/builtin.py](../services/ai-engine-py/app/tools/builtin.py)
3. [services/ai-engine-py/app/tools/policy.py](../services/ai-engine-py/app/tools/policy.py)
4. [services/ai-engine-py/app/tools/audit.py](../services/ai-engine-py/app/tools/audit.py)
5. [services/ai-engine-py/app/config.py](../services/ai-engine-py/app/config.py)

## 3. 内置工具清单

1. retrieval（低风险）：检索长期记忆片段。
2. calculator（低风险）：受限数学表达式计算。
3. browser_fetch（高风险）：HTTP 页面抓取。
4. http_api（高风险）：HTTP API 拉取。
5. code_exec（高风险）：表达式执行（受开关控制）。
6. json_echo（低风险）：调试回显。

## 4. 角色授权默认策略

1. admin：允许全部工具（*）。
2. user：默认允许 retrieval/calculator/browser_fetch/http_api/json_echo。
3. code_exec 不在 user 默认白名单中。

## 5. 审批策略

1. 当 SYNAPSE_AGENT_REQUIRE_APPROVAL_FOR_HIGH_RISK=true 时，高风险工具默认进入审批门禁。
2. 元数据 approval_granted=true 可作为全局放行。
3. 元数据 approved_tools 可做工具级放行。
4. 若审批条件不满足，Runtime 产生 approval_required 并暂停。

## 6. 可配置策略（JSON）

通过 SYNAPSE_AGENT_TOOL_POLICY_JSON 覆盖策略，主要字段：

1. role_allow：按角色指定允许工具。
2. approval_required：强制审批的工具集合。
3. disabled_tools：禁用工具集合（优先级最高）。

## 7. 外联与执行边界

1. SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST：限制可访问域名。
2. SYNAPSE_AGENT_TOOL_HTTP_TIMEOUT_SECONDS：限制工具请求耗时。
3. SYNAPSE_AGENT_ENABLE_CODE_EXECUTION：控制 code_exec 是否启用。
4. 即使工具被选中，策略和审批依然可阻断执行。

## 8. 审计日志

1. 通过 SYNAPSE_AGENT_TOOL_AUDIT_LOG_FILE 启用工具调用审计。
2. 单行 JSON 记录：task_id、user_id、user_role、tool、tool_input_preview、ok、outcome、reason、duration_ms。
3. 失败场景（policy_blocked、approval_required）也会落审计记录。

## 9. 运维建议

1. 生产环境建议默认关闭 code_exec，仅在受控环境开启。
2. 对 external HTTP 工具必须配置 allowlist，避免任意外联。
3. 定期回放审计日志，分析高风险工具调用分布与失败原因。
4. 对审批通过率、阻断率、平均执行时长建立看板。
