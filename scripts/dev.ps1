param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "proto",
        "proto-go",
        "proto-py",
        "gateway",
        "ai",
        "web",
        "agent-regression",
        "verify-agent-mode",
        "up",
        "up-mirror",
        "up-openai",
        "up-openai-mirror",
        "up-gemini",
        "up-gemini-mirror",
        "up-zhipu",
        "up-zhipu-mirror",
        "down"
    )]
    [string]$Task = "proto"
)

$ErrorActionPreference = "Stop"

function Invoke-ProtoGo {
    # 生成 Go 端 gRPC/Proto 代码。
    New-Item -ItemType Directory -Path "services/gateway-go/internal/gen" -Force | Out-Null
    protoc -I proto `
        --go_out=services/gateway-go/internal/gen --go_opt=paths=source_relative `
        --go-grpc_out=services/gateway-go/internal/gen --go-grpc_opt=paths=source_relative `
        proto/synapse/v1/agent.proto
}

function Invoke-ProtoPy {
    # 生成 Python 端 gRPC/Proto 代码并补齐包初始化文件。
    New-Item -ItemType Directory -Path "services/ai-engine-py" -Force | Out-Null
    python -m grpc_tools.protoc -I proto `
        --python_out=services/ai-engine-py `
        --grpc_python_out=services/ai-engine-py `
        proto/synapse/v1/agent.proto
    python scripts/post_gen.py services/ai-engine-py/synapse
}

function Invoke-ComposeUpWithEnvFiles {
    param(
        [string[]]$EnvFiles
    )

    $composeArgs = @()
    foreach ($envFile in $EnvFiles) {
        if (-not (Test-Path $envFile)) {
            throw "missing env file: $envFile"
        }
        $composeArgs += @("--env-file", $envFile)
    }

    $composeArgs += @("up", "--build", "-d")
    docker compose @composeArgs
}

function Invoke-VerifyAgentMode {
    Push-Location "services/ai-engine-py"
    try {
        $pythonSnippet = @'
import json
from app.config import load_config

config = load_config()
summary = {
    "model_provider": config.model_provider,
    "model_provider_alias": config.model_provider_alias,
    "agent_enabled_default": config.agent_enabled_default,
    "agent_max_plan_steps": config.agent_max_plan_steps,
    "agent_require_approval_for_high_risk": config.agent_require_approval_for_high_risk,
    "agent_memory_file": config.agent_memory_file,
    "agent_memory_max_entries_per_user": config.agent_memory_max_entries_per_user,
    "agent_memory_recall_limit": config.agent_memory_recall_limit,
    "agent_tool_http_allowlist": list(config.agent_tool_http_allowlist),
    "agent_tool_http_timeout_seconds": config.agent_tool_http_timeout_seconds,
    "agent_enable_code_execution": config.agent_enable_code_execution,
    "agent_tool_policy_json_configured": bool(config.agent_tool_policy_json.strip()),
    "agent_tool_audit_log_file": config.agent_tool_audit_log_file,
}

print(json.dumps(summary, ensure_ascii=True, indent=2))

if config.model_provider == "openai" and not config.openai_api_key:
    raise SystemExit("SYNAPSE_OPENAI_API_KEY is required when SYNAPSE_MODEL_PROVIDER=openai")
'@

        $pythonSnippet | python -
    }
    finally {
        Pop-Location
    }
}

switch ($Task) {
    "proto" {
        Invoke-ProtoGo
        Invoke-ProtoPy
    }
    "proto-go" {
        Invoke-ProtoGo
    }
    "proto-py" {
        Invoke-ProtoPy
    }
    "gateway" {
        # 本地启动 Go 网关服务。
        Push-Location "services/gateway-go"
        try {
            go run ./cmd/server
        }
        finally {
            Pop-Location
        }
    }
    "ai" {
        # 本地启动 Python AI 引擎。
        Push-Location "services/ai-engine-py"
        try {
            python -m app.main
        }
        finally {
            Pop-Location
        }
    }
    "web" {
        # 本地启动前端开发服务器。
        Push-Location "apps/web"
        try {
            npm run dev
        }
        finally {
            Pop-Location
        }
    }
    "agent-regression" {
        # 运行 Agent 评估基准与回归门禁。
        Push-Location "services/ai-engine-py"
        try {
            python -m app.benchmarks.regression
        }
        finally {
            Pop-Location
        }
    }
    "verify-agent-mode" {
        # 校验并打印当前 Agent 运行模式关键配置。
        Invoke-VerifyAgentMode
    }
    "up" {
        # 通过 Docker Compose 启动全部服务，后台运行。
        docker compose up --build -d
    }
    "up-mirror" {
        # 网络受限场景：仅使用镜像源加速启动默认配置。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.mirror.env")
    }
    "up-openai" {
        # 使用 OpenAI 配置文件启动全栈。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.openai.env")
    }
    "up-openai-mirror" {
        # 网络受限场景：镜像代理 + OpenAI 配置联合启动。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.mirror.env", "docker-compose.openai.env")
    }
    "up-gemini" {
        # 使用 Gemini 配置文件启动全栈。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.gemini.env")
    }
    "up-gemini-mirror" {
        # 网络受限场景：镜像代理 + Gemini 配置联合启动。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.mirror.env", "docker-compose.gemini.env")
    }
    "up-zhipu" {
        # 使用智谱配置文件启动全栈。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.zhipu.env")
    }
    "up-zhipu-mirror" {
        # 网络受限场景：镜像代理 + 智谱配置联合启动。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.mirror.env", "docker-compose.zhipu.env")
    }
    "down" {
        # 停止并移除 Compose 服务。
        docker compose down
    }
}
