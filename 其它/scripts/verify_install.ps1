param([switch]$KeepInstalled)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEvidence = Join-Path $taskRoot 'verification'
$taskInstall = Join-Path $taskEvidence 'installed-app'
$taskInstaller = Join-Path $taskRoot '安装包\ReviewDesk-0.1.0-Setup-win-x64.exe'
if (-not (Test-Path -LiteralPath $taskInstaller)) { throw 'Build the installer first.' }
if (-not ([IO.Path]::GetFullPath($taskInstall)).StartsWith(([IO.Path]::GetFullPath($taskEvidence)) + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe install target.' }
$taskLog = Join-Path $taskEvidence 'installer.log'
$taskProcess=Start-Process -FilePath $taskInstaller -WindowStyle Hidden -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',('/DIR="'+$taskInstall+'"'),('/LOG="'+$taskLog+'"')) -Wait -PassThru
if ($taskProcess.ExitCode -ne 0) { throw ('Installer failed: '+$taskProcess.ExitCode) }
$env:PATH=$env:SystemRoot+'\System32;'+$env:SystemRoot
$env:PYTHONPATH='C:\ReviewDesk-No-Python'
$env:QT_QPA_PLATFORM='windows'
$env:REVIEWDESK_DATA_DIR=Join-Path $taskEvidence 'installed-userdata'
$taskSmoke=Join-Path $taskEvidence 'installed-smoke.json'
$taskShot=Join-Path $taskEvidence 'installed-desktop.png'
$taskApp=Join-Path $taskInstall 'ReviewDesk.exe'
$taskRun=Start-Process -FilePath $taskApp -WorkingDirectory $taskEvidence -WindowStyle Hidden -ArgumentList @('--smoke-test',('"'+$taskSmoke+'"'),'--screenshot',('"'+$taskShot+'"')) -Wait -PassThru
if ($taskRun.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $taskSmoke)) { throw ('Installed app failed: '+$taskRun.ExitCode) }
$taskResult=Get-Content -LiteralPath $taskSmoke -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $taskResult.ok -or -not $taskResult.frozen -or -not $taskResult.report_persisted -or -not $taskResult.markdown_exported -or -not $taskResult.json_exported) {throw 'Smoke validation failed.'}
$taskUninstallExit=$null
if (-not $KeepInstalled) {
    $taskUninstaller=Join-Path $taskInstall 'unins000.exe'
    $taskUninstall=Start-Process -FilePath $taskUninstaller -WindowStyle Hidden -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -Wait -PassThru
    $taskUninstallExit=$taskUninstall.ExitCode
    if ($taskUninstallExit -ne 0 -or (Test-Path -LiteralPath $taskApp)) {throw 'Uninstall verification failed.'}
}
$taskReport=@{installer_exit=$taskProcess.ExitCode; application_exit=$taskRun.ExitCode; uninstall_exit=$taskUninstallExit; application_removed=(-not (Test-Path -LiteralPath $taskApp)); preserved_user_report=(Test-Path -LiteralPath (Join-Path $env:REVIEWDESK_DATA_DIR 'reviews.db')); isolated_path=$env:PATH; pythonpath=$env:PYTHONPATH; host_os=[System.Environment]::OSVersion.VersionString; other_physical_computer_tested=$false; smoke=$taskResult}
$taskReport | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskEvidence 'installer-verification.json') -Encoding UTF8
$taskReport | ConvertTo-Json -Depth 5
