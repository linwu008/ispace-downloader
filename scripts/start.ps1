$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) { & "$PSScriptRoot\setup.ps1" }
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$url = 'http://127.0.0.1:8765'
$ready = $false
try { $state = Invoke-RestMethod "$url/api/state" -TimeoutSec 2; $ready = ($state.version -eq '0.1.0') } catch { }
if (-not $ready) {
    $runtime = Join-Path $projectRoot '.runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $process = Start-Process -FilePath $python -ArgumentList @('-m','ispace','serve') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$runtime\server.out.log" -RedirectStandardError "$runtime\server.err.log"
    $process.Id | Set-Content -LiteralPath "$runtime\server.pid"
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try { $state = Invoke-RestMethod "$url/api/state" -TimeoutSec 2; $ready = ($state.version -eq '0.1.0') } catch { }
        if ($ready) { break }
        if ($process.HasExited) { break }
    }
}
if (-not $ready) { throw 'Could not start the app. Check .runtime/server.err.log and whether port 8765 is in use.' }
Start-Process $url
