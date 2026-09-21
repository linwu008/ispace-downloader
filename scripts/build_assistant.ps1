param([string]$PythonPath = "")
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$python = if ($PythonPath) { (Get-Command $PythonPath -ErrorAction Stop).Source } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/setup.ps1 first.' }
& $python scripts/sync_shared_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Shared asset preparation failed.' }
$appIcon = Join-Path $projectRoot 'ispace\static\logo.ico'
& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name CourseNestHelper --icon $appIcon --distpath dist --workpath build --specpath build --collect-all pypdf --collect-data ispace --collect-all playwright --collect-all keyring --collect-submodules uvicorn --collect-data tzdata --copy-metadata ispace-downloader scripts/assistant_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Assistant build failed.' }
Copy-Item -LiteralPath 'docs\WINDOWS-ASSISTANT.md' -Destination 'dist\CourseNestHelper\README.md'
Compress-Archive -LiteralPath 'dist\CourseNestHelper' -DestinationPath 'dist\CourseNestHelper-0.7.0-windows-x64.zip' -Force
Get-FileHash -LiteralPath 'dist\CourseNestHelper-0.7.0-windows-x64.zip' -Algorithm SHA256 | Format-List
