@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "TMPBUILD=%TEMP%\wx-auto_pyinstaller"

echo ================================================
echo  wx-auto one-file EXE build
echo ================================================

if not exist "%PY%" (
  echo [ERROR] .venv not found. Run install_dependencies.bat first.
  goto fail
)

rem --- validate the interpreter itself; a stale venv can leave python.exe present
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] .venv exists but its Python interpreter is invalid.
  echo Run install_dependencies.bat to recreate the environment.
  goto fail
)

rem --- install PyInstaller only when the module is missing
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo [1/3] installing pyinstaller into .venv ...
  "%PY%" -m pip install --quiet pyinstaller
  if errorlevel 1 goto fail
) else (
  echo [1/3] pyinstaller already installed, skip
)

echo [2/3] cleaning old output ...
if exist "%ROOT%\dist" rmdir /s /q "%ROOT%\dist"
if exist "%TMPBUILD%" rmdir /s /q "%TMPBUILD%"

rem --- optional UPX compression (smaller exe):
rem     put upx.exe at scripts\upx\upx.exe or set UPX_HOME env var.
rem     skipped automatically if upx is not present.
set "UPXDIR="
if exist "%~dp0upx\upx.exe" set "UPXDIR=%~dp0upx"
if not defined UPXDIR if defined UPX_HOME if exist "%UPX_HOME%\upx.exe" set "UPXDIR=%UPX_HOME%"

rem --- restrict PATH so PyInstaller does not scan huge npm/pnpm trees
set "SAFE_PATH=%SystemRoot%\system32;%SystemRoot%;%ROOT%\.venv\Scripts"
set "PATH=%SAFE_PATH%"
set "PYINSTALLER_ZLIB_COMPRESSION_LEVEL=9"

set "ARGS=--noconfirm --clean --onefile --windowed --optimize 2 --name wx-auto --distpath "%ROOT%\dist" --specpath "%TMPBUILD%" --workpath "%TMPBUILD%\work" --hidden-import win32gui --hidden-import wechatauto.db --hidden-import wechatauto.guia --hidden-import zstandard --exclude-module numpy --exclude-module cv2 --exclude-module imageio_ffmpeg --exclude-module pyautogui --exclude-module pyscreeze --exclude-module mouseinfo --exclude-module pymsgbox --exclude-module pygetwindow --exclude-module pytweening --exclude-module pyrect --exclude-module pypinyin --exclude-module PIL._avif --exclude-module PIL.AvifImagePlugin --exclude-module PIL._webp --exclude-module PIL.WebPImagePlugin"

echo [3/3] building wx-auto.exe ...
if defined UPXDIR (
  echo UPX enabled: %UPXDIR%\upx.exe
  "%PY%" -m PyInstaller --upx-dir "%UPXDIR%" %ARGS% "%ROOT%\wechat_auto_reply.py"
) else (
  "%PY%" -m PyInstaller %ARGS% "%ROOT%\wechat_auto_reply.py"
)
if errorlevel 1 goto fail

echo.
echo [DONE] EXE created: %ROOT%\dist\wx-auto.exe
for %%F in ("%ROOT%\dist\wx-auto.exe") do echo Size: %%~zF bytes
echo Config is saved next to the EXE.
goto done

:fail
echo.
echo [ERROR] Build failed. See messages above.
pause
exit /b 1

:done
pause
exit /b 0
