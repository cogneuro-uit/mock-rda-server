@echo off
:: Double-clickable diagnostic for Windows lab machines.
::
:: Run this when a launcher or install fails, especially with
:: "permission denied (os error 5)" - Windows Access Denied reported by
:: uv.exe or the .venv console launchers. It pinpoints WHICH binary is
:: blocked and prints the most common environmental causes.

setlocal
cd /d "%~dp0.."
echo === mock-rda diagnostic ===
echo Repo: %CD%
echo.

echo [1] Generated state present?
if exist ".tools\uv.exe"             (echo   .tools\uv.exe             OK) else (echo   .tools\uv.exe             MISSING - run install.bat)
if exist ".venv\Scripts\python.exe"  (echo   .venv\Scripts\python.exe  OK) else (echo   .venv\Scripts\python.exe  MISSING - run install.bat)
if exist ".venv\Scripts\mock-rda.exe" (echo   .venv\Scripts\mock-rda.exe OK) else (echo   .venv\Scripts\mock-rda.exe MISSING - run install.bat)
if exist ".uv-python"                (echo   .uv-python               OK) else (echo   .uv-python               MISSING - run install.bat)
echo.

echo [2] Can each binary execute? (a failure here names the culprit)
echo --- .tools\uv.exe --version:
".tools\uv.exe" --version 2>&1
echo --- .venv\Scripts\python.exe --version:
".venv\Scripts\python.exe" --version 2>&1
echo --- .venv\Scripts\python.exe -m mock_rda.cli --help (module path used by run-server):
".venv\Scripts\python.exe" -m mock_rda.cli --help 2>&1 | findstr /i "usage" || echo   (module path did not start)
echo --- .venv\Scripts\mock-rda.exe --help (shim - NOT used by run-server):
".venv\Scripts\mock-rda.exe" --help 2>&1 | findstr /i "usage" || echo   (shim blocked or missing - harmless, run-server bypasses it)
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

echo [5] Permissions on the project folders:
icacls ".tools" 2>nul | findstr /v "^$"
icacls ".venv" 2>nul | findstr /v "^$"
echo.

echo If step [2] printed "permission denied (os error 5)":
echo   - antivirus is likely still scanning the fresh install; wait a minute
echo     and retry, or ask IT for an exclusion on the project folder
echo   - if it persists, the machine's policy (AppLocker) blocks executables
echo     from user folders - ask IT to allow .tools and .venv, or move the
echo     project to an allowed location
echo.
pause