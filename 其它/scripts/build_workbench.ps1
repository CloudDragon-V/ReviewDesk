param([string]$InnoCompiler = '', [string]$NodeExecutable = '', [switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$taskOther = Split-Path -Parent $PSScriptRoot
$taskRoot = Split-Path -Parent $taskOther
$taskFrontend = Join-Path $taskRoot '前端功能'
$taskBackend = Join-Path $taskRoot '后端功能'
$taskWeb = Join-Path $taskFrontend 'web'
$taskPython = Join-Path $taskOther '.venv\Scripts\python.exe'
$taskDist = Join-Path $taskOther '运行文件'
$taskBuild = Join-Path $taskOther '构建缓存'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create 其它/.venv and install 其它/requirements-build.txt first.' }
Set-Location -LiteralPath $taskRoot
if (-not $SkipFrontend) {
    if (-not $NodeExecutable) { $NodeExecutable = (Get-Command node.exe -ErrorAction Stop).Source }
    $taskVite = Join-Path $taskWeb 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $taskVite)) { throw 'Run npm ci in 前端功能/web before building.' }
    Push-Location -LiteralPath $taskWeb
    try {
        & $NodeExecutable $taskVite build
        if ($LASTEXITCODE -ne 0) { throw 'Vue production build failed.' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath (Join-Path $taskWeb 'dist\index.html'))) { throw 'Vue dist/index.html is missing.' }
$env:PYTHONPATH = $taskFrontend + ';' + $taskBackend
$env:PYTHONIOENCODING = 'utf-8'
$taskBasePython = & $taskPython -c 'import sys; print(sys.base_prefix)'
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the Python base runtime.' }
# Avoid document-runtime ICU DLLs with incompatible exports being accidentally bundled.
$env:PATH = (Join-Path $taskOther '.venv\Scripts') + ';' + $taskBasePython + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
& $taskPython (Join-Path $PSScriptRoot 'collect_workbench_licenses.py')
if ($LASTEXITCODE -ne 0) { throw 'Workbench license collection failed.' }
& $taskPython -m PyInstaller --clean --noconfirm --windowed --name ReviewDeskReading --paths $taskBackend --distpath $taskDist --workpath $taskBuild --specpath (Join-Path $taskOther 'packaging') --icon (Join-Path $taskFrontend 'resources\reviewdesk.ico') --add-data ($taskWeb+'\dist;web') --add-data ($taskOther+'\licenses-workbench;licenses') --add-data ($taskOther+'\LICENSE;.') --add-data ($taskOther+'\WORKBENCH_THIRD_PARTY_NOTICES.md;.') --exclude-module PySide6 --exclude-module shiboken6 --exclude-module tkinter --exclude-module pytest --exclude-module httpx --exclude-module httpcore --exclude-module uvicorn.loops.uvloop --exclude-module uvicorn.protocols.http.httptools_impl --exclude-module uvicorn.protocols.websockets (Join-Path $taskFrontend 'workbench.py')
if ($LASTEXITCODE -ne 0) { throw 'Workbench PyInstaller build failed.' }
$taskBundle = Join-Path $taskDist 'ReviewDeskReading'
Copy-Item -LiteralPath (Join-Path $taskOther 'README.md'),(Join-Path $taskOther 'WORKBENCH_THIRD_PARTY_NOTICES.md'),(Join-Path $taskOther 'LICENSE') -Destination $taskBundle -Force
& $taskPython (Join-Path $PSScriptRoot 'archive_workbench.py') --check-secrets
if ($LASTEXITCODE -ne 0) { throw 'Credential preflight rejected the installer inputs.' }
if (-not $InnoCompiler) { $InnoCompiler = Join-Path $taskOther '.tools\InnoSetup\ISCC.exe' }
if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw 'Inno Setup compiler missing; pass -InnoCompiler.' }
& $InnoCompiler (Join-Path $taskOther 'packaging\reviewdesk-workbench.iss')
if ($LASTEXITCODE -ne 0) { throw 'Workbench installer compilation failed.' }
Get-FileHash -LiteralPath (Join-Path $taskOther '安装包\ReviewDeskReading-0.2.0-Setup-win-x64.exe') -Algorithm SHA256 | Format-List
