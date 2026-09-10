$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.tools\cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.tools\python'
if (-not (Test-Path '.bootstrap\Scripts\uv.exe')) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Install 64-bit Python 3.13 from https://www.python.org/downloads/windows/ first.' }
    & $python.Source -m venv .bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Could not create bootstrap environment.' }
    & '.\.bootstrap\Scripts\python.exe' -m pip install 'uv==0.12.12'
    if ($LASTEXITCODE -ne 0) { throw 'Could not install uv.' }
}
$uv = Join-Path $projectRoot '.bootstrap\Scripts\uv.exe'
& $uv python install 3.13
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python 3.13.' }
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & $uv venv --python 3.13 .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create project environment.' }
}
& $uv pip sync requirements.lock --python .venv\Scripts\python.exe
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $uv pip install --python .venv\Scripts\python.exe --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
& '.\.venv\Scripts\python.exe' -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed. Rerun setup when the network is available.' }
Write-Host 'Ready. Run start.cmd to open iSpace Downloader.'
