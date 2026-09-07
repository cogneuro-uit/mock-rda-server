@echo off
REM Double-click launcher: synthetic mock RDA stream on port 61234.
REM 32 EEG + 2 EMG channels (EMG, EMG2), 5000 Hz.
REM Press Enter in this window to inject a Stimulus marker.
title RDA mock server (synth, port 61234)
mock-rda synth --port 61234 --emg-channels 2
echo.
echo Server exited. Press any key to close...
pause >nul