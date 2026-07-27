param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "baseline", "create")]
    [string]$Command = "status",

    [string]$DatabaseUrl = "",
    [string]$Name = "",
    [int]$Steps = 1,
    [uint32]$Version = 4,
    [string]$MigrationsPath = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$GatewayRoot = Join-Path $RepoRoot "services/gateway-go"
if ([string]::IsNullOrWhiteSpace($MigrationsPath)) {
    $MigrationsPath = Join-Path $GatewayRoot "migrations"
}
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    $DatabaseUrl = $env:SYNAPSE_DATABASE_URL
}
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    $DatabaseUrl = "postgres://synapse:synapse@localhost:15432/synapse?sslmode=disable"
}

$goArgs = @(
    "run",
    "./cmd/migrate",
    $Command,
    "-path",
    $MigrationsPath
)

if ($Command -ne "create") {
    $goArgs += @("-database-url", $DatabaseUrl)
}

switch ($Command) {
    "down" {
        $goArgs += @("-steps", $Steps.ToString())
    }
    "baseline" {
        $goArgs += @("-version", $Version.ToString())
    }
    "create" {
        if ([string]::IsNullOrWhiteSpace($Name)) {
            throw "migration name is required for create"
        }
        $goArgs += @("-name", $Name)
    }
}

Push-Location $GatewayRoot
try {
    go @goArgs
}
finally {
    Pop-Location
}
