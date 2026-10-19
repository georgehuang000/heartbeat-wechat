param(
    [string]$Compiler = '',
    [switch]$SkipAppBuild,
    [ValidateSet('standard','offline')][string]$WebViewMode = 'standard',
    [string]$WebViewInstallerPath = '',
    [string]$CertificateThumbprint = '',
    [string]$SignToolPath = '',
    [string]$TimestampUrl = '',
    [switch]$RequireSignedRelease,
    [switch]$PublishDownload
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDirectory
. (Join-Path $PSScriptRoot 'windows_signing.ps1')
. (Join-Path $PSScriptRoot 'webview2_runtime.ps1')
$signing = $null
if ($RequireSignedRelease -or $CertificateThumbprint) {
    $signing = Get-HeartSigningContext -CertificateThumbprint $CertificateThumbprint -SignToolPath $SignToolPath -TimestampUrl $TimestampUrl
} elseif ($SignToolPath -or $TimestampUrl) {
    throw 'Signing options were supplied without a certificate thumbprint. Refusing an unsigned fallback.'
}
if ($PublishDownload -and -not $signing) { throw 'Automatic download promotion requires signing. For an explicitly labeled preview, use sync_installer_download.ps1 -AllowUnsigned.' }
$pythonExecutable = Join-Path $projectDirectory '.venv/Scripts/python.exe'
if (-not $Compiler) { $Compiler = Join-Path $projectDirectory 'build/installer-tools/InnoSetup/ISCC.exe' }
if (-not (Test-Path -LiteralPath $Compiler)) { throw '缺少 Inno Setup 编译器；请按 packaging/README.md 准备。' }
if (-not $WebViewInstallerPath) {
    $WebViewInstallerPath = Join-Path $projectDirectory ('build/installer-prerequisites/' + (Get-HeartWebViewConfiguration -Mode $WebViewMode).file)
}
$webViewReceipt = Assert-HeartWebViewInstaller -Path $WebViewInstallerPath -Mode $WebViewMode
if (-not $SkipAppBuild) {
    & $pythonExecutable -m pip check
    if ($LASTEXITCODE -ne 0) { throw '依赖检查失败。' }
    $env:Path = 'D:\tools;' + $env:Path
    & 'D:\tools\npm.cmd' --prefix 'prototype-heart-wechat' run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
    $previousBundleMode = $env:GALCHAT_BUNDLE_MODE
    $env:GALCHAT_BUNDLE_MODE = 'onefile'
    try {
        & $pythonExecutable -m PyInstaller --noconfirm --distpath 'release/installer-payload' --workpath 'build/pyinstaller-installer' 'packaging/heart-wechat.spec'
        if ($LASTEXITCODE -ne 0) { throw '应用打包失败。' }
    } finally {
        if ($null -eq $previousBundleMode) { Remove-Item Env:\GALCHAT_BUNDLE_MODE -ErrorAction SilentlyContinue }
        else { $env:GALCHAT_BUNDLE_MODE = $previousBundleMode }
    }
}
$candidateDirectory = Join-Path $projectDirectory ('build/installer-staging/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $candidateDirectory -Force | Out-Null
$candidateWebView = Join-Path $candidateDirectory $webViewReceipt.file
Copy-Item -LiteralPath $WebViewInstallerPath -Destination $candidateWebView
$webViewReceipt = Assert-HeartWebViewInstaller -Path $candidateWebView -Mode $WebViewMode
$payloadPath = Join-Path $projectDirectory 'release/installer-payload/心动微信.exe'
$candidatePayload = Join-Path $candidateDirectory '心动微信.exe'
Copy-Item -LiteralPath $payloadPath -Destination $candidatePayload
$receipts = @()
if ($signing) { $receipts += Invoke-HeartFileSigning -FilePath $candidatePayload -Context $signing }
$auditPath = Join-Path $candidateDirectory 'resource-audit.json'
& $pythonExecutable 'scripts/installer_resource_audit.py' '--payload' $candidatePayload '--output' $auditPath
if ($LASTEXITCODE -ne 0) { throw '发布资源审计失败。' }
$sourceAuditPath = Join-Path $candidateDirectory 'source-audit.json'
& $pythonExecutable 'scripts/verify_background_pause_package.py' '--exe' $candidatePayload '--report' $sourceAuditPath
if ($LASTEXITCODE -ne 0) { throw '主程序与当前源码不一致；停止安装包构建。' }
$compilerArguments = @('/Qp', ('/O' + $candidateDirectory), ('/DPayloadFile=' + $candidatePayload),
    ('/DWebViewMode=' + $WebViewMode), ('/DWebViewInstallerFile=' + $candidateWebView),
    ('/DWebViewInstallerSha256=' + $webViewReceipt.sha256))
$signingEnvironmentNames = @('HEART_SIGN_THUMBPRINT','HEART_SIGN_TOOL','HEART_SIGN_TIMESTAMP','HEART_SIGN_RECEIPTS')
$previousSigningEnvironment = @{}
foreach ($name in $signingEnvironmentNames) { $previousSigningEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    if ($signing) {
        $env:HEART_SIGN_THUMBPRINT = $signing.Thumbprint
        $env:HEART_SIGN_TOOL = $signing.Tool
        $env:HEART_SIGN_TIMESTAMP = $signing.TimestampUrl
        $env:HEART_SIGN_RECEIPTS = Join-Path $candidateDirectory 'inno-signatures.jsonl'
        $shellExecutable = (Get-Process -Id $PID).Path
        $signingCallback = Join-Path $PSScriptRoot 'sign_inno_file.ps1'
        # Inno expands $q and $f. No certificate password or token PIN is stored here.
        $signingCommand = '$q' + $shellExecutable + '$q -NoLogo -NoProfile -File $q' + $signingCallback + '$q -FilePath $f'
        $compilerArguments += '/DSignRelease'
        $compilerArguments += '/Sheartwechat=' + $signingCommand
    }
    $compilerArguments += 'packaging/heart-wechat.iss'
    & $Compiler @compilerArguments
    if ($LASTEXITCODE -ne 0) { throw '安装包编译或签名失败；现有下载保持不变。' }
    if ($signing) {
        $innoReceipts = @(Get-Content -LiteralPath $env:HEART_SIGN_RECEIPTS | ForEach-Object { $_ | ConvertFrom-Json })
        if ($innoReceipts.Count -lt 2) { throw 'Missing Setup / Uninstall signing receipts; release promotion stopped.' }
        $receipts += $innoReceipts
    }
} finally {
    foreach ($name in $signingEnvironmentNames) { [Environment]::SetEnvironmentVariable($name, $previousSigningEnvironment[$name], 'Process') }
}
$candidateInstaller = Join-Path $candidateDirectory 'HeartWeChat-Setup-0.1.1-x64.exe'
if ($signing) { $receipts += Assert-HeartFileSignature -FilePath $candidateInstaller -Context $signing }
elseif ((Get-AuthenticodeSignature -LiteralPath $candidateInstaller).Status -ne 'NotSigned') { throw 'Unexpected installer signature state.' }
$installerDirectory = Join-Path $projectDirectory $(if ($WebViewMode -eq 'offline') { 'release/installer-offline' } else { 'release/installer' })
New-Item -ItemType Directory -Path $installerDirectory -Force | Out-Null
$installerPath = Join-Path $installerDirectory 'HeartWeChat-Setup-0.1.1-x64.exe'
Copy-Item -LiteralPath $candidatePayload -Destination $payloadPath
Copy-Item -LiteralPath $candidateInstaller -Destination $installerPath
Copy-Item -LiteralPath $auditPath -Destination (Join-Path $installerDirectory 'resource-audit.json')
Copy-Item -LiteralPath $sourceAuditPath -Destination (Join-Path $installerDirectory 'source-audit.json')
Copy-Item -LiteralPath 'packaging/quick-start.txt' -Destination (Join-Path $installerDirectory '使用说明.txt')
$hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $installerDirectory 'SHA256SUMS.txt'), "$hash  HeartWeChat-Setup-0.1.1-x64.exe`n", [Text.UTF8Encoding]::new($false))
$manifest = [ordered]@{
    version = '0.1.1'; built_at = (Get-Date).ToUniversalTime().ToString('o')
    installer = [IO.Path]::GetFileName($installerPath); bytes = (Get-Item -LiteralPath $installerPath).Length
    sha256 = $hash; platform = 'Windows 10 19041+ / Windows 11 x64'; signed = [bool]$signing
    payload_sha256 = (Get-FileHash -LiteralPath $payloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
    installer_mode = $WebViewMode
    webview2_bundled = $true; webview2_runtime_bundled = ($WebViewMode -eq 'offline')
    webview2 = $webViewReceipt; signature_receipts = $receipts
    installation_check = 'not_run_for_this_build'; website_visual_qa = 'blocked'; published = $false
}
[IO.File]::WriteAllText((Join-Path $installerDirectory 'release-manifest.json'), ($manifest | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
if ($PublishDownload) { & (Join-Path $PSScriptRoot 'sync_installer_download.ps1') -InstallerPath $installerPath -ExpectedThumbprint $signing.Thumbprint }
Get-Item -LiteralPath $installerPath | Select-Object FullName, Length
