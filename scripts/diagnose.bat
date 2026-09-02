@echo off
:: Double-clickable diagnostic for Windows lab machines.
::
:: Run this when a launcher or install fails, especially with
:: "permission denied (os error 5)" / "uv trampoline failed to spawn Python
:: child process". It pinpoints WHICH binary is blocked and prints the
:: most common environmental causes.

setlocal EnableDelayedExpansion
cd /d "%~dp0.."
echo === mock-rda diagnostic ===
echo Repo: %CD%
echo.

echo [1] Generated state present?
if exist ".tools\uv.exe"              (echo   .tools\uv.exe              OK) else (echo   .tools\uv.exe              MISSING - run install.bat)
if exist ".venv\Scripts\python.exe"   (echo   .venv\Scripts\python.exe   OK) else (echo   .venv\Scripts\python.exe   MISSING - run install.bat)
if exist ".venv\Scripts\mock-rda.exe" (echo   .venv\Scripts\mock-rda.exe OK) else (echo   .venv\Scripts\mock-rda.exe MISSING - run install.bat)
if exist ".uv-python"                (echo   .uv-python                 OK) else (echo   .uv-python                 MISSING - run install.bat)
echo.

echo [2] Binary execution tests (a failure here names the culprit)
echo --- .tools\uv.exe --version:
".tools\uv.exe" --version 2>&1
echo.

echo --- resolving the REAL interpreter from .venv\pyvenv.cfg ...
set "PYHOME="
for /f "tokens=2 delims==" %%i in ('findstr /b "home" ".venv\pyvenv.cfg"') do set "PYHOME=%%i"
if defined PYHOME if "%PYHOME:~0,1%"==" " set "PYHOME=%PYHOME:~1%"
if not defined PYHOME (
    echo   could not read .venv\pyvenv.cfg - is the venv installed^?
) else (
    echo   interpreter dir: %PYHOME%
    if exist "%PYHOME%\python.exe" (
        echo --- direct test of the real interpreter^: python.exe --version:
        "%PYHOME%\python.exe" --version 2>&1
        echo   ^(this is the binary the uv trampoline failed to spawn^)
    ) else (
        echo   MISSING: %PYHOME%\python.exe
        echo   The interpreter was quarantined/deleted by antivirus or policy^!
        echo   Ask IT for an exclusion on %CD%, then re-run install.bat.
    )
)
echo.

echo --- .venv\Scripts\python.exe -m mock_rda.cli --help (path used by run-server):
".venv\Scripts\python.exe" -m mock_rda.cli --help 2>&1 | findstr /i "usage" || echo   failed ^(expected if the direct test above failed^)
echo --- .venv\Scripts\mock-rda.exe --help (shim^):
".venv\Scripts\mock-rda.exe" --help 2>&1 | findstr /i "usage" || echo   failed ^(same cause, harmless^)
echo.

echo [3] Repo location risk (OneDrive/Documents/Desktop lock files):
echo %CD% | findstr /i "OneDrive Documents Desktop" >nul && (
    echo   WARNING: the repo is in a synced/protected folder.
    echo   Move it to a plain local path, e.g. C:\mock-rda-server, and re-run install.bat.
) || echo   path looks safe
echo.

echo [4] Windows reserved port ranges (would block binding 51244):
netsh interface ipv4 show excludedportrange protocol=tcp 2>nul | findstr "51244" >nul && echo   WARNING: 51244 is in an excluded range || echo   51244 not in an excluded range
echo.

echo [5] System Python installations (fallback if the managed one is blocked):
where python 2>nul || echo   no "python" on PATH
py -0p 2>nul || echo   no py launcher
echo.

echo [6] Permissions on the project folders:
icacls ".tools" 2>nul | findstr /v "^$"
icacls ".venv" 2>nul | findstr /v "^$"
echo.

echo ================= Interpretation =================
echo If uv.exe runs but the REAL python.exe fails with os error 5 (or is
echo missing), the machine's antivirus or lab policy is blocking the
echo portable CPython in .uv-python\ - a frequent antivirus false-positive
echo against unsigned portable Python builds. uv.exe is left alone, which
echo is why install.bat completed and only Python launch fails.
echo.
echo FIX, in this order:
echo   1. Wait ~5 minutes and re-run this script - antivirus scans of a
echo      fresh install are often slow and the block can be transient.
echo   2. Ask IT to add an antivirus exclusion for the folder:
echo      %CD%
echo      then re-run install.bat (it restores quarantined files).
echo   3. If step [5] listed a system Python 3.12, bootstrap against it
echo      instead of the managed one:
echo      set UV_PYTHON_PREFERENCE=system
echo      install.bat
echo      (the vendored wheels are CPython 3.12 builds; a system 3.11
echo      will NOT work offline)
echo.
pause