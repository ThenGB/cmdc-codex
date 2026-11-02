$ErrorActionPreference = 'Stop'
python (Join-Path $PSScriptRoot 'cmdc-codex.py') install
exit $LASTEXITCODE
