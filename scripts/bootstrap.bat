@echo off
setlocal EnableDelayedExpansion

:: Pin the vendored uv version. Bump this constant to refresh the binary.
set "UV_VERSION=0.12.9"

for %%F in ("%~dp0..") do set "ROOT=%%~fF"
cd /d "%ROOT%"

:: Parse arguments before calling env.bat because --system-python controls
:: how env.bat configures uv's Python discovery.
set "OFFLINE="
set "SYSTEM_PYTHON="
for %%A in (%*) do (
    if "%%~A"=="--offline" set "OFFLINE=1"
    if "%%~A"=="--system-python" set "SYSTEM_PYTHON=1"
)

if defined SYSTEM_PYTHON set "MOCK_RDA_SYSTEM_PYTHON=1"

call "%~dp0env.bat"

:: Only AMD64 is supported by the upstream Windows builds.
if /I not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    echo Error: this bootstrap targets x86_64 Windows, but PROCESSOR_ARCHITECTURE=%PROCESSOR_ARCHITECTURE%
    exit /b 1
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
    if defined SYSTEM_PYTHON (
        echo ==^> offline bootstrap: using vendored wheels + system Python...
    ) else (
        echo ==^> offline bootstrap: using vendored Python tarball and wheels...
    )

    :: Keep the bootstrap entirely inside the repo: don't write a persistent
    :: uv cache for the offline install path.
    set "UV_NO_CACHE=1"

    :: In system-python mode we do NOT install a managed interpreter.
    if not defined SYSTEM_PYTHON (
        :: Ensure the managed Python 3.12 is installed from the local mirror.
        :: --no-bin: skip uv's default %LOCALAPPDATA% python symlink so nothing
        :: escapes the project.
        "%UV_INSTALL_DIR%\uv.exe" python install 3.12 --offline --no-bin
        if errorlevel 1 exit /b 1
    )

    :: Create a fresh venv. For system mode, uv discovers the system interpreter
    :: when managed pythons are forbidden; the user may override with UV_PYTHON.
    :: Vendored wheels are cp312, so 3.12 is required for offline installs.
    :: Always seed pip: harmless for managed mode and avoids "externally managed"
    :: errors with system interpreters.
    if exist ".venv" rmdir /s /q ".venv"
    if defined UV_PYTHON (
        "%UV_INSTALL_DIR%\uv.exe" venv --python "%UV_PYTHON%" --seed
    ) else (
        "%UV_INSTALL_DIR%\uv.exe" venv --python 3.12 --seed
    )
    if errorlevel 1 (
        echo ERROR: system Python 3.12 required (vendored wheels are cp312).
        echo Found system Pythons:
        "%UV_INSTALL_DIR%\uv.exe" python find 3.12 2>nul
        echo Run without --system-python to use the managed Python, or set UV_PYTHON
        echo to an explicit system Python 3.12 path, or ask IT to install Python 3.12.
        exit /b 1
    )

    :: Install all locked runtime/test/dev dependencies from the flat requirements
    :: file using the local wheelhouse only. If uv pip fails on an EXTERNALLY-MANAGED
    :: system interpreter, fall back to the venv's seeded pip.
    set "UV_OFFLINE=1"
    "%UV_INSTALL_DIR%\uv.exe" pip install -r "%VENDOR_DIR%\reqs-flat.txt" --find-links "%VENDOR_DIR%\wheels" --no-index
    if errorlevel 1 if defined SYSTEM_PYTHON (
        .venv\Scripts\python.exe -m pip install --no-index --find-links "%VENDOR_DIR%\wheels" -r "%VENDOR_DIR%\reqs-flat.txt"
    )
    if errorlevel 1 exit /b 1

    :: Install the project itself. Keep the editable link so src/mock_rda is
    :: imported directly. Use the vendored hatchling wheel for the build.
    "%UV_INSTALL_DIR%\uv.exe" pip install -e . --no-deps --find-links "%VENDOR_DIR%\wheels" --no-index
    if errorlevel 1 if defined SYSTEM_PYTHON (
        .venv\Scripts\python.exe -m pip install --no-index --find-links "%VENDOR_DIR%\wheels" -e . --no-deps
    )
    if errorlevel 1 exit /b 1
) else if defined SYSTEM_PYTHON (
    echo ==^> syncing dependencies (extra=test, group=dev) against system Python...
    "%UV_INSTALL_DIR%\uv.exe" sync --extra test --group dev
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
if defined OFFLINE if defined SYSTEM_PYTHON (
    echo Bootstrap complete (offline + system Python). Everything lives inside this repository:
    echo   .tools\      vendored uv binary
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
    echo This venv uses the system Python interpreter. Antivirus did not block it
    echo because it is an IT-installed, signed binary. To switch back to uv's managed
    echo Python, re-run bootstrap without the --system-python flag.
    echo.
    echo To refresh the vendor\ tree after changing dependencies, run on an internet
    echo machine:
    echo   scripts\vendor.bat
) else if defined OFFLINE (
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
) else if defined SYSTEM_PYTHON (
    echo Bootstrap complete (system Python). Everything lives inside this repository:
    echo   .tools\      vendored uv binary
    echo   .uv-cache\   package cache
    echo   .venv\       virtual environment (linked to the system interpreter)
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
    echo This venv uses the system Python interpreter. Antivirus did not block it
    echo because it is an IT-installed, signed binary. To switch back to uv's managed
    echo Python, re-run bootstrap without the --system-python flag.
    echo.
    echo To bump the pinned uv version, edit the UV_VERSION constant in:
    echo   scripts\bootstrap.bat
    echo   scripts\bootstrap.sh
    echo.
    echo For fully offline/air-gapped installs, see the "Offline / air-gapped install"
    echo section in README.md
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
    echo   set MOCK_RDA_SYSTEM_PYTHON=1
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
