@echo off
rem Double-click to start Pin Palette. Installs itself on first run.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  exit /b
)
title Pin Palette
".venv\Scripts\python.exe" app.py %*
pause
