@echo off
REM Launch the Electron desktop app (run after the frontend is up on :3000).
cd /d "%~dp0..\desktop"

REM CRITICAL: VS Code's integrated terminal sets ELECTRON_RUN_AS_NODE=1, which makes
REM electron.exe run as plain Node (app becomes undefined -> crash). Clear it here so
REM the desktop app launches correctly no matter where this is started from.
set "ELECTRON_RUN_AS_NODE="

if not exist "node_modules" (
    echo Installing Electron - one time, downloads ~100MB...
    npm install
)

REM If the Electron binary failed to extract during install, fix it before launching.
if not exist "node_modules\electron\dist\electron.exe" (
    echo Electron binary missing - repairing install...
    node node_modules\electron\install.js
)

echo.
echo Launching Fanar Agent desktop app...
npm start
