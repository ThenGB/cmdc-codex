@echo off
setlocal
chcp 65001 >nul
title cmdc-codex Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -PauseOnExit
set "installerCode=%ERRORLEVEL%"
endlocal & exit /b %installerCode%
