@echo off
setlocal
title Log Fusion Simulator
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run-simulator.ps1" %*
if errorlevel 1 pause
