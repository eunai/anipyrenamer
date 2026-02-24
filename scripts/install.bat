@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
pip install -e .
exit /b %ERRORLEVEL%
