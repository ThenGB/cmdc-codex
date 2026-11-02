$ErrorActionPreference = 'Stop'
python (Join-Path $PSScriptRoot 'cmdc-codex.py') uninstall
exit $LASTEXITCODE
