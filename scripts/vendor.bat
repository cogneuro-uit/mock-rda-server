@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0vendor.ps1" %*
exit /b %ERRORLEVEL%
