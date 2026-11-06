@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title SchoolPass 2.5.3 - Build installer only
where powershell.exe >nul 2>&1
if errorlevel 1 (
  echo PowerShell was not found. Windows 10 or later is required.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_installer_only.ps1"
if errorlevel 1 (
  echo.
  echo INSTALLER BUILD FAILED. Existing portable files and SchoolPass data were not removed.
  pause
  exit /b 1
)
pause
