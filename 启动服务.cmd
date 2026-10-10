@echo off
setlocal
title Log Fusion Server (0.0.0.0:8080)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run-server.ps1" %*
if errorlevel 1 pause
