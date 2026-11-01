@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title SchoolPass 2.5.3 - Build Windows installer
where powershell.exe >nul 2>&1
if errorlevel 1 (
  echo PowerShell was not found. Windows 10 or later is required.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
if errorlevel 1 (
  echo.
  echo BUILD FAILED. Copy the red error text above and keep it.
  echo Your existing database in %LOCALAPPDATA%\SchoolPass was NOT touched.
  pause
  exit /b 1
)
echo.
echo DONE: SchoolPass 2.5.3 installer was created and checked.
echo File: %CD%\dist\SchoolPass_Setup.exe
pause
