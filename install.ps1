param([switch]$PauseOnExit)

$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'cmdc-codex Installer'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}
Set-Location -LiteralPath $PSScriptRoot

Write-Host ''
Write-Host 'cmdc-codex installer' -ForegroundColor Cyan
Write-Host 'CommandCode + native Codex models for Codex Desktop and CLI'
Write-Host ''

try {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        $python = Get-Command py.exe -ErrorAction SilentlyContinue
    }
    if ($null -eq $python) {
        throw 'Python 3.11+ was not found. Install Python and enable the Python launcher or PATH.'
    }
    & $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw 'Python 3.11 or newer is required.'
    }
    & $python.Source (Join-Path $PSScriptRoot 'cmdc-codex.py') install
    if ($LASTEXITCODE -ne 0) {
        throw "Installation failed with exit code $LASTEXITCODE."
    }
    Write-Host ''
    Write-Host 'Installation completed. Restart Codex before choosing a CommandCode model.' -ForegroundColor Green
    $installCode = 0
} catch {
    Write-Host ''
    Write-Host ('Installation failed: ' + $_.Exception.Message) -ForegroundColor Red
    $installCode = 1
}

if ($PauseOnExit) {
    Write-Host ''
    [void](Read-Host 'Press Enter to close this window')
}
exit $installCode
