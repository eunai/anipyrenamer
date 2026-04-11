@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
git rev-parse --is-inside-work-tree 2>nul || exit /b 0
set "NAME="
set "EMAIL="
for /f "delims=" %%a in ('git config user.name 2^>nul') do set "NAME=%%a"
for /f "delims=" %%a in ('git config user.email 2^>nul') do set "EMAIL=%%a"
if "%NAME%"=="" (
  echo Git user.name is not set. Set it before committing.
  echo.
  echo For this repo only:
  echo   git config user.name "Your Name"
  echo   git config user.email "you@example.com"
  echo.
  echo For all repos ^(recommended^):
  echo   git config --global user.name "Your Name"
  echo   git config --global user.email "you@example.com"
  exit /b 1
)
if "%EMAIL%"=="" (
  echo Git user.email is not set. Set it before committing.
  echo.
  echo For this repo only:
  echo   git config user.name "Your Name"
  echo   git config user.email "you@example.com"
  echo.
  echo For all repos ^(recommended^):
  echo   git config --global user.name "Your Name"
  echo   git config --global user.email "you@example.com"
  exit /b 1
)
exit /b 0
