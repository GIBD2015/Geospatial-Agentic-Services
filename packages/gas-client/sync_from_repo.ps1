param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$Source = Join-Path $RepoRoot "gas_client"
$Target = Join-Path $PSScriptRoot "gas_client"

if (-not (Test-Path $Source)) {
    throw "Source gas_client package was not found at $Source"
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -Force (Join-Path $Source "client.py") (Join-Path $Target "client.py")
Copy-Item -Force (Join-Path $Source "__init__.py") (Join-Path $Target "__init__.py")

Write-Host "Synced gas_client package from $Source to $Target"
