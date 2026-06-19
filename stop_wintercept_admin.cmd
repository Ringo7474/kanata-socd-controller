@echo off
cd /d "%~dp0"
net session >nul 2>nul
if %errorlevel% neq 0 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
taskkill /IM kanata_windows_gui_wintercept_x64.exe /F /T
taskkill /IM kanata_windows_tty_wintercept_x64.exe /F /T
