@echo off
chcp 65001 >nul
rem Optional: allow phones on the PRIVATE network to open SchoolPass QR pages (TCP 8765).
rem Run: right-click this file -> "Run as administrator".
net session >nul 2>&1
if errorlevel 1 (
  echo This file must be run as administrator: right-click it and choose "Run as administrator".
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="SchoolPass QR" >nul 2>&1
netsh advfirewall firewall add rule name="SchoolPass QR" dir=in action=allow protocol=TCP localport=8765 profile=private
if errorlevel 1 (echo FAILED) else (echo OK: rule added for PRIVATE networks only.)
echo To remove it later: netsh advfirewall firewall delete rule name="SchoolPass QR"
pause
