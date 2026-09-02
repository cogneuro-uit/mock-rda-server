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

:: Call python -m mock_rda.cli instead of the mock-rda.exe shim: the shim is
:: a small unsigned launcher that antivirus / lab policies commonly block
:: ("permission denied (os error 5)"), while python.exe itself ran fine
:: during install.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo mock-rda is not installed yet. Double-click install.bat first.
    pause
    exit /b 1
)

if /I "%~x1"==".vhdr" (
    ".venv\Scripts\python.exe" -m mock_rda.cli file "%~1" --loop
) else if "%~1"=="" (
    ".venv\Scripts\python.exe" -m mock_rda.cli synth --channels 32 --rate 5000 --block-ms 4 --stim-period 2.0 --tep-template default
) else (
    ".venv\Scripts\python.exe" -m mock_rda.cli %*
)

echo.
echo Server exited.
pause