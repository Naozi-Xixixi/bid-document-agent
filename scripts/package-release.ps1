param(
  [string]$PackageName = "new_biaoshuai_deploy"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WorkspaceRoot = (Resolve-Path (Join-Path $ProjectRoot "..")).Path
$PackagingRoot = Join-Path $WorkspaceRoot ".packaging"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$StageRoot = Join-Path $PackagingRoot "$PackageName`_$Stamp"
$StageProjectRoot = Join-Path $StageRoot "new_biaoshuai"
$ZipPath = Join-Path $PackagingRoot "$PackageName`_$Stamp.zip"
$RuntimeDataDirs = @(
  (Join-Path $ProjectRoot "backend\data"),
  (Join-Path $ProjectRoot "backend\data_verify")
)

$ExcludeDirs = @(
  ".git",
  ".venv",
  "venv",
  "env",
  ".uv-cache",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  "node_modules",
  "dist",
  "build",
  ".vite",
  "logs",
  "tmp",
  "temp",
  ".cache"
)

$ExcludeFiles = @(
  ".env",
  ".env.local",
  ".env.development",
  ".env.production",
  "*.sqlite3",
  "*.sqlite",
  "*.db",
  "*.db-shm",
  "*.db-wal",
  "*.pyc",
  "*.pyo",
  "*.log",
  "*.tmp",
  "*.map",
  "server-*.out.log",
  "server-*.err.log",
  "test-tender-ai.txt"
)

function Test-ExcludedDir {
  param([string]$Name)
  foreach ($Pattern in $ExcludeDirs) {
    if ($Name -ieq $Pattern) { return $true }
  }
  return $false
}

function Test-ExcludedFile {
  param([string]$Name)
  if ($Name -ieq ".env.example" -or $Name -ieq ".env.cloud.example") { return $false }
  foreach ($Pattern in $ExcludeFiles) {
    if ($Name -like $Pattern) { return $true }
  }
  return $false
}

function Test-RuntimeDataDir {
  param([string]$Path)
  $CleanPath = $Path.TrimEnd('\')
  foreach ($RuntimeDir in $RuntimeDataDirs) {
    if ($CleanPath.Equals($RuntimeDir.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) { return $true }
  }
  return $false
}

function Copy-CleanDirectory {
  param(
    [string]$Source,
    [string]$Destination
  )

  if (-not (Test-Path $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  }

  Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
    if ($_.PSIsContainer) {
      if ((-not (Test-ExcludedDir $_.Name)) -and (-not (Test-RuntimeDataDir $_.FullName))) {
        Copy-CleanDirectory -Source $_.FullName -Destination (Join-Path $Destination $_.Name)
      }
    } else {
      if (-not (Test-ExcludedFile $_.Name)) {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Force
      }
    }
  }
}

New-Item -ItemType Directory -Path $PackagingRoot -Force | Out-Null
if (Test-Path $StageRoot) {
  Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null

Write-Host "[package] staging project files..."
Copy-CleanDirectory -Source $ProjectRoot -Destination $StageProjectRoot

foreach ($TopLevelDoc in @("README.md", "DEPLOYMENT.md", "ALIYUN_ECS_DEPLOYMENT.md", "PACKAGE_MANIFEST.md")) {
  $DocSource = Join-Path $ProjectRoot $TopLevelDoc
  if (Test-Path $DocSource) {
    Copy-Item -LiteralPath $DocSource -Destination (Join-Path $StageRoot $TopLevelDoc) -Force
  }
}

$MermaidDistSource = Join-Path $WorkspaceRoot "mermaid-develop\packages\mermaid\dist"
if (Test-Path (Join-Path $MermaidDistSource "mermaid.min.js")) {
  Write-Host "[package] adding local Mermaid runtime dist..."
  $MermaidDistTarget = Join-Path $StageRoot "mermaid-develop\packages\mermaid\dist"
  Copy-CleanDirectory -Source $MermaidDistSource -Destination $MermaidDistTarget

  foreach ($FileName in @("LICENSE", "README.md", "README.zh-CN.md")) {
    $SourceFile = Join-Path $WorkspaceRoot "mermaid-develop\$FileName"
    if (Test-Path $SourceFile) {
      $TargetDir = Join-Path $StageRoot "mermaid-develop"
      New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
      Copy-Item -LiteralPath $SourceFile -Destination (Join-Path $TargetDir $FileName) -Force
    }
  }
} else {
  Write-Warning "Mermaid dist not found at $MermaidDistSource. Diagram preview may use fallback renderer."
}

Write-Host "[package] creating zip..."
if (Test-Path $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $StageRoot -DestinationPath $ZipPath -Force

$PackagingRootResolved = (Resolve-Path $PackagingRoot).Path
$StageRootResolved = (Resolve-Path $StageRoot).Path
if (-not $StageRootResolved.StartsWith($PackagingRootResolved, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to remove unexpected staging path: $StageRootResolved"
}
Remove-Item -LiteralPath $StageRoot -Recurse -Force

$ZipInfo = Get-Item $ZipPath
Write-Host "[package] done: $($ZipInfo.FullName)"
Write-Host "[package] size: $([Math]::Round($ZipInfo.Length / 1MB, 2)) MB"
