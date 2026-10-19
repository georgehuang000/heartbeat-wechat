$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDirectory
$protectedFiles = @('release/installer/HeartWeChat-Setup-0.1.1-x64.exe','promotion-heart-wechat/dist/index.html')
$before = @{}
foreach ($file in $protectedFiles) { $before[$file] = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash }
function Expect-Rejection([scriptblock]$Action, [string]$Message) {
    $rejected = $false
    try { & $Action | Out-Null } catch {
        if ($_.Exception.Message -notmatch $Message) { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw 'A release guard unexpectedly allowed the action.' }
}
Expect-Rejection { & './scripts/build_installer.ps1' -RequireSignedRelease -SkipAppBuild } 'certificate thumbprint'
Expect-Rejection { & './scripts/build_installer.ps1' -SkipAppBuild -TimestampUrl 'https://example.com/timestamp' } 'without a certificate thumbprint'
Expect-Rejection { & './scripts/build_installer.ps1' -SkipAppBuild -PublishDownload } 'requires signing'
$unsignedInstaller = 'build/installer-before-signing-workflow/HeartWeChat-Setup-0.1.0-x64.exe'
if ((Get-AuthenticodeSignature -LiteralPath $unsignedInstaller).Status -ne 'NotSigned') { throw 'The test fixture is not an unsigned installer.' }
Expect-Rejection { & './scripts/sync_installer_download.ps1' -InstallerPath $unsignedInstaller } 'Download promotion rejected'

# Compile the real signed branch without credentials. The real callback must fail,
# and Inno must stop instead of silently emitting an unsigned release.
$testDirectory = Join-Path $projectDirectory ('build/signing-guard-check/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory -Force | Out-Null
$shellExecutable = (Get-Process -Id $PID).Path
$callback = Join-Path $PSScriptRoot 'sign_inno_file.ps1'
$signingCommand = '$q' + $shellExecutable + '$q -NoLogo -NoProfile -File $q' + $callback + '$q -FilePath $f'
$names = @('HEART_SIGN_THUMBPRINT','HEART_SIGN_TOOL','HEART_SIGN_TIMESTAMP','HEART_SIGN_RECEIPTS')
$previous = @{}
try {
    foreach ($name in $names) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    $env:HEART_SIGN_RECEIPTS = Join-Path $testDirectory 'callback.jsonl'
    $webView = Get-Content -LiteralPath 'packaging/webview2-bootstrapper.json' -Raw | ConvertFrom-Json
    $webViewPath = Join-Path $projectDirectory ('build/installer-prerequisites/' + $webView.file)
    $output = & './build/installer-tools/InnoSetup/ISCC.exe' '/Qp' ('/O' + $testDirectory) '/DSignRelease' ('/Sheartwechat=' + $signingCommand) ('/DWebViewInstallerFile=' + $webViewPath) ('/DWebViewInstallerSha256=' + $webView.sha256) 'packaging/heart-wechat.iss' 2>&1
    $compilerExit = $LASTEXITCODE
    $log = $output -join "`n"
    [IO.File]::WriteAllText((Join-Path $testDirectory 'compiler.log'), $log, [Text.UTF8Encoding]::new($false))
    $callbackError = Join-Path $testDirectory 'callback.jsonl.error.txt'
    if ($compilerExit -eq 0 -or $log -notmatch 'Sign Tool' -or -not (Test-Path -LiteralPath $callbackError) -or
        (Get-Content -LiteralPath $callbackError -Raw) -notmatch '40-character certificate thumbprint') {
        throw ('The signed branch did not fail at the expected callback. Inspect ' + $testDirectory)
    }
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
foreach ($file in $protectedFiles) {
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $before[$file]) { throw ('A rejected release changed ' + $file) }
}
$report = [ordered]@{ passed = $true; installer_sha256 = $before[$protectedFiles[0]].ToLowerInvariant(); missing_certificate_rejected = $true; partial_signing_config_rejected = $true; automatic_unsigned_promotion_rejected = $true; unsigned_download_requires_opt_in = $true; failed_inno_signing_stops_compile = $true; existing_release_preserved = $true; public_signing_success_tested = $false; compiler_exit = $compilerExit }
$json = $report | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $projectDirectory 'release/installer/signing-guard-check.json'), $json, [Text.UTF8Encoding]::new($false))
$json
exit 0
