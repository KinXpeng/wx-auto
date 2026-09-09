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

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto fail
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
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
