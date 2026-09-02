@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0vendor-verify.ps1" %*
exit /b %ERRORLEVEL%
