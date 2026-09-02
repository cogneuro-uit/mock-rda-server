@echo off
:: Double-clickable GUI client launcher for Windows.
::
::   double-click          -> epoch viewer watching the local stream
::                            (trigger "Stimulus", electrode C3)
::   drag a .py onto it    -> not supported; clients must be picked here
::   arguments             -> forwarded to the client verbatim
::
:: Always uses the project's own venv python (with numpy/mne installed).
:: Never depends on the machine's "python" or .py file associations.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo mock-rda is not installed yet. Double-click install.bat first.
    pause
    exit /b 1
)

if "%~1"=="" (
    ".venv\Scripts\python.exe" examples\gui_client.py --trigger Stimulus --window-ms 10 --electrode C3
) else (
    ".venv\Scripts\python.exe" examples\gui_client.py %*
)

echo.
echo Client exited.
pause