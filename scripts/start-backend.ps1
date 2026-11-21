param(
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 3896,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendRoot = Join-Path $ProjectRoot "backend"
$RuntimeRoot = Join-Path $BackendRoot "data\runtime"
$env:TEMP = Join-Path $RuntimeRoot "tmp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $RuntimeRoot "pip"
New-Item -ItemType Directory -Force -Path $env:TEMP, $env:PIP_CACHE_DIR | Out-Null
$VenvPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"

Set-Location $BackendRoot

if (-not (Test-Path $VenvPython)) {
  Write-Host "[backend] creating Python 3.11 virtual environment..."
  try {
    & py -3.11 -m venv .venv
  } catch {
    Write-Host "[backend] py -3.11 not found, falling back to python."
    & python -m venv .venv
  }
}

if (-not $SkipInstall) {
  Write-Host "[backend] installing/updating dependencies..."
  & $VenvPython -m pip install --upgrade pip
  & $VenvPython -m pip install -r requirements.txt
}

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "[backend] created backend\.env from template. Please fill QWEN_API_KEY if needed."
}

Write-Host "[backend] starting FastAPI at http://$BindHost`:$Port"
& $VenvPython -m uvicorn app.main:app --host $BindHost --port $Port
