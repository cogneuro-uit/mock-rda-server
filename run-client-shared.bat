@echo off
:: Shared client launcher: works for ANY Windows user on this machine.
:: Dependencies live in .deps\ (project-local, shared); mock_rda resolves
:: from src\ via the client's own path handling. No per-user state needed.

setlocal
cd /d "%~dp0"

set "PYTHON="
for %%P in (python python3 py) do (
    if not defined PYTHON (
        "%%P" --version >nul 2>&1 && set "PYTHON=%%P"
    )
)

if not defined PYTHON (
    echo ERROR: no Python found on PATH. 1>&2
    echo Python must be installed for this user ^(or on the machine PATH^).
    pause
    exit /b 1
)

if not exist ".deps" (
    echo Client dependencies not installed yet. Run install-shared.bat first.
    pause
    exit /b 1
)

set "PYTHONPATH=%CD%\.deps;%CD%\src"

if "%~1"=="" (
    "%PYTHON%" examples\gui_client.py --trigger Stimulus --window-ms 10 --electrode C3
) else (
    "%PYTHON%" examples\gui_client.py %*
)

if errorlevel 1 (
    echo.
    echo Client exited with an error. If it says "No module named": run
    echo install-shared.bat. If it closed instantly: is the Recorder RDA
    echo stream running on port 51244^?
)
echo.
echo Client exited.
pause