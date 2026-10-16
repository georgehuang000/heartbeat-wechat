param([ValidateRange(1024,65535)][int]$Port = 10001)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$siteDirectory = Join-Path $projectDirectory 'promotion-heart-wechat'
if (-not (Test-Path -LiteralPath (Join-Path $siteDirectory 'dist/index.html'))) {
    throw 'The website is maintained separately. Put its dist and scripts/serve.mjs in promotion-heart-wechat first.'
}
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodeExecutable = if ($nodeCommand) { $nodeCommand.Source } elseif (Test-Path -LiteralPath 'D:/tools/node.exe') { 'D:/tools/node.exe' } else { throw 'Node.js is required for the local website preview.' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; the existing process was left running."
}
$previousPort = $env:PORT
try {
    $env:PORT = [string]$Port
    $process = Start-Process -FilePath $nodeExecutable -ArgumentList 'scripts/serve.mjs' -WorkingDirectory $siteDirectory -WindowStyle Hidden -PassThru
    [pscustomobject]@{ Url="http://127.0.0.1:$Port/"; Pid=$process.Id; LocalOnly=$true }
} finally {
    if ($null -eq $previousPort) { Remove-Item Env:PORT -ErrorAction SilentlyContinue } else { $env:PORT = $previousPort }
}
