@echo off
setlocal EnableDelayedExpansion

:: Project-local uv environment settings for Windows cmd.exe.
:: Run this script once per shell session (do not double-click to execute):
::
::   scripts\env.bat
::
:: Everything is kept inside the repository so no admin rights, home-directory
:: state, or system Python is required. Set UV_PYTHON_PREFERENCE=system before
:: calling if you want to use a system interpreter instead of the managed build.

:: %%~fF resolves the full path (collapses the ".." component); %~dp0.. alone
:: does not, so it must not be used directly.
for %%F in ("%~dp0..") do set "ROOT=%%~fF"

set "UV_INSTALL_DIR=%ROOT%\.tools"
set "UV_CACHE_DIR=%ROOT%\.uv-cache"
set "UV_PYTHON_INSTALL_DIR=%ROOT%\.uv-python"
set "UV_TOOL_DIR=%ROOT%\.uv-tools"
set "UV_TOOL_BIN_DIR=%ROOT%\.uv-tools\bin"
set "UV_NO_MODIFY_PATH=1"
if not defined UV_PYTHON_PREFERENCE set "UV_PYTHON_PREFERENCE=only-managed"

:: Prepend the vendored uv directory to PATH only once per session.
echo "!PATH!" | findstr /I /C:"%UV_INSTALL_DIR%" >nul || set "PATH=%UV_INSTALL_DIR%;%PATH%"

endlocal & set "UV_INSTALL_DIR=%UV_INSTALL_DIR%" & set "UV_CACHE_DIR=%UV_CACHE_DIR%" & set "UV_PYTHON_INSTALL_DIR=%UV_PYTHON_INSTALL_DIR%" & set "UV_NO_MODIFY_PATH=%UV_NO_MODIFY_PATH%" & set "UV_PYTHON_PREFERENCE=%UV_PYTHON_PREFERENCE%" & set "PATH=%PATH%"
