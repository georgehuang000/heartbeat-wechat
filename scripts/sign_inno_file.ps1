param([Parameter(Mandatory)][string]$FilePath)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows_signing.ps1')
try {
    $context = Get-HeartSigningContext -CertificateThumbprint $env:HEART_SIGN_THUMBPRINT -SignToolPath $env:HEART_SIGN_TOOL -TimestampUrl $env:HEART_SIGN_TIMESTAMP
    $receipt = Invoke-HeartFileSigning -FilePath $FilePath -Context $context
    if (-not $env:HEART_SIGN_RECEIPTS) { throw 'Missing release receipt path.' }
    $receipt | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:HEART_SIGN_RECEIPTS -Encoding UTF8
} catch {
    if ($env:HEART_SIGN_RECEIPTS) {
        [IO.File]::WriteAllText(($env:HEART_SIGN_RECEIPTS + '.error.txt'), $_.Exception.Message, [Text.UTF8Encoding]::new($false))
    }
    Write-Error $_ -ErrorAction Continue
    exit 1
}
exit 0
