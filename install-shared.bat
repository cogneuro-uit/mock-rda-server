@echo off
:: Shared, no-admin client setup for multi-user lab machines.
::
:: Installs the client's dependencies into the REPO FOLDER (.deps\), not into
:: any user's site-packages. Every Windows account on this computer can then
:: run the client via run-client-shared.bat - no per-user installs, no admin,
:: nothing outside C:\Experiments\mock-rda-server.
::
:: Needs a Python on PATH (the per-user "py" launcher works - it only runs
:: the interpreter, the packages land in the shared folder).

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
    exit /b 1
)

set "DEPS=%CD%\.deps"
echo Installing client dependencies into %DEPS% (shared, project-local) ...
"%PYTHON%" -m pip install --target "%DEPS%" .

if errorlevel 1 (
    echo.
    echo Offline fallback: installing from the vendored wheelhouse ...
    "%PYTHON%" -m pip install --target "%DEPS%" --no-index --find-links vendor\wheels -r vendor\reqs-flat.txt
    if errorlevel 1 exit /b 1
)

echo.
echo Done. Any user on this machine can now start the client with:
echo     run-client-shared.bat
pause