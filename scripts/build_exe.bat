@echo off
setlocal
cd /d "%~dp0.."

set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "TMPBUILD=%TEMP%\WeChatAutoReply_pyinstaller"

echo ================================================
echo  WeChatAutoReply one-file EXE build
echo ================================================

if not exist "%PY%" (
  echo [ERROR] .venv not found. Run install_dependencies.bat first.
  goto fail
)

echo [1/3] installing pyinstaller into .venv ...
"%PY%" -m pip install --quiet pyinstaller
if errorlevel 1 goto fail

echo [2/3] cleaning old output ...
if exist "%ROOT%\dist" rmdir /s /q "%ROOT%\dist"
if exist "%TMPBUILD%" rmdir /s /q "%TMPBUILD%"

echo [3/3] building WeChatAutoReply.exe ...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name WeChatAutoReply ^
  --distpath "%ROOT%\dist" ^
  --specpath "%TMPBUILD%" --workpath "%TMPBUILD%\work" ^
  --hidden-import win32gui ^
  --hidden-import wechatauto.db ^
  --hidden-import wechatauto.guia ^
  --exclude-module cv2 ^
  --exclude-module imageio_ffmpeg ^
  --exclude-module pyautogui ^
  --exclude-module pyscreeze ^
  --exclude-module mouseinfo ^
  --exclude-module pymsgbox ^
  --exclude-module pygetwindow ^
  --exclude-module pytweening ^
  --exclude-module pyrect ^
  --exclude-module pypinyin ^
  "%ROOT%\wechat_auto_reply.py"
if errorlevel 1 goto fail

echo.
echo [DONE] EXE created: %ROOT%\dist\WeChatAutoReply.exe
echo Config is saved next to the EXE.
goto done

:fail
echo.
echo [ERROR] Build failed.
exit /b 1

:done
pause
exit /b 0
