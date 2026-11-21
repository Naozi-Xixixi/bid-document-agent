param(
  [int[]]$Ports = @(3896, 5181)
)

$ErrorActionPreference = "Continue"

foreach ($Port in $Ports) {
  $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if (-not $Connections) {
    Write-Host "[ports] $Port is free."
    continue
  }

  foreach ($Conn in $Connections) {
    $PidToStop = [int]$Conn.OwningProcess
    if ($PidToStop -le 0) { continue }
    $Proc = Get-Process -Id $PidToStop -ErrorAction SilentlyContinue
    $Name = if ($Proc) { $Proc.ProcessName } else { "unknown" }
    Write-Host "[ports] stopping PID $PidToStop ($Name) on port $Port..."
    Stop-Process -Id $PidToStop -Force -ErrorAction SilentlyContinue
  }
}
