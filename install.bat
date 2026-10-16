@echo off
REM Double-click to install IndexEdge on Windows.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
pause
