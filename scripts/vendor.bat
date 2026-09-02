@echo off
setlocal EnableDelayedExpansion

for %%F in ("%~dp0..") do set "ROOT=%%~fF"

set "PYTHON="
for %%P in (python python3 py) do (
    if not defined PYTHON (
        "%%P" --version >nul 2>&1 && set "PYTHON=%%P"
    )
)

if not defined PYTHON if exist "%ROOT%\.venv\Scripts\python.exe" (
    "%ROOT%\.venv\Scripts\python.exe" --version >nul 2>&1 && set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
)

if not defined PYTHON (
    echo ERROR: no Python interpreter found. Install Python or run scripts\bootstrap.bat first. 1>&2
    exit /b 1
)

"%PYTHON%" "%~dp0pyvendor.py" refresh %*
exit /b %ERRORLEVEL%
