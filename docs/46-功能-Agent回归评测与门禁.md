# 功能：Agent 回归评测与门禁

Agent 回归评测用于在不依赖外部模型的情况下，用 mock provider 验证工具、审批、记忆、浏览、失败恢复等核心行为是否退化。它解决的是“Runtime 行为有没有退化”的问题，不解决“不同真实 provider 谁执行得更好”的问题；后者由独立的 [真实模型 Benchmark](51-功能-真实模型Benchmark.md) 负责。

## 相关实现

| 文件 | 说明 |
|---|---|
| [regression.py](../services/ai-engine-py/app/benchmarks/regression.py) | 加载 cases、运行 Runtime、计算指标、输出 JSON |
| [cases.json](../services/ai-engine-py/app/benchmarks/cases.json) | 回归用例定义 |
| [test_benchmarks_regression.py](../services/ai-engine-py/tests/test_benchmarks_regression.py) | 回归脚本单元测试 |
| [live_benchmark.py](../services/ai-engine-py/app/benchmarks/live_benchmark.py) | 独立真实 provider 评测入口，不参与 mock 门禁 |
| [scripts/dev.ps1](../scripts/dev.ps1) | `agent-regression` 脚本入口 |
| [Makefile](../Makefile) | `make agent-regression` |

## 运行方式

PowerShell：

```powershell
.\scripts\dev.ps1 -Task agent-regression
```

或：

```powershell
Set-Location services/ai-engine-py
python -m app.benchmarks.regression
Set-Location ..\..
```

Makefile：

```bash
make agent-regression
```

## 用例格式

| 字段 | 说明 |
|---|---|
| `id` | 用例标识 |
| `prompt` | 输入 |
| `metadata` | Runtime 控制参数，如角色、审批、上下文 |
| `tags` | 覆盖标签 |
| `min_success` | 最低成功评分 |
| `expect_pause` | 是否期望暂停 |
| `expect_memory_hit` | 是否期望记忆命中 |
| `expect_direct_answer` | 是否期望无工具直接回答 |
| `required_events` | 必须出现的 Agent info 事件 |
| `required_tools` | 必须调用的工具 |
| `required_answer_contains` | 最终回答必须包含的片段 |

## 当前覆盖

| 标签 | 覆盖内容 |
|---|---|
| `calculator` | 数学工具 |
| `retrieval` | 长期记忆检索 |
| `browser` | 浏览和页面摘要工具 |
| `search` | 搜索来源发现 |
| `approval_pause` | 未审批高风险工具触发暂停 |
| `approval_resume` | 工具名级和精确工具调用审批恢复 |
| `tool_protocol` | 标准 ToolCall/ToolResult 事件 |
| `tool_failure_replanning` | 工具失败后重规划 |
| `code_exec` | code_exec 管理员和精确审批 |
| `memory_recall` | 记忆命中 |
| `mock_direct_answer` | 无工具直接回答 |

当前 `cases.json` 包含 12 个用例。

## 输出指标

| 指标 | 说明 |
|---|---|
| `total_cases` | 用例总数 |
| `passed_cases` | 通过用例数 |
| `success_rate` | 用例通过率 |
| `tool_success_rate` / `avg_tool_success_rate` | 非暂停用例工具成功率均值 |
| `approval_pause_rate` | 期望暂停用例的实际暂停率 |
| `memory_hit_rate` | 期望记忆命中用例的命中率 |
| `block_rate` | 发生策略阻断的用例占比 |
| `avg_duration_ms` | 平均耗时 |
| `coverage` | 按 tag 聚合的覆盖率 |
| `failed_metrics` | 未达阈值的指标 |
| `passed` | 总门禁结果 |

## 阈值

| 环境变量 | 默认 |
|---|---|
| `SYNAPSE_AGENT_REGRESSION_MIN_SUCCESS_RATE` | `0.8` |
| `SYNAPSE_AGENT_REGRESSION_MIN_TOOL_SUCCESS_RATE` | `0.6` |
| `SYNAPSE_AGENT_REGRESSION_MIN_APPROVAL_PAUSE_RATE` | `1.0` |
| `SYNAPSE_AGENT_REGRESSION_MIN_MEMORY_HIT_RATE` | `1.0` |
| `SYNAPSE_AGENT_REGRESSION_MAX_BLOCK_RATE` | `0.6` |
| `SYNAPSE_AGENT_REGRESSION_MAX_AVG_DURATION_MS` | `2000` |

任一指标不达标时，`summary.passed=false`，进程退出码为 1。

## 本次验证结果

本次文档更新前执行：

```powershell
python -m app.benchmarks.regression
```

结果摘要：

| 指标 | 结果 |
|---|---|
| total_cases | 12 |
| passed_cases | 12 |
| success_rate | 1.0 |
| tool_success_rate | 0.9545 |
| approval_pause_rate | 1.0 |
| memory_hit_rate | 1.0 |
| passed | true |

## CI 接入建议

1. 在 Python 单元测试后运行 `python -m app.benchmarks.regression`。
2. 保留完整 JSON 输出，便于定位缺失事件、工具或回答片段。
3. `cases.json` 变更需要评审，避免随意降低阈值或删除覆盖场景。
4. 可以按标签分层执行，先跑快速基础门禁，再跑网络/浏览类扩展门禁。

## 与真实模型 Benchmark 的边界

mock regression 继续承担稳定、低成本、可重复的基础门禁；真实模型 benchmark 适合人工验收、模型选型和周期性对比，不应替代 mock regression。两者共享 Runtime 事件语义，但 case 集、指标和运行时机保持分层。
