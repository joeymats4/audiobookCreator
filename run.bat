@echo off
setlocal
cd /d "%~dp0"

REM --- Find Python (prefer a real install; skip the Microsoft Store stub) -----
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
  for /f "delims=" %%p in ('dir /b /s "%LOCALAPPDATA%\Programs\Python\Python3*\python.exe" 2^>nul') do if not defined PY set "PY=%%p"
)
if not defined PY (
  for /f "delims=" %%p in ('dir /b /s "%ProgramFiles%\Python3*\python.exe" 2^>nul') do if not defined PY set "PY=%%p"
)
if not defined PY (
  for /f "delims=" %%p in ('where python 2^>nul') do echo %%p | find /i "WindowsApps" >nul || if not defined PY set "PY=%%p"
)
if not defined PY (
  echo.
  echo   Python was not found.
  echo   Install Python 3 from https://www.python.org/downloads/
  echo   and make sure to tick "Add Python to PATH" during setup.
  echo.
  pause
  exit /b 1
)

REM --- Create the virtual environment on first run ---------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  "%PY%" -m venv .venv || ( echo Failed to create the virtual environment. & pause & exit /b 1 )
)

set "VENV_PY=.venv\Scripts\python.exe"

REM --- Install / update dependencies -----------------------------------------
echo Installing dependencies (first run may take a minute)...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt || ( echo Failed to install dependencies. & pause & exit /b 1 )

REM --- Launch -----------------------------------------------------------------
echo Opening http://127.0.0.1:5000 ...
start "" http://127.0.0.1:5000
"%VENV_PY%" app.py

pause
