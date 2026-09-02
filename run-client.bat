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

if errorlevel 1 (
    echo.
    echo Client exited with an error. If it closed instantly^: is the server
    echo running^? Start it with run-server.bat first ^(the client connects to
    echo localhost:51244 immediately and exits if nothing is listening^).
    echo If the message was "permission denied ^(os error 5^)": the antivirus
    echo blocks the managed python.exe - run scripts\diagnose.bat and see the
    echo README Troubleshooting section.
)
echo.
echo Client exited.
pause