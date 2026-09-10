$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot '.runtime\server.pid'
if (Test-Path -LiteralPath $pidFile) {
    $serverPid = [int](Get-Content -LiteralPath $pidFile)
    $server = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid" -ErrorAction SilentlyContinue
    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if ($server -and $server.ExecutablePath -eq $python -and $server.CommandLine -match '-m ispace serve') {
        & taskkill.exe /PID $serverPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not stop the web service process tree." }
        Write-Host 'Web service stopped. Scheduled checks remain configured.'
    }
}
