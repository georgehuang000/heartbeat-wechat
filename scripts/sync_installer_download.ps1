param(
    [string]$InstallerPath = '',
    [string]$ExpectedThumbprint = '',
    [string]$GitHubRepository = '',
    [string]$GitHubTag = '',
    [switch]$AllowUnsigned,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
if (-not $InstallerPath) { $InstallerPath = Join-Path $projectDirectory 'release/installer/HeartWeChat-Setup-0.1.1-x64.exe' }
if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) { throw 'Installer not found.' }
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
$isSigned = $signature.Status -eq 'Valid'
if ($isSigned) {
    if (-not $signature.TimeStamperCertificate) { throw 'A signed download must have a timestamp.' }
    if (-not $ExpectedThumbprint) { throw 'Supply -ExpectedThumbprint to confirm the publisher of a signed download.' }
    $ExpectedThumbprint = ($ExpectedThumbprint -replace '\s', '').ToUpperInvariant()
    if ($ExpectedThumbprint -ne $signature.SignerCertificate.Thumbprint) { throw 'Publisher thumbprint mismatch.' }
} elseif ($signature.Status -ne 'NotSigned' -or -not $AllowUnsigned -or $ExpectedThumbprint) {
    throw ('Download promotion rejected: signature status is ' + $signature.Status + '. Unsigned previews require explicit -AllowUnsigned and cannot specify a publisher.')
}
$file = Get-Item -LiteralPath $InstallerPath
$hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifestPath = Join-Path (Split-Path -Parent $InstallerPath) 'release-manifest.json'
$installerMode = $null
$webViewMode = $null
if (Test-Path -LiteralPath $manifestPath) {
    $sourceManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($sourceManifest.sha256 -ne $hash) { throw 'Installer manifest checksum mismatch; the webpage was not changed.' }
    $webViewMode = $sourceManifest.webview2.mode
    if ($webViewMode -eq 'evergreenBootstrapper') { $installerMode = 'standard' }
    elseif ($webViewMode -eq 'evergreenStandalone') { $installerMode = 'offline' }
    if ($sourceManifest.installer_mode -and $sourceManifest.installer_mode -ne $installerMode) { throw 'Installer mode does not match the bundled WebView2 component.' }
}
$filename = 'HeartWeChat-Setup-0.1.1-x64-' + $hash.Substring(0, 12) + '.exe'
$siteDirectory = Join-Path $projectDirectory 'promotion-heart-wechat/dist'
$downloadDirectory = Join-Path $siteDirectory 'downloads'
$downloadFile = Join-Path $downloadDirectory $filename
$metadataPath = Join-Path $downloadDirectory 'release.json'
# Keep the selected channel on subsequent checks/builds. A new binary must be
# uploaded and verified on GitHub before this script can point the website at it.
if (-not $GitHubRepository -and -not $GitHubTag -and (Test-Path -LiteralPath $metadataPath)) {
    $previousMetadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    if ($previousMetadata.provider -eq 'github-release') {
        $GitHubRepository = $previousMetadata.repository
        $GitHubTag = $previousMetadata.tag
    }
}
$downloadUrl = 'downloads/' + $filename
$githubRelease = $null
$githubAsset = $null
if ($GitHubRepository -or $GitHubTag) {
    if ($GitHubRepository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or -not $GitHubTag) { throw 'Supply both a GitHub owner/repository and release tag.' }
    $releaseJson = & gh api ('repos/' + $GitHubRepository + '/releases/tags/' + [Uri]::EscapeDataString($GitHubTag))
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify the GitHub release; the webpage was not changed.' }
    $githubRelease = $releaseJson | ConvertFrom-Json
    if ($githubRelease.draft) { throw 'A draft release is not a public download.' }
    $matchingAssets = @($githubRelease.assets | Where-Object { $_.name -ceq $filename })
    if ($matchingAssets.Count -ne 1) { throw 'Upload the exact installer to GitHub before updating the webpage.' }
    $githubAsset = $matchingAssets[0]
    $downloadUrl = 'https://github.com/' + $GitHubRepository + '/releases/download/' + [Uri]::EscapeDataString($GitHubTag) + '/' + $filename
    if ($githubAsset.state -ne 'uploaded' -or $githubAsset.size -ne $file.Length -or
        $githubAsset.digest -ne ('sha256:' + $hash) -or $githubAsset.browser_download_url -cne $downloadUrl) {
        throw 'GitHub asset URL, size, state, or SHA-256 does not match the local installer.'
    }
}
$htmlPath = Join-Path $siteDirectory 'index.html'
$html = [IO.File]::ReadAllText($htmlPath)
$linkPattern = '(data-installer-download\s+href=")[^"]+(")(?:\s+download(?:="[^"]*")?)?'
$summaryPattern = '(<p class="release-status" id="release-status"[^>]*>)[\s\S]*?(</p>)'
$faqPattern = '(<span id="signature-faq">)[\s\S]*?(</span>)'
foreach ($pattern in @($linkPattern, $summaryPattern, $faqPattern)) {
    if ([regex]::Matches($html, $pattern).Count -ne 1) { throw 'Expected exactly one release link / status marker in the webpage. No file was changed.' }
}
$publisher = if ($isSigned) { $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { $null }
$metadata = [ordered]@{
    version = '0.1.1'; file = $filename; url = $downloadUrl; bytes = $file.Length
    sha256 = $hash; signature_status = [string]$signature.Status; signed = $isSigned
    publisher = $publisher; thumbprint = $(if ($isSigned) { $signature.SignerCertificate.Thumbprint } else { $null })
    timestamped = [bool]$signature.TimeStamperCertificate
    provider = $(if ($githubRelease) { 'github-release' } else { 'local' })
    installer_mode = $installerMode; webview2_mode = $webViewMode
}
if ($githubRelease) {
    $metadata.repository = $GitHubRepository
    $metadata.tag = $GitHubTag
    $metadata.release_url = $githubRelease.html_url
    $metadata.asset_id = $githubAsset.id
}
if ($CheckOnly) {
    $publishedMetadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    if ($publishedMetadata.sha256 -ne $hash -or $publishedMetadata.url -cne $downloadUrl -or
        $publishedMetadata.signed -ne $isSigned -or $publishedMetadata.file -ne $filename -or $publishedMetadata.bytes -ne $file.Length -or
        $publishedMetadata.signature_status -ne [string]$signature.Status -or $publishedMetadata.publisher -ne $publisher -or
        $publishedMetadata.installer_mode -ne $installerMode -or $publishedMetadata.webview2_mode -ne $webViewMode -or
        (Get-Content -LiteralPath (Join-Path $downloadDirectory 'SHA256SUMS.txt') -Raw).Trim() -ne ($hash + '  ' + $filename) -or
        -not $html.Contains('id="release-status" data-signed="' + $isSigned.ToString().ToLowerInvariant() + '"') -or
        -not $html.Contains('data-installer-download href="' + $downloadUrl + '"')) {
        throw 'The download, release metadata, source installer, and webpage link do not agree.'
    }
    if ($githubRelease) {
        if ($publishedMetadata.provider -ne 'github-release' -or $publishedMetadata.repository -cne $GitHubRepository -or
            $publishedMetadata.tag -cne $GitHubTag -or $publishedMetadata.asset_id -ne $githubAsset.id) { throw 'GitHub download metadata mismatch.' }
    } elseif (-not (Test-Path -LiteralPath $downloadFile) -or (Get-FileHash -LiteralPath $downloadFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) {
        throw 'Local download checksum mismatch.'
    }
    Write-Output ('Download verified: ' + $downloadUrl + ' / ' + $signature.Status)
    return
}
New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
if (-not $githubRelease -and -not (Test-Path -LiteralPath $downloadFile)) {
    $temporaryDownload = Join-Path $downloadDirectory ($filename + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    Copy-Item -LiteralPath $InstallerPath -Destination $temporaryDownload
    if ((Get-FileHash -LiteralPath $temporaryDownload -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) { throw 'Downloaded copy hash mismatch.' }
    Move-Item -LiteralPath $temporaryDownload -Destination $downloadFile
} elseif (-not $githubRelease -and (Get-FileHash -LiteralPath $downloadFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) {
    throw 'A different file exists at the content-addressed download path.'
}
$sizeLabel = ([Math]::Round($file.Length / 1000000, 1)).ToString('0.0', [Globalization.CultureInfo]::InvariantCulture) + ' MB'
$statusLabel = if ($isSigned) { '已签名 · 发布者：' + $publisher } else { '预览版 · 暂未签名' }
if ($installerMode -eq 'standard') { $statusLabel = '标准包 · ' + $statusLabel }
elseif ($installerMode -eq 'offline') { $statusLabel = '完整离线包 · ' + $statusLabel }
if ($githubRelease) { $statusLabel += ' · 由 GitHub 提供下载' }
$statusHtml = [Net.WebUtility]::HtmlEncode('0.1.1 · ' + $sizeLabel + ' · ' + $statusLabel)
$faqText = if ($isSigned) { '当前安装包已通过数字签名校验，发布者为 ' + $publisher + '。新版本仍可能出现 Windows SmartScreen 信誉提示。' } else { '当前安装包未签名，Windows 可能提示未知发布者。' }
$html = [regex]::Replace($html, $linkPattern, [Text.RegularExpressions.MatchEvaluator]{ param($m)
    $m.Groups[1].Value + $downloadUrl + $m.Groups[2].Value + $(if ($githubRelease) { '' } else { ' download' })
})
$html = [regex]::Replace($html, $summaryPattern, [Text.RegularExpressions.MatchEvaluator]{ param($m) '<p class="release-status" id="release-status" data-signed="' + $isSigned.ToString().ToLowerInvariant() + '">' + $statusHtml + '</p>' })
$html = [regex]::Replace($html, $faqPattern, [Text.RegularExpressions.MatchEvaluator]{ param($m) $m.Groups[1].Value + [Net.WebUtility]::HtmlEncode($faqText) + $m.Groups[2].Value })
function Write-ReleaseText([string]$Path, [string]$Content) {
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary, $Content, [Text.UTF8Encoding]::new($false))
    try {
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { Move-Item -LiteralPath $temporary -Destination $Path }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}
Write-ReleaseText $metadataPath ($metadata | ConvertTo-Json -Depth 4)
Write-ReleaseText (Join-Path $downloadDirectory 'SHA256SUMS.txt') ($hash + '  ' + $filename + "`n")
# The executable is complete before the page begins pointing at it.
Write-ReleaseText $htmlPath $html
Write-Output ('Download prepared locally: ' + $downloadUrl + ' / ' + $signature.Status)
