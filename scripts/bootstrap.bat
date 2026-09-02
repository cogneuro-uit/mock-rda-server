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
    if defined OFFLINE (
        echo ERROR: --offline requires uv to already be present in %UV_INSTALL_DIR% >&2
        exit /b 1
    )
    echo ==^> uv %UV_VERSION% not found in %UV_INSTALL_DIR%; downloading...
    if not exist "%UV_INSTALL_DIR%" mkdir "%UV_INSTALL_DIR%"

    :: Download the pinned release directly from GitHub so nothing is written
    :: outside the repository (the astral.sh installer also drops a receipt in
    :: the user profile). uv.exe/uvx.exe are self-contained static binaries.
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$tmp = New-TemporaryFile; $dir = [IO.Path]::GetDirectoryName($tmp);" ^
        "iwr -useb 'https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip' -OutFile ($dir + '\uv.zip');" ^
        "Expand-Archive ($dir + '\uv.zip') -DestinationPath '%UV_INSTALL_DIR%' -Force;" ^
        "Remove-Item ($dir + '\uv.zip')"
    if not exist "%UV_INSTALL_DIR%\uv.exe" (
        echo Error: uv download failed; check network access to github.com
        exit /b 1
    )
    echo ==^> uv %UV_VERSION% installed from GitHub release
)

if not exist ".python-version" (
    echo 3.12 > .python-version
    echo ==^> wrote .python-version -^> 3.12
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

    :: Create a fresh venv using the managed interpreter.
    if exist ".venv" rmdir /s /q ".venv"
    "%UV_INSTALL_DIR%\uv.exe" venv --python 3.12

    :: Install all locked runtime/test/dev dependencies from the flat requirements
    :: file using the local wheelhouse only.
    set "UV_OFFLINE=1"
    "%UV_INSTALL_DIR%\uv.exe" pip install -r "%VENDOR_DIR%\reqs-flat.txt" --find-links "%VENDOR_DIR%\wheels" --no-index

    :: Install the project itself. Keep the editable link so src/mock_rda is
    :: imported directly. Use the vendored hatchling wheel for the build.
    "%UV_INSTALL_DIR%\uv.exe" pip install -e . --no-deps --find-links "%VENDOR_DIR%\wheels" --no-index
) else (
    echo ==^> syncing dependencies (extra=test, group=dev)...
    "%UV_INSTALL_DIR%\uv.exe" sync --extra test --group dev
)

echo ==^> sanity check: importing the package...
"%UV_INSTALL_DIR%\uv.exe" run python -c "import mock_rda; print('mock-rda ready')"

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
