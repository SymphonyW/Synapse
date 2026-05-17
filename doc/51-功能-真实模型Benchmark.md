# 功能：真实模型 Benchmark

真实模型 Benchmark 用于在同一套 Synapse Agent Runtime 下，横向比较不同真实 provider 的执行效果。它和 mock regression 的分工很清楚：

| 评测 | 解决的问题 | 典型用途 |
|---|---|---|
| mock regression | Runtime 行为是否退化 | CI 门禁、协议回归、快速定位 |
| live benchmark | 真实 provider 谁更稳、更快、更会完成任务 | 模型选型、人工验收、周期性基准对比 |

## 相关实现

| 文件 | 说明 |
|---|---|
| [live_benchmark.py](../services/ai-engine-py/app/benchmarks/live_benchmark.py) | CLI、provider 配置、case 执行、审批恢复流 |
| [live_cases.json](../services/ai-engine-py/app/benchmarks/live_cases.json) | 真实模型任务集 |
| [live_metrics.py](../services/ai-engine-py/app/benchmarks/live_metrics.py) | 指标聚合、规则评分、tag 汇总 |
| [live_report.py](../services/ai-engine-py/app/benchmarks/live_report.py) | JSON / Markdown / 控制台报告 |
| [test_benchmarks_live.py](../services/ai-engine-py/tests/test_benchmarks_live.py) | benchmark 自身单元测试 |

## 首版覆盖

`live_cases.json` 当前包含 12 个场景，覆盖：

| 能力 | 场景 |
|---|---|
| 无工具回答 | direct answer |
| 工具调用 | calculator、retrieval、browser search、browser summarize |
| 审批治理 | 高风险暂停、暂停后恢复继续执行 |
| 失败恢复 | 工具失败后的 replan |
| 生成质量 | 长文本生成 |
| 任务复杂度 | 多步任务 |
| 记忆 | recall、write |

每个 case 包含 `id`、`title`、`prompt`、`metadata`、`tags`、`expectations`、`allowed_tools`、`timeout_seconds`、`judge_rules`。  
其中 `expectations.resume_after_pause=true` 的 case 会先验证暂停，再基于 `approved_tool_call` 自动继续同一任务，覆盖真实审批恢复链路。

## Provider 配置

单 provider 可直接复用现有 OpenAI-compatible 环境变量：

```powershell
$env:SYNAPSE_OPENAI_API_KEY = "replace-me"
$env:SYNAPSE_OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:SYNAPSE_OPENAI_MODEL = "gpt-4o-mini"
```

多 provider 对比时，建议使用 provider 专属变量，避免互相覆盖：

```powershell
$env:SYNAPSE_LIVE_BENCHMARK_OPENAI_API_KEY = "..."
$env:SYNAPSE_LIVE_BENCHMARK_OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:SYNAPSE_LIVE_BENCHMARK_OPENAI_MODEL = "gpt-4o-mini"

$env:SYNAPSE_LIVE_BENCHMARK_ZHIPU_API_KEY = "..."
$env:SYNAPSE_LIVE_BENCHMARK_ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:SYNAPSE_LIVE_BENCHMARK_ZHIPU_MODEL = "glm-4-flash"
```

命令行参数 `--base-url`、`--model`、`--api-key` 仅用于单 provider 运行；多 provider 运行时请使用环境变量。  
如果只想检查配置，不想发起真实调用：

```powershell
Set-Location services/ai-engine-py
python -m app.benchmarks.live_benchmark --provider openai --dry-run-config-check
Set-Location ..\..
```

## 运行方式

```powershell
Set-Location services/ai-engine-py
python -m app.benchmarks.live_benchmark --provider openai
python -m app.benchmarks.live_benchmark --provider zhipu
python -m app.benchmarks.live_benchmark --providers openai,zhipu --markdown
python -m app.benchmarks.live_benchmark --provider openai --case-id calculator_basic
python -m app.benchmarks.live_benchmark --provider openai --tag browser --fail-fast
Set-Location ..\..
```

支持参数：

| 参数 | 说明 |
|---|---|
| `--provider` | 单个 provider alias |
| `--providers` | 多 provider 逗号分隔 |
| `--case-id` | 只运行指定 case，可重复 |
| `--tag` | 只运行包含指定 tag 的 case，可重复 |
| `--output` | 报告输出目录 |
| `--fail-fast` | provider 首个 case 失败后停止 |
| `--dry-run-config-check` | 只检查配置 |
| `--markdown` | 额外输出 Markdown 对比报告 |

## 报告解读

每次运行都会生成：

1. 每个 provider 一份 JSON；
2. `comparison.live-benchmark.json`；
3. 可选 `comparison.live-benchmark.md`；
4. 控制台摘要。

provider 摘要至少包含：

| 指标 | 含义 |
|---|---|
| `total_cases` / `passed_cases` / `success_rate` | 总量、通过量、通过率 |
| `avg_latency_ms` | 平均耗时 |
| `avg_tool_success_rate` | 有工具调用 case 的平均工具成功率 |
| `pause_correctness_rate` | 审批暂停语义是否符合预期 |
| `replan_cases` | 发生 replan 的 case 数 |
| `failed_cases` | 失败 case 列表 |

case 级还会记录：

| 类别 | 指标 |
|---|---|
| 任务 | `completed_or_paused_correctly`、`final_status`、`latency_ms`、`total_events`、`final_answer_chars` |
| 工具 | `required_tool_called`、`unexpected_tool_called`、`tool_call_count`、`tool_success_rate`、`tool_failure_count`、`replan_count` |
| 治理 | `expected_pause_matched`、`blocked_action_count` |
| 记忆 | `memory_recall_hit_count`、`memory_write_happened` |
| 回答质量 | 关键字、必要结论、空答、截断 |

`comparison` 报告额外提供：

1. provider 总表；
2. 按 case 横向对比；
3. 按 tag 横向对比。

## 当前限制

1. 首版质量判分是规则评分，不是 LLM-as-a-judge；代码结构已留出后续替换空间。
2. 真实 provider 仍走现有 Runtime 的启发式 planner，因此首版更适合评估“真实生成 + 工具链路整体表现”，不是专门评估模型自主选工具能力。
3. token / cost 字段已保留，但当前 Runtime 还没有稳定、统一的 usage 事件；报告中先输出 `null`，待 provider 侧 usage 捕获能力补齐后再填充。
4. live benchmark 依赖外部 provider 和网络，不应作为 mock regression 的替代门禁。
