@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
if exist .venv (
    echo .venv already exists. Activate it and run scripts\install-dev.bat if needed.
    exit /b 0
)
py -3.13 -m venv .venv
call .venv\Scripts\activate.bat
pip install -e ".[dev]"
echo Created .venv and installed anipyrenamer[dev]. Activate with: .venv\Scripts\activate.bat
exit /b %ERRORLEVEL%
