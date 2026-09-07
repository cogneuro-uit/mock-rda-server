@echo off
REM Double-click launcher: rda-itep viewer against the mock stream.
REM Start one of the start-mock-*.bat launchers first (port 61234).
REM Against a real BrainVision Recorder instead? Run:
REM   rda-itep --host <RECORDER_IP> --port 51244
title rda-itep (port 61234)
rda-itep --port 61234
echo.
echo Viewer exited (exit code %errorlevel%: 2 = could not connect, 3 = no data).
pause