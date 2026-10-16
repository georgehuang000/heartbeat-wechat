param([ValidateSet('onedir', 'onefile')][string]$Mode = 'onefile')
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDirectory
$env:Path = 'D:\tools;' + $env:Path
& '.venv\Scripts\python.exe' -m pip check
if ($LASTEXITCODE -ne 0) { throw '依赖检查失败' }
& '.venv\Scripts\python.exe' 'scripts\export_desktop_icon.py'
if ($LASTEXITCODE -ne 0) { throw '图标导出失败' }
& 'D:\tools\npm.cmd' --prefix 'prototype-heart-wechat' run build
if ($LASTEXITCODE -ne 0) { throw '界面构建失败' }
$env:GALCHAT_BUNDLE_MODE = $Mode
try {
    & '.venv\Scripts\python.exe' -m PyInstaller --noconfirm --distpath "release\$Mode" --workpath "build\pyinstaller-$Mode" 'packaging\heart-wechat.spec'
    if ($LASTEXITCODE -ne 0) { throw 'EXE构建失败' }
    if ($Mode -eq 'onefile') {
        Copy-Item -LiteralPath 'release\onefile\心动微信.exe' -Destination '心动微信.exe' -Force
    }
} finally { Remove-Item Env:\GALCHAT_BUNDLE_MODE -ErrorAction SilentlyContinue }
