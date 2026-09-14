$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$runtime = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$node = if ($nodeCommand) { $nodeCommand.Source } elseif (Test-Path 'D:\node.js\node.exe') { 'D:\node.js\node.exe' } else { throw 'Node.js 24 is needed for the local website preview.' }
$website = 'http://127.0.0.1:8787'
$ready = $false
try { $ready = ((Invoke-RestMethod "$website/api/health" -TimeoutSec 2).version -eq '0.4.0') } catch { }
if (-not $ready) {
    $serverScript = Join-Path $projectRoot 'cloud\dev.mjs'
    $process = Start-Process -FilePath $node -ArgumentList @('"' + $serverScript + '"') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$runtime\cloud.out.log" -RedirectStandardError "$runtime\cloud.err.log"
    $process.Id | Set-Content -LiteralPath "$runtime\cloud.pid"
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try { $ready = ((Invoke-RestMethod "$website/api/health" -TimeoutSec 2).version -eq '0.4.0') } catch { }
        if ($ready -or $process.HasExited) { break }
    }
}
if (-not $ready) { throw 'Website startup failed. Check .runtime/cloud.err.log.' }
& "$PSScriptRoot\start.ps1"
Start-Process $website
