@echo off
REM Double-click launcher: replay the bundled example recording (looped)
REM as an RDA stream on port 61234.
REM Press Enter in this window to inject a Stimulus marker on top of the
REM recording's own markers.
title RDA mock server (file loop, port 61234)
mock-rda file "%~dp0..\..\example_data\thea_session_2.vhdr" --loop --port 61234
echo.
echo Server exited. Press any key to close...
pause >nul