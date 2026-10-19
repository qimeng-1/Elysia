@echo off
REM One-line wrapper for scripts\gate.ps1 (ASCII only, safe for cmd/double-click).
REM Usage: scripts\gate.bat  [ -Skip pytest ]  [ -SelfTest ]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gate.ps1" %*
exit /b %ERRORLEVEL%
