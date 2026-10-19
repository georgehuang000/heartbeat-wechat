# Shared by prerequisite preparation and release builds. Nothing is installed here.
function Get-HeartWebViewConfiguration([ValidateSet('standard','offline')][string]$Mode = 'standard') {
    $root = Split-Path -Parent $PSScriptRoot
    $filename = if ($Mode -eq 'offline') { 'webview2-runtime.json' } else { 'webview2-bootstrapper.json' }
    Get-Content -LiteralPath (Join-Path $root ('packaging/' + $filename)) -Raw | ConvertFrom-Json
}

function Assert-HeartWebViewInstaller([string]$Path, [ValidateSet('standard','offline')][string]$Mode = 'standard') {
    $configuration = Get-HeartWebViewConfiguration -Mode $Mode
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw ('缺少 WebView2 安装组件。请先执行 scripts/prepare_webview2.ps1 -WebViewMode ' + $Mode + '；构建不会跳过依赖。')
    }
    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $configuration.sha256) { throw 'WebView2 安装组件与固定清单的 SHA-256 不一致。' }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') {
        throw 'WebView2 安装组件的 Microsoft 签名校验未通过。'
    }
    [pscustomobject][ordered]@{
        mode = $configuration.mode; version = $configuration.version; architecture = $configuration.architecture
        file = $configuration.file; bytes = (Get-Item -LiteralPath $Path).Length; sha256 = $hash
        source = $configuration.url; manifest_source = $configuration.manifest_source
        version_kind = $(if ($configuration.version_kind) { $configuration.version_kind } else { 'runtime_version' })
        sha256_source = $(if ($configuration.sha256_source) { $configuration.sha256_source } else { 'Microsoft WinGet manifest' })
        signature_status = [string]$signature.Status; signer = $signature.SignerCertificate.Subject
    }
}
