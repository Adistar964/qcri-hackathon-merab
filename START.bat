@echo off
REM ===================================================================
REM  Fanar Agent - one-click launcher (no PowerShell policy issues)
REM  Double-click this file. It starts backend + frontend + desktop app.
REM ===================================================================
setlocal
cd /d "%~dp0"

REM --- First-run: make sure the API key is set ---
if not exist "backend\.env" (
    copy "backend\.env.example" "backend\.env" >nul
    echo.
    echo  ------------------------------------------------------------
    echo   Created backend\.env
    echo   Opening it now - paste your FANAR_API_KEY, SAVE, then
    echo   run START.bat again.
    echo  ------------------------------------------------------------
    echo.
    notepad "backend\.env"
    pause
    exit /b
)

echo.
echo  Starting Fanar Agent...
echo   1/3 backend   (http://localhost:8008)
echo   2/3 frontend  (http://localhost:3000)
echo   3/3 desktop app
echo.

REM --- Launch the real Chrome the agent drives over CDP. REQUIRED for the MOI/Tawtheeq
REM     sign-in: it uses reCAPTCHA Enterprise, which rejects headless/automated browsers, so the
REM     ONLY reliable way to pass it is a real Chrome. Sign into Google in the window it opens ONCE.
echo  0/3 launching the real Chrome for secure portal sign-in (reCAPTCHA)...
call "%~dp0start-chrome-debug.bat"
echo.

start "Fanar Backend"  cmd /k "%~dp0scripts\start-backend.bat"
start "Fanar Frontend" cmd /k "%~dp0scripts\start-frontend.bat"

echo  Waiting ~15s for the UI before launching the desktop app...
timeout /t 15 /nobreak >nul

start "Fanar Desktop"  cmd /k "%~dp0scripts\start-desktop.bat"

echo.
echo  Three windows opened. Close them to stop everything.
echo  (Tip: press Ctrl+Shift+F anywhere to show/hide the agent.)
echo.
exit /b
