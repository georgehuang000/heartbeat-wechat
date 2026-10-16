$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$candidate = 'D:\software\Anaconda3\envs\wechat-ai-quiz-py312\python.exe'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw 'Choose an independent Python 3.12 environment before setup; do not install into conda base.'
    }
    & uv venv --python $candidate --system-site-packages .venv
    if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed' }
}
& uv pip install --python .venv\Scripts\python.exe -r requirements-reader.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& '.venv\Scripts\python.exe' 'scripts\prepare_reader.py'
if ($LASTEXITCODE -ne 0) { throw 'Reader preparation failed' }
Write-Host 'Ready. Open the launch CMD, then add your DeepSeek API key in Settings.'
