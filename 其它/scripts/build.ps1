param([string]$InnoCompiler = "")
$ErrorActionPreference = 'Stop'
$taskOther = Split-Path -Parent $PSScriptRoot
$taskRoot = Split-Path -Parent $taskOther
$taskFrontend = Join-Path $taskRoot '前端功能'
$taskBackend = Join-Path $taskRoot '后端功能'
$taskDist = Join-Path $taskOther '运行文件'
$taskBuild = Join-Path $taskOther '构建缓存'
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskOther '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create the environment at 其它/.venv and install 其它/requirements-build.txt first.' }
$env:PYTHONPATH = $taskFrontend + ';' + $taskBackend
$env:PYTHONIOENCODING = 'utf-8'
# PyInstaller searches PATH for DLLs. Codex document runtimes can expose an ICU
# with the same filename but incompatible exports, so build with a minimal PATH.
$taskBasePython = & $taskPython -c 'import sys; print(sys.base_prefix)'
$env:PATH = (Join-Path $taskOther '.venv\Scripts') + ';' + $taskBasePython + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
& $taskPython 其它\scripts\make_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon build failed.' }
& $taskPython 其它\scripts\collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& $taskPython -m PyInstaller --clean --noconfirm --windowed --name ReviewDesk --paths $taskFrontend --paths $taskBackend --distpath $taskDist --workpath $taskBuild --specpath (Join-Path $taskOther 'packaging') --icon (Join-Path $taskFrontend 'resources\reviewdesk.ico') --add-data ($taskFrontend+'\resources;resources') --add-data ($taskOther+'\licenses;licenses') --add-data ($taskOther+'\LICENSE;.') --add-data ($taskOther+'\THIRD_PARTY_NOTICES.md;.') (Join-Path $taskFrontend 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
Copy-Item -LiteralPath 其它\README.md,其它\THIRD_PARTY_NOTICES.md,其它\LICENSE -Destination (Join-Path $taskDist 'ReviewDesk') -Force
if (-not $InnoCompiler) { $InnoCompiler = Join-Path $taskOther '.tools\InnoSetup\ISCC.exe' }
if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw 'Install Inno Setup 6.7.3 and pass -InnoCompiler <ISCC.exe>.' }
& $InnoCompiler 其它\packaging\reviewdesk.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
Get-FileHash -LiteralPath 其它\安装包\ReviewDesk-0.1.0-Setup-win-x64.exe -Algorithm SHA256 | Format-List
