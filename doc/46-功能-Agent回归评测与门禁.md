# 功能：Agent 回归评测与门禁

## 1. 功能目标

为 Agent 新增能力提供可重复、可量化的回归基线，避免策略或运行时改动导致行为退化。

## 2. 相关实现

1. [services/ai-engine-py/app/benchmarks/regression.py](../services/ai-engine-py/app/benchmarks/regression.py)
2. [services/ai-engine-py/app/benchmarks/cases.json](../services/ai-engine-py/app/benchmarks/cases.json)
3. [scripts/dev.ps1](../scripts/dev.ps1)
4. [Makefile](../Makefile)

## 3. 运行方式

PowerShell：

1. .\scripts\dev.ps1 -Task agent-regression

Makefile：

1. make agent-regression

## 4. 用例定义格式

cases.json 每个用例包含：

1. id：用例标识。
2. prompt：测试输入。
3. metadata：运行控制参数（如角色、审批、上下文）。
4. min_success：最低成功阈值。
5. expect_pause：是否期望进入暂停。

## 5. 当前覆盖场景

1. calc_plan：计算 + 摘要。
2. retrieval_context：历史上下文检索。
3. http_with_approval：已授权 HTTP 抓取。
4. approval_pause：未授权高风险工具触发暂停。
5. code_exec_admin：管理员代码执行场景。

## 6. 评测输出指标

1. success_rate：通过率。
2. avg_tool_success_rate：工具执行成功率均值。
3. block_rate：阻断率（策略或审批导致）。
4. avg_duration_ms：平均执行耗时。
5. failed_metrics：未达标指标列表。

## 7. 可调门禁阈值

1. SYNAPSE_AGENT_REGRESSION_MIN_SUCCESS_RATE（默认 0.8）。
2. SYNAPSE_AGENT_REGRESSION_MIN_TOOL_SUCCESS_RATE（默认 0.6）。
3. SYNAPSE_AGENT_REGRESSION_MAX_BLOCK_RATE（默认 0.6）。
4. SYNAPSE_AGENT_REGRESSION_MAX_AVG_DURATION_MS（默认 2000）。

## 8. 结果判定

1. 所有门禁指标达标：summary.passed=true，进程退出码为 0。
2. 任一指标不达标：summary.passed=false，进程退出码为 1。

## 9. CI 接入建议

1. 在 CI 中把 agent-regression 放在单元测试后执行。
2. 对失败输出保留 JSON 报告，便于追踪回归来源。
3. 对 cases.json 变更执行评审，避免阈值和场景被随意放宽。
4. 建议按版本冻结一组“稳定基线用例”，新增用例逐步扩展。
