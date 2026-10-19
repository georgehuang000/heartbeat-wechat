# Shared by the release builder and Inno Setup's signing callback.
function Get-HeartSigningContext {
    param([string]$CertificateThumbprint, [string]$SignToolPath, [string]$TimestampUrl)
    $thumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
    if ($thumbprint -notmatch '^[0-9A-F]{40}$') { throw 'Signing requires a 40-character certificate thumbprint; no release was changed.' }
    $timestampUri = $null
    if (-not [Uri]::TryCreate($TimestampUrl, [UriKind]::Absolute, [ref]$timestampUri) -or
        $timestampUri.Scheme -notin @('https', 'http') -or $timestampUri.UserInfo -or
        $TimestampUrl -match '[\s"\x00-\x1f]') { throw 'Provide the certificate provider''s RFC 3161 HTTP(S) timestamp URL.' }
    if (-not $SignToolPath) {
        $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($command) { $SignToolPath = $command.Source }
    }
    if (-not $SignToolPath -or -not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) {
        throw 'SignTool was not found. Install Windows SDK Signing Tools or supply -SignToolPath.'
    }
    $SignToolPath = (Resolve-Path -LiteralPath $SignToolPath).Path
    $toolSignature = Get-AuthenticodeSignature -LiteralPath $SignToolPath
    if ($toolSignature.Status -ne 'Valid' -or $toolSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') {
        throw 'SignTool must have a valid Microsoft signature.'
    }
    $certificatePath = 'Cert:\CurrentUser\My\' + $thumbprint
    if (-not (Test-Path -LiteralPath $certificatePath)) { throw 'Certificate not found in CurrentUser/My. Connect the hardware token or sign in to the provider''s desktop client first.' }
    $certificate = Get-Item -LiteralPath $certificatePath
    if (-not $certificate.HasPrivateKey) { throw 'The certificate has no accessible private key. Activate the token or cloud signing provider.' }
    if ((Get-Date) -lt $certificate.NotBefore -or (Get-Date) -ge $certificate.NotAfter) { throw 'The signing certificate is not currently valid.' }
    if ($certificate.Subject -eq $certificate.Issuer -or -not $certificate.Verify()) { throw 'The signing certificate must chain to a trusted issuer; self-signed certificates are not release credentials.' }
    if ('1.3.6.1.5.5.7.3.3' -notin @($certificate.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId })) {
        throw 'The certificate is not enabled for Code Signing.'
    }
    [pscustomobject]@{ Thumbprint = $thumbprint; Tool = $SignToolPath; TimestampUrl = $TimestampUrl }
}

function Assert-HeartFileSignature {
    param([Parameter(Mandatory)][string]$FilePath, [Parameter(Mandatory)]$Context)
    & $Context.Tool 'verify' '/pa' '/all' '/v' '/tw' $FilePath | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed: $FilePath" }
    $signature = Get-AuthenticodeSignature -LiteralPath $FilePath
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $Context.Thumbprint -or -not $signature.TimeStamperCertificate) {
        throw "Expected a valid, timestamped signature from the selected publisher: $FilePath"
    }
    [pscustomobject]@{
        file = [IO.Path]::GetFileName($FilePath)
        sha256 = (Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash.ToLowerInvariant()
        status = [string]$signature.Status
        publisher = $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
        thumbprint = $signature.SignerCertificate.Thumbprint
        timestamped = $true
    }
}

function Invoke-HeartFileSigning {
    param([Parameter(Mandatory)][string]$FilePath, [Parameter(Mandatory)]$Context)
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) { throw "Signing input not found: $FilePath" }
    # /sha1 selects the certificate. Both the file and timestamp digest use SHA-256.
    & $Context.Tool 'sign' '/s' 'My' '/sha1' $Context.Thumbprint '/fd' 'SHA256' '/tr' $Context.TimestampUrl '/td' 'SHA256' '/d' 'HeartWeChat' $FilePath | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Signing failed; release promotion stopped: $FilePath" }
    Assert-HeartFileSignature -FilePath $FilePath -Context $Context
}
