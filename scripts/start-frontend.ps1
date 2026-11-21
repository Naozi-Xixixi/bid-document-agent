param(
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 5181,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $ProjectRoot "frontend"

Set-Location $FrontendRoot

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm was not found. Please install Node.js 20+ first."
}

if ((-not (Test-Path "node_modules")) -and (-not $SkipInstall)) {
  Write-Host "[frontend] installing dependencies..."
  if (Test-Path "package-lock.json") {
    npm ci
  } else {
    npm install
  }
}

if (-not (Test-Path ".env.local")) {
  Copy-Item ".env.example" ".env.local"
  Write-Host "[frontend] created frontend\.env.local from template."
}

Write-Host "[frontend] starting Vite at http://$BindHost`:$Port"
npm run dev -- --host $BindHost --port $Port
