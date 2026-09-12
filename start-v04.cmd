@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_v04.ps1"
if errorlevel 1 pause
