param([ValidateSet('standard','offline')][string]$WebViewMode = 'standard')
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$testDirectory = Join-Path $projectDirectory ('build/webview2-prerequisite-check/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory -Force | Out-Null
$compiler = Join-Path $projectDirectory 'build/installer-tools/InnoSetup/ISCC.exe'
& $compiler '/Qp' ('/O' + $testDirectory) ('/DWebViewMode=' + $WebViewMode) (Join-Path $projectDirectory 'packaging/tests/webview2-prerequisite-test.iss')
if ($LASTEXITCODE -ne 0) { throw 'Prerequisite test compilation failed.' }
$executable = Join-Path $testDirectory 'webview2-prerequisite-test.exe'
$process = Start-Process -FilePath $executable -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="' + (Join-Path $testDirectory 'test.log') + '"')) -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(30000)) { throw ('Test timeout; inspect process ' + $process.Id) }
$reportPath = Join-Path $testDirectory 'prerequisite-logic-check.json'
if (-not (Test-Path -LiteralPath $reportPath)) { throw ('Missing test report; inspect ' + $testDirectory) }
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
$expectedCases = if ($WebViewMode -eq 'standard') { 10 } else { 8 }
if (-not $report.passed -or $report.cases -ne $expectedCases) { throw 'Prerequisite logic checks failed.' }
$report | Add-Member -NotePropertyName installer_mode -NotePropertyValue $WebViewMode
$report | Add-Member -NotePropertyName logic_sha256 -NotePropertyValue (Get-FileHash -LiteralPath (Join-Path $projectDirectory 'packaging/webview2-prerequisite.iss')).Hash.ToLowerInvariant()
$report | Add-Member -NotePropertyName evidence_directory -NotePropertyValue $testDirectory
$json = $report | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $projectDirectory 'build/webview2-prerequisite-check/latest.json'), $json, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $projectDirectory ('build/webview2-prerequisite-check/latest-' + $WebViewMode + '.json')), $json, [Text.UTF8Encoding]::new($false))
$json
