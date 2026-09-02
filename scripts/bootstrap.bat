@echo off
setlocal EnableDelayedExpansion

:: Pin the vendored uv version. Bump this constant to refresh the binary.
set "UV_VERSION=0.12.9"

for %%F in ("%~dp0..") do set "ROOT=%%~fF"
cd /d "%ROOT%"

:: Set session-scoped environment variables.
set "UV_INSTALL_DIR=%ROOT%\.tools"
set "UV_CACHE_DIR=%ROOT%\.uv-cache"
set "UV_PYTHON_INSTALL_DIR=%ROOT%\.uv-python"
set "UV_NO_MODIFY_PATH=1"
if not defined UV_PYTHON_PREFERENCE set "UV_PYTHON_PREFERENCE=only-managed"
echo "!PATH!" | findstr /I /C:"%UV_INSTALL_DIR%" >nul || set "PATH=%UV_INSTALL_DIR%;%PATH%"

:: Use the vendored python-build-standalone mirror if it exists. We set this
:: early so both online and offline paths can install managed Python locally.
if exist "%ROOT%\vendor\python" (
    set "UV_PYTHON_INSTALL_MIRROR=file://%ROOT%/vendor/python"
)

:: Only AMD64 is supported by the upstream Windows builds.
if /I not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    echo Error: this bootstrap targets x86_64 Windows, but PROCESSOR_ARCHITECTURE=%PROCESSOR_ARCHITECTURE%
    exit /b 1
)

:: Parse arguments. The only flag we honor is --offline.
set "OFFLINE="
for %%A in (%*) do (
    if "%%~A"=="--offline" set "OFFLINE=1"
)

if not exist "%UV_INSTALL_DIR%\uv.exe" (
    if exist "%ROOT%\vendor\uv-bin\windows-x86_64\uv.exe" (
        echo ==^> uv %UV_VERSION% found in vendor\uv-bin; copying to .tools ...
        if not exist "%UV_INSTALL_DIR%" mkdir "%UV_INSTALL_DIR%"
        copy /Y "%ROOT%\vendor\uv-bin\windows-x86_64\uv.exe" "%UV_INSTALL_DIR%\uv.exe" >nul
        copy /Y "%ROOT%\vendor\uv-bin\windows-x86_64\uvx.exe" "%UV_INSTALL_DIR%\uvx.exe" >nul
        copy /Y "%ROOT%\vendor\uv-bin\windows-x86_64\uvw.exe" "%UV_INSTALL_DIR%\uvw.exe" >nul
        if not exist "%UV_INSTALL_DIR%\uv.exe" (
            echo ERROR: failed to copy vendored uv to %UV_INSTALL_DIR% 1>&2
            exit /b 1
        )
    ) else (
        echo ==^> uv %UV_VERSION% not found in %UV_INSTALL_DIR% and no vendored copy; trying python bootstrap ...
        set "PYTHON="
        for %%P in (python python3 py) do (
            if not defined PYTHON (
                "%%P" --version >nul 2>&1 && set "PYTHON=%%P"
            )
        )
        if not defined PYTHON (
            echo ERROR: no Python interpreter found and no vendored uv.exe available. 1>&2
            echo Run scripts\vendor.bat on an internet-connected machine first, or use a network bootstrap. 1>&2
            exit /b 1
        )
        "%PYTHON%" "%~dp0pyvendor.py" ensure-uv
        if not exist "%UV_INSTALL_DIR%\uv.exe" (
            echo ERROR: failed to bootstrap uv via python 1>&2
            exit /b 1
        )
    )
    echo ==^> uv %UV_VERSION% installed
)

if not exist ".python-version" (
    echo 3.12 > .python-version
    echo ==^> wrote .python-version -> 3.12
)

set "VENDOR_DIR=%ROOT%\vendor"
if defined OFFLINE if exist "%VENDOR_DIR%\wheels" (
    echo ==^> offline bootstrap: using vendored Python tarball and wheels...

    :: Keep the bootstrap entirely inside the repo: don't write a persistent
    :: uv cache for the offline install path.
    set "UV_NO_CACHE=1"

    :: Ensure the managed Python 3.12 is installed from the local mirror.
    :: --no-bin: skip uv's default %LOCALAPPDATA% python symlink so nothing
    :: escapes the project.
    "%UV_INSTALL_DIR%\uv.exe" python install 3.12 --offline --no-bin
    if errorlevel 1 exit /b 1

    :: Create a fresh venv using the managed interpreter.
    if exist ".venv" rmdir /s /q ".venv"
    "%UV_INSTALL_DIR%\uv.exe" venv --python 3.12
    if errorlevel 1 exit /b 1

    :: Install all locked runtime/test/dev dependencies from the flat requirements
    :: file using the local wheelhouse only.
    set "UV_OFFLINE=1"
    "%UV_INSTALL_DIR%\uv.exe" pip install -r "%VENDOR_DIR%\reqs-flat.txt" --find-links "%VENDOR_DIR%\wheels" --no-index
    if errorlevel 1 exit /b 1

    :: Install the project itself. Keep the editable link so src/mock_rda is
    :: imported directly. Use the vendored hatchling wheel for the build.
    "%UV_INSTALL_DIR%\uv.exe" pip install -e . --no-deps --find-links "%VENDOR_DIR%\wheels" --no-index
    if errorlevel 1 exit /b 1
) else (
    echo ==^> syncing dependencies (extra=test, group=dev)...
    "%UV_INSTALL_DIR%\uv.exe" sync --extra test --group dev
    if errorlevel 1 exit /b 1
)

echo ==^> sanity check: importing the package...
"%UV_INSTALL_DIR%\uv.exe" run python -c "import mock_rda; print('mock-rda ready')"
if errorlevel 1 exit /b 1

echo.
if defined OFFLINE (
    echo Bootstrap complete (offline). Everything lives inside this repository:
    echo   .tools\      vendored uv binary
    echo   .uv-python\  managed Python interpreter (installed from vendor/python mirror)
    echo   .venv\       virtual environment (installed from vendor/wheels)
    echo.
    echo Daily use:
    echo   scripts\env.bat              once per shell session
    echo   uv run pytest -q -rs        run the test suite
    echo   uv run ruff check .         lint
    echo.
    echo You can also call .venv\Scripts\* directly without any env vars:
    echo   .venv\Scripts\pytest -q -rs
    echo   .venv\Scripts\mock-rda --help
    echo.
    echo To refresh the vendor\ tree after changing dependencies, run on an internet
    echo machine:
    echo   scripts\vendor.bat
) else (
    echo Bootstrap complete. Everything lives inside this repository:
    echo   .tools\      vendored uv binary
    echo   .uv-python\  managed Python interpreter (only-managed default)
    echo   .uv-cache\   package cache
    echo   .venv\       virtual environment
    echo.
    echo Daily use:
    echo   scripts\env.bat              once per shell session
    echo   uv run pytest -q -rs        run the test suite
    echo   uv run ruff check .         lint
    echo.
    echo You can also call .venv\Scripts\* directly without any env vars:
    echo   .venv\Scripts\pytest -q -rs
    echo   .venv\Scripts\mock-rda --help
    echo.
    echo To use a system interpreter instead of the managed build:
    echo   set UV_PYTHON_PREFERENCE=system
    echo   scripts\env.bat
    echo   uv sync --extra test --group dev
    echo.
    echo To bump the pinned uv version, edit the UV_VERSION constant in:
    echo   scripts\bootstrap.bat
    echo   scripts\bootstrap.sh
    echo.
    echo For fully offline/air-gapped installs, see the "Offline / air-gapped install"
    echo section in README.md
)
