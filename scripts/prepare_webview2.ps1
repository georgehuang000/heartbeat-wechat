param([string]$SourceFile = '', [ValidateSet('standard','offline')][string]$WebViewMode = 'standard')
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'webview2_runtime.ps1')
$configuration = Get-HeartWebViewConfiguration -Mode $WebViewMode
$directory = Join-Path $projectDirectory 'build/installer-prerequisites'
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$destination = Join-Path $directory $configuration.file
if (Test-Path -LiteralPath $destination) {
    Assert-HeartWebViewInstaller -Path $destination -Mode $WebViewMode
    return
}
$temporary = Join-Path $directory ($configuration.file + '.' + [Guid]::NewGuid().ToString('N') + '.partial')
try {
    if ($SourceFile) { Copy-Item -LiteralPath $SourceFile -Destination $temporary }
    else {
        Write-Host ('正在从 Microsoft 下载 ' + $configuration.mode + ' ' + $configuration.version + '；不会在开发机运行安装器。')
        Invoke-WebRequest -Uri $configuration.url -OutFile $temporary -TimeoutSec 180
    }
    $receipt = Assert-HeartWebViewInstaller -Path $temporary -Mode $WebViewMode
    Move-Item -LiteralPath $temporary -Destination $destination
    [IO.File]::WriteAllText((Join-Path $directory ('webview2-' + $WebViewMode + '-receipt.json')), ($receipt | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
    $receipt
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
