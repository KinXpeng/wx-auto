@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   WeChat Auto Reply - Install Dependencies
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo Please install Python 3.10+ and enable "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo Existing .venv is invalid, recreating it ...
    rmdir /s /q ".venv"
  )
)

if not exist "%VENV_PY%" (
  python -m venv .venv
  if errorlevel 1 goto fail
)

"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto fail
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail

goto done

:fail
echo.
echo [ERROR] Failed to create .venv or install dependencies.
pause
exit /b 1

:done
echo.
echo Done. Now run the VBS launcher.
pause
