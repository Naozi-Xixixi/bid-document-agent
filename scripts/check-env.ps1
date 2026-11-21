$ErrorActionPreference = "Continue"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"

Write-Host "== new_biaoshuai environment check =="
Write-Host "Project: $ProjectRoot"

Write-Host ""
Write-Host "[Python]"
$VenvPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
  & $VenvPython --version
  & $VenvPython -c "import sys; print('executable=' + sys.executable)"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  py -3.11 --version
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  python --version
} else {
  Write-Warning "Python was not found. Please install Python 3.11+."
}

Write-Host ""
Write-Host "[Node.js]"
if (Get-Command node -ErrorAction SilentlyContinue) { node --version } else { Write-Warning "node was not found. Please install Node.js 20+." }
if (Get-Command npm -ErrorAction SilentlyContinue) { npm --version } else { Write-Warning "npm was not found." }

Write-Host ""
Write-Host "[Backend env]"
$BackendEnv = Join-Path $BackendRoot ".env"
if (Test-Path $BackendEnv) {
  Write-Host "backend\.env exists"
  $EnvText = Get-Content $BackendEnv -Raw
  if ($EnvText -match "(?m)^QWEN_API_KEY=\S+") { Write-Host "QWEN_API_KEY is configured" } else { Write-Warning "QWEN_API_KEY is empty. AI features will degrade." }
  if ($EnvText -match "(?m)^VECTOR_BACKEND=milvus\s*$") { Write-Host "VECTOR_BACKEND=milvus" } else { Write-Host "VECTOR_BACKEND=local or unset" }
  if ($EnvText -match "(?m)^MILVUS_URI=(\S+)") { Write-Host "MILVUS_URI=$($Matches[1])" }
} else {
  Write-Warning "backend\.env is missing. Copy backend\.env.example first."
}

Write-Host ""
Write-Host "[Frontend env]"
$FrontendEnv = Join-Path $FrontendRoot ".env.local"
if (Test-Path $FrontendEnv) { Write-Host "frontend\.env.local exists" } else { Write-Warning "frontend\.env.local is missing. Copy frontend\.env.example first." }

Write-Host ""
Write-Host "[Mermaid]"
$CandidatePaths = @(
  (Join-Path $ProjectRoot "mermaid-develop\packages\mermaid\dist\mermaid.min.js"),
  (Join-Path (Split-Path $ProjectRoot -Parent) "mermaid-develop\packages\mermaid\dist\mermaid.min.js")
)
$FoundMermaid = $false
foreach ($Path in $CandidatePaths) {
  if (Test-Path $Path) {
    Write-Host "found: $Path"
    $FoundMermaid = $true
  }
}
if (-not $FoundMermaid) { Write-Warning "Mermaid dist was not found. Diagram preview may use the fallback renderer." }

Write-Host ""
Write-Host "[Ports]"
foreach ($Port in @(3896, 5181)) {
  $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($Connections) {
    foreach ($Conn in $Connections) {
      $Proc = Get-Process -Id $Conn.OwningProcess -ErrorAction SilentlyContinue
      Write-Host "port $Port is occupied by PID $($Conn.OwningProcess) $($Proc.ProcessName)"
    }
  } else {
    Write-Host "port $Port is free"
  }
}
