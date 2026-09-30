param([switch]$KeepInstalled)
$ErrorActionPreference = 'Stop'
$taskOther = Split-Path -Parent $PSScriptRoot
$taskEvidence = Join-Path $taskOther 'verification'
$taskInstall = Join-Path $taskEvidence 'v02-installed-app'
$taskData = Join-Path $taskEvidence 'v02-installed-userdata'
$taskInstaller = Join-Path $taskOther '安装包\ReviewDeskReading-0.2.0-Setup-win-x64.exe'
$taskEvidenceResolved = [IO.Path]::GetFullPath($taskEvidence)
$taskInstallResolved = [IO.Path]::GetFullPath($taskInstall)
if (-not $taskInstallResolved.StartsWith($taskEvidenceResolved + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe isolated install directory.' }
if (-not (Test-Path -LiteralPath $taskInstaller)) { throw 'Build the v0.2 installer first.' }
$taskNewRegistry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A3C99F8B-48EE-4C35-8C32-5F773401F8E7}_is1'
if (Test-Path -LiteralPath $taskNewRegistry) {
    $taskExistingInstall = (Get-ItemProperty -LiteralPath $taskNewRegistry).InstallLocation
    if ($taskExistingInstall -and [IO.Path]::GetFullPath($taskExistingInstall).TrimEnd('\') -ne $taskInstallResolved.TrimEnd('\')) { throw 'An existing normal v0.2 installation is registered; refusing to replace it during verification.' }
}
$taskLegacy = Join-Path $env:LOCALAPPDATA 'Programs\ReviewDesk\ReviewDesk.exe'
$taskLegacyHash = if (Test-Path -LiteralPath $taskLegacy) { (Get-FileHash -LiteralPath $taskLegacy -Algorithm SHA256).Hash } else { $null }
function Invoke-TaskProcess([string]$Executable, [string[]]$TaskArguments, [int]$TimeoutSeconds = 180) {
    $taskProcess = Start-Process -FilePath $Executable -WindowStyle Hidden -ArgumentList $TaskArguments -WorkingDirectory $taskEvidence -PassThru
    if (-not $taskProcess.WaitForExit($TimeoutSeconds * 1000)) {
        $taskProcess.Kill()
        $taskProcess.WaitForExit()
        throw ('Verification process timed out: ' + $Executable)
    }
    $taskProcess.WaitForExit()
    if ($taskProcess.ExitCode -ne 0) { throw ('Verification process failed: ' + $taskProcess.ExitCode + ' ' + $Executable) }
    return $taskProcess.ExitCode
}
$taskLog = Join-Path $taskEvidence 'v02-installer.log'
$taskInstallExit = Invoke-TaskProcess $taskInstaller @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS',('/DIR="'+$taskInstall+'"'),('/LOG="'+$taskLog+'"'))
$env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
$env:PYTHONPATH = 'C:\ReviewDesk-No-Python'
$env:PYTHONHOME = $null
$taskSmoke = Join-Path $taskEvidence 'v02-installed-smoke.json'
$taskApp = Join-Path $taskInstall 'ReviewDeskReading.exe'
$taskInstalledExeHash = (Get-FileHash -LiteralPath $taskApp -Algorithm SHA256).Hash.ToLowerInvariant()
$taskApplicationExit = Invoke-TaskProcess $taskApp @('--no-browser','--data-dir',('"'+$taskData+'"'),'--smoke-test',('"'+$taskSmoke+'"'))
$taskResult = Get-Content -LiteralPath $taskSmoke -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $taskResult.ok -or -not $taskResult.frozen -or -not $taskResult.auto_exported -or -not $taskResult.download_verified -or -not $taskResult.frontend_served -or $taskResult.api_called) { throw 'Frozen app smoke validation failed.' }
$taskUninstallExit = $null
if (-not $KeepInstalled) {
    $taskUninstallExit = Invoke-TaskProcess (Join-Path $taskInstall 'unins000.exe') @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART')
    if (Test-Path -LiteralPath $taskApp) { throw 'Uninstaller left the executable behind.' }
}
$taskLegacyPreserved = if ($taskLegacyHash) { (Test-Path -LiteralPath $taskLegacy) -and (Get-FileHash -LiteralPath $taskLegacy -Algorithm SHA256).Hash -eq $taskLegacyHash } else { -not (Test-Path -LiteralPath $taskLegacy) }
if (-not $taskLegacyPreserved) { throw 'Legacy v0.1 executable changed during v0.2 verification.' }
$taskReport = @{installer_exit=$taskInstallExit;application_exit=$taskApplicationExit;uninstall_exit=$taskUninstallExit;application_removed=(-not (Test-Path -LiteralPath $taskApp));preserved_user_knowledge=(Test-Path -LiteralPath (Join-Path $taskData 'knowledge.db'));preserved_user_manuals=(Test-Path -LiteralPath (Join-Path $taskData 'manuals'));legacy_v01_preserved=$taskLegacyPreserved;isolated_path=$env:PATH;pythonpath=$env:PYTHONPATH;host_os=[System.Environment]::OSVersion.VersionString;other_physical_computer_tested=$false;api_called=$false;smoke=$taskResult}
$taskReport.verified_at = [DateTime]::UtcNow.ToString('o')
$taskReport.installer_sha256 = (Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
$taskReport.installed_executable_sha256 = $taskInstalledExeHash
$taskReport | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $taskEvidence 'v02-installer-verification.json') -Encoding UTF8
$taskReport | ConvertTo-Json -Depth 6
