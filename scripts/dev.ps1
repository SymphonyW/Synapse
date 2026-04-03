param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "proto",
        "proto-go",
        "proto-py",
        "gateway",
        "ai",
        "web",
        "up",
        "up-openai",
        "up-gemini",
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
    "up" {
        # 通过 Docker Compose 启动全部服务，后台运行。
        docker compose up --build -d
    }
    "up-openai" {
        # 使用 OpenAI 配置文件启动全栈。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.openai.env")
    }
    "up-gemini" {
        # 使用 Gemini 配置文件启动全栈。
        Invoke-ComposeUpWithEnvFiles -EnvFiles @("docker-compose.gemini.env")
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
