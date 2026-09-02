@echo off
:: Double-clickable mock-rda launcher for Windows.
::
::   double-click          -> synthetic source (32 ch, 5 kHz, stim every 2 s)
::   drag a .vhdr onto it  -> stream that recording, looping
::   arguments             -> forwarded verbatim to mock-rda (power users)
::
:: The Tk control window (Inject trigger / Inject burst) opens with the
:: server; keep this console window focused and press Enter to fire a
:: Stimulus/S  1 marker at the next block.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\mock-rda.exe" (
    echo mock-rda is not installed yet. Double-click install.bat first.
    pause
    exit /b 1
)

if /I "%~x1"==".vhdr" (
    ".venv\Scripts\mock-rda.exe" file "%~1" --loop
) else if "%~1"=="" (
    ".venv\Scripts\mock-rda.exe" synth --channels 32 --rate 5000 --block-ms 4 --stim-period 2.0 --tep-template default
) else (
    ".venv\Scripts\mock-rda.exe" %*
)

echo.
echo Server exited.
pause