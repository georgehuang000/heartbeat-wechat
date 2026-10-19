param(
    [string]$ExpectedThumbprint = '',
    [ValidateSet('standard','offline')][string]$WebViewMode = 'standard'
)
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
Set-Location -LiteralPath $projectDirectory
$qaDirectory = Join-Path $projectDirectory 'build/installer-smoke'
$appDirectory = [IO.Path]::GetFullPath((Join-Path $qaDirectory 'app'))
$qaPrefix = [IO.Path]::GetFullPath($qaDirectory) + [IO.Path]::DirectorySeparatorChar
if (-not $appDirectory.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'QA path escapes workspace.' }
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8D76F019-61CC-4993-A8E3-78C709614D0A}_is1'
if (Test-Path -LiteralPath $uninstallKey) { throw 'An existing registered installation was found. Do not alter it for smoke testing.' }
if (Test-Path -LiteralPath (Join-Path $appDirectory 'unins000.exe')) { throw 'A previous QA install requires inspection before continuing.' }
New-Item -ItemType Directory -Path $qaDirectory -Force | Out-Null
$installerDirectory = Join-Path $projectDirectory $(if ($WebViewMode -eq 'offline') { 'release/installer-offline' } else { 'release/installer' })
$installer = Join-Path $installerDirectory 'HeartWeChat-Setup-0.1.1-x64.exe'
$payload = Join-Path $projectDirectory 'release/installer-payload/心动微信.exe'
$manifestPath = Join-Path $installerDirectory 'release-manifest.json'
$releaseManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($releaseManifest.sha256 -ne (Get-FileHash -LiteralPath $installer).Hash.ToLowerInvariant()) { throw 'Release manifest identifies a different installer.' }
$installerSignature = Get-AuthenticodeSignature -LiteralPath $installer
$signedRelease = $installerSignature.Status -eq 'Valid'
$ExpectedThumbprint = ($ExpectedThumbprint -replace '\s', '').ToUpperInvariant()
if ($signedRelease -and $ExpectedThumbprint -notmatch '^[0-9A-F]{40}$') { throw 'Pass -ExpectedThumbprint to verify all three signed executables.' }
if (-not $signedRelease -and ($installerSignature.Status -ne 'NotSigned' -or $ExpectedThumbprint)) { throw 'Unexpected installer signature state.' }
function Check-SignedReleaseFile([string]$Path) {
    $fileSignature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($fileSignature.Status -ne 'Valid' -or $fileSignature.SignerCertificate.Thumbprint -ne $ExpectedThumbprint -or -not $fileSignature.TimeStamperCertificate) {
        throw ('Missing, invalid, untimestamped, or wrong-publisher signature: ' + $Path)
    }
}
if ($signedRelease) { Check-SignedReleaseFile $installer }
$profile = Join-Path $qaDirectory 'preserved-profile'
New-Item -ItemType Directory -Path $profile -Force | Out-Null
$settings = Join-Path $profile 'settings.json'
[IO.File]::WriteAllText($settings, '{"qa_sentinel":"keep-on-upgrade-and-uninstall"}', [Text.UTF8Encoding]::new($false))
$before = (Get-FileHash -LiteralPath $settings -Algorithm SHA256).Hash
$startMenuLink = Join-Path ([Environment]::GetFolderPath('Programs')) '心动微信/心动微信.lnk'
$qaStartMenuLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'HeartWeChat-Installer-QA/心动微信.lnk'
if ((Test-Path -LiteralPath $startMenuLink) -or (Test-Path -LiteralPath $qaStartMenuLink)) { throw 'An existing Start Menu shortcut was found; do not replace it during QA.' }
$report = [ordered]@{ installer_sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant(); signed = $signedRelease; three_signatures_verified = $false; installed = $false; upgraded = $false; payload_matches = $false; runtime_passed = $false; uninstalled = $false; profile_preserved = $false; user_added_file_preserved = $false; no_shortcuts_created = $false; real_profile_touched = $false; wechat_operated = $false; model_called = $false }
$report.webview2_bundled = [bool]$releaseManifest.webview2_bundled
$report.webview2_path = 'not_checked'
function Invoke-QuietProcess([string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(120000)) { throw ('Timed out; process left running for inspection: ' + $process.Id) }
    if ($process.ExitCode -ne 0) { throw ('Process failed with exit code: ' + $process.ExitCode) }
}
$arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS','/TASKS=""',('/DIR="'+$appDirectory+'"'),'/GROUP="HeartWeChat-Installer-QA"',('/LOG="'+(Join-Path $qaDirectory 'install.log')+'"'))
Invoke-QuietProcess $installer $arguments
$installLog = Get-Content -LiteralPath (Join-Path $qaDirectory 'install.log') -Raw
if ($installLog.Contains('WebView2: existing Runtime detected; bundled installer skipped.')) { $report.webview2_path = 'existing_runtime_skipped' }
elseif ($installLog.Contains('WebView2: Runtime registration verified after installation.')) { $report.webview2_path = 'bundled_install_verified' }
if ($report.webview2_bundled -and $report.webview2_path -eq 'not_checked') { throw 'Missing WebView2 prerequisite evidence in installation log.' }
$installedExe = Join-Path $appDirectory '心动微信.exe'
if (-not (Test-Path -LiteralPath $installedExe)) { throw 'Missing installed payload.' }
$registered = Get-ItemProperty -LiteralPath $uninstallKey
if ($registered.InstallLocation.TrimEnd('\') -ne $appDirectory.TrimEnd('\')) { throw 'Unexpected registration path.' }
$report.installed = $true
$sentinel = Join-Path $appDirectory 'user-added-file.txt'
[IO.File]::WriteAllText($sentinel, 'Do not remove user-added content.')
$upgradeArguments = $arguments | ForEach-Object { if ($_.StartsWith('/LOG=')) { '/LOG="'+(Join-Path $qaDirectory 'upgrade.log')+'"' } else { $_ } }
Invoke-QuietProcess $installer $upgradeArguments
$report.upgraded = $true
$report.no_shortcuts_created = -not (Test-Path -LiteralPath $startMenuLink) -and -not (Test-Path -LiteralPath $qaStartMenuLink)
$report.payload_matches = (Get-FileHash -LiteralPath $installedExe -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash
Invoke-QuietProcess $installedExe @('--check-runtime', ('--data-dir="'+$profile+'"'))
$runtime = Get-Content -LiteralPath (Join-Path $profile 'runtime-check.json') -Raw | ConvertFrom-Json
$report.runtime_passed = $runtime.passed -eq $true
$uninstaller = [IO.Path]::GetFullPath((Join-Path $appDirectory 'unins000.exe'))
if (-not $uninstaller.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Uninstaller is outside QA workspace.' }
if ($signedRelease) {
    Check-SignedReleaseFile $installedExe
    Check-SignedReleaseFile $uninstaller
    $report.three_signatures_verified = $true
}
Invoke-QuietProcess $uninstaller @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+(Join-Path $qaDirectory 'uninstall.log')+'"'))
$report.uninstalled = -not (Test-Path -LiteralPath $installedExe) -and -not (Test-Path -LiteralPath $uninstallKey)
$report.profile_preserved = (Test-Path -LiteralPath $settings) -and ((Get-FileHash -LiteralPath $settings -Algorithm SHA256).Hash -eq $before)
$report.user_added_file_preserved = Test-Path -LiteralPath $sentinel
$report.passed = $report.installed -and $report.upgraded -and $report.payload_matches -and $report.runtime_passed -and $report.uninstalled -and $report.profile_preserved -and $report.user_added_file_preserved -and $report.no_shortcuts_created
$json = $report | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $installerDirectory 'installation-check.json'), $json, [Text.UTF8Encoding]::new($false))
$json
if (-not $report.passed) { throw 'Installer verification failed.' }
$manifestPath = Join-Path $installerDirectory 'release-manifest.json'
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.sha256 -ne $report.installer_sha256) { throw 'Release manifest identifies a different installer.' }
    $manifest.installation_check = 'passed'
    [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
}
