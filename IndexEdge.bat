@echo off
REM Starts the IndexEdge server and opens the app in your browser.
cd /d "%~dp0"
title IndexEdge
if not exist ".venv\Scripts\python.exe" (
  echo IndexEdge is not installed yet. Run install.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m indexedge run %*
if errorlevel 1 pause
