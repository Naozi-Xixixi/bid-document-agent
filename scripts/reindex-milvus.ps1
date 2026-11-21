param(
  [string]$ApiBase = "http://127.0.0.1:3896/api"
)

$ErrorActionPreference = "Stop"

$Url = "$ApiBase/knowledge/vector/reindex"
Write-Host "[milvus] rebuilding vector index via $Url"
$Result = Invoke-RestMethod -Method Post -Uri $Url -ContentType "application/json" -Body "{}"
$Result | ConvertTo-Json -Depth 8
