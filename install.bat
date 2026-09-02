@echo off
:: Double-clickable installer for Windows.
:: Installs uv, Python 3.12, and all dependencies fully inside this folder
:: (offline from vendor\ when present - no admin, no system installs).
:: Safe to run again at any time; it only fills in what is missing.

cd /d "%~dp0"
echo Installing mock-rda (project-local, no admin) ...
call scripts\bootstrap.bat --offline
echo.
pause